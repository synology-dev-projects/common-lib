import math
import pytest
from scripts.diagnose_te_formulas import (
    norm_cdf,
    norm_pdf,
    bsm_greeks,
    calc_dollar_gex_per_1pct,
    calc_share_gex_dollar,
    calc_dollar_dex,
    evaluate_tradingedge_payload,
    DiagnosticReport
)


def test_norm_cdf_and_pdf_accuracy():
    # Symmetry and benchmark values
    assert math.isclose(norm_cdf(0.0), 0.5, abs_tol=1e-7)
    assert math.isclose(norm_cdf(1.959964), 0.975, abs_tol=1e-4)
    assert math.isclose(norm_cdf(-1.959964), 0.025, abs_tol=1e-4)
    # PDF peak at 0
    assert math.isclose(norm_pdf(0.0), 1.0 / math.sqrt(2.0 * math.pi), abs_tol=1e-7)


def test_bsm_greeks_atm_properties():
    spot = 100.0
    strike = 100.0
    tau = 0.25  # 3 months
    vol = 0.20
    r = 0.05
    q = 0.0

    greeks = bsm_greeks(spot, strike, tau, vol, r, q)

    call_delta = greeks["call_delta"]
    put_delta = greeks["put_delta"]
    gamma = greeks["gamma"]

    # Invariants
    assert 0.5 < call_delta < 0.6  # Drift due to r > 0
    assert -0.5 < put_delta < -0.4
    assert math.isclose(call_delta - put_delta, 1.0, abs_tol=1e-5)
    assert gamma > 0.0


def test_gex_and_dex_scaling_formulas():
    gamma = 0.02
    delta = 0.50
    spot = 500.0
    oi = 1000.0

    dollar_gex = calc_dollar_gex_per_1pct(gamma, spot, oi)
    # 0.01 * 500^2 * 0.02 * 1000 * 100 = 0.01 * 250,000 * 0.02 * 100,000 = 5,000,000
    assert math.isclose(dollar_gex, 5_000_000.0, rel_tol=1e-6)

    dollar_dex = calc_dollar_dex(delta, spot, oi)
    # 0.50 * 1000 * 100 * 500 = 25,000,000
    assert math.isclose(dollar_dex, 25_000_000.0, rel_tol=1e-6)


def test_evaluate_tradingedge_payload_full_consistency():
    # Construct complete consistent payload
    payload = {
        "ticker": "SPY",
        "spot_price": 500.0,
        "call_wall": 500.0,
        "put_wall": 480.0,
        "zero_gex_level": 500.0,
        "net_gex": 20_000_000.0,
        "net_dex": 10_000_000.0,
        "call_put_ratio": 1.4,
        "strikes": [
            {"strike": 480.0, "call_gex": 5_000_000.0, "put_gex": 30_000_000.0, "call_dex": 2_000_000.0, "put_dex": 12_000_000.0},
            {"strike": 490.0, "call_gex": 15_000_000.0, "put_gex": 20_000_000.0, "call_dex": 8_000_000.0, "put_dex": 18_000_000.0},
            {"strike": 500.0, "call_gex": 25_000_000.0, "put_gex": 0.0, "call_dex": 15_000_000.0, "put_dex": 0.0},
            {"strike": 510.0, "call_gex": 20_000_000.0, "put_gex": 0.0, "call_dex": 10_000_000.0, "put_dex": 0.0},
            {"strike": 520.0, "call_gex": 5_000_000.0, "put_gex": 0.0, "call_dex": 5_000_000.0, "put_dex": 0.0}
        ]
    }
    # Total call gex = 5 + 15 + 25 + 20 + 5 = 70M
    # Total put gex = 30 + 20 = 50M
    # Net GEX = 70M - 50M = 20M (matches payload)
    # Total call dex = 2 + 8 + 15 + 10 + 5 = 40M
    # Total put dex = 12 + 18 = 30M
    # Net DEX = 40M - 30M = 10M (matches payload)

    report = evaluate_tradingedge_payload(payload)

    assert report.ticker == "SPY"
    assert report.net_gex_conservation is True
    assert report.net_dex_conservation is True
    assert report.call_wall_max_strike == 500.0
    assert report.put_wall_max_strike == 480.0
    assert report.call_put_ratio_gex == 1.4
