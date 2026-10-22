from datetime import date, datetime
from unittest.mock import MagicMock, call
import pytest
import math

from common_lib.extractors.ibkr_options import (
    OptionContractRecord,
    OptionsChainExtractionResult,
    IBKROptionsExtractor,
    extract_options_chain_snapshot
)


def test_option_contract_record_to_dict():
    rec = OptionContractRecord(
        ticker="SPY",
        strike=500.0,
        expiration="20261120",
        right="C",
        dte=30,
        bid=10.5,
        ask=10.7,
        last=10.6,
        volume=1200,
        open_interest=5000,
        implied_vol=0.185,
        delta=0.52,
        gamma=0.015,
        vega=0.25,
        theta=-0.08,
        und_price=501.2
    )
    d = rec.to_dict()
    assert d["ticker"] == "SPY"
    assert d["strike"] == 500.0
    assert d["delta"] == 0.52
    assert d["gamma"] == 0.015
    assert d["open_interest"] == 5000


def test_qualify_underlying_index_vs_stock():
    mock_ib = MagicMock()
    mock_ib.qualifyContracts.side_effect = lambda c: [c]
    extractor = IBKROptionsExtractor(ib=mock_ib)

    # Index
    spx_contract = extractor.qualify_underlying("SPX")
    assert spx_contract.symbol == "SPX"
    assert spx_contract.secType == "IND"
    assert spx_contract.exchange == "CBOE"

    # Stock / ETF
    spy_contract = extractor.qualify_underlying("SPY")
    assert spy_contract.symbol == "SPY"
    assert spy_contract.secType == "STK"
    assert spy_contract.exchange == "SMART"


def test_fetch_spot_price_resolves_and_cancels():
    mock_ib = MagicMock()
    mock_ticker = MagicMock()
    mock_ticker.marketPrice.return_value = 585.50
    mock_ib.reqMktData.return_value = mock_ticker

    extractor = IBKROptionsExtractor(ib=mock_ib)
    contract = MagicMock()
    price = extractor.fetch_spot_price(contract, timeout=1.0)

    assert price == 585.50
    mock_ib.reqMktData.assert_called_once_with(contract, "", False, False)
    mock_ib.cancelMktData.assert_called_once_with(contract)


def test_select_chains_and_strikes_dte_and_range_filtering():
    mock_ib = MagicMock()
    mock_chain = MagicMock()
    mock_chain.exchange = "SMART"
    mock_chain.multiplier = "100"
    # Today is set to 2026-10-22
    # 20261025 -> 3 DTE (valid)
    # 20261120 -> 29 DTE (valid)
    # 20270115 -> 85 DTE (> 50 max_dte -> filtered out)
    # 20261010 -> -12 DTE (< 0 -> filtered out)
    mock_chain.expirations = ["20261025", "20261120", "20270115", "20261010"]
    # Spot is 100.0, strike_range_pct is 0.25 (75.0 to 125.0)
    # 50.0 -> filtered out (< 75)
    # 80.0, 100.0, 120.0 -> valid
    # 150.0 -> filtered out (> 125)
    mock_chain.strikes = [50.0, 80.0, 100.0, 120.0, 150.0]

    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    extractor = IBKROptionsExtractor(ib=mock_ib)
    underlying = MagicMock(symbol="XYZ", secType="STK", conId=123)

    specs = extractor.select_chains_and_strikes(
        underlying,
        spot_price=100.0,
        max_dte=50,
        strike_range_pct=0.25,
        target_date=date(2026, 10, 22)
    )

    # 2 valid expirations * 3 valid strikes = 6 specs
    assert len(specs) == 6
    expirations_present = {s["expiration"] for s in specs}
    assert expirations_present == {"20261025", "20261120"}
    strikes_present = {s["strike"] for s in specs}
    assert strikes_present == {80.0, 100.0, 120.0}


def test_select_chains_and_strikes_with_strike_range_count():
    mock_ib = MagicMock()
    mock_chain = MagicMock()
    mock_chain.exchange = "SMART"
    mock_chain.multiplier = "100"
    mock_chain.expirations = ["20261120"]
    # 7 strikes: 70, 80, 90, 100, 110, 120, 130
    mock_chain.strikes = [70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0]
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    extractor = IBKROptionsExtractor(ib=mock_ib)
    underlying = MagicMock(symbol="XYZ", secType="STK", conId=123)

    # Spot is 102.0. Closest ATM strike is 100.0 (index 3).
    # strike_range = 2 -> takes indices 1..5: [80.0, 90.0, 100.0, 110.0, 120.0] (5 strikes)
    specs = extractor.select_chains_and_strikes(
        underlying,
        spot_price=102.0,
        max_dte=50,
        strike_range=2,
        target_date=date(2026, 10, 22)
    )

    assert len(specs) == 5
    strikes_present = sorted([s["strike"] for s in specs])
    assert strikes_present == [80.0, 90.0, 100.0, 110.0, 120.0]


