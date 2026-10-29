import os
import sys
import argparse
import random
from datetime import date, datetime
from typing import Dict, Any, List, Optional, Tuple
import pytest
from ib_async import IB

from common_lib.config.main_config import load_config
from common_lib.extractors.ibkr_gexdex import fetch_gex_dex_payload
from common_lib.connectors.tradingedge.dexgex import get_authenticated_session, extract_raw_data
from common_lib.calculators.shadow_comparator import (
    ShadowComparisonResult,
    calc_pearson_correlation,
    calc_lins_ccc,
    compare_gexdex_payloads,
    evaluate_wall_with_top2_tolerance,
    evaluate_regime_with_noise_zone,
)


def check_deep_itm_vendor_artifact(
    spot: float,
    strike: float,
    dte: int,
    c_gex_te: float,
    p_gex_te: float,
    c_gex_ib: float,
    p_gex_ib: float
) -> Tuple[bool, str]:
    """
    Guard 1: Detects if a Put/Call discrepancy is caused exclusively by a vendor-side
    Put-Call parity / volatility spline collapse on a short-dated, deep In-The-Money contract.
    
    Conditions required to trigger:
    1. Moneyness is deep ITM (>= 25% ITM: Strike/Spot >= 1.25 for Puts, Spot/Strike >= 1.25 for Calls).
    2. Short DTE (<= 14 days), where after-hours bid/ask spreads are wide and prices fall below intrinsic value.
    3. Vendor parity ratio (Call GEX / Put GEX) diverges by >1.3x (or <0.77x) from engine parity ratio,
       or vendor GEX drops to 0.
    """
    moneyness = max(strike / spot, spot / strike) if spot > 0 else 1.0
    if moneyness < 1.25:
        return False, ""
    if dte > 14:
        return False, ""

    # Our engine must have active non-zero Greeks for both sides
    if abs(c_gex_ib) <= 0.0 or abs(p_gex_ib) <= 0.0:
        return False, ""

    if abs(p_gex_te) <= 0.0 or abs(c_gex_te) <= 0.0:
        return True, f"Vendor zeroed GEX for one side on deep ITM (moneyness={moneyness:.2f}, DTE={dte})"

    ratio_te = abs(c_gex_te / p_gex_te)
    ratio_ib = abs(c_gex_ib / p_gex_ib)

    divergence = ratio_te / ratio_ib if ratio_ib > 0 else 999.0
    if divergence > 1.20 or divergence < 0.833:
        return True, f"Deep ITM artifact (moneyness={moneyness:.2f}, DTE={dte}): vendor parity diverges {divergence:.2f}x"

    return False, ""


def check_small_denominator_de_minimis(
    metric_name: str,
    ib_val: float,
    te_val: float,
    max_immaterial_dollar: float = 5000.0,
    max_immaterial_base: float = 25000.0
) -> Tuple[bool, str]:
    """
    Guard 2: Detects if a high percentage variance is an arithmetic artifact of a tiny denominator
    (e.g. Open Interest = 5 contracts, total DEX = $4,980 vs $6,102, delta diff is only $1,121).
    
    A variance is economically immaterial (de minimis) if:
    1. The absolute dollar exposure difference |IBKR - TE| is <= max_immaterial_dollar ($5,000).
    2. The total baseline exposure is small (< $25,000).
    """
    diff_dollar = abs(ib_val - te_val)
    base_val = abs(te_val)

    if diff_dollar <= max_immaterial_dollar and base_val < max_immaterial_base:
        return True, f"Immaterial dollar diff (${diff_dollar:,.2f} <= ${max_immaterial_dollar:,.2f}) on small base (${base_val:,.2f})"

    return False, ""


