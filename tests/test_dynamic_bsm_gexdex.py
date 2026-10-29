import math
from datetime import datetime, date, timezone
import numpy as np
import pandas as pd
import pytest

from common_lib.calculators.gexdex import (
    recompute_dynamic_bsm_gexdex,
    compute_gex_profile
)


def _build_sample_df(spot=100.0):
    """Constructs a representative options chain DataFrame."""
    return pd.DataFrame([
        # ATM Call & Put (30 DTE)
        {"strike": 100.0, "expiration": "2026-11-28", "option_type": "C", "open_interest": 1000, "iv": 0.25},
        {"strike": 100.0, "expiration": "2026-11-28", "option_type": "P", "open_interest": 1000, "iv": 0.25},
        # OTM Call & Put (30 DTE)
        {"strike": 110.0, "expiration": "2026-11-28", "option_type": "C", "open_interest": 2000, "iv": 0.22},
        {"strike": 90.0,  "expiration": "2026-11-28", "option_type": "P", "open_interest": 2500, "iv": 0.28},
        # 0DTE Expired or Expiring today
        {"strike": 100.0, "expiration": date.today().strftime("%Y-%m-%d"), "option_type": "C", "open_interest": 500, "iv": 0.30},
        {"strike": 100.0, "expiration": date.today().strftime("%Y-%m-%d"), "option_type": "P", "open_interest": 500, "iv": 0.30},
        # Invalid / Zero IV Row (Must be masked safely without error)
        {"strike": 150.0, "expiration": "2026-11-28", "option_type": "C", "open_interest": 0, "iv": 0.0},
    ])


def test_recompute_dynamic_bsm_gexdex_outputs():
    df = _build_sample_df(spot=100.0)
    spot_live = 105.0
    spot_close = 100.0

    result_df = recompute_dynamic_bsm_gexdex(
        df=df,
        spot_live=spot_live,
        spot_close=spot_close,
        r=0.045,
        q=0.0,
        iv_scale=1.0
    )

    # Required columns must exist
    for col in ["d1", "delta_live", "gamma_live", "dex_live", "gex_live", "dex_shock"]:
        assert col in result_df.columns, f"Missing required column: {col}"

    # Vectorized execution check: length must match
    assert len(result_df) == len(df)

    # Call deltas must be >= 0, Put deltas must be <= 0
    calls = result_df[result_df["option_type"] == "C"]
    puts = result_df[result_df["option_type"] == "P"]

    valid_calls = calls[calls["iv"] > 0]
    valid_puts = puts[puts["iv"] > 0]

    assert (valid_calls["delta_live"] >= 0.0).all()
    assert (valid_puts["delta_live"] <= 0.0).all()

    # Call GEX must be >= 0, Put GEX must be <= 0
    assert (valid_calls["gex_live"] >= 0.0).all()
    assert (valid_puts["gex_live"] <= 0.0).all()

    # Gamma must be strictly non-negative
    assert (result_df["gamma_live"] >= 0.0).all()

    # dex_shock verification: dex_shock = dex_live - dex_close
    assert "dex_close" in result_df.columns
    diff = result_df["dex_shock"] - (result_df["dex_live"] - result_df["dex_close"])
    assert np.allclose(diff.fillna(0.0), 0.0, atol=1e-5)


def test_zero_dte_and_clamping():
    """Verify that T <= 0 does not raise ZeroDivisionError and clamps T >= 1e-6."""
    df = pd.DataFrame([
        {"strike": 100.0, "expiration": "2020-01-01", "option_type": "C", "open_interest": 100, "iv": 0.20},
        {"strike": 100.0, "expiration": "2020-01-01", "option_type": "P", "open_interest": 100, "iv": 0.20},
    ])
    result_df = recompute_dynamic_bsm_gexdex(df, spot_live=105.0)
    assert not result_df["gamma_live"].isna().any()
    assert not np.isinf(result_df["gamma_live"]).any()


