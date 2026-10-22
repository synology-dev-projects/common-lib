import logging
import math
from datetime import date, datetime
from typing import List, Dict, Any, Optional, Tuple, Sequence
import numpy as np

from common_lib.extractors.ibkr_options import OptionContractRecord

logger = logging.getLogger("quant.calculators.gexdex")


# ==============================================================================
# Pure Python / NumPy BSM Analytical Greek Solver (Zero scipy)
# ==============================================================================

def norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard Normal Probability Density Function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bsm_delta_gamma(
    spot: float,
    strike: float,
    tau: float,
    vol: float,
    r: float = 0.045,
    q: float = 0.013
) -> Tuple[float, float, float]:
    """
    Calculates analytical Black-Scholes-Merton Greeks:
    Returns (call_delta, put_delta, gamma).
    """
    if tau <= 1e-5 or vol <= 1e-4 or spot <= 1e-4 or strike <= 1e-4:
        # Near expiration or zero volatility boundary
        call_d = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        put_d = call_d - 1.0
        return call_d, put_d, 0.0

    sqrt_tau = math.sqrt(tau)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * tau) / (vol * sqrt_tau)

    df_q = math.exp(-q * tau)
    nd1 = norm_cdf(d1)
    npd1 = norm_pdf(d1)

    call_delta = df_q * nd1
    put_delta = df_q * (nd1 - 1.0)
    gamma = (df_q * npd1) / (spot * vol * sqrt_tau)

    return call_delta, put_delta, gamma


# ==============================================================================
# In-Memory GEX/DEX Calculation Engine
# ==============================================================================

