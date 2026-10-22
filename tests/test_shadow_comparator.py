import pytest
from common_lib.calculators.shadow_comparator import (
    calc_pearson_correlation,
    compare_gexdex_payloads,
    StrikeComparisonRow,
    ShadowComparisonResult
)


def test_calc_pearson_correlation_identical():
    x = [10.0, 20.0, 30.0, 40.0, 50.0]
    y = [10.0, 20.0, 30.0, 40.0, 50.0]
    r = calc_pearson_correlation(x, y)
    assert abs(r - 1.0) < 1e-6


def test_calc_pearson_correlation_inversely_correlated():
    x = [10.0, 20.0, 30.0, 40.0, 50.0]
    y = [50.0, 40.0, 30.0, 20.0, 10.0]
    r = calc_pearson_correlation(x, y)
    assert abs(r - (-1.0)) < 1e-6


def test_calc_pearson_correlation_flat_or_short():
    assert calc_pearson_correlation([10.0], [10.0]) == 0.0
    assert calc_pearson_correlation([10.0, 10.0, 10.0], [10.0, 10.0, 10.0]) == 1.0
    assert calc_pearson_correlation([10.0, 20.0, 30.0], [5.0, 5.0, 5.0]) == 0.0


def test_compare_gexdex_payloads_perfect_match():
    te_payload = {
        "ticker": "SPY",
        "spot_price": 500.0,
        "net_gex": 125000000.0,
        "call_wall": 510.0,
        "put_wall": 490.0,
        "zero_gex_level": 498.5,
        "strikes": [
            {"strike": 490.0, "call_gex": 100000.0, "put_gex": 800000.0},
            {"strike": 500.0, "call_gex": 500000.0, "put_gex": 400000.0},
            {"strike": 510.0, "call_gex": 1200000.0, "put_gex": 50000.0},
        ]
    }
    ibkr_payload = {
        "ticker": "SPY",
        "spot_price": 500.0,
        "net_gex": 125000000.0,
        "call_wall": 510.0,
        "put_wall": 490.0,
        "zero_gex_level": 498.5,
        "strikes": [
            {"strike": 490.0, "call_gex": 100000.0, "put_gex": 800000.0},
            {"strike": 500.0, "call_gex": 500000.0, "put_gex": 400000.0},
            {"strike": 510.0, "call_gex": 1200000.0, "put_gex": 50000.0},
        ]
    }

    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    assert res.ticker == "SPY"
    assert res.net_gex_diff_pct == 0.0
    assert res.gex_pearson_r == 1.0
    assert res.call_wall_matched is True
    assert res.put_wall_matched is True
    assert res.regime_matched is True
    assert res.total_aligned_strikes == 3
    assert len(res.strike_rows) == 3


def test_compare_gexdex_payloads_slight_variance():
    te_payload = {
        "ticker": "QQQ",
        "spot_price": 450.0,
        "net_gex": 50000000.0,
        "call_wall": 460.0,
        "put_wall": 440.0,
        "zero_gex_level": 448.0,
        "strikes": [
            {"strike": 440.0, "call_gex": 200000.0, "put_gex": 1200000.0},
            {"strike": 450.0, "call_gex": 600000.0, "put_gex": 500000.0},
            {"strike": 460.0, "call_gex": 1500000.0, "put_gex": 100000.0},
        ]
    }
    ibkr_payload = {
        "ticker": "QQQ",
        "spot_price": 450.0,
        "net_gex": 51000000.0,
        "call_wall": 460.0,
        "put_wall": 440.0,
        "zero_gex_level": 448.2,
        "strikes": [
            {"strike": 440.0, "call_gex": 210000.0, "put_gex": 1180000.0},
            {"strike": 450.0, "call_gex": 590000.0, "put_gex": 510000.0},
            {"strike": 460.0, "call_gex": 1490000.0, "put_gex": 95000.0},
        ]
    }

    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    assert res.gex_pearson_r > 0.99
    assert res.call_wall_matched is True
    assert res.put_wall_matched is True
    assert res.regime_matched is True
    assert res.zero_flip_diff_pct < 0.1
    assert "strike_rows" in res.to_dict()
