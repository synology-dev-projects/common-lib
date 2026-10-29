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
    gex_lins_ccc: float = 0.0
    dex_lins_ccc: float = 0.0
    lins_ccc: Dict[str, float] = field(default_factory=dict)
    granular_wmape: Dict[str, float] = field(default_factory=dict)
    signed_bias: Dict[str, float] = field(default_factory=dict)
    te_call_wall: float = 0.0
    ibkr_call_wall: float = 0.0
    call_wall_matched: bool = False
    call_wall_within_tolerance: bool = False
    te_put_wall: float = 0.0
    ibkr_put_wall: float = 0.0
    put_wall_matched: bool = False
    put_wall_within_tolerance: bool = False
    wall_within_tolerance: bool = False
    te_zero_flip: float = 0.0
    ibkr_zero_flip: float = 0.0
    zero_flip_diff_pct: float = 0.0
    regime_matched: bool = False
    net_to_gross_ratio: float = 0.0
    in_flip_noise_zone: bool = False
    regime_classification: str = "Neutral"
    gross_norm_net_gex_err_pct: float = 0.0
    gross_norm_net_dex_err_pct: float = 0.0
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
            "gex_lins_ccc": self.gex_lins_ccc,
            "dex_lins_ccc": self.dex_lins_ccc,
            "lins_ccc": self.lins_ccc,
            "granular_wmape": self.granular_wmape,
            "signed_bias": self.signed_bias,
            "te_call_wall": self.te_call_wall,
            "ibkr_call_wall": self.ibkr_call_wall,
            "call_wall_matched": self.call_wall_matched,
            "call_wall_within_tolerance": self.call_wall_within_tolerance,
            "te_put_wall": self.te_put_wall,
            "ibkr_put_wall": self.ibkr_put_wall,
            "put_wall_matched": self.put_wall_matched,
            "put_wall_within_tolerance": self.put_wall_within_tolerance,
            "wall_within_tolerance": self.wall_within_tolerance,
            "te_zero_flip": self.te_zero_flip,
            "ibkr_zero_flip": self.ibkr_zero_flip,
            "zero_flip_diff_pct": self.zero_flip_diff_pct,
            "regime_matched": self.regime_matched,
            "net_to_gross_ratio": self.net_to_gross_ratio,
            "in_flip_noise_zone": self.in_flip_noise_zone,
            "regime_classification": self.regime_classification,
            "gross_norm_net_gex_err_pct": self.gross_norm_net_gex_err_pct,
            "gross_norm_net_dex_err_pct": self.gross_norm_net_dex_err_pct,
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


def calc_lins_ccc(vec_x: List[float], vec_y: List[float]) -> float:
    """
    Computes Lin's Concordance Correlation Coefficient (rho_c), measuring both
    profile shape correlation (r) and scale/location concordance simultaneously:
        rho_c = (2 * r * sigma_x * sigma_y) / (sigma_x^2 + sigma_y^2 + (mu_x - mu_y)^2)
    """
    if len(vec_x) < 2 or len(vec_y) < 2 or len(vec_x) != len(vec_y):
        return 0.0

    x = np.array(vec_x, dtype=np.float64)
    y = np.array(vec_y, dtype=np.float64)

    mu_x = float(np.mean(x))
    mu_y = float(np.mean(y))
    var_x = float(np.var(x))
    var_y = float(np.var(y))
    std_x = math.sqrt(var_x)
    std_y = math.sqrt(var_y)

    if std_x < 1e-9 and std_y < 1e-9:
        return 1.0 if abs(mu_x - mu_y) < 1e-6 else 0.0
    if std_x < 1e-9 or std_y < 1e-9:
        return 0.0

    r = calc_pearson_correlation(vec_x, vec_y)
    denom = var_x + var_y + ((mu_x - mu_y) ** 2)
    if denom <= 1e-18:
        return 0.0

    rho_c = (2.0 * r * std_x * std_y) / denom
    if math.isnan(rho_c) or math.isinf(rho_c):
        return 0.0
    return max(-1.0, min(1.0, float(rho_c)))


