from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from common_lib.connectors.cboe_oi import fetch_cboe_open_interest, fetch_cboe_delayed_data
from common_lib.extractors.ibkr_gexdex import fetch_gex_dex_payload


def test_reproduce_cboe_oi_integration_and_snapshot_retry():
    """
    Verifies that:
    1. CBOE OCC Open Interest feed is queried and merged into option contracts when available.
    """
    mock_ib = MagicMock()
    mock_ib.sleep = MagicMock()

    mock_und = SimpleNamespace(symbol="AMKR", secType="STK", conId=38708075)
    mock_ib.qualifyContracts.return_value = [mock_und]
    spot_ticker = SimpleNamespace(marketPrice=lambda: 39.05)

    mock_chain = SimpleNamespace(
        exchange="SMART",
        tradingClass="AMKR",
        multiplier="100",
        expirations=["20261030"],
        strikes=[40.0]
    )
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    # Mock streaming ticker where IBKR reports decayed OI of 22 instead of official OCC 86
    streaming_ticker = SimpleNamespace(
        modelGreeks=SimpleNamespace(gamma=0.0628, delta=0.4915, impliedVol=0.68, vega=0.05, theta=-0.02, undPrice=39.05),
        callOpenInterest=22.0,  # Decayed after-hours IBKR tick
        putOpenInterest=254.0,
        openInterest=float("nan")
    )
    mock_ib.reqMktData.side_effect = [spot_ticker, streaming_ticker, streaming_ticker]

    # Mock CBOE returning official OCC open interest (86 for Call)
    cboe_mock_map = {
        ("20261030", 40.0, "C"): 86.0,
        ("20261030", 40.0, "P"): 251.0
    }

    with patch("common_lib.extractors.ibkr_gexdex.fetch_cboe_open_interest", return_value=cboe_mock_map):
        payload = fetch_gex_dex_payload(
            ib=mock_ib,
            symbol="AMKR",
            min_strike=40,
            max_strike=40,
            max_expirations=1
        )

    stk40 = payload["strikes"][0]
    m = stk40["expirations"]["2026-10-30"]

    # If CBOE OI (86) was properly used instead of decayed IBKR OI (22):
    # Call GEX with 86 OI at spot 39.05 & gamma 0.0628 should be > $700,000 (not ~$214,000)
    assert m["call_gex"] > 700000.0, f"Expected Call GEX > $700k using CBOE OI 86, got {m['call_gex']}"


def test_reproduce_cboe_greek_fallback_when_ibkr_greeks_missing():
    """
    Verifies that when IBKR after-hours market data is frozen/missing modelGreeks,
    official CBOE settlement Greeks (gamma, delta, iv) are used as fallback,
    preventing IncompleteOptionChainError while preserving strict Zero-Synthetic BSM.
    """
    mock_ib = MagicMock()
    mock_ib.sleep = MagicMock()

    mock_und = SimpleNamespace(symbol="AMKR", secType="STK", conId=38708075)
    mock_ib.qualifyContracts.return_value = [mock_und]
    spot_ticker = SimpleNamespace(marketPrice=lambda: 39.05)

    mock_chain = SimpleNamespace(
        exchange="SMART",
        tradingClass="AMKR",
        multiplier="100",
        expirations=["20261120"],
        strikes=[40.0]
    )
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    # IBKR streaming ticker has NO modelGreeks (e.g. after-hours illiquid contract)
    missing_greeks_ticker = SimpleNamespace(
        modelGreeks=None,
        callOpenInterest=None,
        putOpenInterest=None,
        openInterest=None
    )
    mock_ib.reqMktData.side_effect = [spot_ticker, missing_greeks_ticker, missing_greeks_ticker]

    # CBOE official exchange delayed quotes provides the settlement Greeks
    cboe_delayed_map = {
        ("20261120", 40.0, "C"): {
            "open_interest": 92.0,
            "gamma": 0.0456,
            "delta": 0.5239,
            "iv": 0.8107,
            "vega": 0.0435,
            "theta": -0.0625,
            "bid": 3.6,
            "ask": 4.0,
            "volume": 4.0
        },
        ("20261120", 40.0, "P"): {
            "open_interest": 636.0,
            "gamma": 0.0459,
            "delta": -0.4777,
            "iv": 0.8127,
            "vega": 0.0435,
            "theta": -0.0625,
            "bid": 3.6,
            "ask": 4.0,
            "volume": 4.0
        }
    }

    with patch("common_lib.extractors.ibkr_gexdex.fetch_cboe_delayed_data", return_value=cboe_delayed_map):
        payload = fetch_gex_dex_payload(
            ib=mock_ib,
            symbol="AMKR",
            min_strike=40,
            max_strike=40,
            max_expirations=1
        )

    stk40 = payload["strikes"][0]
    m = stk40["expirations"]["2026-11-20"]

    # Verify that Greeks were resolved from CBOE settlement data
    assert m["call_gex"] > 500000.0, f"Expected Call GEX > $500k, got {m['call_gex']}"
    assert m["put_gex"] < -3000000.0, f"Expected Put GEX < -$3M, got {m['put_gex']}"
    assert m["call_dex"] > 100000.0, f"Expected Call DEX > $100k, got {m['call_dex']}"
    assert m["put_dex"] < -1000000.0, f"Expected Put DEX < -$1M, got {m['put_dex']}"
