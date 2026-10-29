import logging
import math
from datetime import date, datetime
from typing import List, Dict, Any, Optional, Tuple, Sequence, TYPE_CHECKING
import numpy as np
import pandas as pd
from datetime import timezone
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from common_lib.extractors.ibkr_options import OptionContractRecord
else:
    OptionContractRecord = Any

logger = logging.getLogger("quant.calculators.gexdex")


# ==============================================================================
# Pure Python / NumPy BSM Analytical Greek Solver (Zero scipy)
# ==============================================================================

_vec_erf = np.vectorize(math.erf, otypes=[float])


def norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard Normal Probability Density Function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _vec_norm_cdf(x: np.ndarray) -> np.ndarray:
    """Vectorized Standard Normal Cumulative Distribution Function (Zero scipy)."""
    return 0.5 * (1.0 + _vec_erf(x / math.sqrt(2.0)))


def _vec_norm_pdf(x: np.ndarray) -> np.ndarray:
    """Vectorized Standard Normal Probability Density Function (Zero scipy)."""
    return np.exp(-0.5 * np.square(x)) / math.sqrt(2.0 * math.pi)


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

class IncompleteOptionChainError(RuntimeError):
    """Raised when an options chain lacks real market Greeks (gamma/delta) for one or more contracts."""
    def __init__(self, ticker: str, missing_contracts: List[str]):
        self.ticker = ticker
        self.missing_contracts = missing_contracts
        sample = missing_contracts[:5]
        super().__init__(
            f"Ticker-level atomicity violation for {ticker}: "
            f"{len(missing_contracts)} contract(s) missing real market Greeks: {sample}"
        )