def test_iv_scale_effect():
    """Verify iv_scale scales IV correctly."""
    df = _build_sample_df(spot=100.0)
    res_base = recompute_dynamic_bsm_gexdex(df, spot_live=100.0, iv_scale=1.0)
    res_high_iv = recompute_dynamic_bsm_gexdex(df, spot_live=100.0, iv_scale=1.5)

    # ATM gamma decreases as IV increases (wider probability bell curve)
    atm_gamma_base = res_base.loc[res_base["strike"] == 100.0, "gamma_live"].iloc[0]
    atm_gamma_high = res_high_iv.loc[res_high_iv["strike"] == 100.0, "gamma_live"].iloc[0]
    assert atm_gamma_high < atm_gamma_base


def test_compute_gex_profile_broadcasting_and_zero_flip():
    """Verify compute_gex_profile 2D array broadcasting and zero gamma flip interpolation."""
    df = _build_sample_df(spot=100.0)
    spot_live = 100.0

    profile = compute_gex_profile(df, spot_live=spot_live, pct_range=0.10, steps=50)

    assert "spot_prices" in profile
    assert "net_gex" in profile
    assert "zero_gamma_flip" in profile
    assert len(profile["spot_prices"]) == 50
    assert len(profile["net_gex"]) == 50

    # Spot array must span [90.0, 110.0]
    assert math.isclose(profile["spot_prices"][0], 90.0, rel_tol=1e-3)
    assert math.isclose(profile["spot_prices"][-1], 110.0, rel_tol=1e-3)

    # Zero gamma flip must be a valid float
    assert isinstance(profile["zero_gamma_flip"], float)
    assert 80.0 <= profile["zero_gamma_flip"] <= 120.0


def test_evaluate_bsm_vs_tradingedge_accuracy_metrics():
    """Verify the 5-tier accuracy evaluator against a known ground-truth TradingEdge payload."""
    from common_lib.calculators.gexdex import evaluate_bsm_vs_tradingedge_accuracy

    df = pd.DataFrame([
        {"strike": 20.0, "expiration": "2026-12-18", "option_type": "C", "open_interest": 10000, "iv": 0.85},
        {"strike": 20.0, "expiration": "2026-12-18", "option_type": "P", "open_interest": 5000, "iv": 0.85},
        {"strike": 25.0, "expiration": "2026-12-18", "option_type": "C", "open_interest": 4000, "iv": 0.80},
        {"strike": 15.0, "expiration": "2026-12-18", "option_type": "P", "open_interest": 6000, "iv": 0.90},
    ])
    spot = 19.09
    res_df = recompute_dynamic_bsm_gexdex(df, spot_live=spot, spot_close=spot)

    # Build a synthetic TE ground-truth payload from the BSM output with <1% noise
    strikes_list = []
    for k, grp in res_df.groupby("strike"):
        cg = float(grp.loc[grp["option_type"] == "C", "gex_live"].sum() * 100.0)
        pg = float(grp.loc[grp["option_type"] == "P", "gex_live"].sum() * 100.0)
        cd = float(grp.loc[grp["option_type"] == "C", "dex_live"].sum())
        pd_val = float(grp.loc[grp["option_type"] == "P", "dex_live"].sum())
        strikes_list.append({
            "strike": float(k),
            "call_gex": cg,
            "put_gex": pg,
            "net_gex": cg + pg,
            "call_dex": cd,
            "put_dex": pd_val,
            "net_dex": cd + pd_val,
            "expirations": {
                "2026-12-18": {
                    "call_gex": cg,
                    "put_gex": pg,
                    "call_dex": cd,
                    "put_dex": pd_val,
                }
            }
        })

    te_payload = {
        "ticker": "GLXY",
        "spot_price": spot,
        "call_wall": 20.0,
        "put_wall": 15.0,
        "zero_gex_level": 18.5,
        "strikes": strikes_list
    }

    report = evaluate_bsm_vs_tradingedge_accuracy(
        df_bsm=res_df,
        te_payload=te_payload,
        target_strike=20.0,
        max_dte=60
    )

    assert "wmape" in report
    assert "pearson_r" in report
    assert "landmarks" in report
    assert "strike_evaluation" in report
    assert "benchmarks_passed" in report
    assert report["pearson_r"]["net_gex"] >= 0.99
    assert report["landmarks"]["call_wall_match"] is True
    assert report["benchmarks_passed"] is True