class GexDexCalculator:
    """
    Calculates full options gamma & delta exposure distributions, structural walls,
    zero-flip level, and gamma centroid from option contracts.
    Emits an unadulterated TradingEdge-compatible dictionary suitable for
    downstream storage, PWA cockpit APIs, and chart generation.
    """
    def __init__(
        self,
        risk_free_rate: float = 0.045,
        default_dividend_yield: float = 0.013,
        default_fallback_iv: float = 0.20
    ):
        self.r = float(risk_free_rate)
        self.q = float(default_dividend_yield)
        self.fallback_iv = max(0.01, float(default_fallback_iv))

    def calculate(
        self,
        contracts: Sequence[OptionContractRecord],
        spot_price: float,
        ticker: str,
        target_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Computes GEX/DEX surface across strikes and expirations.
        """
        sym = ticker.upper().strip()
        spot = float(spot_price)
        if spot <= 0.0:
            raise ValueError(f"Spot price must be > 0.0 to compute GEX/DEX for {sym}. Received: {spot}")

        if not contracts:
            logger.warning(f"Empty contracts list provided for {sym}.")
            return self._empty_payload(sym, spot)

        today = target_date or date.today()

        # Step 1: Process individual contracts and compute GEX/DEX
        # Structure: strike -> expiration_str -> dict(metrics)
        strike_exp_map: Dict[float, Dict[str, Dict[str, float]]] = {}
        all_strikes_set = set()

        for c in contracts:
            k = float(c.strike)
            if k <= 0.0:
                continue

            all_strikes_set.add(k)
            exp_formatted = self._format_expiration_date(c.expiration)
            dte = max(0, int(c.dte))
            # Tau in years (calendar fraction)
            tau = max(dte / 365.25, 0.5 / 365.25 if dte == 0 else 1e-4)

            # Resolve Open Interest (fallback to volume if OI unavailable)
            oi = float(c.open_interest or 0.0)
            if oi <= 0.0 and c.volume and c.volume > 0:
                oi = float(c.volume)

            # Resolve IV and Greeks with BSM fallback
            iv = c.implied_vol if (c.implied_vol and c.implied_vol > 0.001) else self.fallback_iv
            b_call_d, b_put_d, b_gamma = bsm_delta_gamma(spot, k, tau, iv, self.r, self.q)

            delta = c.delta if (c.delta is not None and not math.isnan(c.delta)) else (b_call_d if c.right == "C" else b_put_d)
            gamma = c.gamma if (c.gamma is not None and not math.isnan(c.gamma) and c.gamma > 0.0) else b_gamma

            # GEX Scaling: Dollar GEX per 1% move in spot: 0.01 * Spot^2 * Gamma * OI * 100
            # Both Call and Put GEX are recorded as positive unsigned magnitudes
            contract_gex = 0.01 * (spot ** 2) * gamma * oi * 100.0

            # DEX Scaling: Dollar Delta: Delta * OI * 100 * Spot
            contract_dex = delta * oi * 100.0 * spot

            if k not in strike_exp_map:
                strike_exp_map[k] = {}
            if exp_formatted not in strike_exp_map[k]:
                strike_exp_map[k][exp_formatted] = {
                    "call_gex": 0.0,
                    "put_gex": 0.0,
                    "call_dex": 0.0,
                    "put_dex": 0.0
                }

            if c.right == "C":
                strike_exp_map[k][exp_formatted]["call_gex"] += contract_gex
                strike_exp_map[k][exp_formatted]["call_dex"] += contract_dex
            else:
                strike_exp_map[k][exp_formatted]["put_gex"] += contract_gex
                # Put DEX stored as positive magnitude for consistency with call_put_ratio
                strike_exp_map[k][exp_formatted]["put_dex"] += abs(contract_dex)

        # Step 2: Build strike-level aggregations
        sorted_strikes = sorted(all_strikes_set)
        strikes_payload: List[Dict[str, Any]] = []
        rolling_strikes: List[float] = []
        rolling_call_gex: List[float] = []
        rolling_put_gex: List[float] = []
        rolling_call_dex: List[float] = []
        rolling_put_dex: List[float] = []

        tot_call_gex = 0.0
        tot_put_gex = 0.0
        tot_call_dex = 0.0
        tot_put_dex = 0.0

        max_cg = -1.0
        call_wall = spot
        max_pg = -1.0
        put_wall = spot

        strike_net_gex_map: Dict[float, float] = {}
        centroid_num = 0.0
        centroid_den = 0.0

        for k in sorted_strikes:
            exp_dict = strike_exp_map.get(k, {})
            stk_cg = sum(m["call_gex"] for m in exp_dict.values())
            stk_pg = sum(m["put_gex"] for m in exp_dict.values())
            stk_cd = sum(m["call_dex"] for m in exp_dict.values())
            stk_pd = sum(m["put_dex"] for m in exp_dict.values())

            tot_call_gex += stk_cg
            tot_put_gex += stk_pg
            tot_call_dex += stk_cd
            tot_put_dex += stk_pd

            net_stk_gex = stk_cg - stk_pg
            strike_net_gex_map[k] = net_stk_gex
            abs_net = abs(net_stk_gex)
            centroid_num += k * abs_net
            centroid_den += abs_net

            # Track global structural walls
            if stk_cg > max_cg:
                max_cg = stk_cg
                call_wall = k
            if stk_pg > max_pg:
                max_pg = stk_pg
                put_wall = k

            strikes_payload.append({
                "strike": k,
                "call_gex": round(stk_cg, 2),
                "put_gex": round(stk_pg, 2),
                "call_dex": round(stk_cd, 2),
                "put_dex": round(stk_pd, 2),
                "expirations": {
                    exp_k: {
                        "call_gex": round(v["call_gex"], 2),
                        "put_gex": round(v["put_gex"], 2),
                        "call_dex": round(v["call_dex"], 2),
                        "put_dex": round(v["put_dex"], 2)
                    }
                    for exp_k, v in sorted(exp_dict.items())
                }
            })

            rolling_strikes.append(k)
            rolling_call_gex.append(round(stk_cg, 2))
            rolling_put_gex.append(round(stk_pg, 2))
            rolling_call_dex.append(round(stk_cd, 2))
            rolling_put_dex.append(round(stk_pd, 2))

        # Step 3: Compute global metrics & zero gamma flip level
        net_gex = tot_call_gex - tot_put_gex
        net_dex = tot_call_dex - tot_put_dex

        zero_gex_level = self._compute_zero_gex_level(sorted_strikes, strike_net_gex_map, spot, call_wall, put_wall)
        gamma_centroid = round(centroid_num / centroid_den, 2) if centroid_den > 0.0 else spot
        call_put_ratio = round(tot_call_gex / tot_put_gex, 2) if tot_put_gex > 0.0 else 1.0

        gamma_regime = "Positive Gamma (+GEX)" if net_gex >= 0.0 else "Negative Gamma (-GEX)"

        return {
            "ticker": sym,
            "spot_price": round(spot, 2),
            "spotPrice": round(spot, 2),
            "call_wall": round(call_wall, 2),
            "gex_wall": round(call_wall, 2),
            "put_wall": round(put_wall, 2),
            "zero_gex_level": round(zero_gex_level, 2),
            "zero_gamma_flip": round(zero_gex_level, 2),
            "gamma_centroid": gamma_centroid,
            "net_gex": round(net_gex, 2),
            "net_dex": round(net_dex, 2),
            "total_call_gex": round(tot_call_gex, 2),
            "total_put_gex": round(tot_put_gex, 2),
            "total_call_dex": round(tot_call_dex, 2),
            "total_put_dex": round(tot_put_dex, 2),
            "call_put_ratio": call_put_ratio,
            "callPutRatio": call_put_ratio,
            "call_split_ratio": call_put_ratio,
            "gamma_regime": gamma_regime,
            "strikes": strikes_payload,
            "rolling": {
                "strikes": rolling_strikes,
                "call_gex": rolling_call_gex,
                "put_gex": rolling_put_gex,
                "call_dex": rolling_call_dex,
                "put_dex": rolling_put_dex
            }
        }

    @staticmethod
    def _compute_zero_gex_level(
        sorted_strikes: List[float],
        strike_net_map: Dict[float, float],
        spot: float,
        call_wall: float,
        put_wall: float
    ) -> float:
        """
        Calculates exact zero gamma flip level via cumulative net gamma zero-crossing.
        """
        if not sorted_strikes:
            return spot

        cum_gex = 0.0
        prev_k = None
        prev_cum = None
        for k in sorted_strikes:
            cum_gex += strike_net_map[k]
            if prev_cum is not None and ((prev_cum <= 0.0 and cum_gex > 0.0) or (prev_cum >= 0.0 and cum_gex < 0.0)):
                d_cum = cum_gex - prev_cum
                if d_cum != 0.0:
                    t = (0.0 - prev_cum) / d_cum
                    return prev_k + t * (k - prev_k)
            prev_k = k
            prev_cum = cum_gex

        # Fallback if no zero crossing within sampled strike range
        if cum_gex > 0.0:
            return round(put_wall * 0.96, 2)
        else:
            return round(call_wall * 1.04, 2)

    @staticmethod
    def _format_expiration_date(exp_str: str) -> str:
        """Formats YYYYMMDD into ISO YYYY-MM-DD."""
        clean = str(exp_str).strip()
        if len(clean) == 8 and clean.isdigit():
            return f"{clean[:4]}-{clean[4:6]}-{clean[6:]}"
        return clean

    @staticmethod
    def _empty_payload(ticker: str, spot: float) -> Dict[str, Any]:
        return {
            "ticker": ticker,
            "spot_price": round(spot, 2),
            "spotPrice": round(spot, 2),
            "call_wall": round(spot * 1.05, 2),
            "gex_wall": round(spot * 1.05, 2),
            "put_wall": round(spot * 0.95, 2),
            "zero_gex_level": round(spot, 2),
            "zero_gamma_flip": round(spot, 2),
            "gamma_centroid": round(spot, 2),
            "net_gex": 0.0,
            "net_dex": 0.0,
            "total_call_gex": 0.0,
            "total_put_gex": 0.0,
            "total_call_dex": 0.0,
            "total_put_dex": 0.0,
            "call_put_ratio": 1.0,
            "callPutRatio": 1.0,
            "call_split_ratio": 1.0,
            "gamma_regime": "Neutral",
            "strikes": [],
            "rolling": {
                "strikes": [],
                "call_gex": [],
                "put_gex": [],
                "call_dex": [],
                "put_dex": []
            }
        }


def calculate_gexdex_distribution(
    contracts: Sequence[OptionContractRecord],
    spot_price: float,
    ticker: str,
    target_date: Optional[date] = None,
    risk_free_rate: float = 0.045,
    default_dividend_yield: float = 0.013
) -> Dict[str, Any]:
    """
    Convenience function to compute complete GEX/DEX distribution from option contract records.
    """
    calc = GexDexCalculator(
        risk_free_rate=risk_free_rate,
        default_dividend_yield=default_dividend_yield
    )
    return calc.calculate(
        contracts=contracts,
        spot_price=spot_price,
        ticker=ticker,
        target_date=target_date
    )
