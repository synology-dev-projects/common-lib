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
    gex_diff_pct: float = 0.0
    te_call_dex: float = 0.0
    ibkr_call_dex: float = 0.0
    te_put_dex: float = 0.0
    ibkr_put_dex: float = 0.0
    te_net_dex: float = 0.0
    ibkr_net_dex: float = 0.0
    dex_diff_pct: float = 0.0


@dataclass
class ShadowComparisonResult:
    ticker: str
    run_date: str
    spot_price: float
    te_net_gex: float
    ibkr_net_gex: float
    net_gex_diff_pct: float
    gex_pearson_r: float
    te_net_dex: float = 0.0
    ibkr_net_dex: float = 0.0
    net_dex_diff_pct: float = 0.0
    dex_pearson_r: float = 0.0
    te_call_wall: float = 0.0
    ibkr_call_wall: float = 0.0
    call_wall_matched: bool = False
    te_put_wall: float = 0.0
    ibkr_put_wall: float = 0.0
    put_wall_matched: bool = False
    te_zero_flip: float = 0.0
    ibkr_zero_flip: float = 0.0
    zero_flip_diff_pct: float = 0.0
    regime_matched: bool = False
    total_aligned_strikes: int = 0
    ibkr_contracts_count: int = 0
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
            "te_net_dex": self.te_net_dex,
            "ibkr_net_dex": self.ibkr_net_dex,
            "net_dex_diff_pct": self.net_dex_diff_pct,
            "dex_pearson_r": self.dex_pearson_r,
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
                    "ibkr_net_gex": r.ibkr_net_gex,
                    "gex_diff_pct": r.gex_diff_pct,
                    "te_call_dex": r.te_call_dex,
                    "ibkr_call_dex": r.ibkr_call_dex,
                    "te_put_dex": r.te_put_dex,
                    "ibkr_put_dex": r.ibkr_put_dex,
                    "te_net_dex": r.te_net_dex,
                    "ibkr_net_dex": r.ibkr_net_dex,
                    "dex_diff_pct": r.dex_diff_pct
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


def _calc_diff_pct(base_val: float, comp_val: float) -> float:
    if abs(base_val) > 1e-4:
        return min(999999.99, round((abs(base_val - comp_val) / abs(base_val)) * 100.0, 2))
    elif abs(comp_val) > 1e-4:
        return min(999999.99, round((abs(comp_val - base_val) / abs(comp_val)) * 100.0, 2))
    return 0.0


