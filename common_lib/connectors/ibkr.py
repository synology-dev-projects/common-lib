import logging

from ib_insync import *
import pandas as pd
import datetime
from common_lib.config.main_config import MainConfig
from common_lib.utility.market_datetime import get_trading_day_count


def extract_ibkr_ticker_data(
        config: MainConfig,
        symbol:str,
        exchange: str,
        startDateStr: str,
        endDateStr: str,
        barSizeSetting: str = "1 day",
        whatToShow: str = "TRADES",
        useRTH: bool = False,
        currency:str = 'USD') -> pd.DataFrame:
    """
    :param contract:
    :param endDateTime:
    :param durationStr:
    :param barSizeSetting:
    :param whatToShow:
    :param useRTH:
    :return:
    """

    #Connect to fgateway
    ib = _connect_to_gateway(config)

    #calculate duration/convert dateStr
    duration_str = str(get_trading_day_count(startDateStr, endDateStr)) + " D"
    end_datetime = datetime.datetime.strptime(endDateStr, "%Y-%m-%d") # IBKR expects 'YYYYMMDD HH:mm:ss'

    #define contract
    contract = _define_contract(ib, symbol, exchange)

    #define error listener
    current_error = {}

    #inline function definition to prevent global variables
    def capture_error(reqId, errorCode, errorString, contract):
        current_error['code'] = errorCode
        current_error['msg'] = errorString

    ib.errorEvent += capture_error


    #Make Request
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_datetime,
        durationStr=duration_str,
        barSizeSetting=barSizeSetting,
        whatToShow=whatToShow,
        useRTH=useRTH
    )

    if not bars:
        raise PermissionError(f"Request returned error code: {current_error['code']} Reason: {current_error['msg']}")

    #Convert to df/dtypes
    df = util.df(bars)

    ib.disconnect()
    return df


def _define_contract(ib:IB, symbol:str, exchange:str)-> Contract:
    """
    Smartly defines the contract and returns the right secType
    If a index and stock have the same ticker symbol, it'll define it as a stock first
    :param ib:
    :param symbol:
    :param exchange:
    :return:
    """
    # 1. Attempt to define as a Stock (Most common)
    contract = Contract(symbol=symbol, secType='STK', exchange=exchange, currency='USD')

    # qualifyContracts returns a list. If it's not empty, it's valid.
    if ib.qualifyContracts(contract):
        return contract

    # 2. If Stock failed, attempt to define as an Index
    contract.secType = 'IND'

    if ib.qualifyContracts(contract):
        return contract

    # 3. If both failed, the data is wrong
    raise ValueError(f"Could not resolve '{symbol}' on '{exchange}' as either Stock or Index.")

def _connect_to_gateway(config: MainConfig) -> IB:
    ib = IB()
    ib.connect(config.ibkr_gateway_ip, config.ibkr_gateway_port, clientId=1)
    return ib