def evaluate_wall_with_top2_tolerance(
    model_strike_map: Dict[float, float],
    te_strike_map: Dict[float, float],
    wall_model: float,
    wall_te: float,
    max_rel_diff: float = 0.03
) -> Tuple[bool, bool]:
    """
    Evaluates Call/Put Wall concordance with Top-2 Magnitude Tolerance for twin-strike peaks.
    Returns (exact_match, passed_with_tolerance).
    Passes if:
      1. wall_model == wall_te (exact strike match), OR
      2. wall_model is in TradingEdge's top 2 strikes by magnitude AND the model's exposure
         at wall_te is within <= max_rel_diff (3%) of the model's peak exposure at wall_model.
    """
    if wall_model == wall_te and wall_te > 0:
        return True, True

    if not te_strike_map or not model_strike_map:
        return False, False

    te_sorted = sorted(te_strike_map.items(), key=lambda kv: abs(kv[1]), reverse=True)
    te_top2_strikes = [k for k, _ in te_sorted[:2]]

    if wall_model not in te_top2_strikes:
        return False, False

    exp_model_peak = abs(float(model_strike_map.get(wall_model, 0.0)))
    exp_model_at_te_wall = abs(float(model_strike_map.get(wall_te, 0.0)))

    if exp_model_peak <= 0.0:
        return False, False

    rel_diff = abs(exp_model_peak - exp_model_at_te_wall) / exp_model_peak
    within_tol = rel_diff <= max_rel_diff
    return False, within_tol


def evaluate_regime_with_noise_zone(
    te_net_gex: float,
    model_net_gex: float,
    te_call_gex: float,
    te_put_gex: float,
    zero_flip_diff_pct: float = 0.0,
    ngr_threshold: float = 0.02,
    max_gross_norm_err_pct: float = 5.0,
    max_zero_flip_err_pct: float = 0.25
) -> Tuple[bool, bool, float, str]:
    """
    Evaluates Gamma Regime sign agreement with a Zero-Gamma 'Noise Zone' Guard.
    Returns (regime_matched, in_flip_noise_zone, net_to_gross_ratio, regime_classification).
    If NGR = |Net GEX_TE| / (|Call GEX_TE| + |Put GEX_TE|) < 0.02 (2%), classifies as
    'NEUTRAL / FLIP_ZONE' and gates pass/fail on Gross-Normalized Net Error <= 5% and
    Zero Gamma Flip Error <= 0.25%.
    """
    gross_gex = abs(te_call_gex) + abs(te_put_gex)
    ngr = (abs(te_net_gex) / gross_gex) if gross_gex > 0.0 else 0.0
    gross_norm_err_pct = (abs(model_net_gex - te_net_gex) / gross_gex * 100.0) if gross_gex > 0.0 else 0.0

    in_noise_zone = (gross_gex > 0.0 and ngr < ngr_threshold)
    if in_noise_zone:
        regime_cls = "NEUTRAL / FLIP_ZONE"
        matched = (gross_norm_err_pct <= max_gross_norm_err_pct) and (zero_flip_diff_pct <= max_zero_flip_err_pct)
    else:
        regime_cls = "Positive Gamma (+GEX)" if te_net_gex >= 0.0 else "Negative Gamma (-GEX)"
        matched = (te_net_gex >= 0.0 and model_net_gex >= 0.0) or (te_net_gex < 0.0 and model_net_gex < 0.0)

    return matched, in_noise_zone, round(ngr, 6), regime_cls


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