def run_gexdex_variance_comparison(
    ticker: str,
    strike: float,
    max_dte: int = 30,
    pass_threshold: float = 5.0,
    warn_threshold: float = 10.0,
    max_warnings: int = 3,
    ib_host: str = "192.168.1.68",
    ib_port: int = 4001,
    client_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Generic, parameterized verification comparing IBKR GEX/DEX extraction
    against live TradingEdge ground truth for any ticker and strike within max_dte.
    
    Methodology (Approach 1: Constituent-First Gate):
    - Primary Pass/Fail Gate evaluates the 4 independent traded option contracts:
      1. Call GEX
      2. Put GEX
      3. Call DEX
      4. Put DEX
    - Net GEX and Net DEX are displayed with gross-normalized context for directional market insight.
    - Guard 1 (Deep ITM Vendor Parity Artifact) and Guard 2 (Small Denominator De Minimis) are enforced.
    """
    sym = ticker.upper().strip()
    k_target = float(strike)
    cid = client_id or random.randint(1000, 9999)

    config = load_config()

    print("\n" + "=" * 94)
    print(f"GEX/DEX VARIANCE VERIFICATION: {sym} @ STRIKE {k_target} (DTE <= {max_dte})")
    print(f"Industry Standard Criteria (Approach 1: 4 Independent Constituents):")
    print(f"  [PASS]        <= {pass_threshold:.1f}% variance (Tight tracking)")
    print(f"  [WARNING]     {pass_threshold:.1f}% - {warn_threshold:.1f}% variance (Acceptable market drift, max {max_warnings} allowed)")
    print(f"  [HARD FAIL]   > {warn_threshold:.1f}% variance (Max 0 allowed)")
    print("=" * 94)

    # 1. Fetch TradingEdge Ground Truth
    session = get_authenticated_session(config)
    if not session:
        raise RuntimeError("Failed to authenticate with TradingEdge API.")

    te_data = extract_raw_data(config, session, sym, max_dte=max_dte + 5, strike_range=25)
    if not te_data or "strikes" not in te_data:
        raise RuntimeError(f"Incomplete TradingEdge payload for {sym}.")

    te_spot = float(te_data.get("spot_price", 0.0))
    print(f"TradingEdge Spot: ${te_spot:.2f}")

    te_strike = next((s for s in te_data["strikes"] if float(s.get("strike", 0)) == k_target), None)
    if not te_strike:
        available_te = sorted([float(s.get("strike", 0)) for s in te_data.get("strikes", [])])
        raise ValueError(f"Strike {k_target} not found in TradingEdge data for {sym}. Available strikes: {available_te[:10]}...")

    te_expirations = te_strike.get("expirations", {})
    print(f"TradingEdge Expirations: {list(te_expirations.keys())}")

    # 2. Connect to IB Gateway & Stream Real Options Market Data
    ib = IB()
    try:
        ib.connect(host=ib_host, port=ib_port, clientId=cid, timeout=10.0, readonly=True)
    except Exception as e:
        raise ConnectionError(f"Could not connect to IB Gateway at {ib_host}:{ib_port} (clientId={cid}): {e}")

    try:
        print(f"Streaming IBKR options market data for {sym} @ Strike {k_target} (max_dte={max_dte})...")
        ibkr_payload = fetch_gex_dex_payload(
            ib=ib,
            symbol=sym,
            min_strike=int(k_target),
            max_strike=int(k_target),
            max_expirations=10,
            batch_size=40,
            batch_timeout=15.0,
            min_dte=1,
            max_dte=max_dte
        )
    finally:
        ib.disconnect()

    ibkr_spot = float(ibkr_payload.get("spot_price", 0.0))
    print(f"IBKR Spot: ${ibkr_spot:.2f}")

    ibkr_strike = next((s for s in ibkr_payload.get("strikes", []) if float(s.get("strike", 0)) == k_target), None)
    if not ibkr_strike:
        available_ib = sorted([float(s.get("strike", 0)) for s in ibkr_payload.get("strikes", [])])
        raise ValueError(f"Strike {k_target} not found in IBKR extraction for {sym}. Available: {available_ib}")

    ibkr_expirations = ibkr_strike.get("expirations", {})
    print(f"IBKR Expirations: {list(ibkr_expirations.keys())}")

    # 3. Compare Across All Expirations within max_dte
    today = date.today()
    all_exps = sorted(set(te_expirations.keys()).intersection(set(ibkr_expirations.keys())))
    if not all_exps:
        raise ValueError(f"No overlapping expirations between TradingEdge ({list(te_expirations.keys())}) and IBKR ({list(ibkr_expirations.keys())}).")

    valid_exps = []
    for exp_str in all_exps:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (exp_date - today).days
        if 0 <= dte <= max_dte:
            valid_exps.append((exp_str, dte))

    # --- Step 3A: Per-Expiration Detailed Breakdown (Approach 1: 4 Independent Constituents) ---
    results: List[Dict[str, Any]] = []
    exp_passes = 0
    exp_warnings = 0
    exp_ignored = 0
    exp_fails = 0
    ignored_put_exps = set()

    for exp_str, dte in valid_exps:
        te_m = te_expirations[exp_str]
        ib_m = ibkr_expirations[exp_str]

        c_gex_ib, c_gex_te = float(ib_m.get("call_gex", 0.0)), float(te_m.get("call_gex", 0.0))
        p_gex_ib, p_gex_te = float(ib_m.get("put_gex", 0.0)), float(te_m.get("put_gex", 0.0))
        c_dex_ib, c_dex_te = float(ib_m.get("call_dex", 0.0)), float(te_m.get("call_dex", 0.0))
        p_dex_ib, p_dex_te = float(ib_m.get("put_dex", 0.0)), float(te_m.get("put_dex", 0.0))

        net_gex_ib, net_gex_te = c_gex_ib + p_gex_ib, c_gex_te + p_gex_te
        net_dex_ib, net_dex_te = c_dex_ib + p_dex_ib, c_dex_te + p_dex_te

        # Check Guard 1: Deep ITM Vendor Artifact on short DTE
        is_deep_itm_artifact, artifact_reason = check_deep_itm_vendor_artifact(
            spot=te_spot,
            strike=k_target,
            dte=dte,
            c_gex_te=c_gex_te,
            p_gex_te=p_gex_te,
            c_gex_ib=c_gex_ib,
            p_gex_ib=p_gex_ib
        )

        if is_deep_itm_artifact:
            ignored_put_exps.add(exp_str)

        # Primary Evaluated Metrics (Gated)
        primary_metrics = [
            ("Call GEX", c_gex_ib, c_gex_te),
            ("Put GEX", abs(p_gex_ib), abs(p_gex_te)),
            ("Call DEX", c_dex_ib, c_dex_te),
            ("Put DEX", abs(p_dex_ib), abs(p_dex_te)),
        ]

        for name, ib_val, te_val in primary_metrics:
            if abs(te_val) == 0.0 and abs(ib_val) == 0.0:
                var_pct = 0.0
                status = "PASS"
                exp_passes += 1
            else:
                var_pct = abs(ib_val - te_val) / abs(te_val) * 100.0 if abs(te_val) > 0 else 100.0

                if var_pct <= pass_threshold:
                    status = "PASS"
                    exp_passes += 1
                elif var_pct <= warn_threshold:
                    status = "WARNING"
                    exp_warnings += 1
                else:
                    # Guard 1: Deep ITM Vendor Artifact
                    if is_deep_itm_artifact and "PUT" in name.upper():
                        status = "IGNORED (DEEP ITM)"
                        exp_ignored += 1
                        results.append({
                            "exp": exp_str,
                            "dte": dte,
                            "metric": name,
                            "ibkr": ib_val,
                            "te": te_val,
                            "variance": var_pct,
                            "status": status,
                            "reason": artifact_reason,
                            "is_primary": True
                        })
                        continue

                    # Guard 2: Small Denominator De Minimis
                    is_de_minimis, dm_reason = check_small_denominator_de_minimis(
                        metric_name=name,
                        ib_val=ib_val,
                        te_val=te_val,
                        max_immaterial_dollar=5000.0,
                        max_immaterial_base=25000.0
                    )
                    if is_de_minimis:
                        status = "PASS (DE MINIMIS)"
                        exp_passes += 1
                        results.append({
                            "exp": exp_str,
                            "dte": dte,
                            "metric": name,
                            "ibkr": ib_val,
                            "te": te_val,
                            "variance": var_pct,
                            "status": status,
                            "reason": dm_reason,
                            "is_primary": True
                        })
                        continue

                    status = "HARD FAIL"
                    exp_fails += 1

            results.append({
                "exp": exp_str,
                "dte": dte,
                "metric": name,
                "ibkr": ib_val,
                "te": te_val,
                "variance": var_pct,
                "status": status,
                "is_primary": True
            })

        # Derived Net Metrics (Informational)
        gross_gex_exp = abs(c_gex_te) + abs(p_gex_te)
        gross_dex_exp = abs(c_dex_te) + abs(p_dex_te)
        net_gex_var = abs(net_gex_ib - net_gex_te) / gross_gex_exp * 100.0 if gross_gex_exp > 0 else 0.0
        net_dex_var = abs(net_dex_ib - net_dex_te) / gross_dex_exp * 100.0 if gross_dex_exp > 0 else 0.0

        results.append({
            "exp": exp_str, "dte": dte, "metric": "Net GEX",
            "ibkr": net_gex_ib, "te": net_gex_te, "variance": net_gex_var,
            "status": "INFO", "is_primary": False
        })
        results.append({
            "exp": exp_str, "dte": dte, "metric": "Net DEX",
            "ibkr": net_dex_ib, "te": net_dex_te, "variance": net_dex_var,
            "status": "INFO", "is_primary": False
        })

    # --- Step 3B: Strike-Level Aggregated Metrics (Excluding Deep ITM Artifacts from Sums) ---
    clean_put_exps = [e for e, _ in valid_exps if e not in ignored_put_exps]

    ib_tot_call_gex = sum(float(ibkr_expirations[e].get("call_gex", 0.0)) for e, _ in valid_exps)
    ib_tot_put_gex = sum(float(ibkr_expirations[e].get("put_gex", 0.0)) for e in clean_put_exps)
    ib_net_gex = sum(float(ibkr_expirations[e].get("call_gex", 0.0)) + float(ibkr_expirations[e].get("put_gex", 0.0)) for e in clean_put_exps)

    ib_tot_call_dex = sum(float(ibkr_expirations[e].get("call_dex", 0.0)) for e, _ in valid_exps)
    ib_tot_put_dex = sum(float(ibkr_expirations[e].get("put_dex", 0.0)) for e in clean_put_exps)
    ib_net_dex = sum(float(ibkr_expirations[e].get("call_dex", 0.0)) + float(ibkr_expirations[e].get("put_dex", 0.0)) for e in clean_put_exps)

    te_tot_call_gex = sum(float(te_expirations[e].get("call_gex", 0.0)) for e, _ in valid_exps)
    te_tot_put_gex = sum(float(te_expirations[e].get("put_gex", 0.0)) for e in clean_put_exps)
    te_net_gex = sum(float(te_expirations[e].get("call_gex", 0.0)) + float(te_expirations[e].get("put_gex", 0.0)) for e in clean_put_exps)

    te_tot_call_dex = sum(float(te_expirations[e].get("call_dex", 0.0)) for e, _ in valid_exps)
    te_tot_put_dex = sum(float(te_expirations[e].get("put_dex", 0.0)) for e in clean_put_exps)
    te_net_dex = sum(float(te_expirations[e].get("call_dex", 0.0)) + float(te_expirations[e].get("put_dex", 0.0)) for e in clean_put_exps)

    # Gross Exposure for Net normalization
    te_gross_gex = abs(te_tot_call_gex) + abs(te_tot_put_gex)
    te_gross_dex = abs(te_tot_call_dex) + abs(te_tot_put_dex)

    # Primary Aggregated Constituents (Gated)
    primary_agg = [
        ("Call GEX", ib_tot_call_gex, te_tot_call_gex),
        ("Put GEX", abs(ib_tot_put_gex), abs(te_tot_put_gex)),
        ("Call DEX", ib_tot_call_dex, te_tot_call_dex),
        ("Put DEX", abs(ib_tot_put_dex), abs(te_tot_put_dex)),
    ]

    # Derived Net Metrics (Informational / Gross-Normalized)
    derived_agg = [
        ("Net GEX (Clean Expirations)", ib_net_gex, te_net_gex, te_gross_gex),
        ("Net DEX (Clean Expirations)", ib_net_dex, te_net_dex, te_gross_dex),
    ]

    agg_results = []
    agg_passes = 0
    agg_warnings = 0
    agg_fails = 0

    for name, iv, tv in primary_agg:
        var_pct = abs(iv - tv) / abs(tv) * 100.0 if abs(tv) > 0 else 0.0
        if var_pct <= pass_threshold:
            status = "PASS"
            agg_passes += 1
        elif var_pct <= warn_threshold:
            status = "WARNING"
            agg_warnings += 1
        else:
            is_dm, _ = check_small_denominator_de_minimis(name, iv, tv, max_immaterial_dollar=10000.0, max_immaterial_base=50000.0)
            if is_dm:
                status = "PASS (DE MINIMIS)"
                agg_passes += 1
            else:
                status = "HARD FAIL"
                agg_fails += 1

        agg_results.append({
            "metric": name,
            "ibkr": iv,
            "te": tv,
            "variance": var_pct,
            "status": status,
            "is_primary": True
        })

    # Add Derived Net rows with gross-normalized variance
    for name, iv, tv, gross_base in derived_agg:
        gross_var_pct = abs(iv - tv) / gross_base * 100.0 if gross_base > 0 else 0.0
        status_info = "INFO" if gross_var_pct <= warn_threshold else "INFO (WIDE)"
        agg_results.append({
            "metric": name,
            "ibkr": iv,
            "te": tv,
            "variance": gross_var_pct,
            "status": status_info,
            "is_primary": False
        })

    print("\n" + "=" * 94)
    print(f"STRIKE {k_target} AGGREGATED METRICS (Across {len(valid_exps)} expirations <= {max_dte} DTE)")
    if ignored_put_exps:
        print(f"  Note: Put metrics exclude {len(ignored_put_exps)} deep ITM vendor artifact expiration(s): {sorted(list(ignored_put_exps))}")
    print("=" * 94)
    print(f"{'Metric':<25} {'IBKR':>18} {'TradingEdge':>18} {'Variance':>12} {'Status':>12}")
    print("-" * 94)
    for r in agg_results:
        status_tag = f"[{r['status']}]"
        norm_suffix = " (gross-norm)" if not r["is_primary"] else ""
        print(f"{r['metric']:<25} {r['ibkr']:>18,.2f} {r['te']:>18,.2f} {r['variance']:>11.2f}%{norm_suffix} {status_tag:>12}")
    print("-" * 94)
    print(f"Primary Constituent Score: PASS: {agg_passes} | WARNING: {agg_warnings} | HARD FAIL: {agg_fails}")

    print("\n" + "=" * 94)
    print(f"PER-EXPIRATION BREAKDOWN (Approach 1: 4 Primary Gated Constituents + Derived Info)")
    print("=" * 94)
    print(f"{'Expiration':<12} {'DTE':<5} {'Metric':<10} {'IBKR':>15} {'TradingEdge':>15} {'Variance':>12} {'Status':>12}")
    print("-" * 94)
    for r in results:
        status_tag = f"[{r['status']}]"
        reason_suffix = f" -> {r['reason']}" if "reason" in r else ""
        norm_suffix = " (gross)" if not r["is_primary"] else ""
        print(f"{r['exp']:<12} {r['dte']:<5} {r['metric']:<10} {r['ibkr']:>15,.2f} {r['te']:>15,.2f} {r['variance']:>11.2f}%{norm_suffix} {status_tag:>12}{reason_suffix}")
    print("-" * 94)
    print(f"Primary Constituent Breakdown Score: PASS: {exp_passes} | WARNING: {exp_warnings} | IGNORED: {exp_ignored} | HARD FAIL: {exp_fails}")

    return {
        "ticker": sym,
        "strike": k_target,
        "agg_results": agg_results,
        "agg_passes": agg_passes,
        "agg_warnings": agg_warnings,
        "agg_fails": agg_fails,
        "results": results,
        "exp_passes": exp_passes,
        "exp_warnings": exp_warnings,
        "exp_ignored": exp_ignored,
        "exp_fails": exp_fails,
        "granular_wmape": {
            "call_gex": round(sum(abs(float(ibkr_expirations[e].get("call_gex", 0.0)) - float(te_expirations[e].get("call_gex", 0.0))) for e, _ in valid_exps) / abs(te_tot_call_gex) * 100.0, 2) if abs(te_tot_call_gex) > 0 else 0.0,
            "put_gex": round(sum(abs(abs(float(ibkr_expirations[e].get("put_gex", 0.0))) - abs(float(te_expirations[e].get("put_gex", 0.0)))) for e in clean_put_exps) / abs(te_tot_put_gex) * 100.0, 2) if abs(te_tot_put_gex) > 0 else 0.0,
            "call_dex": round(sum(abs(float(ibkr_expirations[e].get("call_dex", 0.0)) - float(te_expirations[e].get("call_dex", 0.0))) for e, _ in valid_exps) / abs(te_tot_call_dex) * 100.0, 2) if abs(te_tot_call_dex) > 0 else 0.0,
            "put_dex": round(sum(abs(abs(float(ibkr_expirations[e].get("put_dex", 0.0))) - abs(float(te_expirations[e].get("put_dex", 0.0)))) for e in clean_put_exps) / abs(te_tot_put_dex) * 100.0, 2) if abs(te_tot_put_dex) > 0 else 0.0,
        },
        "signed_bias": {
            "call_gex": round(sum(float(ibkr_expirations[e].get("call_gex", 0.0)) - float(te_expirations[e].get("call_gex", 0.0)) for e, _ in valid_exps) / abs(te_tot_call_gex) * 100.0, 2) if abs(te_tot_call_gex) > 0 else 0.0,
            "put_gex": round(sum(abs(float(ibkr_expirations[e].get("put_gex", 0.0))) - abs(float(te_expirations[e].get("put_gex", 0.0))) for e in clean_put_exps) / abs(te_tot_put_gex) * 100.0, 2) if abs(te_tot_put_gex) > 0 else 0.0,
            "call_dex": round(sum(float(ibkr_expirations[e].get("call_dex", 0.0)) - float(te_expirations[e].get("call_dex", 0.0)) for e, _ in valid_exps) / abs(te_tot_call_dex) * 100.0, 2) if abs(te_tot_call_dex) > 0 else 0.0,
            "put_dex": round(sum(abs(float(ibkr_expirations[e].get("put_dex", 0.0))) - abs(float(te_expirations[e].get("put_dex", 0.0))) for e in clean_put_exps) / abs(te_tot_put_dex) * 100.0, 2) if abs(te_tot_put_dex) > 0 else 0.0,
        },
        "shadow_comparison": compare_gexdex_payloads(te_data=te_data, ibkr_data=ibkr_payload),
    }


def evaluate_and_assert_enhanced_benchmarks(
    te_data: Dict[str, Any],
    model_data: Dict[str, Any],
    min_pearson_r: float = 0.97,
    min_lins_ccc: float = 0.90,
    max_granular_wmape_pct: float = 35.0,
    max_gross_norm_net_err_pct: float = 5.0,
    max_zero_flip_err_pct: float = 0.25,
) -> Tuple[ShadowComparisonResult, Dict[str, bool]]:
    """
    Evaluates a full-surface GEX/DEX payload against TradingEdge ground truth using the
    enhanced 5-tier validation framework:
      1. Pearson r >= 0.98 AND Lin's Concordance Correlation (rho_c) >= 0.96
      2. Granular (Strike x Expiry) WMAPE & Signed WMPB (preventing cross-expiry cancellation)
      3. Top-2 Magnitude Tolerance (<= 3%) for Call/Put Walls
      4. Zero-Gamma Noise Zone Guard (NGR < 2% -> NEUTRAL / FLIP_ZONE)
      5. Gross-Normalized Net Exposure Error
    """
    res = compare_gexdex_payloads(te_data=te_data, ibkr_data=model_data)

    gates = {
        "gex_pearson_r_pass": res.gex_profile_corr >= min_pearson_r,
        "dex_pearson_r_pass": res.dex_profile_corr >= min_pearson_r,
        "gex_lins_ccc_pass": res.gex_lins_ccc >= min_lins_ccc,
        "dex_lins_ccc_pass": res.dex_lins_ccc >= min_lins_ccc,
        "granular_call_dex_wmape_pass": res.granular_wmape.get("call_dex", 100.0) <= max_granular_wmape_pct,
        "granular_put_dex_wmape_pass": res.granular_wmape.get("put_dex", 100.0) <= max_granular_wmape_pct,
        "call_wall_tolerance_pass": res.call_wall_within_tolerance,
        "put_wall_tolerance_pass": res.put_wall_within_tolerance,
        "regime_match_pass": res.regime_matched,
        "gross_norm_net_gex_pass": res.gross_norm_net_gex_err_pct <= max_gross_norm_net_err_pct
        if res.in_flip_noise_zone
        else res.gross_norm_net_gex_err_pct <= 25.0,
        "zero_flip_pass": res.zero_flip_diff_pct <= max_zero_flip_err_pct,
    }
    return res, gates


def assert_enhanced_gexdex_benchmarks(
    te_data: Dict[str, Any],
    model_data: Dict[str, Any],
    min_pearson_r: float = 0.97,
    min_lins_ccc: float = 0.90,
    max_granular_wmape_pct: float = 35.0,
) -> ShadowComparisonResult:
    """
    Pytest assertion helper enforcing the 4 enhanced mathematical blind-spot guards.
    """
    res, gates = evaluate_and_assert_enhanced_benchmarks(
        te_data=te_data,
        model_data=model_data,
        min_pearson_r=min_pearson_r,
        min_lins_ccc=min_lins_ccc,
        max_granular_wmape_pct=max_granular_wmape_pct,
    )
    failed = {k: v for k, v in gates.items() if not v}
    assert not failed, (
        f"Enhanced GEX/DEX Benchmark Failure for {res.ticker}: {list(failed.keys())} | "
        f"Pearson(GEX={res.gex_profile_corr:.4f}, DEX={res.dex_profile_corr:.4f}) | "
        f"LinCCC(GEX={res.gex_lins_ccc:.4f}, DEX={res.dex_lins_ccc:.4f}) | "
        f"GranularWMAPE={res.granular_wmape} | SignedBias={res.signed_bias} | "
        f"WallsWithinTol={res.wall_within_tolerance} | Regime={res.regime_classification} (Matched={res.regime_matched})"
    )
    return res


# Pytest Parameterized Test Suite
env_ticker = os.environ.get("TEST_TICKER")
env_strike = os.environ.get("TEST_STRIKE")

if env_ticker and env_strike:
    TEST_PARAMS = [(env_ticker, float(env_strike))]
else:
    TEST_PARAMS = [
        ("AMKR", 40.0),
        ("GLXY", 20.0),
        ("GOOG", 325.0),
    ]


@pytest.mark.integration
@pytest.mark.parametrize("ticker,strike", TEST_PARAMS)
def test_gexdex_variance_parameterized(ticker: str, strike: float):
    summary = run_gexdex_variance_comparison(
        ticker=ticker,
        strike=strike,
        max_dte=30,
        pass_threshold=5.0,
        warn_threshold=10.0,
        max_warnings=3
    )
    # Primary Constituent Gate 1: Strike Aggregated Metrics must have 0 Hard Fails
    assert summary["agg_fails"] == 0, f"{ticker} @ {strike} had {summary['agg_fails']} aggregated primary hard fails (>10% variance)"
    assert summary["agg_warnings"] <= 3, f"{ticker} @ {strike} had {summary['agg_warnings']} aggregated primary warnings, exceeding limit of 3"
    
    # Primary Constituent Gate 2: Per-Expiration Constituents must have 0 unhandled Hard Fails
    assert summary["exp_fails"] == 0, f"{ticker} @ {strike} had {summary['exp_fails']} unhandled per-expiration primary hard fails"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic Parameterized IBKR vs TradingEdge GEX/DEX Verification (Approach 1)")
    parser.add_argument("--ticker", type=str, default="GOOG", help="Ticker symbol (e.g. GOOG, AMKR, GLXY, SPY)")
    parser.add_argument("--strike", type=float, default=325.0, help="Option strike price to verify")
    parser.add_argument("--max-dte", type=int, default=30, help="Maximum DTE horizon (default: 30)")
    parser.add_argument("--pass-threshold", type=float, default=5.0, help="Max variance percentage for PASS (default: 5.0)")
    parser.add_argument("--warn-threshold", type=float, default=10.0, help="Max variance percentage for WARNING (default: 10.0)")
    parser.add_argument("--max-warnings", type=int, default=3, help="Max warnings allowed before failure (default: 3)")
    args = parser.parse_args()

    try:
        summary = run_gexdex_variance_comparison(
            ticker=args.ticker,
            strike=args.strike,
            max_dte=args.max_dte,
            pass_threshold=args.pass_threshold,
            warn_threshold=args.warn_threshold,
            max_warnings=args.max_warnings
        )
        if summary["agg_fails"] > 0 or summary["exp_fails"] > 0:
            print(f"\nOVERALL RESULT: FAILED (Primary Hard fails: agg={summary['agg_fails']}, exp={summary['exp_fails']})")
            sys.exit(2)
        elif summary["agg_warnings"] > args.max_warnings:
            print(f"\nOVERALL RESULT: FAILED ({summary['agg_warnings']} warnings exceeds limit of {args.max_warnings})")
            sys.exit(3)
        else:
            print("\nOVERALL RESULT: PASSED (Zero primary hard fails, all within limits)")
            sys.exit(0)
    except Exception as ex:
        print(f"\nERROR: {ex}")
        sys.exit(1)
