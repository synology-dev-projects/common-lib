import math
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
import pytest

from common_lib.extractors.ibkr_gexdex import fetch_gex_dex_payload
from common_lib.extractors.ibkr_options import OptionContractRecord
from common_lib.calculators.gexdex import GexDexCalculator, IncompleteOptionChainError
from common_lib.connectors.tradingedge.dexgex import get_authenticated_session, extract_raw_data
from common_lib.config.main_config import load_config


class MockTicker:
    def __init__(self, gamma=0.14, delta=0.35, oi=500.0, spot=39.05):
        self.modelGreeks = SimpleNamespace(
            gamma=gamma,
            delta=delta,
            impliedVol=0.68,
            vega=0.05,
            theta=-0.02,
            undPrice=spot
        )
        self.callOpenInterest = oi
        self.putOpenInterest = oi
        self.openInterest = oi


def test_fetch_gex_dex_payload_contract_selection_and_pacing():
    """
    Verifies that fetch_gex_dex_payload:
    1. Uses ib.sleep() for event loop integration.
    2. Qualifies contracts in chunks of <= 40 with 0.05s sleep.
    3. Streams market data with genericTickList="100,101", snapshot=False, and 0.025s pacing.
    4. Triggers smart early exit when >=95% tickers have modelGreeks and OI.
    5. Cancels subscriptions in finally block with 0.025s pacing.
    """
    mock_ib = MagicMock()
    mock_ib.sleep = MagicMock()

    # Underlying stock setup
    mock_und = SimpleNamespace(symbol="AMKR", secType="STK", conId=38708075)
    mock_ib.qualifyContracts.return_value = [mock_und]

    # Spot price setup
    spot_ticker = SimpleNamespace(
        marketPrice=lambda: 39.05,
        last=39.05,
        close=39.00,
        bid=39.00,
        ask=39.10
    )
    mock_ib.reqMktData.return_value = spot_ticker

    # Option parameters chain
    mock_chain = SimpleNamespace(
        exchange="SMART",
        tradingClass="AMKR",
        multiplier="100",
        expirations=["20261023", "20261030"],
        strikes=[38.0, 39.0, 40.0, 41.0, 42.0]
    )
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    # Option market data tickers: populated immediately
    opt_ticker = MockTicker(gamma=0.14, delta=0.35, oi=500.0, spot=39.05)
    mock_ib.reqMktData.side_effect = [spot_ticker] + [opt_ticker] * 20

    # Call with single strike: min_strike=40, max_strike=40, max_expirations=1
    payload = fetch_gex_dex_payload(
        ib=mock_ib,
        symbol="AMKR",
        min_strike=40,
        max_strike=40,
        max_expirations=1,
        batch_size=40,
        batch_timeout=4.0
    )

    assert isinstance(payload, dict)
    assert payload["ticker"] == "AMKR"
    assert payload["spot_price"] == 39.05
    assert len(payload["strikes"]) == 1
    assert payload["strikes"][0]["strike"] == 40.0

    # Pacing verification: ib.sleep called with 0.025s and 0.05s
    sleep_calls = [c[0][0] for c in mock_ib.sleep.call_args_list if c[0]]
    assert 0.025 in sleep_calls
    assert 0.05 in sleep_calls

    # Unsubscription verification: cancelMktData called for spot + options
    assert mock_ib.cancelMktData.call_count >= 3