def _extract_strike_metrics(strike_obj: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
    """
    Extracts (call_gex, put_gex_signed, net_gex, call_dex, put_dex_signed, net_dex)
    from either top-level strike keys or nested expirations dictionary.
    Normalizes put_gex and put_dex to signed negative values matching TradingEdge.
    """
    exps = strike_obj.get("expirations", {}) or {}
    cg = strike_obj.get("call_gex")
    pg = strike_obj.get("put_gex")
    cd = strike_obj.get("call_dex")
    pd_val = strike_obj.get("put_dex")

    if cg is None and exps:
        cg = sum(float(v.get("call_gex") or 0.0) for v in exps.values())
    if pg is None and exps:
        pg = sum(-abs(float(v.get("put_gex") or 0.0)) for v in exps.values())
    if cd is None and exps:
        cd = sum(float(v.get("call_dex") or 0.0) for v in exps.values())
    if pd_val is None and exps:
        pd_val = sum(-abs(float(v.get("put_dex") or 0.0)) for v in exps.values())

    cg_f = abs(float(cg or 0.0))
    pg_f = -abs(float(pg or 0.0))
    net_gex_f = cg_f + pg_f

    cd_f = abs(float(cd or 0.0))
    pd_f = -abs(float(pd_val or 0.0))
    net_dex_f = cd_f + pd_f

    return round(cg_f, 2), round(pg_f, 2), round(net_gex_f, 2), round(cd_f, 2), round(pd_f, 2), round(net_dex_f, 2)


def compare_gexdex_payloads(
    te_payload: Dict[str, Any],
    ibkr_payload: Dict[str, Any],
    target_date: Optional[date] = None
) -> ShadowComparisonResult:
    """
    Computes comprehensive mathematical agreement between TradingEdge baseline
    and IBKR shadow calculation across both GEX and DEX.
    """
    ticker = str(ibkr_payload.get("ticker") or te_payload.get("ticker") or "UNKNOWN").upper()
    run_date_str = str(target_date or date.today())
    spot = float(ibkr_payload.get("spot_price") or te_payload.get("spot_price") or 0.0)

    # Strike-by-strike alignment
    te_strikes = {float(s["strike"]): s for s in te_payload.get("strikes", []) if "strike" in s}
    ibkr_strikes = {float(s["strike"]): s for s in ibkr_payload.get("strikes", []) if "strike" in s}

    common_strikes = sorted(set(te_strikes.keys()).intersection(ibkr_strikes.keys()))

    te_gex_vec: List[float] = []
    ibkr_gex_vec: List[float] = []
    te_dex_vec: List[float] = []
    ibkr_dex_vec: List[float] = []
    strike_rows: List[StrikeComparisonRow] = []

    sum_te_net_dex = 0.0
    sum_ibkr_net_dex = 0.0

    for k in common_strikes:
        tcg, tpg, tnet_gex, tcd, tpd, tnet_dex = _extract_strike_metrics(te_strikes[k])
        icg, ipg, inet_gex, icd, ipd, inet_dex = _extract_strike_metrics(ibkr_strikes[k])

        sum_te_net_dex += tnet_dex
        sum_ibkr_net_dex += inet_dex

        te_gex_vec.append(tnet_gex)
        ibkr_gex_vec.append(inet_gex)
        te_dex_vec.append(tnet_dex)
        ibkr_dex_vec.append(inet_dex)

        strike_rows.append(StrikeComparisonRow(
            strike=k,
            te_call_gex=tcg,
            ibkr_call_gex=icg,
            te_put_gex=tpg,
            ibkr_put_gex=ipg,
            te_net_gex=tnet_gex,
            ibkr_net_gex=inet_gex,
            gex_diff_pct=_calc_diff_pct(tnet_gex, inet_gex),
            te_call_dex=tcd,
            ibkr_call_dex=icd,
            te_put_dex=tpd,
            ibkr_put_dex=ipd,
            te_net_dex=tnet_dex,
            ibkr_net_dex=inet_dex,
            dex_diff_pct=_calc_diff_pct(tnet_dex, inet_dex)
        ))

    te_net_gex = float(te_payload.get("net_gex") if te_payload.get("net_gex") is not None else sum(te_gex_vec))
    ibkr_net_gex = float(ibkr_payload.get("net_gex") if ibkr_payload.get("net_gex") is not None else sum(ibkr_gex_vec))
    net_gex_diff_pct = _calc_diff_pct(te_net_gex, ibkr_net_gex)

    te_net_dex = float(te_payload.get("net_dex") if te_payload.get("net_dex") is not None else sum_te_net_dex)
    ibkr_net_dex = float(ibkr_payload.get("net_dex") if ibkr_payload.get("net_dex") is not None else sum_ibkr_net_dex)
    net_dex_diff_pct = _calc_diff_pct(te_net_dex, ibkr_net_dex)

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

    gex_pearson_r = round(calc_pearson_correlation(te_gex_vec, ibkr_gex_vec), 4)
    dex_pearson_r = round(calc_pearson_correlation(te_dex_vec, ibkr_dex_vec), 4)

    ibkr_contracts_count = len(ibkr_payload.get("contracts", []))
    if ibkr_contracts_count == 0:
        ibkr_contracts_count = sum(len(s.get("expirations", {})) * 2 for s in ibkr_payload.get("strikes", []))

    return ShadowComparisonResult(
        ticker=ticker,
        run_date=run_date_str,
        spot_price=spot,
        te_net_gex=round(te_net_gex, 2),
        ibkr_net_gex=round(ibkr_net_gex, 2),
        net_gex_diff_pct=net_gex_diff_pct,
        gex_pearson_r=gex_pearson_r,
        te_net_dex=round(te_net_dex, 2),
        ibkr_net_dex=round(ibkr_net_dex, 2),
        net_dex_diff_pct=net_dex_diff_pct,
        dex_pearson_r=dex_pearson_r,
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