def test_extract_chain_snapshot_chunks_and_collects():
    mock_ib = MagicMock()
    mock_underlying = MagicMock(symbol="SPY", secType="STK", conId=756733)
    mock_ib.qualifyContracts.return_value = [mock_underlying]

    # Spot price
    mock_spot_ticker = MagicMock()
    mock_spot_ticker.marketPrice.return_value = 500.0
    mock_ib.reqMktData.side_effect = lambda contract, *args: mock_spot_ticker

    mock_chain = MagicMock()
    mock_chain.exchange = "SMART"
    mock_chain.multiplier = "100"
    mock_chain.expirations = ["20261105"]  # 14 DTE
    mock_chain.strikes = [490.0, 500.0, 510.0]
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    # Model greeks mock
    mock_greeks = MagicMock()
    mock_greeks.impliedVol = 0.20
    mock_greeks.delta = 0.50
    mock_greeks.gamma = 0.02
    mock_greeks.vega = 0.15
    mock_greeks.theta = -0.05
    mock_greeks.undPrice = 500.0

    mock_opt_ticker = MagicMock()
    mock_opt_ticker.bid = 5.20
    mock_opt_ticker.ask = 5.30
    mock_opt_ticker.last = 5.25
    mock_opt_ticker.volume = 300
    mock_opt_ticker.openInterest = 1500
    mock_opt_ticker.modelGreeks = mock_greeks

    # Re-wire reqMktData for options
    def mock_req_mkt(contract, generic_ticks, *args):
        if generic_ticks == "101,106":
            return mock_opt_ticker
        return mock_spot_ticker

    mock_ib.reqMktData.side_effect = mock_req_mkt

    extractor = IBKROptionsExtractor(
        ib=mock_ib,
        requests_per_batch=2,  # Force multiple chunks
        batch_delay_seconds=0.01,
        request_timeout_seconds=0.01
    )

    result = extractor.extract_chain_snapshot(
        ticker="SPY",
        max_dte=50,
        strike_range_pct=0.10,
        target_date=date(2026, 10, 22)
    )

    assert isinstance(result, OptionsChainExtractionResult)
    assert result.ticker == "SPY"
    assert result.spot_price == 500.0
    # 1 expiration * 3 strikes * 2 rights (C & P) = 6 contracts
    assert result.total_contracts_extracted == 6
    assert len(result.contracts) == 6

    # Verify generic ticks requested
    req_calls = [c for c in mock_ib.reqMktData.call_args_list if c[0][1] == "101,106"]
    assert len(req_calls) == 6

    # Verify cancelMktData called for all options + spot
    assert mock_ib.cancelMktData.call_count >= 7


def test_process_batch_chunk_unconditional_cancel_on_error():
    mock_ib = MagicMock()
    mock_ib.sleep.side_effect = RuntimeError("Simulated timeout or network glitch")

    extractor = IBKROptionsExtractor(ib=mock_ib, request_timeout_seconds=0.01)
    contract_pairs = [(MagicMock(), {}), (MagicMock(), {})]

    with pytest.raises(RuntimeError, match="Simulated timeout"):
        extractor._process_batch_chunk(contract_pairs, 1, 1)

    # cancelMktData MUST have been called for both contracts in finally block
    assert mock_ib.cancelMktData.call_count == 2


def test_sanitize_float_and_missing_greeks():
    extractor = IBKROptionsExtractor(ib=MagicMock())
    assert extractor._sanitize_float(10.5) == 10.5
    assert extractor._sanitize_float(None) is None
    assert extractor._sanitize_float(float("nan")) is None
    assert extractor._sanitize_float(float("inf")) is None
    assert extractor._sanitize_float("invalid") is None


def test_process_batch_chunk_dynamic_polling_early_exit():
    mock_ib = MagicMock()
    # Mock ticker with ready greeks
    mock_ticker = MagicMock()
    mock_ticker.modelGreeks = MagicMock(
        impliedVol=0.20,
        delta=0.5,
        gamma=0.01,
        vega=0.1,
        theta=-0.05,
        undPrice=500.0
    )
    mock_ticker.openInterest = 100
    mock_ticker.volume = 50
    mock_ticker.bid = 5.0
    mock_ticker.ask = 5.2
    mock_ticker.last = 5.1
    mock_ib.reqMktData.return_value = mock_ticker

    extractor = IBKROptionsExtractor(
        ib=mock_ib,
        requests_per_batch=10,
        request_timeout_seconds=5.0,  # Long timeout
        min_wait_seconds=0.01,
        min_ready_pct=0.8
    )

    contract_pairs = [(MagicMock(), {"symbol": "SPY", "strike": 500.0, "dte": 10})]
    start_time = datetime.now()
    records = extractor._process_batch_chunk(contract_pairs, 1, 1)
    elapsed = (datetime.now() - start_time).total_seconds()

    # Dynamic polling should exit almost immediately (< 1.0s), NOT sleep full 5.0s
    assert len(records) == 1
    assert elapsed < 1.0