def _compute_granular_surface_metrics(
    te_strikes: Dict[float, Dict[str, Any]],
    ibkr_strikes: Dict[float, Dict[str, Any]],
    common_strikes: List[float],
    spot: float,
    today: date
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Computes Granular (Strike x Expiry) WMAPE and signed Weighted Mean Percentage Bias (WMPB)
    at the individual (k, t) contract level across all valid strikes, preventing cross-expiration
    error cancellation while respecting Deep ITM Parity and Small Denominator De Minimis guards.
    """
    abs_err = {"call_gex": 0.0, "put_gex": 0.0, "call_dex": 0.0, "put_dex": 0.0}
    signed_err = {"call_gex": 0.0, "put_gex": 0.0, "call_dex": 0.0, "put_dex": 0.0}
    te_base = {"call_gex": 0.0, "put_gex": 0.0, "call_dex": 0.0, "put_dex": 0.0}

    for k in common_strikes:
        t_exps = te_strikes[k].get("expirations", {}) or {}
        i_exps = ibkr_strikes[k].get("expirations", {}) or {}
        common_exps = sorted(set(t_exps.keys()).intersection(set(i_exps.keys())))

        if common_exps:
            for exp_str in common_exps:
                tm = t_exps[exp_str]
                im = i_exps[exp_str]
                try:
                    dte = (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days
                except ValueError:
                    dte = 30

                cg_t, cg_i = abs(float(tm.get("call_gex", 0.0))), abs(float(im.get("call_gex", 0.0)))
                pg_t, pg_i = abs(float(tm.get("put_gex", 0.0))), abs(float(im.get("put_gex", 0.0)))
                cd_t, cd_i = abs(float(tm.get("call_dex", 0.0))), abs(float(im.get("call_dex", 0.0)))
                pd_t, pd_i = abs(float(tm.get("put_dex", 0.0))), abs(float(im.get("put_dex", 0.0)))

                # Deep ITM Vendor Parity Guard on short-DTE puts
                moneyness = max(k / spot, spot / k) if spot > 0 else 1.0
                deep_itm_put_artifact = (
                    (moneyness >= 1.25 and dte <= 14 and (pg_t == 0.0 or cg_t == 0.0))
                    or (dte <= 14 and pg_t == 0.0 and pg_i > 0.0)
                )

                # Accumulate Call GEX & Call DEX
                abs_err["call_gex"] += abs(cg_i - cg_t)
                signed_err["call_gex"] += (cg_i - cg_t)
                te_base["call_gex"] += cg_t

                abs_err["call_dex"] += abs(cd_i - cd_t)
                signed_err["call_dex"] += (cd_i - cd_t)
                te_base["call_dex"] += cd_t

                if not deep_itm_put_artifact:
                    abs_err["put_gex"] += abs(pg_i - pg_t)
                    signed_err["put_gex"] += (pg_i - pg_t)
                    te_base["put_gex"] += pg_t

                    abs_err["put_dex"] += abs(pd_i - pd_t)
                    signed_err["put_dex"] += (pd_i - pd_t)
                    te_base["put_dex"] += pd_t
        else:
            tcg, tpg, _, tcd, tpd, _ = _extract_strike_metrics(te_strikes[k])
            icg, ipg, _, icd, ipd, _ = _extract_strike_metrics(ibkr_strikes[k])

            abs_err["call_gex"] += abs(icg - tcg)
            signed_err["call_gex"] += (icg - tcg)
            te_base["call_gex"] += abs(tcg)

            abs_err["put_gex"] += abs(abs(ipg) - abs(tpg))
            signed_err["put_gex"] += (abs(ipg) - abs(tpg))
            te_base["put_gex"] += abs(tpg)

            abs_err["call_dex"] += abs(icd - tcd)
            signed_err["call_dex"] += (icd - tcd)
            te_base["call_dex"] += abs(tcd)

            abs_err["put_dex"] += abs(abs(ipd) - abs(tpd))
            signed_err["put_dex"] += (abs(ipd) - abs(tpd))
            te_base["put_dex"] += abs(tpd)

    wmape = {}
    bias = {}
    for key in ("call_gex", "put_gex", "call_dex", "put_dex"):
        denom = te_base[key]
        wmape[key] = round((abs_err[key] / denom) * 100.0, 2) if denom > 0 else 0.0
        bias[key] = round((signed_err[key] / denom) * 100.0, 2) if denom > 0 else 0.0

    tot_gex_denom = te_base["call_gex"] + te_base["put_gex"]
    tot_dex_denom = te_base["call_dex"] + te_base["put_dex"]
    wmape["total_gex"] = round(((abs_err["call_gex"] + abs_err["put_gex"]) / tot_gex_denom) * 100.0, 2) if tot_gex_denom > 0 else 0.0
    wmape["total_dex"] = round(((abs_err["call_dex"] + abs_err["put_dex"]) / tot_dex_denom) * 100.0, 2) if tot_dex_denom > 0 else 0.0
    bias["total_gex"] = round(((signed_err["call_gex"] + signed_err["put_gex"]) / tot_gex_denom) * 100.0, 2) if tot_gex_denom > 0 else 0.0
    bias["total_dex"] = round(((signed_err["call_dex"] + signed_err["put_dex"]) / tot_dex_denom) * 100.0, 2) if tot_dex_denom > 0 else 0.0

    return wmape, bias


def compare_gexdex_payloads(
    te_payload: Dict[str, Any],
    ibkr_payload: Dict[str, Any],
    target_date: Optional[date] = None
) -> ShadowComparisonResult:
    """
    Computes comprehensive mathematical agreement between TradingEdge baseline
    and IBKR/BSM shadow calculation across both GEX and DEX.
    Incorporates:
      1. Lin's Concordance Correlation Coefficient (rho_c) alongside Pearson r.
      2. Granular (Strike x Expiry) WMAPE and signed WMPB.
      3. Top-2 Magnitude Tolerance for Call/Put Walls.
      4. Zero-Gamma 'Noise Zone' Guard (NGR < 2%) for Regime Sign Matching.
    """
    today = target_date or date.today()
    ticker = str(ibkr_payload.get("ticker") or te_payload.get("ticker") or "UNKNOWN").upper()
    run_date_str = str(today)
    spot = float(ibkr_payload.get("spot_price") or te_payload.get("spot_price") or 0.0)

    te_strikes = {float(s["strike"]): s for s in te_payload.get("strikes", []) if "strike" in s}
    ibkr_strikes = {float(s["strike"]): s for s in ibkr_payload.get("strikes", []) if "strike" in s}

    common_strikes = sorted(set(te_strikes.keys()).intersection(ibkr_strikes.keys()))

    te_cg_vec: List[float] = []
    ibkr_cg_vec: List[float] = []
    te_pg_vec: List[float] = []
    ibkr_pg_vec: List[float] = []
    te_gex_vec: List[float] = []
    ibkr_gex_vec: List[float] = []

    te_cd_vec: List[float] = []
    ibkr_cd_vec: List[float] = []
    te_pd_vec: List[float] = []
    ibkr_pd_vec: List[float] = []
    te_dex_vec: List[float] = []
    ibkr_dex_vec: List[float] = []

    te_cg_map: Dict[float, float] = {}
    ibkr_cg_map: Dict[float, float] = {}
    te_pg_map: Dict[float, float] = {}
    ibkr_pg_map: Dict[float, float] = {}

    strike_rows: List[StrikeComparisonRow] = []
    sum_te_net_dex = 0.0
    sum_ibkr_net_dex = 0.0

    for k in common_strikes:
        tcg, tpg, tnet_gex, tcd, tpd, tnet_dex = _extract_strike_metrics(te_strikes[k])
        icg, ipg, inet_gex, icd, ipd, inet_dex = _extract_strike_metrics(ibkr_strikes[k])

        sum_te_net_dex += tnet_dex
        sum_ibkr_net_dex += inet_dex

        te_cg_vec.append(tcg)
        ibkr_cg_vec.append(icg)
        te_pg_vec.append(abs(tpg))
        ibkr_pg_vec.append(abs(ipg))
        te_gex_vec.append(tnet_gex)
        ibkr_gex_vec.append(inet_gex)

        te_cd_vec.append(tcd)
        ibkr_cd_vec.append(icd)
        te_pd_vec.append(abs(tpd))
        ibkr_pd_vec.append(abs(ipd))
        te_dex_vec.append(tnet_dex)
        ibkr_dex_vec.append(inet_dex)

        te_cg_map[k] = tcg
        ibkr_cg_map[k] = icg
        te_pg_map[k] = abs(tpg)
        ibkr_pg_map[k] = abs(ipd if abs(ipg) == 0 and abs(ipd) > 0 else ipg)

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

    # Resolve Call & Put Walls
    te_cw = float(te_payload.get("call_wall") or (max(te_cg_map, key=te_cg_map.get) if te_cg_map else 0.0))
    ibkr_cw = float(ibkr_payload.get("call_wall") or (max(ibkr_cg_map, key=ibkr_cg_map.get) if ibkr_cg_map else 0.0))
    _, cw_tol = evaluate_wall_with_top2_tolerance(ibkr_cg_map, te_cg_map, ibkr_cw, te_cw, max_rel_diff=0.03)
    call_wall_matched = cw_tol or (te_cw > 0 and ibkr_cw > 0 and abs(te_cw - ibkr_cw) <= max(1.0, spot * 0.015))

    te_pw = float(te_payload.get("put_wall") or (max(te_pg_map, key=te_pg_map.get) if te_pg_map else 0.0))
    ibkr_pw = float(ibkr_payload.get("put_wall") or (max(ibkr_pg_map, key=ibkr_pg_map.get) if ibkr_pg_map else 0.0))
    _, pw_tol = evaluate_wall_with_top2_tolerance(ibkr_pg_map, te_pg_map, ibkr_pw, te_pw, max_rel_diff=0.03)
    put_wall_matched = pw_tol or (te_pw > 0 and ibkr_pw > 0 and abs(te_pw - ibkr_pw) <= max(1.0, spot * 0.015))

    te_zf = float(te_payload.get("zero_gex_level", 0.0) or spot)
    ibkr_zf = float(ibkr_payload.get("zero_gex_level", 0.0) or spot)
    spot_denom = max(spot, 1.0)
    zero_flip_diff_pct = round((abs(te_zf - ibkr_zf) / spot_denom) * 100.0, 3)

    # Gross-Normalized Error & Regime with Noise Zone Guard
    tot_te_cg = sum(te_cg_vec)
    tot_te_pg = sum(te_pg_vec)
    tot_te_cd = sum(te_cd_vec)
    tot_te_pd = sum(te_pd_vec)
    gross_gex = tot_te_cg + tot_te_pg
    gross_dex = tot_te_cd + tot_te_pd

    gross_norm_net_gex_err_pct = round((abs(ibkr_net_gex - te_net_gex) / gross_gex) * 100.0, 2) if gross_gex > 0 else 0.0
    gross_norm_net_dex_err_pct = round((abs(ibkr_net_dex - te_net_dex) / gross_dex) * 100.0, 2) if gross_dex > 0 else 0.0

    regime_matched, in_flip_noise_zone, ngr, regime_cls = evaluate_regime_with_noise_zone(
        te_net_gex=te_net_gex,
        model_net_gex=ibkr_net_gex,
        te_call_gex=tot_te_cg,
        te_put_gex=tot_te_pg,
        zero_flip_diff_pct=zero_flip_diff_pct,
        ngr_threshold=0.02
    )

    # Pearson r & Lin's Concordance Correlation Coefficient (rho_c)
    gex_pearson_r = round(calc_pearson_correlation(te_gex_vec, ibkr_gex_vec), 4)
    dex_pearson_r = round(calc_pearson_correlation(te_dex_vec, ibkr_dex_vec), 4)
    gex_lins_ccc = round(calc_lins_ccc(te_gex_vec, ibkr_gex_vec), 4)
    dex_lins_ccc = round(calc_lins_ccc(te_dex_vec, ibkr_dex_vec), 4)

    lins_ccc_map = {
        "call_gex": round(calc_lins_ccc(te_cg_vec, ibkr_cg_vec), 4),
        "put_gex": round(calc_lins_ccc(te_pg_vec, ibkr_pg_vec), 4),
        "net_gex": gex_lins_ccc,
        "call_dex": round(calc_lins_ccc(te_cd_vec, ibkr_cd_vec), 4),
        "put_dex": round(calc_lins_ccc(te_pd_vec, ibkr_pd_vec), 4),
        "net_dex": dex_lins_ccc,
    }

    granular_wmape, signed_bias = _compute_granular_surface_metrics(
        te_strikes=te_strikes,
        ibkr_strikes=ibkr_strikes,
        common_strikes=common_strikes,
        spot=spot,
        today=today
    )

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
        gex_lins_ccc=gex_lins_ccc,
        dex_lins_ccc=dex_lins_ccc,
        lins_ccc=lins_ccc_map,
        granular_wmape=granular_wmape,
        signed_bias=signed_bias,
        te_call_wall=te_cw,
        ibkr_call_wall=ibkr_cw,
        call_wall_matched=call_wall_matched,
        call_wall_within_tolerance=cw_tol,
        te_put_wall=te_pw,
        ibkr_put_wall=ibkr_pw,
        put_wall_matched=put_wall_matched,
        put_wall_within_tolerance=pw_tol,
        wall_within_tolerance=(cw_tol and pw_tol),
        te_zero_flip=te_zf,
        ibkr_zero_flip=ibkr_zf,
        zero_flip_diff_pct=zero_flip_diff_pct,
        regime_matched=regime_matched,
        net_to_gross_ratio=ngr,
        in_flip_noise_zone=in_flip_noise_zone,
        regime_classification=regime_cls,
        gross_norm_net_gex_err_pct=gross_norm_net_gex_err_pct,
        gross_norm_net_dex_err_pct=gross_norm_net_dex_err_pct,
        total_aligned_strikes=len(common_strikes),
        ibkr_contracts_count=ibkr_contracts_count,
        strike_rows=strike_rows
    )