class GexDexCalculator:
    """
    Calculates full options gamma & delta exposure distributions, structural walls,
    zero-flip level, and gamma centroid from option contracts.
    Emits a 1:1 TradingEdge-compatible dictionary suitable for
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
        Computes GEX/DEX surface across strikes and expirations with 1:1 TradingEdge
        aggregation and signed conventions.
        """
        sym = ticker.upper().strip()
        spot = float(spot_price)
        if spot <= 0.0:
            raise ValueError(f"Spot price must be > 0.0 to compute GEX/DEX for {sym}. Received: {spot}")

        if not contracts:
            logger.warning(f"Empty contracts list provided for {sym}.")
            return self._empty_payload(sym, spot)

        today = target_date or date.today()

        # Step 0: Enforce Ticker-Level Atomicity (Strict Zero Synthetic Fallback)
        missing_contracts = []
        for c in contracts:
            if c.gamma is None or math.isnan(c.gamma) or c.delta is None or math.isnan(c.delta):
                missing_contracts.append(f"{c.expiration} {c.strike} {c.right}")

        if missing_contracts:
            raise IncompleteOptionChainError(sym, missing_contracts)

        # Step 1: Process individual contracts using 100% verified real market Greeks
        # Structure: strike -> expiration_str -> dict(metrics)
        # TradingEdge convention in expirations: call_gex >= 0, put_gex <= 0, call_dex >= 0, put_dex <= 0
        strike_exp_map: Dict[float, Dict[str, Dict[str, float]]] = {}
        all_strikes_set = set()

        for c in contracts:
            k = float(c.strike)
            if k <= 0.0:
                continue

            all_strikes_set.add(k)
            exp_formatted = self._format_expiration_date(c.expiration)

            # Resolve Open Interest (only fallback to volume if open_interest is None, not 0)
            if c.open_interest is not None and not math.isnan(c.open_interest):
                oi = max(0.0, float(c.open_interest))
            else:
                oi = max(0.0, float(c.volume or 0.0))

            gamma = float(c.gamma)
            delta = float(c.delta)

            # GEX Scaling: Matches TradingEdge exact formula (Gamma * OI * 100 * Spot^2)
            contract_gex = (spot ** 2) * gamma * oi * 100.0

            # DEX Scaling: Dollar Delta matches TradingEdge (Delta * OI * 100 * Spot)
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
                strike_exp_map[k][exp_formatted]["call_gex"] += abs(contract_gex)
                strike_exp_map[k][exp_formatted]["call_dex"] += abs(contract_dex)
            else:
                # TradingEdge stores put_gex and put_dex as signed negative values in expirations & rolling
                strike_exp_map[k][exp_formatted]["put_gex"] -= abs(contract_gex)
                strike_exp_map[k][exp_formatted]["put_dex"] -= abs(contract_dex)

        # Step 2: Build strike-level and cumulative rolling aggregations
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
        above_spot_call_dex = 0.0

        cum_cg = 0.0
        cum_pg = 0.0
        cum_cd = 0.0
        cum_pd = 0.0

        max_cg = -1.0
        call_wall = spot
        max_pg = -1.0
        put_wall = spot
        max_gex_abs = -1.0
        gex_wall = spot

        strike_net_gex_map: Dict[float, float] = {}
        centroid_num = 0.0
        centroid_den = 0.0

        for k in sorted_strikes:
            exp_dict = strike_exp_map.get(k, {})
            stk_cg = sum(m["call_gex"] for m in exp_dict.values())
            stk_pg_signed = sum(m["put_gex"] for m in exp_dict.values())   # <= 0
            stk_cd = sum(m["call_dex"] for m in exp_dict.values())
            stk_pd_signed = sum(m["put_dex"] for m in exp_dict.values())   # <= 0

            stk_pg_mag = abs(stk_pg_signed)
            stk_pd_mag = abs(stk_pd_signed)

            tot_call_gex += stk_cg
            tot_put_gex += stk_pg_mag
            tot_call_dex += stk_cd
            tot_put_dex += stk_pd_mag
            if k > spot:
                above_spot_call_dex += stk_cd

            net_stk_gex = stk_cg + stk_pg_signed
            net_stk_dex = stk_cd + stk_pd_signed
            strike_net_gex_map[k] = net_stk_gex
            abs_net = abs(net_stk_gex)
            centroid_num += k * abs_net
            centroid_den += abs_net

            # Track global structural walls
            if stk_cg > max_cg:
                max_cg = stk_cg
                call_wall = k
            if stk_pg_mag > max_pg:
                max_pg = stk_pg_mag
                put_wall = k
            stk_dominant_gex = max(stk_cg, stk_pg_mag, abs_net)
            if stk_dominant_gex > max_gex_abs:
                max_gex_abs = stk_dominant_gex
                gex_wall = k

            strikes_payload.append({
                "strike": k,
                "call_gex": round(stk_cg, 2),
                "put_gex": round(stk_pg_signed, 2),
                "net_gex": round(net_stk_gex, 2),
                "call_dex": round(stk_cd, 2),
                "put_dex": round(stk_pd_signed, 2),
                "net_dex": round(net_stk_dex, 2),
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

            cum_cg += stk_cg
            cum_pg += stk_pg_signed
            cum_cd += stk_cd
            cum_pd += stk_pd_signed

            rolling_strikes.append(k)
            rolling_call_gex.append(round(cum_cg, 2))
            rolling_put_gex.append(round(cum_pg, 2))
            rolling_call_dex.append(round(cum_cd, 2))
            rolling_put_dex.append(round(cum_pd, 2))

        # Step 3: Compute global metrics & zero gamma flip level
        net_gex = tot_call_gex - tot_put_gex
        net_dex = tot_call_dex - tot_put_dex

        zero_gex_level = self._compute_zero_gex_level(sorted_strikes, strike_net_gex_map, spot, call_wall, put_wall)
        gamma_centroid = round(centroid_num / centroid_den, 2) if centroid_den > 0.0 else spot
        call_put_ratio = round(tot_call_dex / tot_put_dex, 6) if tot_put_dex > 0.0 else 1.0
        call_split_ratio = round((above_spot_call_dex / tot_call_dex) * 100.0, 4) if tot_call_dex > 0.0 else 0.0

        gamma_regime = "Positive Gamma (+GEX)" if net_gex >= 0.0 else "Negative Gamma (-GEX)"

        return {
            "ticker": sym,
            "spot_price": round(spot, 2),
            "spotPrice": round(spot, 2),
            "call_wall": round(call_wall, 2),
            "gex_wall": round(gex_wall, 2),
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
            "call_split_ratio": call_split_ratio,
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



# ==============================================================================
# Dynamic After-Hours BSM Re-Evaluation & Spot-Slide Profiling (Method B)
# ==============================================================================

def _calculate_annualized_expiry_tau(
    expiration_series: pd.Series,
    eval_timestamp: Optional[Any] = None
) -> np.ndarray:
    """
    Computes annualized fractional time-to-expiry (T) for each contract against eval_timestamp,
    clamping minimum T to 1e-6 to prevent ZeroDivisionError for 0DTE/expired options.
    """
    et_tz = ZoneInfo("America/New_York")
    
    if eval_timestamp is None:
        eval_dt = datetime.now(et_tz)
    elif isinstance(eval_timestamp, (date, datetime)):
        if isinstance(eval_timestamp, datetime) and eval_timestamp.tzinfo is not None:
            eval_dt = eval_timestamp.astimezone(et_tz)
        elif isinstance(eval_timestamp, datetime):
            eval_dt = eval_timestamp.replace(tzinfo=et_tz)
        else:
            eval_dt = datetime(eval_timestamp.year, eval_timestamp.month, eval_timestamp.day, 16, 0, 0, tzinfo=et_tz)
    else:
        eval_dt = pd.to_datetime(eval_timestamp)
        if eval_dt.tzinfo is None:
            eval_dt = eval_dt.tz_localize(et_tz)
        else:
            eval_dt = eval_dt.tz_convert(et_tz)

    eval_ts = eval_dt.timestamp()

    exp_clean = expiration_series.astype(str).str.strip().str.replace("-", "")
    
    def parse_exp_ts(val: str) -> float:
        try:
            if len(val) >= 8 and val[:8].isdigit():
                y, m, d = int(val[:4]), int(val[4:6]), int(val[6:8])
                dt = datetime(y, m, d, 16, 0, 0, tzinfo=et_tz)
                return dt.timestamp()
            parsed = pd.to_datetime(val)
            dt = datetime(parsed.year, parsed.month, parsed.day, 16, 0, 0, tzinfo=et_tz)
            return dt.timestamp()
        except Exception:
            return eval_ts

    exp_ts_arr = np.array([parse_exp_ts(v) for v in exp_clean], dtype=float)
    tau_seconds = exp_ts_arr - eval_ts
    tau_years = tau_seconds / (365.25 * 86400.0)
    return np.maximum(tau_years, 1e-6)


def _ssvi_total_variance(
    k: np.ndarray,
    theta: float,
    rho: float,
    phi: float
) -> np.ndarray:
    """
    Gatheral & Jacquier (2014) SSVI total implied variance w(k, theta):
    w(k) = (theta / 2) * (1 + rho * phi * k + sqrt((phi * k + rho)^2 + 1 - rho^2))
    """
    term = phi * k + rho
    rad = np.maximum(0.0, term * term + (1.0 - rho * rho))
    return 0.5 * theta * (1.0 + rho * phi * k + np.sqrt(rad))


def repair_noisy_iv_with_ssvi(
    df: pd.DataFrame,
    spot_live: float,
    T: Optional[np.ndarray] = None,
    r: float = 0.045,
    q: float = 0.0,
    max_spread_pct: float = 0.35,
    min_liquid_oi: float = 10.0,
    outlier_rel_tol: float = 0.25
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Selectively repairs ONLY noisy, wide bid-ask, or illiquid OTM option IVs using an
    arbitrage-free Gatheral-Jacquier (2014) SSVI surface fitted per expiration slice
    exclusively to clean, liquid anchor contracts.

    Strict Preservation Invariant:
    Clean/liquid ATM and near-the-money contracts are 100.0000% untouched.

    Returns (repaired_iv_array, ssvi_repaired_bool_mask).
    """
    from scipy.optimize import minimize

    n = len(df)
    if n == 0:
        return np.array([], dtype=float), np.array([], dtype=bool)

    raw_iv = pd.to_numeric(df["iv"], errors="coerce").fillna(0.0).astype(float).values
    strikes = pd.to_numeric(df["strike"], errors="coerce").fillna(0.0).astype(float).values
    oi = pd.to_numeric(df["open_interest"], errors="coerce").fillna(0.0).astype(float).values
    exps = df["expiration"].astype(str).str.strip().values

    if T is None:
        T = _calculate_annualized_expiry_tau(df["expiration"], None)

    has_bid_ask = ("bid" in df.columns) and ("ask" in df.columns)
    if has_bid_ask:
        bids = pd.to_numeric(df["bid"], errors="coerce").fillna(-1.0).astype(float).values
        asks = pd.to_numeric(df["ask"], errors="coerce").fillna(-1.0).astype(float).values
    else:
        bids = np.full(n, -1.0)
        asks = np.full(n, -1.0)

    repaired_iv = raw_iv.copy()
    repaired_mask = np.zeros(n, dtype=bool)

    unique_exps = np.unique(exps)
    for exp_val in unique_exps:
        idx = np.where(exps == exp_val)[0]
        if len(idx) == 0:
            continue

        tau = float(T[idx[0]])
        fwd = float(spot_live) * math.exp((r - q) * tau)
        k_slice = np.log(np.maximum(strikes[idx], 1e-4) / max(fwd, 1e-4))
        iv_slice = raw_iv[idx]
        oi_slice = oi[idx]

        # 1. Detect invalid or extreme IVs
        invalid_iv = (~np.isfinite(iv_slice)) | (iv_slice <= 0.01) | (iv_slice >= 4.0)

        # 2. Detect wide bid-ask spreads or zero-bid quotes when bid/ask columns exist
        wide_spread = np.zeros(len(idx), dtype=bool)
        if has_bid_ask:
            b_sl = bids[idx]
            a_sl = asks[idx]
            has_quote = (b_sl >= 0.0) & (a_sl > 0.0)
            mid_sl = 0.5 * (b_sl + a_sl)
            spread_pct = np.where(has_quote & (mid_sl > 0.0), (a_sl - b_sl) / mid_sl, 0.0)
            zero_bid = has_quote & (b_sl <= 0.0)
            wide_spread = zero_bid | (has_quote & (spread_pct > float(max_spread_pct)))

        # 3. Detect illiquid OTM wing IV spikes relative to clean anchors
        prelim_clean = (~invalid_iv) & (~wide_spread) & (oi_slice >= float(min_liquid_oi))
        if np.sum(prelim_clean) < 3:
            prelim_clean = (~invalid_iv) & (~wide_spread)

        illiquid_outlier = np.zeros(len(idx), dtype=bool)
        if np.sum(prelim_clean) >= 3:
            clean_k = k_slice[prelim_clean]
            clean_iv = iv_slice[prelim_clean]
            sort_ord = np.argsort(clean_k)
            interp_iv = np.interp(k_slice, clean_k[sort_ord], clean_iv[sort_ord])
            rel_dev = np.abs(iv_slice - interp_iv) / np.maximum(interp_iv, 1e-4)
            is_otm_wing = np.abs(k_slice) > 0.05
            is_illiquid = oi_slice < float(min_liquid_oi)
            illiquid_outlier = is_otm_wing & is_illiquid & (rel_dev > float(outlier_rel_tol))

        noisy_slice = invalid_iv | wide_spread | illiquid_outlier
        if not np.any(noisy_slice):
            continue

        clean_slice = ~noisy_slice
        if np.sum(clean_slice) < 3:
            # Fallback to median valid IV if fewer than 3 clean anchors exist on this expiry
            valid_ivs = iv_slice[~invalid_iv]
            fallback_val = float(np.median(valid_ivs)) if len(valid_ivs) > 0 else 0.25
            for local_i in np.where(noisy_slice)[0]:
                global_i = idx[local_i]
                repaired_iv[global_i] = fallback_val
                repaired_mask[global_i] = True
            continue

        # Fit Gatheral-Jacquier (2014) SSVI to clean anchors on this expiration
        k_clean = k_slice[clean_slice]
        w_clean = (iv_slice[clean_slice] ** 2) * tau
        weights = np.log1p(np.maximum(1.0, oi_slice[clean_slice]))

        # ATM total variance estimate theta_0 at k = 0
        atm_order = np.argsort(np.abs(k_clean))
        theta_init = float(np.mean(w_clean[atm_order[:min(2, len(atm_order))]]))
        theta_init = max(1e-6, theta_init)

        def loss_fn(params: np.ndarray) -> float:
            th, rh, ph = float(params[0]), float(params[1]), float(params[2])
            if th <= 1e-7 or abs(rh) >= 0.999 or ph <= 1e-5:
                return 1e9
            # Gatheral-Jacquier no-butterfly-arbitrage condition:
            # theta * phi * (1 + |rho|) < 4  and  theta * phi^2 * (1 + |rho|) <= 4
            if th * ph * (1.0 + abs(rh)) >= 3.99:
                return 1e9
            w_model = _ssvi_total_variance(k_clean, th, rh, ph)
            resid = (w_model - w_clean) * weights
            return float(np.sum(resid * resid))

        res = minimize(
            loss_fn,
            x0=np.array([theta_init, -0.25, 1.0]),
            method="Nelder-Mead",
            options={"maxiter": 400, "xatol": 1e-6, "fatol": 1e-8},
        )
        th_opt, rh_opt, ph_opt = float(res.x[0]), float(res.x[1]), float(res.x[2])
        if th_opt <= 1e-7 or abs(rh_opt) >= 0.999 or ph_opt <= 1e-5:
            th_opt, rh_opt, ph_opt = theta_init, 0.0, 0.5

        # Evaluate SSVI ONLY on the noisy_slice indices; leave clean_slice 100% untouched
        noisy_local_idx = np.where(noisy_slice)[0]
        k_noisy = k_slice[noisy_local_idx]
        w_repaired = _ssvi_total_variance(k_noisy, th_opt, rh_opt, ph_opt)
        iv_repaired_vals = np.sqrt(np.maximum(w_repaired, 1e-8) / tau)
        iv_repaired_vals = np.clip(iv_repaired_vals, 0.01, 4.0)

        for local_i, new_sig in zip(noisy_local_idx, iv_repaired_vals):
            global_i = idx[local_i]
            repaired_iv[global_i] = float(new_sig)
            repaired_mask[global_i] = True

    return repaired_iv, repaired_mask


def recompute_dynamic_bsm_gexdex(
    df: pd.DataFrame,
    spot_live: float,
    spot_close: Optional[float] = None,
    eval_timestamp: Optional[Any] = None,
    r: float = 0.045,
    q: float = 0.0,
    iv_scale: float = 1.0,
    apply_ssvi_filter: bool = False,
    max_spread_pct: float = 0.35,
    min_liquid_oi: float = 10.0
) -> pd.DataFrame:
    """
    Dynamically recomputes Black-Scholes-Merton Greeks and GEX/DEX exposures for an options chain
    using live extended/overnight underlying price (spot_live) and fractional time-to-expiry (T).
    When apply_ssvi_filter=True, selectively repairs only noisy OTM / wide bid-ask / illiquid option IVs
    via SSVI while leaving clean, liquid contracts 100% untouched.
    
    :param df: Option chain DataFrame with 'strike', 'expiration', 'option_type' ('C'/'P'), 'open_interest', and 'iv'.
    :param spot_live: Live extended/overnight underlying price (S_ext).
    :param spot_close: Optional 4:00 PM ET closing spot price to compute overnight dealer delta shock (dex_shock).
    :param eval_timestamp: Target datetime for exact time-to-expiry (T) evaluation (defaults to now).
    :param r: Risk-free interest rate (default: 0.045).
    :param q: Continuous dividend yield (default: 0.0).
    :param iv_scale: Multiplier for closing IV (default: 1.0) to model volatility shocks.
    :param apply_ssvi_filter: If True, repairs noisy/wide-spread/illiquid OTM IVs using SSVI (default: False).
    :param max_spread_pct: Maximum (ask - bid) / mid ratio before an option's IV is flagged for SSVI repair (default: 0.35).
    :param min_liquid_oi: Minimum open interest threshold for liquid anchor classification (default: 10.0).
    :return: DataFrame enriched with d1, delta_live, gamma_live, dex_live, gex_live, and optional dex_shock.
    """
    if df.empty:
        return df.copy()

    s_live = float(spot_live)
    if s_live <= 0.0:
        raise ValueError(f"spot_live must be strictly positive. Received: {spot_live}")

    out_df = df.copy()

    # Compute fractional time-to-expiry
    T = _calculate_annualized_expiry_tau(out_df["expiration"], eval_timestamp)
    sqrt_T = np.sqrt(T)

    # Extract vectors (with optional selective SSVI repair for noisy/illiquid contracts)
    strikes = out_df["strike"].astype(float).values
    if apply_ssvi_filter:
        effective_iv, ssvi_repaired = repair_noisy_iv_with_ssvi(
            out_df,
            spot_live=s_live,
            T=T,
            r=r,
            q=q,
            max_spread_pct=max_spread_pct,
            min_liquid_oi=min_liquid_oi,
        )
    else:
        effective_iv = out_df["iv"].astype(float).values
        ssvi_repaired = np.zeros(len(out_df), dtype=bool)

    out_df["iv_effective"] = effective_iv
    out_df["ssvi_repaired"] = ssvi_repaired

    raw_iv = effective_iv
    scaled_iv = raw_iv * float(iv_scale)
    oi = np.maximum(0.0, out_df["open_interest"].astype(float).fillna(0.0).values)

    type_str = out_df["option_type"].astype(str).str.upper().str.strip()
    is_call = type_str.str.startswith("C").values
    sign = np.where(is_call, 1.0, -1.0)

    # Valid mask for numerical computation
    valid_mask = (scaled_iv > 1e-4) & (strikes > 1e-4) & (s_live > 1e-4)
    safe_vol = np.where(valid_mask, scaled_iv, 1.0)
    safe_strikes = np.where(valid_mask, strikes, 1.0)

    # Vectorized BSM formulas
    d1 = (np.log(s_live / safe_strikes) + (r - q + 0.5 * safe_vol * safe_vol) * T) / (safe_vol * sqrt_T)
    df_q = np.exp(-q * T)
    nd1 = _vec_norm_cdf(d1)
    npd1 = _vec_norm_pdf(d1)

    call_delta = np.where(valid_mask, df_q * nd1, 0.0)
    put_delta = np.where(valid_mask, df_q * (nd1 - 1.0), 0.0)
    delta_live = np.where(is_call, call_delta, put_delta)

    gamma_live = np.where(valid_mask, (df_q * npd1) / (s_live * safe_vol * sqrt_T), 0.0)

    # DEX & GEX Scaling per requirement:
    # dex_live = Delta * OI * 100 * S_ext
    # gex_live = Gamma * OI * 100 * S_ext^2 * 0.01 * sign (Calls = +1, Puts = -1)
    dex_live = delta_live * oi * 100.0 * s_live
    gex_live = gamma_live * oi * 100.0 * (s_live ** 2) * 0.01 * sign

    out_df["T_years"] = T
    out_df["d1"] = np.where(valid_mask, d1, np.nan)
    out_df["delta_live"] = delta_live
    out_df["gamma_live"] = gamma_live
    out_df["dex_live"] = dex_live
    out_df["gex_live"] = gex_live

    # Optional Overnight Dealer Hedge Shock
    if spot_close is not None and float(spot_close) > 0.0:
        s_close = float(spot_close)
        if "delta_close" in out_df.columns:
            delta_close = out_df["delta_close"].astype(float).values
        else:
            safe_close_vol = np.where(valid_mask, raw_iv, 1.0)
            d1_close = (np.log(s_close / safe_strikes) + (r - q + 0.5 * safe_close_vol * safe_close_vol) * T) / (safe_close_vol * sqrt_T)
            nd1_close = _vec_norm_cdf(d1_close)
            c_d_c = np.where(valid_mask, df_q * nd1_close, 0.0)
            p_d_c = np.where(valid_mask, df_q * (nd1_close - 1.0), 0.0)
            delta_close = np.where(is_call, c_d_c, p_d_c)

        dex_close = delta_close * oi * 100.0 * s_close
        out_df["dex_close"] = dex_close
        out_df["dex_shock"] = dex_live - dex_close

    return out_df


def compute_gex_profile(
    df: pd.DataFrame,
    spot_live: float,
    pct_range: float = 0.10,
    steps: int = 100,
    r: float = 0.045,
    q: float = 0.0,
    iv_scale: float = 1.0,
    eval_timestamp: Optional[Any] = None,
    apply_ssvi_filter: bool = False,
    max_spread_pct: float = 0.35,
    min_liquid_oi: float = 10.0
) -> Dict[str, Any]:
    """
    Vectorized helper broadcasting BSM calculation across a 2D simulated spot grid (spot_live * (1 ± pct_range))
    to output the Net GEX curve and interpolate the exact Zero Gamma (Gamma Flip) level.
    
    :param df: Option chain DataFrame with 'strike', 'expiration', 'option_type', 'open_interest', and 'iv'.
    :param spot_live: Current underlying spot price.
    :param pct_range: Fraction above and below spot to evaluate (e.g. 0.10 = ±10%).
    :param steps: Number of discrete evaluation spot points along the curve.
    :param apply_ssvi_filter: If True, selectively repairs noisy/wide-spread/illiquid OTM IVs using SSVI.
    :return: Dict containing simulated spot_prices, call_gex, put_gex, net_gex, and zero_gamma_flip.
    """
    s_live = float(spot_live)
    if s_live <= 0.0:
        raise ValueError(f"spot_live must be strictly positive. Received: {spot_live}")

    sim_spots = np.linspace(s_live * (1.0 - pct_range), s_live * (1.0 + pct_range), int(steps))
    if df.empty:
        return {
            "spot_prices": sim_spots,
            "call_gex": np.zeros(len(sim_spots)),
            "put_gex": np.zeros(len(sim_spots)),
            "net_gex": np.zeros(len(sim_spots)),
            "zero_gamma_flip": round(s_live, 2)
        }

    T = _calculate_annualized_expiry_tau(df["expiration"], eval_timestamp)
    sqrt_T = np.sqrt(T)

    strikes = df["strike"].astype(float).values
    if apply_ssvi_filter:
        raw_iv, _ = repair_noisy_iv_with_ssvi(
            df,
            spot_live=s_live,
            T=T,
            r=r,
            q=q,
            max_spread_pct=max_spread_pct,
            min_liquid_oi=min_liquid_oi,
        )
    else:
        raw_iv = df["iv"].astype(float).values
    scaled_iv = raw_iv * float(iv_scale)
    oi = np.maximum(0.0, df["open_interest"].astype(float).fillna(0.0).values)

    type_str = df["option_type"].astype(str).str.upper().str.strip()
    is_call = type_str.str.startswith("C").values
    sign = np.where(is_call, 1.0, -1.0)

    valid_mask = (scaled_iv > 1e-4) & (strikes > 1e-4)
    safe_vol = np.where(valid_mask, scaled_iv, 1.0)
    safe_strikes = np.where(valid_mask, strikes, 1.0)

    # 2D Broadcasting:
    # Spots grid S: shape (steps, 1)
    # Contracts: shape (1, N)
    S = sim_spots[:, np.newaxis]
    K = safe_strikes[np.newaxis, :]
    vol = safe_vol[np.newaxis, :]
    t = T[np.newaxis, :]
    sq_t = sqrt_T[np.newaxis, :]
    val_2d = valid_mask[np.newaxis, :]

    d1_2d = (np.log(S / K) + (r - q + 0.5 * vol * vol) * t) / (vol * sq_t)
    npd1_2d = _vec_norm_pdf(d1_2d)
    gamma_2d = np.where(val_2d, (np.exp(-q * t) * npd1_2d) / (S * vol * sq_t), 0.0)

    # GEX per contract across simulated spots: shape (steps, N)
    gex_2d = gamma_2d * (oi[np.newaxis, :] * 100.0 * (S ** 2) * 0.01) * sign[np.newaxis, :]

    call_mask = is_call[np.newaxis, :]
    call_gex_curve = np.sum(np.where(call_mask, gex_2d, 0.0), axis=1)
    put_gex_curve = np.sum(np.where(~call_mask, gex_2d, 0.0), axis=1)
    net_gex_curve = np.sum(gex_2d, axis=1)

    # Interpolate exact Zero Gamma Flip level
    zero_flip = float(s_live)
    for i in range(len(sim_spots) - 1):
        y0, y1 = net_gex_curve[i], net_gex_curve[i + 1]
        if (y0 <= 0.0 and y1 > 0.0) or (y0 >= 0.0 and y1 < 0.0):
            dy = y1 - y0
            if dy != 0.0:
                t_frac = (0.0 - y0) / dy
                zero_flip = float(sim_spots[i] + t_frac * (sim_spots[i + 1] - sim_spots[i]))
                break

    return {
        "spot_prices": sim_spots,
        "call_gex": call_gex_curve,
        "put_gex": put_gex_curve,
        "net_gex": net_gex_curve,
        "zero_gamma_flip": round(zero_flip, 2)
    }



def evaluate_bsm_vs_tradingedge_accuracy(
    df_bsm: pd.DataFrame,
    te_payload: Dict[str, Any],
    target_strike: Optional[float] = None,
    max_dte: int = 60,
    eval_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Comprehensive 5-Tier Quantitative Accuracy Evaluator comparing Dynamic BSM GEX/DEX
    outputs against TradingEdge API ground truth.

    Evaluates:
      1. Constituent-First Relative Error (Call GEX, Put GEX, Call DEX, Put DEX) with
         Deep ITM Parity Guard and Small Denominator De Minimis Guard.
      2. Gross-Normalized Net Exposure Error (Net GEX & Net DEX normalized by Gross Exposure).
      3. Exposure-Weighted Mean Absolute Percentage Error (WMAPE).
      4. Cross-Strike Surface Shape Correlation (Pearson r).
      5. Structural Landmark Concordance (Call Wall, Put Wall, Zero Gamma Flip, Gamma Regime).
    """
    from common_lib.calculators.shadow_comparator import (
        calc_pearson_correlation,
        calc_lins_ccc,
        evaluate_wall_with_top2_tolerance,
        evaluate_regime_with_noise_zone,
        _compute_granular_surface_metrics,
    )

    today = eval_date or date.today()
    spot = float(te_payload.get("spot_price", 0.0) or 1.0)

    if df_bsm.empty or not te_payload or "strikes" not in te_payload:
        raise ValueError("Both df_bsm and te_payload['strikes'] must be non-empty.")

    work_df = df_bsm.copy()
    # Normalize expiration string to YYYY-MM-DD
    work_df["exp_iso"] = work_df["expiration"].apply(GexDexCalculator._format_expiration_date)

    # Filter to valid DTE <= max_dte
    def _calc_dte(exp_str: str) -> int:
        try:
            dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
            return (dt - today).days
        except ValueError:
            return 9999

    work_df["dte"] = work_df["exp_iso"].apply(_calc_dte)
    work_df = work_df[(work_df["dte"] >= 0) & (work_df["dte"] <= max_dte)].copy()

    # TradingEdge convention scales GEX as Spot^2 * Gamma * OI * 100 (which is 100x the 1% move GEX)
    work_df["gex_te_units"] = work_df["gex_live"] * 100.0
    work_df["dex_te_units"] = work_df["dex_live"]

    is_call = work_df["option_type"].astype(str).str.upper().str.startswith("C")
    work_df["call_gex"] = np.where(is_call, work_df["gex_te_units"], 0.0)
    work_df["put_gex"] = np.where(~is_call, work_df["gex_te_units"], 0.0)
    work_df["call_dex"] = np.where(is_call, work_df["dex_te_units"], 0.0)
    work_df["put_dex"] = np.where(~is_call, work_df["dex_te_units"], 0.0)

    # --------------------------------------------------------------------------
    # Tier 4 & 5: Cross-Strike Surface Aggregation (<= max_dte)
    # --------------------------------------------------------------------------
    te_strikes_map: Dict[float, Dict[str, float]] = {}
    for s_obj in te_payload.get("strikes", []):
        k = float(s_obj.get("strike", 0.0))
        cg_sum, pg_sum, cd_sum, pd_sum = 0.0, 0.0, 0.0, 0.0
        exps = s_obj.get("expirations", {})
        if exps:
            for exp_iso, m in exps.items():
                dte_val = _calc_dte(exp_iso)
                if 0 <= dte_val <= max_dte:
                    cg_sum += float(m.get("call_gex", 0.0))
                    pg_sum += float(m.get("put_gex", 0.0))
                    cd_sum += float(m.get("call_dex", 0.0))
                    pd_sum += float(m.get("put_dex", 0.0))
        else:
            cg_sum = float(s_obj.get("call_gex", 0.0))
            pg_sum = float(s_obj.get("put_gex", 0.0))
            cd_sum = float(s_obj.get("call_dex", 0.0))
            pd_sum = float(s_obj.get("put_dex", 0.0))
        te_strikes_map[k] = {
            "call_gex": cg_sum,
            "put_gex": pg_sum,
            "net_gex": cg_sum + pg_sum,
            "call_dex": cd_sum,
            "put_dex": pd_sum,
            "net_dex": cd_sum + pd_sum,
        }

    bsm_strike_grp = work_df.groupby("strike").agg(
        call_gex=("call_gex", "sum"),
        put_gex=("put_gex", "sum"),
        call_dex=("call_dex", "sum"),
        put_dex=("put_dex", "sum"),
    )

    common_strikes = sorted(set(te_strikes_map.keys()).intersection(set(bsm_strike_grp.index)))

    bsm_cg_vec, te_cg_vec = [], []
    bsm_pg_vec, te_pg_vec = [], []
    bsm_ng_vec, te_ng_vec = [], []
    bsm_cd_vec, te_cd_vec = [], []
    bsm_pd_vec, te_pd_vec = [], []
    bsm_nd_vec, te_nd_vec = [], []

    for k in common_strikes:
        b_row = bsm_strike_grp.loc[k]
        t_row = te_strikes_map[k]
        b_cg, b_pg = float(b_row["call_gex"]), float(b_row["put_gex"])
        b_cd, b_pd = float(b_row["call_dex"]), float(b_row["put_dex"])

        bsm_cg_vec.append(b_cg)
        te_cg_vec.append(t_row["call_gex"])
        bsm_pg_vec.append(abs(b_pg))
        te_pg_vec.append(abs(t_row["put_gex"]))
        bsm_ng_vec.append(b_cg + b_pg)
        te_ng_vec.append(t_row["net_gex"])

        bsm_cd_vec.append(b_cd)
        te_cd_vec.append(t_row["call_dex"])
        bsm_pd_vec.append(abs(b_pd))
        te_pd_vec.append(abs(t_row["put_dex"]))
        bsm_nd_vec.append(b_cd + b_pd)
        te_nd_vec.append(t_row["net_dex"])

    pearson_r = {
        "call_gex": round(calc_pearson_correlation(bsm_cg_vec, te_cg_vec), 6),
        "put_gex": round(calc_pearson_correlation(bsm_pg_vec, te_pg_vec), 6),
        "net_gex": round(calc_pearson_correlation(bsm_ng_vec, te_ng_vec), 6),
        "call_dex": round(calc_pearson_correlation(bsm_cd_vec, te_cd_vec), 6),
        "put_dex": round(calc_pearson_correlation(bsm_pd_vec, te_pd_vec), 6),
        "net_dex": round(calc_pearson_correlation(bsm_nd_vec, te_nd_vec), 6),
    }

    lins_ccc = {
        "call_gex": round(calc_lins_ccc(bsm_cg_vec, te_cg_vec), 6),
        "put_gex": round(calc_lins_ccc(bsm_pg_vec, te_pg_vec), 6),
        "net_gex": round(calc_lins_ccc(bsm_ng_vec, te_ng_vec), 6),
        "call_dex": round(calc_lins_ccc(bsm_cd_vec, te_cd_vec), 6),
        "put_dex": round(calc_lins_ccc(bsm_pd_vec, te_pd_vec), 6),
        "net_dex": round(calc_lins_ccc(bsm_nd_vec, te_nd_vec), 6),
    }

    # Build filtered payloads for Granular (Strike x Expiry) WMAPE & Signed WMPB
    te_filtered_strikes_map: Dict[float, Dict[str, Any]] = {}
    bsm_filtered_strikes_map: Dict[float, Dict[str, Any]] = {}
    bsm_se_grp = work_df.groupby(["strike", "exp_iso"]).agg(
        call_gex=("call_gex", "sum"),
        put_gex=("put_gex", "sum"),
        call_dex=("call_dex", "sum"),
        put_dex=("put_dex", "sum"),
    )
    for s_obj in te_payload.get("strikes", []):
        k = float(s_obj.get("strike", 0.0))
        if k not in common_strikes:
            continue
        te_exps_f = {}
        bsm_exps_f = {}
        for exp_iso, m in s_obj.get("expirations", {}).items():
            dte_val = _calc_dte(exp_iso)
            if 0 <= dte_val <= max_dte and (k, exp_iso) in bsm_se_grp.index:
                te_exps_f[exp_iso] = m
                b_row_se = bsm_se_grp.loc[(k, exp_iso)]
                bsm_exps_f[exp_iso] = {
                    "call_gex": float(b_row_se["call_gex"]),
                    "put_gex": float(b_row_se["put_gex"]),
                    "call_dex": float(b_row_se["call_dex"]),
                    "put_dex": float(b_row_se["put_dex"]),
                }
        te_filtered_strikes_map[k] = {"strike": k, "expirations": te_exps_f, **te_strikes_map[k]}
        b_s_row = bsm_strike_grp.loc[k]
        bsm_filtered_strikes_map[k] = {
            "strike": k,
            "expirations": bsm_exps_f,
            "call_gex": float(b_s_row["call_gex"]),
            "put_gex": float(b_s_row["put_gex"]),
            "call_dex": float(b_s_row["call_dex"]),
            "put_dex": float(b_s_row["put_dex"]),
        }

    granular_wmape, signed_bias = _compute_granular_surface_metrics(
        te_strikes=te_filtered_strikes_map,
        ibkr_strikes=bsm_filtered_strikes_map,
        common_strikes=common_strikes,
        spot=spot,
        today=today,
    )

    # Structural Landmarks across common strikes (with Top-2 Wall Tolerance & Noise Zone Guard)
    if common_strikes:
        bsm_call_wall = common_strikes[int(np.argmax(bsm_cg_vec))]
        te_call_wall = common_strikes[int(np.argmax(te_cg_vec))]
        bsm_put_wall = common_strikes[int(np.argmax(bsm_pg_vec))]
        te_put_wall = common_strikes[int(np.argmax(te_pg_vec))]
        bsm_net_gex_total = float(np.sum(bsm_ng_vec))
        te_net_gex_total = float(np.sum(te_ng_vec))
        te_call_gex_total = float(np.sum(np.abs(te_cg_vec)))
        te_put_gex_total = float(np.sum(np.abs(te_pg_vec)))
    else:
        bsm_call_wall = te_call_wall = spot
        bsm_put_wall = te_put_wall = spot
        bsm_net_gex_total = te_net_gex_total = 0.0
        te_call_gex_total = te_put_gex_total = 0.0

    te_call_mag_map = {k: abs(te_strikes_map[k]["call_gex"]) for k in common_strikes}
    bsm_call_mag_map = {k: abs(float(bsm_strike_grp.loc[k]["call_gex"])) for k in common_strikes}
    te_put_mag_map = {k: abs(te_strikes_map[k]["put_gex"]) for k in common_strikes}
    bsm_put_mag_map = {k: abs(float(bsm_strike_grp.loc[k]["put_gex"])) for k in common_strikes}

    _, call_wall_tol = evaluate_wall_with_top2_tolerance(
        model_strike_map=bsm_call_mag_map,
        te_strike_map=te_call_mag_map,
        wall_model=bsm_call_wall,
        wall_te=te_call_wall,
        max_rel_diff=0.03,
    )
    _, put_wall_tol = evaluate_wall_with_top2_tolerance(
        model_strike_map=bsm_put_mag_map,
        te_strike_map=te_put_mag_map,
        wall_model=bsm_put_wall,
        wall_te=te_put_wall,
        max_rel_diff=0.03,
    )

    regime_ok, in_noise_zone, ngr_val, regime_class = evaluate_regime_with_noise_zone(
        te_net_gex=te_net_gex_total,
        model_net_gex=bsm_net_gex_total,
        te_call_gex=te_call_gex_total,
        te_put_gex=te_put_gex_total,
        zero_flip_diff_pct=0.0,
        ngr_threshold=0.02,
        max_gross_norm_err_pct=5.0,
        max_zero_flip_err_pct=0.25,
    )

    landmarks = {
        "bsm_call_wall": bsm_call_wall,
        "te_call_wall": te_call_wall,
        "call_wall_match": bsm_call_wall == te_call_wall,
        "call_wall_within_tolerance": call_wall_tol,
        "bsm_put_wall": bsm_put_wall,
        "te_put_wall": te_put_wall,
        "put_wall_match": bsm_put_wall == te_put_wall,
        "put_wall_within_tolerance": put_wall_tol,
        "wall_within_tolerance": call_wall_tol and put_wall_tol,
        "bsm_net_gex": round(bsm_net_gex_total, 2),
        "te_net_gex": round(te_net_gex_total, 2),
        "net_to_gross_ratio": round(ngr_val, 6),
        "in_flip_noise_zone": in_noise_zone,
        "regime_classification": regime_class,
        "regime_match": regime_ok,
    }

    # --------------------------------------------------------------------------
    # Tier 1, 2 & 3: Single-Strike Per-Expiration Constituent & WMAPE Evaluation
    # --------------------------------------------------------------------------
    k_eval = float(target_strike) if target_strike is not None else bsm_call_wall
    te_s_obj = next((s for s in te_payload.get("strikes", []) if float(s.get("strike", 0.0)) == k_eval), None)
    te_exps = te_s_obj.get("expirations", {}) if te_s_obj else {}

    stk_df = work_df[work_df["strike"] == k_eval]
    bsm_exp_grp = stk_df.groupby("exp_iso").agg(
        call_gex=("call_gex", "sum"),
        put_gex=("put_gex", "sum"),
        call_dex=("call_dex", "sum"),
        put_dex=("put_dex", "sum"),
        dte=("dte", "first"),
    )

    eval_exps = sorted(set(te_exps.keys()).intersection(set(bsm_exp_grp.index)))
    per_expiration_rows = []
    clean_put_exps = []

    for exp_iso in eval_exps:
        b_m = bsm_exp_grp.loc[exp_iso]
        t_m = te_exps[exp_iso]
        dte_val = int(b_m["dte"])

        c_gex_b, c_gex_t = float(b_m["call_gex"]), float(t_m.get("call_gex", 0.0))
        p_gex_b, p_gex_t = float(b_m["put_gex"]), float(t_m.get("put_gex", 0.0))
        c_dex_b, c_dex_t = float(b_m["call_dex"]), float(t_m.get("call_dex", 0.0))
        p_dex_b, p_dex_t = float(b_m["put_dex"]), float(t_m.get("put_dex", 0.0))

        # Vendor Zeroed Put GEX Artifact Detection (when TE zeroes out short-dated put GEX while OI > 0)
        put_vendor_zeroed = (abs(p_gex_t) == 0.0 and abs(p_gex_b) > 0.0 and dte_val <= 14)
        if not put_vendor_zeroed:
            clean_put_exps.append(exp_iso)

        gross_gex = abs(c_gex_t) + abs(p_gex_t)
        gross_dex = abs(c_dex_t) + abs(p_dex_t)
        net_gex_b, net_gex_t = c_gex_b + p_gex_b, c_gex_t + p_gex_t
        net_dex_b, net_dex_t = c_dex_b + p_dex_b, c_dex_t + p_dex_t

        per_expiration_rows.append({
            "expiration": exp_iso,
            "dte": dte_val,
            "call_gex_bsm": round(c_gex_b, 2),
            "call_gex_te": round(c_gex_t, 2),
            "call_gex_err_pct": round(abs(c_gex_b - c_gex_t) / abs(c_gex_t) * 100.0, 2) if abs(c_gex_t) > 0 else 0.0,
            "put_gex_bsm": round(p_gex_b, 2),
            "put_gex_te": round(p_gex_t, 2),
            "put_gex_err_pct": round(abs(abs(p_gex_b) - abs(p_gex_t)) / abs(p_gex_t) * 100.0, 2) if abs(p_gex_t) > 0 else (0.0 if put_vendor_zeroed else 0.0),
            "put_vendor_zeroed": put_vendor_zeroed,
            "call_dex_bsm": round(c_dex_b, 2),
            "call_dex_te": round(c_dex_t, 2),
            "call_dex_err_pct": round(abs(c_dex_b - c_dex_t) / abs(c_dex_t) * 100.0, 2) if abs(c_dex_t) > 0 else 0.0,
            "put_dex_bsm": round(p_dex_b, 2),
            "put_dex_te": round(p_dex_t, 2),
            "put_dex_err_pct": round(abs(abs(p_dex_b) - abs(p_dex_t)) / abs(p_dex_t) * 100.0, 2) if abs(p_dex_t) > 0 else 0.0,
            "net_gex_gross_norm_err_pct": round(abs(net_gex_b - net_gex_t) / gross_gex * 100.0, 2) if gross_gex > 0 else 0.0,
            "net_dex_gross_norm_err_pct": round(abs(net_dex_b - net_dex_t) / gross_dex * 100.0, 2) if gross_dex > 0 else 0.0,
        })

    # Compute WMAPE & Aggregated Totals at Target Strike
    sum_cg_b = sum(r["call_gex_bsm"] for r in per_expiration_rows)
    sum_cg_t = sum(r["call_gex_te"] for r in per_expiration_rows)
    sum_pg_b = sum(r["put_gex_bsm"] for r in per_expiration_rows if not r["put_vendor_zeroed"])
    sum_pg_t = sum(r["put_gex_te"] for r in per_expiration_rows if not r["put_vendor_zeroed"])

    sum_cd_b = sum(r["call_dex_bsm"] for r in per_expiration_rows)
    sum_cd_t = sum(r["call_dex_te"] for r in per_expiration_rows)
    sum_pd_b = sum(r["put_dex_bsm"] for r in per_expiration_rows if not r["put_vendor_zeroed"])
    sum_pd_t = sum(r["put_dex_te"] for r in per_expiration_rows if not r["put_vendor_zeroed"])

    wmape_call_gex = sum(abs(r["call_gex_bsm"] - r["call_gex_te"]) for r in per_expiration_rows) / abs(sum_cg_t) * 100.0 if abs(sum_cg_t) > 0 else 0.0
    wmape_put_gex = sum(abs(abs(r["put_gex_bsm"]) - abs(r["put_gex_te"])) for r in per_expiration_rows if not r["put_vendor_zeroed"]) / abs(sum_pg_t) * 100.0 if abs(sum_pg_t) > 0 else 0.0
    wmape_call_dex = sum(abs(r["call_dex_bsm"] - r["call_dex_te"]) for r in per_expiration_rows) / abs(sum_cd_t) * 100.0 if abs(sum_cd_t) > 0 else 0.0
    wmape_put_dex = sum(abs(abs(r["put_dex_bsm"]) - abs(r["put_dex_te"])) for r in per_expiration_rows if not r["put_vendor_zeroed"]) / abs(sum_pd_t) * 100.0 if abs(sum_pd_t) > 0 else 0.0

    gross_gex_tot = abs(sum_cg_t) + abs(sum_pg_t)
    gross_dex_tot = abs(sum_cd_t) + abs(sum_pd_t)
    net_gex_b_tot = sum_cg_b + sum_pg_b
    net_gex_t_tot = sum_cg_t + sum_pg_t
    net_dex_b_tot = sum_cd_b + sum_pd_b
    net_dex_t_tot = sum_cd_t + sum_pd_t

    gross_norm_net_gex_err = abs(net_gex_b_tot - net_gex_t_tot) / gross_gex_tot * 100.0 if gross_gex_tot > 0 else 0.0
    gross_norm_net_dex_err = abs(net_dex_b_tot - net_dex_t_tot) / gross_dex_tot * 100.0 if gross_dex_tot > 0 else 0.0

    wmape = {
        "call_gex_wmape_pct": round(wmape_call_gex, 2),
        "put_gex_wmape_pct": round(wmape_put_gex, 2),
        "call_dex_wmape_pct": round(wmape_call_dex, 2),
        "put_dex_wmape_pct": round(wmape_put_dex, 2),
        "gross_norm_net_gex_err_pct": round(gross_norm_net_gex_err, 2),
        "gross_norm_net_dex_err_pct": round(gross_norm_net_dex_err, 2),
    }

    strike_evaluation = {
        "strike": k_eval,
        "expirations_evaluated": len(per_expiration_rows),
        "totals": {
            "call_gex_bsm": round(sum_cg_b, 2),
            "call_gex_te": round(sum_cg_t, 2),
            "put_gex_bsm": round(sum_pg_b, 2),
            "put_gex_te": round(sum_pg_t, 2),
            "net_gex_bsm": round(net_gex_b_tot, 2),
            "net_gex_te": round(net_gex_t_tot, 2),
            "call_dex_bsm": round(sum_cd_b, 2),
            "call_dex_te": round(sum_cd_t, 2),
            "put_dex_bsm": round(sum_pd_b, 2),
            "put_dex_te": round(sum_pd_t, 2),
            "net_dex_bsm": round(net_dex_b_tot, 2),
            "net_dex_te": round(net_dex_t_tot, 2),
        },
        "per_expiration": per_expiration_rows,
    }

    benchmarks = {
        "net_gex_pearson_r_pass": pearson_r["net_gex"] >= 0.97,
        "net_dex_pearson_r_pass": pearson_r["net_dex"] >= 0.97,
        "net_gex_lins_ccc_pass": lins_ccc["net_gex"] >= 0.90,
        "net_dex_lins_ccc_pass": lins_ccc["net_dex"] >= 0.90,
        "call_wall_match_pass": landmarks["call_wall_within_tolerance"],
        "put_wall_match_pass": landmarks["put_wall_within_tolerance"],
        "regime_match_pass": landmarks["regime_match"],
        "call_dex_wmape_pass": wmape["call_dex_wmape_pct"] <= 20.0,
        "gross_norm_net_dex_pass": wmape["gross_norm_net_dex_err_pct"] <= 25.0,
        "gross_norm_net_gex_pass": wmape["gross_norm_net_gex_err_pct"] <= 50.0,
    }

    return {
        "ticker": te_payload.get("ticker", "UNKNOWN"),
        "spot_price": spot,
        "max_dte": max_dte,
        "wmape": wmape,
        "granular_wmape": granular_wmape,
        "signed_bias": signed_bias,
        "pearson_r": pearson_r,
        "lins_ccc": lins_ccc,
        "landmarks": landmarks,
        "strike_evaluation": strike_evaluation,
        "benchmarks": benchmarks,
        "benchmarks_passed": all(benchmarks.values()),
    }
