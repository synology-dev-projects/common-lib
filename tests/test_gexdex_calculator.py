from datetime import date
import math
import pytest
import pandas as pd

from common_lib.extractors.ibkr_options import OptionContractRecord
from common_lib.calculators.gexdex import (
    GexDexCalculator,
    calculate_gexdex_distribution,
    bsm_delta_gamma
)
from common_lib.connectors.tradingedge.dexgex import (
    convert_raw_to_df,
    generate_gexdex_chart
)


def test_bsm_delta_gamma_solver():
    spot = 500.0
    strike = 500.0
    tau = 30.0 / 365.25
    vol = 0.20

    call_d, put_d, gamma = bsm_delta_gamma(spot, strike, tau, vol)
    assert 0.50 < call_d < 0.60
    assert -0.50 < put_d < -0.40
    assert math.isclose(call_d - put_d, math.exp(-0.013 * tau), rel_tol=1e-4)
    assert gamma > 0.0


def test_calculate_gexdex_distribution_synthetic_chain():
    # Construct 6 contracts: 480, 500, 520 (Calls & Puts)
    contracts = [
        OptionContractRecord(
            ticker="SPY", strike=480.0, expiration="20261120", right="C", dte=30,
            open_interest=5000, implied_vol=0.22, delta=0.75, gamma=0.012
        ),
        OptionContractRecord(
            ticker="SPY", strike=480.0, expiration="20261120", right="P", dte=30,
            open_interest=15000, implied_vol=0.22, delta=-0.25, gamma=0.012
        ),
        OptionContractRecord(
            ticker="SPY", strike=500.0, expiration="20261120", right="C", dte=30,
            open_interest=20000, implied_vol=0.20, delta=0.52, gamma=0.018
        ),
        OptionContractRecord(
            ticker="SPY", strike=500.0, expiration="20261120", right="P", dte=30,
            open_interest=8000, implied_vol=0.20, delta=-0.48, gamma=0.018
        ),
        OptionContractRecord(
            ticker="SPY", strike=520.0, expiration="20261120", right="C", dte=30,
            open_interest=25000, implied_vol=0.18, delta=0.28, gamma=0.010
        ),
        OptionContractRecord(
            ticker="SPY", strike=520.0, expiration="20261120", right="P", dte=30,
            open_interest=2000, implied_vol=0.18, delta=-0.72, gamma=0.010
        ),
    ]

    spot_price = 500.0
    payload = calculate_gexdex_distribution(
        contracts=contracts,
        spot_price=spot_price,
        ticker="SPY",
        target_date=date(2026, 10, 22)
    )

    assert payload["ticker"] == "SPY"
    assert payload["spot_price"] == 500.0
    assert payload["call_wall"] == 500.0  # Strike 500 has highest call gamma * OI (0.018 * 20000 = 360 vs 0.010 * 25000 = 250)
    assert payload["put_wall"] == 480.0   # Strike 480 has highest put gamma * OI (0.012 * 15000 = 180)

    # Invariants
    assert payload["total_call_gex"] > 0
    assert payload["total_put_gex"] > 0
    assert math.isclose(payload["net_gex"], payload["total_call_gex"] - payload["total_put_gex"], abs_tol=0.05)
    assert math.isclose(payload["net_dex"], payload["total_call_dex"] - payload["total_put_dex"], abs_tol=0.05)
    assert payload["gamma_regime"] in ("Positive Gamma (+GEX)", "Negative Gamma (-GEX)")

    # Expirations formatted as YYYY-MM-DD
    assert len(payload["strikes"]) == 3
    for s in payload["strikes"]:
        assert "2026-11-20" in s["expirations"]


def test_calculate_gexdex_distribution_missing_greeks_triggers_bsm_fallback():
    # Contracts with None Greeks & IV
    contracts = [
        OptionContractRecord(
            ticker="QQQ", strike=450.0, expiration="20261115", right="C", dte=24,
            open_interest=1000, implied_vol=None, delta=None, gamma=None
        ),
        OptionContractRecord(
            ticker="QQQ", strike=450.0, expiration="20261115", right="P", dte=24,
            open_interest=1000, implied_vol=None, delta=None, gamma=None
        )
    ]

    payload = calculate_gexdex_distribution(
        contracts=contracts,
        spot_price=450.0,
        ticker="QQQ"
    )

    assert payload["ticker"] == "QQQ"
    assert payload["total_call_gex"] > 0.0
    assert payload["total_put_gex"] > 0.0
    assert payload["total_call_dex"] > 0.0
    assert payload["total_put_dex"] > 0.0


def test_output_is_fully_compatible_with_convert_raw_to_df_and_chart():
    contracts = [
        OptionContractRecord(
            ticker="NVDA", strike=120.0, expiration="20261101", right="C", dte=10,
            open_interest=10000, implied_vol=0.35, delta=0.55, gamma=0.025
        ),
        OptionContractRecord(
            ticker="NVDA", strike=120.0, expiration="20261101", right="P", dte=10,
            open_interest=5000, implied_vol=0.35, delta=-0.45, gamma=0.025
        ),
        OptionContractRecord(
            ticker="NVDA", strike=130.0, expiration="20261101", right="C", dte=10,
            open_interest=12000, implied_vol=0.32, delta=0.30, gamma=0.020
        ),
        OptionContractRecord(
            ticker="NVDA", strike=130.0, expiration="20261101", right="P", dte=10,
            open_interest=2000, implied_vol=0.32, delta=-0.70, gamma=0.020
        )
    ]

    payload = calculate_gexdex_distribution(
        contracts=contracts,
        spot_price=122.50,
        ticker="NVDA"
    )

    # 1. Verify convert_raw_to_df accepts payload cleanly
    df = convert_raw_to_df(payload)
    assert df is not None
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "strike" in df.columns
    assert "exp_call_gex" in df.columns
    assert "exp_put_gex" in df.columns
    assert "spot_price" in df.columns
    assert df["spot_price"].iloc[0] == 122.50

    # 2. Verify generate_gexdex_chart renders valid PNG image bytes
    img_bytes = generate_gexdex_chart(df)
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 500
    assert img_bytes.startswith(b"\x89PNG\r\n\x1a\n")
