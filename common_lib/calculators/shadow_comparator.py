import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
import numpy as np


@dataclass
class StrikeComparisonRow:
    strike: float
    te_call_gex: float
    ibkr_call_gex: float
    te_put_gex: float
    ibkr_put_gex: float
    te_net_gex: float
    ibkr_net_gex: float


@dataclass
class ShadowComparisonResult:
    ticker: str
    run_date: str
    spot_price: float
    te_net_gex: float
    ibkr_net_gex: float
    net_gex_diff_pct: float
    gex_pearson_r: float
    te_call_wall: float
    ibkr_call_wall: float
    call_wall_matched: bool
    te_put_wall: float
    ibkr_put_wall: float
    put_wall_matched: bool
    te_zero_flip: float
    ibkr_zero_flip: float
    zero_flip_diff_pct: float
    regime_matched: bool
    total_aligned_strikes: int
    ibkr_contracts_count: int
    strike_rows: List[StrikeComparisonRow] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "run_date": self.run_date,
            "spot_price": self.spot_price,
            "te_net_gex": self.te_net_gex,
            "ibkr_net_gex": self.ibkr_net_gex,
            "net_gex_diff_pct": self.net_gex_diff_pct,
            "gex_pearson_r": self.gex_pearson_r,
            "te_call_wall": self.te_call_wall,
            "ibkr_call_wall": self.ibkr_call_wall,
            "call_wall_matched": self.call_wall_matched,
            "te_put_wall": self.te_put_wall,
            "ibkr_put_wall": self.ibkr_put_wall,
            "put_wall_matched": self.put_wall_matched,
            "te_zero_flip": self.te_zero_flip,
            "ibkr_zero_flip": self.ibkr_zero_flip,
            "zero_flip_diff_pct": self.zero_flip_diff_pct,
            "regime_matched": self.regime_matched,
            "total_aligned_strikes": self.total_aligned_strikes,
            "ibkr_contracts_count": self.ibkr_contracts_count,
            "strike_rows": [
                {
                    "strike": r.strike,
                    "te_call_gex": r.te_call_gex,
                    "ibkr_call_gex": r.ibkr_call_gex,
                    "te_put_gex": r.te_put_gex,
                    "ibkr_put_gex": r.ibkr_put_gex,
                    "te_net_gex": r.te_net_gex,
                    "ibkr_net_gex": r.ibkr_net_gex
                }
                for r in self.strike_rows
            ]
        }


def calc_pearson_correlation(vec_x: List[float], vec_y: List[float]) -> float:
    """
    Computes Pearson correlation coefficient r between two vectors.
    Returns 1.0 if identical or both zero-variance, 0.0 if orthogonal or empty.
    """
    if len(vec_x) < 2 or len(vec_y) < 2 or len(vec_x) != len(vec_y):
        return 0.0

    x = np.array(vec_x, dtype=np.float64)
    y = np.array(vec_y, dtype=np.float64)

    std_x = np.std(x)
    std_y = np.std(y)

    if std_x < 1e-9 and std_y < 1e-9:
        return 1.0
    if std_x < 1e-9 or std_y < 1e-9:
        return 0.0

    r = float(np.corrcoef(x, y)[0, 1])
    if math.isnan(r) or math.isinf(r):
        return 0.0
    return max(-1.0, min(1.0, r))