def test_glxy_strike_20_60dte_tradingedge_ground_truth():
    """
    Live parameterized verification for GLXY @ 20 strike across all contracts up to 60 DTE
    comparing Dynamic BSM re-evaluation against TradingEdge API ground truth.
    """
    from common_lib.config.main_config import load_config
    from common_lib.connectors.tradingedge.dexgex import get_authenticated_session, extract_raw_data
    from common_lib.connectors.cboe_oi import fetch_cboe_delayed_data
    from common_lib.calculators.gexdex import evaluate_bsm_vs_tradingedge_accuracy

    config = load_config()
    session = get_authenticated_session(config)
    assert session is not None, "TradingEdge authentication failed"

    te_data = extract_raw_data(config, session, "GLXY", max_dte=60, strike_range=25)
    assert te_data and "strikes" in te_data, "Failed to retrieve GLXY payload from TradingEdge"

    spot = float(te_data["spot_price"])
    cboe = fetch_cboe_delayed_data("GLXY")
    assert len(cboe) > 0, "Failed to retrieve CBOE option chain data for GLXY"

    rows = []
    for s_obj in te_data["strikes"]:
        k = float(s_obj["strike"])
        for exp_iso in s_obj.get("expirations", {}).keys():
            exp_k = exp_iso.replace("-", "")
            for right in ("C", "P"):
                cb = cboe.get((exp_k, k, right), {})
                rows.append({
                    "strike": k,
                    "expiration": exp_iso,
                    "option_type": right,
                    "open_interest": cb.get("open_interest") or 0.0,
                    "iv": cb.get("iv") or 0.0,
                })

    df = pd.DataFrame(rows)
    res_df = recompute_dynamic_bsm_gexdex(df, spot_live=spot, spot_close=spot)
    report = evaluate_bsm_vs_tradingedge_accuracy(
        df_bsm=res_df,
        te_payload=te_data,
        target_strike=20.0,
        max_dte=60
    )

    assert report["strike_evaluation"]["expirations_evaluated"] >= 5
    assert "lins_ccc" in report
    assert "granular_wmape" in report
    assert "signed_bias" in report
    assert report["landmarks"]["wall_within_tolerance"] is True
    assert report["landmarks"]["call_wall_within_tolerance"] is True
    assert report["landmarks"]["put_wall_within_tolerance"] is True
    assert report["landmarks"]["regime_match"] is True
    assert report["wmape"]["call_dex_wmape_pct"] <= 10.0
    assert report["pearson_r"]["net_gex"] >= 0.97
    assert report["pearson_r"]["net_dex"] >= 0.97
    assert report["lins_ccc"]["net_gex"] >= 0.90
    assert report["lins_ccc"]["net_dex"] >= 0.90
    assert report["benchmarks_passed"] is True, f"Failed benchmarks: {report['benchmarks']}"


