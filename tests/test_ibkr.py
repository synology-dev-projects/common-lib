import datetime
from unittest.mock import MagicMock, patch
import pytest
import unittest
from ib_insync import *
from common_lib.config.history_req_config import HistoryReqConfig
from common_lib.connectors.ibkr import extract_ibkr_ticker_data, _define_contract
from common_lib.utility.market_datetime import convert_to_valid_market_date_range


def test_define_contract(env_config):
    """
    Test define contract with mocked IB gateway
    :return:
    """
    mock_ib = MagicMock()

    def side_effect(contract):
        if contract.symbol == "SPX" and contract.secType == "IND":
            return [contract]
        elif contract.symbol == "AAPL" and contract.secType == "STK":
            return [contract]
        return []

    mock_ib.qualifyContracts.side_effect = side_effect

    # Define asset using mock_ib directly
    contract_index = _define_contract(mock_ib, "SPX", "CBOE")
    contract_stock = _define_contract(mock_ib, "AAPL", "NASDAQ")

    assert contract_index.secType == "IND"
    assert contract_stock.secType == "STK"


@patch("common_lib.connectors.ibkr._connect_to_gateway")
def test_get_7_days_data(mock_connect, env_config):
    """
    Test historical data extraction with mocked IB gateway
    :return:
    """
    mock_ib = MagicMock()
    mock_connect.return_value = mock_ib
    mock_ib.qualifyContracts.side_effect = lambda c: [c]

    mock_bars = [
        BarData(date=datetime.date(2025, 7, 1), open=500.0, high=505.0, low=499.0, close=504.0, volume=1000, average=502.0, barCount=10),
        BarData(date=datetime.date(2025, 7, 11), open=504.0, high=508.0, low=503.0, close=507.0, volume=1200, average=505.0, barCount=12),
    ]
    mock_ib.reqHistoricalData.return_value = mock_bars

    start_date = "2025-07-01"
    end_date = "2025-07-12"
    h_config = HistoryReqConfig(symbol="SPY"
                                , exchange="NASDAQ"
                                , startDateStr=start_date
                                , endDateStr=end_date)

    df = extract_ibkr_ticker_data(env_config, h_config)

    correct_start_date, correct_end_date = convert_to_valid_market_date_range(start_date, end_date)

    assert correct_start_date == df["date"].min().strftime("%Y-%m-%d")
    assert correct_end_date == df["date"].max().strftime("%Y-%m-%d")


if __name__ == '__main__':
    unittest.main()