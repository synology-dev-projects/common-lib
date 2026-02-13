import pytest
import unittest
from ib_insync import *
from common_lib.connectors.ibkr import *
from common_lib.connectors.ibkr import _define_contract, _connect_to_gateway
from common_lib.utility.market_datetime import convert_to_valid_market_date_range

def test_define_contract(env_config):
    """
    Test define contract
    :return:
    """
    ib = _connect_to_gateway(env_config)
    # Define asset
    contract_index = _define_contract(ib, "SPX", "CBOE")
    contract_stock = _define_contract(ib, "AAPL", "NASDAQ")

    ib.disconnect()
    assert contract_index.secType == "IND"
    assert contract_stock.secType == "STK"

def test_get_7_days_data(env_config):
    start_date = "2025-07-01"
    end_date = "2025-07-12"
    df = extract_ibkr_ticker_data(config=env_config,
                             symbol="SPX",
                             exchange="CBOE",
                             startDateStr=start_date,
                             endDateStr=end_date)



    correct_start_date, correct_end_date = convert_to_valid_market_date_range(start_date, end_date)

    assert correct_start_date == df["date"].min().strftime("%Y-%m-%d")
    assert correct_end_date == df["date"].max().strftime("%Y-%m-%d")


if __name__ == '__main__':
    unittest.main()