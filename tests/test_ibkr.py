import datetime
from unittest.mock import MagicMock, patch
import pytest
import unittest

# 1. Independent SafeIBConnection import
from common_lib.connectors.ibkr import SafeIBConnection

# 2. Optional ib_insync dependent components
try:
    from ib_insync import BarData
    from common_lib.connectors.ibkr import (
        extract_ibkr_ticker_data,
        _define_contract,
        HistoryReqConfig
    )
    from common_lib.utility.market_datetime import convert_to_valid_market_date_range
except ImportError:
    BarData = None
    extract_ibkr_ticker_data = None
    _define_contract = None
    HistoryReqConfig = None
    convert_to_valid_market_date_range = None


def test_safe_ib_connection_unconditional_disconnect_on_exception():
    """
    PIPE-01: Verifies that an unhandled exception or timeout inside the context
    manager unconditionally triggers ib.disconnect().
    """
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True

    with pytest.raises(RuntimeError, match="Simulated ETL failure"):
        with SafeIBConnection(host="127.0.0.1", port=4002, ib_instance=mock_ib) as ib:
            raise RuntimeError("Simulated ETL failure")

    mock_ib.disconnect.assert_called_once()


def test_safe_ib_connection_client_id_fallback():
    """
    PIPE-01: Verifies that if clientId=1 fails (e.g. Error 326: client id in use),
    SafeIBConnection catches the exception and retries with clientId=2.
    """
    mock_ib = MagicMock()
    attempts = []

    def mock_connect(host, port, clientId, timeout):
        attempts.append(clientId)
        if clientId == 1:
            raise ConnectionError("Error 326: client id is already in use")
        return None

    mock_ib.connect.side_effect = mock_connect
    mock_ib.isConnected.return_value = True

    conn = SafeIBConnection(host="127.0.0.1", port=4002, base_client_id=1, max_retries=5, ib_instance=mock_ib)
    with conn as ib:
        assert ib == mock_ib
        assert conn.connected_client_id == 2

    assert attempts == [1, 2]
    # Disconnected twice: once on retry cleanup after clientId=1 failure, once on context exit
    assert mock_ib.disconnect.call_count == 2


def test_safe_ib_connection_pool_exhaustion_raises():
    """
    PIPE-01: Verifies that if all client IDs fail in the pool,
    a ConnectionError is raised with descriptive message.
    """
    mock_ib = MagicMock()
    mock_ib.connect.side_effect = ConnectionError("All ports refused")
    mock_ib.isConnected.return_value = False

    conn = SafeIBConnection(host="127.0.0.1", port=4002, base_client_id=1, max_retries=3, ib_instance=mock_ib)
    with pytest.raises(ConnectionError, match="across client ID pool"):
        with conn:
            pass


@pytest.mark.skipif(BarData is None, reason="ib_insync not installed")
def test_define_contract(env_config):
    mock_ib = MagicMock()

    def side_effect(contract):
        if contract.symbol == "SPX" and contract.secType == "IND":
            return [contract]
        elif contract.symbol == "AAPL" and contract.secType == "STK":
            return [contract]
        return []

    mock_ib.qualifyContracts.side_effect = side_effect

    contract_index = _define_contract(mock_ib, "SPX", "CBOE")
    contract_stock = _define_contract(mock_ib, "AAPL", "NASDAQ")

    assert contract_index.secType == "IND"
    assert contract_stock.secType == "STK"


@pytest.mark.skipif(BarData is None, reason="ib_insync not installed")
@patch("common_lib.connectors.ibkr.SafeIBConnection")
def test_get_7_days_data(mock_safe_conn, env_config):
    mock_ib = MagicMock()
    mock_safe_conn.return_value.__enter__.return_value = mock_ib
    mock_safe_conn.return_value.__exit__.return_value = False
    mock_ib.qualifyContracts.side_effect = lambda c: [c]

    mock_bars = [
        BarData(date=datetime.date(2025, 7, 1), open=500.0, high=505.0, low=499.0, close=504.0, volume=1000, average=502.0, barCount=10),
        BarData(date=datetime.date(2025, 7, 11), open=504.0, high=508.0, low=503.0, close=507.0, volume=1200, average=505.0, barCount=12),
    ]
    mock_ib.reqHistoricalData.return_value = mock_bars

    start_date = "2025-07-01"
    end_date = "2025-07-12"
    h_config = HistoryReqConfig(symbol="SPY", exchange="NASDAQ", startDateStr=start_date, endDateStr=end_date)

    df = extract_ibkr_ticker_data(env_config, h_config)

    correct_start_date, correct_end_date = convert_to_valid_market_date_range(start_date, end_date)

    assert correct_start_date == df["date"].min().strftime("%Y-%m-%d")
    assert correct_end_date == df["date"].max().strftime("%Y-%m-%d")


if __name__ == '__main__':
    unittest.main()