def compare_gexdex_payloads(
    te_payload: Dict[str, Any],
    ibkr_payload: Dict[str, Any],
    target_date: Optional[date] = None
) -> ShadowComparisonResult:
    """
    Computes comprehensive mathematical agreement between TradingEdge baseline
    and IBKR shadow calculation.
    """
    ticker = str(ibkr_payload.get("ticker") or te_payload.get("ticker") or "UNKNOWN").upper()
    run_date_str = str(target_date or date.today())
    spot = float(ibkr_payload.get("spot_price") or te_payload.get("spot_price") or 0.0)

    te_net_gex = float(te_payload.get("net_gex", 0.0) or 0.0)
    ibkr_net_gex = float(ibkr_payload.get("net_gex", 0.0) or 0.0)

    # Calculate percentage difference bounded to prevent numeric overflow
    if abs(te_net_gex) > 1e-4:
        net_gex_diff_pct = min(999999.99, round((abs(te_net_gex - ibkr_net_gex) / abs(te_net_gex)) * 100.0, 2))
    elif abs(ibkr_net_gex) > 1e-4:
        net_gex_diff_pct = min(999999.99, round((abs(ibkr_net_gex - te_net_gex) / abs(ibkr_net_gex)) * 100.0, 2))
    else:
        net_gex_diff_pct = 0.0

    te_cw = float(te_payload.get("call_wall", 0.0) or 0.0)
    ibkr_cw = float(ibkr_payload.get("call_wall", 0.0) or 0.0)
    call_wall_matched = (te_cw > 0 and ibkr_cw > 0 and abs(te_cw - ibkr_cw) <= max(1.0, spot * 0.015))

    te_pw = float(te_payload.get("put_wall", 0.0) or 0.0)
    ibkr_pw = float(ibkr_payload.get("put_wall", 0.0) or 0.0)
    put_wall_matched = (te_pw > 0 and ibkr_pw > 0 and abs(te_pw - ibkr_pw) <= max(1.0, spot * 0.015))

    te_zf = float(te_payload.get("zero_gex_level", 0.0) or spot)
    ibkr_zf = float(ibkr_payload.get("zero_gex_level", 0.0) or spot)
    spot_denom = max(spot, 1.0)
    zero_flip_diff_pct = round((abs(te_zf - ibkr_zf) / spot_denom) * 100.0, 3)

    regime_matched = (te_net_gex >= 0 and ibkr_net_gex >= 0) or (te_net_gex < 0 and ibkr_net_gex < 0)

    # Strike-by-strike alignment
    te_strikes = {float(s["strike"]): s for s in te_payload.get("strikes", []) if "strike" in s}
    ibkr_strikes = {float(s["strike"]): s for s in ibkr_payload.get("strikes", []) if "strike" in s}

    common_strikes = sorted(set(te_strikes.keys()).intersection(ibkr_strikes.keys()))

    te_gex_vec: List[float] = []
    ibkr_gex_vec: List[float] = []
    strike_rows: List[StrikeComparisonRow] = []

    for k in common_strikes:
        ts = te_strikes[k]
        ibs = ibkr_strikes[k]

        tcg = float(ts.get("call_gex", 0.0) or 0.0)
        tpg = float(ts.get("put_gex", 0.0) or 0.0)
        tnet = tcg - tpg

        icg = float(ibs.get("call_gex", 0.0) or 0.0)
        ipg = float(ibs.get("put_gex", 0.0) or 0.0)
        inet = icg - ipg

        te_gex_vec.append(tnet)
        ibkr_gex_vec.append(inet)

        strike_rows.append(StrikeComparisonRow(
            strike=k,
            te_call_gex=tcg,
            ibkr_call_gex=icg,
            te_put_gex=tpg,
            ibkr_put_gex=ipg,
            te_net_gex=tnet,
            ibkr_net_gex=inet
        ))

    pearson_r = round(calc_pearson_correlation(te_gex_vec, ibkr_gex_vec), 4)

    ibkr_contracts_count = len(ibkr_payload.get("contracts", []))
    if ibkr_contracts_count == 0:
        ibkr_contracts_count = sum(len(s.get("expirations", {})) * 2 for s in ibkr_payload.get("strikes", []))

    return ShadowComparisonResult(
        ticker=ticker,
        run_date=run_date_str,
        spot_price=spot,
        te_net_gex=te_net_gex,
        ibkr_net_gex=ibkr_net_gex,
        net_gex_diff_pct=net_gex_diff_pct,
        gex_pearson_r=pearson_r,
        te_call_wall=te_cw,
        ibkr_call_wall=ibkr_cw,
        call_wall_matched=call_wall_matched,
        te_put_wall=te_pw,
        ibkr_put_wall=ibkr_pw,
        put_wall_matched=put_wall_matched,
        te_zero_flip=te_zf,
        ibkr_zero_flip=ibkr_zf,
        zero_flip_diff_pct=zero_flip_diff_pct,
        regime_matched=regime_matched,
        total_aligned_strikes=len(common_strikes),
        ibkr_contracts_count=ibkr_contracts_count,
        strike_rows=strike_rows
    )