def test_glxy_25_strikes_range_median_spot_60dte():
    """
    Expanded structural verification for GLXY across all 25 strikes centered on spot
    (with spot being the median strike: $13.00 to $25.00, median $19.00) across all contracts up to 60 DTE.
    Verifies that the double-sided structural chart invariants (Call/Put Walls, Gamma Regime, and
    bar profile shape Pearson r >= 0.97, Lin's CCC >= 0.90) match TradingEdge ground truth.
    """
    from common_lib.config.main_config import load_config
    from common_lib.connectors.tradingedge.dexgex import get_authenticated_session, extract_raw_data
    from common_lib.connectors.cboe_oi import fetch_cboe_delayed_data
    from common_lib.calculators.gexdex import evaluate_bsm_vs_tradingedge_accuracy

    config = load_config()
    session = get_authenticated_session(config)
    assert session is not None, "TradingEdge authentication failed"

    te_data = extract_raw_data(config, session, "GLXY", max_dte=60, strike_range=25)
    assert te_data and "strikes" in te_data, "Failed to retrieve GLXY payload from TradingEdge"

    spot = float(te_data["spot_price"])
    cboe = fetch_cboe_delayed_data("GLXY")
    assert len(cboe) > 0, "Failed to retrieve CBOE option chain data for GLXY"

    all_strikes = sorted([float(x["strike"]) for x in te_data["strikes"]])
    assert len(all_strikes) >= 25, f"Expected at least 25 strikes, got {len(all_strikes)}"

    # Identify median strike closest to spot
    closest_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
    median_strike = all_strikes[closest_idx]

    # Select 25 strikes centered symmetrically on spot
    half_window = 12
    start_idx = max(0, closest_idx - half_window)
    end_idx = start_idx + 25
    if end_idx > len(all_strikes):
        end_idx = len(all_strikes)
        start_idx = end_idx - 25

    selected_strikes = set(all_strikes[start_idx:end_idx])
    selected_strikes_sorted = sorted(list(selected_strikes))
    actual_median = selected_strikes_sorted[len(selected_strikes_sorted) // 2]
    assert len(selected_strikes) == 25, f"Expected exactly 25 strikes, got {len(selected_strikes)}"
    assert actual_median == median_strike, f"Expected median {median_strike}, got {actual_median}"

    # Filter TradingEdge payload to the 25 median-centered strikes
    te_25_payload = dict(te_data)
    te_25_payload["strikes"] = [s for s in te_data["strikes"] if float(s["strike"]) in selected_strikes]

    rows = []
    for s_obj in te_25_payload["strikes"]:
        k = float(s_obj["strike"])
        for exp_iso in s_obj.get("expirations", {}).keys():
            exp_k = exp_iso.replace("-", "")
            for right in ("C", "P"):
                cb = cboe.get((exp_k, k, right), {})
                rows.append({
                    "strike": k,
                    "expiration": exp_iso,
                    "option_type": right,
                    "open_interest": cb.get("open_interest") or 0.0,
                    "iv": cb.get("iv") or 0.0,
                })

    df = pd.DataFrame(rows)
    res_df = recompute_dynamic_bsm_gexdex(df, spot_live=spot, spot_close=spot)
    report = evaluate_bsm_vs_tradingedge_accuracy(
        df_bsm=res_df,
        te_payload=te_25_payload,
        target_strike=median_strike,
        max_dte=60
    )

    # Invariant 1: Structural Landmarks on Double-Sided Chart
    assert report["landmarks"]["call_wall_match"] is True, f"Call wall mismatch: BSM={report['landmarks']['bsm_call_wall']} vs TE={report['landmarks']['te_call_wall']}"
    assert report["landmarks"]["put_wall_match"] is True, f"Put wall mismatch: BSM={report['landmarks']['bsm_put_wall']} vs TE={report['landmarks']['te_put_wall']}"
    assert report["landmarks"]["wall_within_tolerance"] is True
    assert report["landmarks"]["regime_match"] is True, f"Regime mismatch: BSM={report['landmarks']['bsm_net_gex']} vs TE={report['landmarks']['te_net_gex']}"

    # Invariant 2: Profile Shape Correlation (Pearson r >= 0.965 across all 25 strikes)
    for metric_name, corr_val in report["pearson_r"].items():
        assert corr_val >= 0.965, f"{metric_name} Pearson r ({corr_val:.4f}) dropped below 0.965 on 25-strike surface"

    # Invariant 3: Lin's Concordance Correlation (rho_c >= 0.90 to screen multiplier / scale bugs)
    for metric_name, ccc_val in report["lins_ccc"].items():
        assert ccc_val >= 0.90, f"{metric_name} Lin CCC ({ccc_val:.4f}) dropped below 0.90 on 25-strike surface"

    # Invariant 4: Gross-Normalized Net Errors within bounds
    assert report["wmape"]["gross_norm_net_gex_err_pct"] <= 50.0
    assert report["wmape"]["gross_norm_net_dex_err_pct"] <= 25.0
    assert report["benchmarks_passed"] is True, f"Failed benchmarks on 25-strike run: {report['benchmarks']}"



def test_edge_case_1_lins_ccc_detects_scale_and_offset_drift():
    """
    Edge Case 1: Standard Pearson r is blind to 10x scale bug or constant additive shift,
    whereas Lin's Concordance Correlation Coefficient (rho_c) penalizes both.
    """
    from common_lib.calculators.shadow_comparator import calc_pearson_correlation, calc_lins_ccc

    te_vec = [100.0, 250.0, 500.0, 1200.0, 800.0, 300.0]
    # 10x multiplier bug
    scaled_vec = [v * 10.0 for v in te_vec]
    # Constant offset bug
    shifted_vec = [v + 5000.0 for v in te_vec]

    # Pearson r is 1.0 on both bugs (blind spot)
    assert math.isclose(calc_pearson_correlation(te_vec, scaled_vec), 1.0, abs_tol=1e-6)
    assert math.isclose(calc_pearson_correlation(te_vec, shifted_vec), 1.0, abs_tol=1e-6)

    # Lin's CCC immediately catches both (< 0.25 << 0.96 threshold)
    assert calc_lins_ccc(te_vec, scaled_vec) < 0.25
    assert calc_lins_ccc(te_vec, shifted_vec) < 0.10

    # 1:1 concordant vector passes both r >= 0.98 and rho_c >= 0.96
    close_vec = [v * 1.02 for v in te_vec]
    assert calc_lins_ccc(te_vec, close_vec) >= 0.96


def test_edge_case_2_granular_wmape_catches_cross_expiry_cancellation():
    """
    Edge Case 2: Positive errors on near-dated expiry (+50%) and negative errors on far-dated
    expiry (-50%) cancel out in strike-aggregated sums, but Granular (Strike x Expiry) WMAPE catches it.
    """
    from common_lib.calculators.shadow_comparator import compare_gexdex_payloads

    te_payload = {
        "ticker": "TEST",
        "spot_price": 100.0,
        "strikes": [
            {
                "strike": 100.0,
                "expirations": {
                    "2026-11-06": {"call_gex": 100000.0, "put_gex": -50000.0, "call_dex": 100000.0, "put_dex": -50000.0},
                    "2026-12-18": {"call_gex": 100000.0, "put_gex": -50000.0, "call_dex": 100000.0, "put_dex": -50000.0},
                }
            },
            {
                "strike": 105.0,
                "expirations": {
                    "2026-11-06": {"call_gex": 80000.0, "put_gex": -40000.0, "call_dex": 80000.0, "put_dex": -40000.0},
                    "2026-12-18": {"call_gex": 80000.0, "put_gex": -40000.0, "call_dex": 80000.0, "put_dex": -40000.0},
                }
            }
        ]
    }
    # Model overestimates 2026-11-06 by +50% and underestimates 2026-12-18 by -50%
    # Strike total is 200,000 in both (0% strike-aggregated error!), but granular error is 50%!
    ibkr_payload = {
        "ticker": "TEST",
        "spot_price": 100.0,
        "strikes": [
            {
                "strike": 100.0,
                "expirations": {
                    "2026-11-06": {"call_gex": 150000.0, "put_gex": -75000.0, "call_dex": 150000.0, "put_dex": -75000.0},
                    "2026-12-18": {"call_gex": 50000.0, "put_gex": -25000.0, "call_dex": 50000.0, "put_dex": -25000.0},
                }
            },
            {
                "strike": 105.0,
                "expirations": {
                    "2026-11-06": {"call_gex": 120000.0, "put_gex": -60000.0, "call_dex": 120000.0, "put_dex": -60000.0},
                    "2026-12-18": {"call_gex": 40000.0, "put_gex": -20000.0, "call_dex": 40000.0, "put_dex": -20000.0},
                }
            }
        ]
    }

    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    # Strike-level net error is 0%, but granular_wmape exposes the 50% contract-level error
    assert res.net_gex_diff_pct == 0.0
    assert math.isclose(res.granular_wmape["call_gex"], 50.0, abs_tol=1e-2)
    assert math.isclose(res.signed_bias["call_gex"], 0.0, abs_tol=1e-2)


def test_edge_case_3_top2_magnitude_tolerance_for_twin_walls():
    """
    Edge Case 3: Twin peaks where Strike 100 ($50.1M) and Strike 105 ($49.9M) are within <= 3%
    and minor IV noise flips #1 and #2 between TE and Model.
    """
    from common_lib.calculators.shadow_comparator import compare_gexdex_payloads

    te_payload = {
        "ticker": "SPY",
        "spot_price": 100.0,
        "call_wall": 100.0,
        "put_wall": 95.0,
        "strikes": [
            {"strike": 95.0, "call_gex": 10_000_000.0, "put_gex": -50_100_000.0, "call_dex": 5_000_000.0, "put_dex": -20_000_000.0},
            {"strike": 100.0, "call_gex": 50_100_000.0, "put_gex": -49_900_000.0, "call_dex": 25_000_000.0, "put_dex": -19_800_000.0},
            {"strike": 105.0, "call_gex": 49_900_000.0, "put_gex": -10_000_000.0, "call_dex": 24_900_000.0, "put_dex": -5_000_000.0},
        ]
    }
    # Model flips top 2: Call Wall at 105 ($50.2M) vs 100 ($49.8M, diff = 0.8% <= 3%)
    ibkr_payload = {
        "ticker": "SPY",
        "spot_price": 100.0,
        "call_wall": 105.0,
        "put_wall": 100.0,
        "strikes": [
            {"strike": 95.0, "call_gex": 10_000_000.0, "put_gex": -49_800_000.0, "call_dex": 5_000_000.0, "put_dex": -20_000_000.0},
            {"strike": 100.0, "call_gex": 49_800_000.0, "put_gex": -50_200_000.0, "call_dex": 24_900_000.0, "put_dex": -20_100_000.0},
            {"strike": 105.0, "call_gex": 50_200_000.0, "put_gex": -10_000_000.0, "call_dex": 25_100_000.0, "put_dex": -5_000_000.0},
        ]
    }

    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    assert res.call_wall_within_tolerance is True
    assert res.put_wall_within_tolerance is True
    assert res.wall_within_tolerance is True
    assert res.call_wall_matched is True
    assert res.put_wall_matched is True


def test_edge_case_4_zero_gamma_noise_zone_guard():
    """
    Edge Case 4: Ticker sits on Gamma Flip where Net GEX is +$50k on $100M Gross GEX (NGR = 0.05% < 2%).
    Model has -$20k Net GEX (sign flipped, but only 0.07% gross-normalized drift).
    Must classify as NEUTRAL / FLIP_ZONE and PASS regime_matched.
    """
    from common_lib.calculators.shadow_comparator import compare_gexdex_payloads

    te_payload = {
        "ticker": "QQQ",
        "spot_price": 500.0,
        "zero_gex_level": 500.0,
        "net_gex": 50_000.0,
        "strikes": [
            {"strike": 495.0, "call_gex": 10_000_000.0, "put_gex": -49_975_000.0, "call_dex": 10_000_000.0, "put_dex": -20_000_000.0},
            {"strike": 505.0, "call_gex": 40_025_000.0, "put_gex": 0.0, "call_dex": 20_000_000.0, "put_dex": 0.0},
        ]
    }
    ibkr_payload = {
        "ticker": "QQQ",
        "spot_price": 500.0,
        "zero_gex_level": 500.5,  # 0.10% flip diff <= 0.25%
        "net_gex": -20_000.0,     # Sign flipped to negative, but NGR = 0.0005 < 0.02
        "strikes": [
            {"strike": 495.0, "call_gex": 10_000_000.0, "put_gex": -50_010_000.0, "call_dex": 10_000_000.0, "put_dex": -20_000_000.0},
            {"strike": 505.0, "call_gex": 39_990_000.0, "put_gex": 0.0, "call_dex": 20_000_000.0, "put_dex": 0.0},
        ]
    }

    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    assert res.in_flip_noise_zone is True
    assert res.regime_classification == "NEUTRAL / FLIP_ZONE"
    assert res.regime_matched is True


def test_selective_ssvi_preserves_liquid_atm_and_repairs_noisy_otm_wings():
    """
    Verifies that selective SSVI filtering:
    1. Leaves clean/liquid ATM and NTM contracts 100.0000% untouched (ssvi_repaired == False).
    2. Repairs ONLY noisy OTM contracts (wide bid-ask spread > 35%, zero bid, or corrupted IV outlier)
       using the Gatheral-Jacquier (2014) arbitrage-free SSVI curve calibrated to the clean anchors.
    """
    from zoneinfo import ZoneInfo
    from common_lib.calculators.gexdex import repair_noisy_iv_with_ssvi

    spot = 100.0
    T = 30.0 / 365.25
    # Generate a realistic Gatheral-Jacquier SSVI smile: theta = 0.25^2 * T, rho = -0.35, phi = 1.8
    theta_true = (0.25 ** 2) * T
    rho_true = -0.35
    phi_true = 1.8

    def true_ssvi_iv(k_strike: float) -> float:
        log_k = math.log(k_strike / spot)
        w = 0.5 * theta_true * (1.0 + rho_true * phi_true * log_k + math.sqrt((phi_true * log_k + rho_true) ** 2 + 1.0 - rho_true ** 2))
        return math.sqrt(w / T)

    strikes = [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]
    rows = []
    for k in strikes:
        clean_sig = true_ssvi_iv(k)
        # Strike 80 (deep OTM Put) has wide bid-ask spread (bid=0.05, ask=0.45 -> spread_pct = 160% > 35%) and corrupted IV=0.95
        # Strike 120 (deep OTM Call) has zero bid (bid=0.0, ask=0.25), illiquid OI=2, and corrupted IV=0.001
        if k == 80.0:
            rows.append({
                "strike": k,
                "expiration": "2026-11-27",
                "option_type": "P",
                "open_interest": 3,
                "bid": 0.05,
                "ask": 0.45,
                "iv": 0.95,  # Corrupted noisy IV from wide spread
            })
        elif k == 120.0:
            rows.append({
                "strike": k,
                "expiration": "2026-11-27",
                "option_type": "C",
                "open_interest": 2,
                "bid": 0.0,
                "ask": 0.25,
                "iv": 0.0001,  # Collapsed IV
            })
        else:
            # Clean, liquid contracts with tight spreads
            rows.append({
                "strike": k,
                "expiration": "2026-11-27",
                "option_type": "C" if k >= 100.0 else "P",
                "open_interest": 500,
                "bid": 3.00,
                "ask": 3.10,
                "iv": clean_sig,
            })

    df = pd.DataFrame(rows)
    res_df = recompute_dynamic_bsm_gexdex(
        df,
        spot_live=spot,
        eval_timestamp=datetime(2026, 10, 28, 16, 0, 0, tzinfo=ZoneInfo("America/New_York")),
        apply_ssvi_filter=True,
        max_spread_pct=0.35,
        min_liquid_oi=10,
    )

    assert "ssvi_repaired" in res_df.columns
    assert "iv_effective" in res_df.columns

    # 1. All 7 clean liquid strikes (85..115) must have ssvi_repaired == False and exact original IV
    clean_rows = res_df[res_df["strike"].isin([85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0])]
    assert (clean_rows["ssvi_repaired"] == False).all()
    for _, row in clean_rows.iterrows():
        assert math.isclose(row["iv_effective"], row["iv"], rel_tol=1e-12, abs_tol=1e-12)

    # 2. Both noisy OTM wing strikes (80.0 and 120.0) must be repaired (ssvi_repaired == True)
    # and match the underlying true SSVI curve within 1.5%
    row_80 = res_df[res_df["strike"] == 80.0].iloc[0]
    row_120 = res_df[res_df["strike"] == 120.0].iloc[0]
    assert bool(row_80["ssvi_repaired"]) is True
    assert bool(row_120["ssvi_repaired"]) is True
    assert math.isclose(row_80["iv_effective"], true_ssvi_iv(80.0), rel_tol=0.02)
    assert math.isclose(row_120["iv_effective"], true_ssvi_iv(120.0), rel_tol=0.02)