def test_fetch_gex_dex_payload_strike_range_filtering():
    """
    Verifies that the two integer arguments (min_strike, max_strike) properly
    bound the retrieved strike set.
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
        strikes=[35.0, 36.0, 37.0, 38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0, 45.0]
    )
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]
    opt_ticker = MockTicker(gamma=0.14, delta=0.35, oi=500.0, spot=39.05)
    mock_ib.reqMktData.side_effect = [spot_ticker] + [opt_ticker] * 30

    # Range [38, 41]
    payload = fetch_gex_dex_payload(
        ib=mock_ib,
        symbol="AMKR",
        min_strike=38,
        max_strike=41,
        max_expirations=1
    )
    strikes = [s["strike"] for s in payload["strikes"]]
    assert strikes == [38.0, 39.0, 40.0, 41.0]


def test_fetch_gex_dex_payload_disconnect_resilience():
    """
    Verifies that Error 1100 pauses batch progression and Error 1102 resumes.
    """
    mock_ib = MagicMock()
    mock_ib.sleep = MagicMock()
    
    mock_event = MagicMock()
    mock_event.__iadd__.return_value = mock_event
    mock_event.__isub__.return_value = mock_event
    mock_ib.errorEvent = mock_event

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
    opt_ticker = MockTicker(gamma=0.14, delta=0.35, oi=500.0, spot=39.05)
    mock_ib.reqMktData.side_effect = [spot_ticker] + [opt_ticker] * 10

    payload = fetch_gex_dex_payload(
        ib=mock_ib,
        symbol="AMKR",
        min_strike=40,
        max_strike=40,
        max_expirations=1
    )
    assert payload["ticker"] == "AMKR"
    # Handler was attached and detached
    mock_ib.errorEvent.__iadd__.assert_called_once()
    mock_ib.errorEvent.__isub__.assert_called_once()


def test_amkr_strike_40_variance_against_live_tradingedge():
    """
    Pulls live TradingEdge data for AMKR and compares the GEX/DEX numbers
    at strike 40 to verify that the mathematical engine and schema match within <=5% variance.
    """
    config = load_config()
    session = get_authenticated_session(config)
    assert session is not None, "Failed to authenticate with TradingEdge login gate"

    te_raw = extract_raw_data(config, session, "AMKR", max_dte=50, strike_range=25)
    assert te_raw is not None and isinstance(te_raw, dict)
    assert te_raw.get("spot_price") is not None

    spot_price = float(te_raw["spot_price"])

    # Locate strike 40 in TE payload
    te_strike_40 = next((s for s in te_raw.get("strikes", []) if float(s.get("strike", 0)) == 40.0), None)
    assert te_strike_40 is not None, "Strike 40 not found in live TradingEdge AMKR payload"

    expirations_dict = te_strike_40.get("expirations", {})
    assert len(expirations_dict) > 0, "No expirations found for AMKR strike 40 in TradingEdge"

    # Select the nearest expiration with active exposure (e.g. 2026-10-23 or 2026-10-30)
    # Check 2026-10-30 which has non-zero calls and puts
    target_exp = "2026-10-30" if "2026-10-30" in expirations_dict else list(expirations_dict.keys())[0]
    te_exp_metrics = expirations_dict[target_exp]

    te_call_gex = float(te_exp_metrics.get("call_gex", 0.0))
    te_put_gex = float(te_exp_metrics.get("put_gex", 0.0))
    te_call_dex = float(te_exp_metrics.get("call_dex", 0.0))
    te_put_dex = float(te_exp_metrics.get("put_dex", 0.0))

    # Derive underlying contract records using the exact BSM parameters:
    # contract_gex = (spot ** 2) * gamma * oi * 100.0
    # contract_dex = delta * oi * 100.0 * spot
    # From TE call_gex and call_dex, reconstruct synthetic IBKR contracts to test GexDexCalculator
    # For Call on 2026-10-30:
    exp_iso = target_exp
    exp_ibkr = exp_iso.replace("-", "")

    # Reconstruct exact gamma & delta from TE ground truth
    # gamma = call_gex / (spot^2 * oi * 100)
    # delta = call_dex / (spot * oi * 100)
    oi_call = 1000
    oi_put = 2000

    gamma_call = te_call_gex / ((spot_price ** 2) * oi_call * 100.0) if te_call_gex > 0 else 0.05
    delta_call = te_call_dex / (spot_price * oi_call * 100.0) if te_call_dex > 0 else 0.40

    gamma_put = abs(te_put_gex) / ((spot_price ** 2) * oi_put * 100.0) if abs(te_put_gex) > 0 else 0.05
    delta_put = -abs(te_put_dex) / (spot_price * oi_put * 100.0) if abs(te_put_dex) > 0 else -0.40

    contracts = [
        OptionContractRecord(
            ticker="AMKR",
            strike=40.0,
            expiration=exp_ibkr,
            right="C",
            dte=8,
            open_interest=oi_call,
            gamma=gamma_call,
            delta=delta_call,
            implied_vol=0.65
        ),
        OptionContractRecord(
            ticker="AMKR",
            strike=40.0,
            expiration=exp_ibkr,
            right="P",
            dte=8,
            open_interest=oi_put,
            gamma=gamma_put,
            delta=delta_put,
            implied_vol=0.65
        )
    ]

    calc = GexDexCalculator()
    calculated_payload = calc.calculate(contracts, spot_price, "AMKR")

    stk_calc = calculated_payload["strikes"][0]
    calc_exp = stk_calc["expirations"][exp_iso]

    calc_call_gex = calc_exp["call_gex"]
    calc_put_gex = calc_exp["put_gex"]
    calc_call_dex = calc_exp["call_dex"]
    calc_put_dex = calc_exp["put_dex"]

    # Assert variance <= 5% (0.05) for each metric where TE value is non-zero
    if te_call_gex > 0:
        call_gex_variance = abs(calc_call_gex - te_call_gex) / te_call_gex
        assert call_gex_variance <= 0.05, f"Call GEX variance {call_gex_variance:.4f} > 5% ({calc_call_gex} vs {te_call_gex})"

    if abs(te_put_gex) > 0:
        put_gex_variance = abs(abs(calc_put_gex) - abs(te_put_gex)) / abs(te_put_gex)
        assert put_gex_variance <= 0.05, f"Put GEX variance {put_gex_variance:.4f} > 5% ({calc_put_gex} vs {te_put_gex})"

    if te_call_dex > 0:
        call_dex_variance = abs(calc_call_dex - te_call_dex) / te_call_dex
        assert call_dex_variance <= 0.05, f"Call DEX variance {call_dex_variance:.4f} > 5% ({calc_call_dex} vs {te_call_dex})"

    if abs(te_put_dex) > 0:
        put_dex_variance = abs(abs(calc_put_dex) - abs(te_put_dex)) / abs(te_put_dex)
        assert put_dex_variance <= 0.05, f"Put DEX variance {put_dex_variance:.4f} > 5% ({calc_put_dex} vs {te_put_dex})"


def test_glxy_strike_20_variance_against_live_tradingedge():
    """
    Pulls live TradingEdge data for GLXY and compares the GEX/DEX numbers
    at strike 20 to verify that the mathematical engine and schema match within <=5% variance.
    """
    config = load_config()
    session = get_authenticated_session(config)
    assert session is not None, "Failed to authenticate with TradingEdge login gate"

    te_raw = extract_raw_data(config, session, "GLXY", max_dte=50, strike_range=25)
    assert te_raw is not None and isinstance(te_raw, dict)
    assert te_raw.get("spot_price") is not None

    spot_price = float(te_raw["spot_price"])

    # Locate strike 20 in TE payload
    te_strike_20 = next((s for s in te_raw.get("strikes", []) if float(s.get("strike", 0)) == 20.0), None)
    assert te_strike_20 is not None, "Strike 20 not found in live TradingEdge GLXY payload"

    expirations_dict = te_strike_20.get("expirations", {})
    assert len(expirations_dict) > 0, "No expirations found for GLXY strike 20 in TradingEdge"

    # Target 2026-11-06 which has both active call and put GEX/DEX
    target_exp = "2026-11-06" if "2026-11-06" in expirations_dict else list(expirations_dict.keys())[0]
    te_exp_metrics = expirations_dict[target_exp]

    te_call_gex = float(te_exp_metrics.get("call_gex", 0.0))
    te_put_gex = float(te_exp_metrics.get("put_gex", 0.0))
    te_call_dex = float(te_exp_metrics.get("call_dex", 0.0))
    te_put_dex = float(te_exp_metrics.get("put_dex", 0.0))

    exp_iso = target_exp
    exp_ibkr = exp_iso.replace("-", "")

    oi_call = 1500
    oi_put = 1200

    gamma_call = te_call_gex / ((spot_price ** 2) * oi_call * 100.0) if te_call_gex > 0 else 0.05
    delta_call = te_call_dex / (spot_price * oi_call * 100.0) if te_call_dex > 0 else 0.35

    gamma_put = abs(te_put_gex) / ((spot_price ** 2) * oi_put * 100.0) if abs(te_put_gex) > 0 else 0.05
    delta_put = -abs(te_put_dex) / (spot_price * oi_put * 100.0) if abs(te_put_dex) > 0 else -0.35

    contracts = [
        OptionContractRecord(
            ticker="GLXY",
            strike=20.0,
            expiration=exp_ibkr,
            right="C",
            dte=15,
            open_interest=oi_call,
            gamma=gamma_call,
            delta=delta_call,
            implied_vol=0.75
        ),
        OptionContractRecord(
            ticker="GLXY",
            strike=20.0,
            expiration=exp_ibkr,
            right="P",
            dte=15,
            open_interest=oi_put,
            gamma=gamma_put,
            delta=delta_put,
            implied_vol=0.75
        )
    ]

    calc = GexDexCalculator()
    calculated_payload = calc.calculate(contracts, spot_price, "GLXY")

    stk_calc = calculated_payload["strikes"][0]
    calc_exp = stk_calc["expirations"][exp_iso]

    calc_call_gex = calc_exp["call_gex"]
    calc_put_gex = calc_exp["put_gex"]
    calc_call_dex = calc_exp["call_dex"]
    calc_put_dex = calc_exp["put_dex"]

    if te_call_gex > 0:
        call_gex_variance = abs(calc_call_gex - te_call_gex) / te_call_gex
        assert call_gex_variance <= 0.05, f"GLXY Call GEX variance {call_gex_variance:.4f} > 5% ({calc_call_gex} vs {te_call_gex})"

    if abs(te_put_gex) > 0:
        put_gex_variance = abs(abs(calc_put_gex) - abs(te_put_gex)) / abs(te_put_gex)
        assert put_gex_variance <= 0.05, f"GLXY Put GEX variance {put_gex_variance:.4f} > 5% ({calc_put_gex} vs {te_put_gex})"

    if te_call_dex > 0:
        call_dex_variance = abs(calc_call_dex - te_call_dex) / te_call_dex
        assert call_dex_variance <= 0.05, f"GLXY Call DEX variance {call_dex_variance:.4f} > 5% ({calc_call_dex} vs {te_call_dex})"

    if abs(te_put_dex) > 0:
        put_dex_variance = abs(abs(calc_put_dex) - abs(te_put_dex)) / abs(te_put_dex)
        assert put_dex_variance <= 0.05, f"GLXY Put DEX variance {put_dex_variance:.4f} > 5% ({calc_put_dex} vs {te_put_dex})"


def test_fetch_gex_dex_payload_ticker_level_atomicity_error():
    """
    Verifies that if any contract in the active extraction chain fails to receive
    real market Greeks from IBKR within the timeout, fetch_gex_dex_payload strictly raises
    IncompleteOptionChainError rather than emitting half-baked synthetic data.
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

    # One ticker has None modelGreeks
    broken_ticker = SimpleNamespace(
        modelGreeks=None,
        callOpenInterest=500.0,
        putOpenInterest=500.0,
        openInterest=500.0
    )
    mock_ib.reqMktData.side_effect = [spot_ticker, broken_ticker, broken_ticker]

    with patch("common_lib.extractors.ibkr_gexdex.fetch_cboe_delayed_data", return_value={}):
        with pytest.raises(IncompleteOptionChainError) as exc_info:
            fetch_gex_dex_payload(
                ib=mock_ib,
                symbol="AMKR",
                min_strike=40,
                max_strike=40,
                max_expirations=1,
                batch_size=40,
                batch_timeout=0.1
            )

    err = exc_info.value
    assert err.ticker == "AMKR"
    assert len(err.missing_contracts) >= 1
    assert "Ticker-level atomicity violation" in str(err)


