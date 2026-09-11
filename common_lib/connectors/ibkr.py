import logging
import datetime
from typing import Optional, Any
import pandas as pd

try:
    from ib_insync import IB, Contract, util
except ImportError:
    IB = None
    Contract = None
    util = None

from common_lib.config.main_config import MainConfig
from common_lib.config.history_req_config import HistoryReqConfig

logger = logging.getLogger("quant.common_lib.ibkr")


class SafeIBConnection:
    """
    Context manager for Interactive Brokers gateway connections.
    Guarantees:
    1. Unconditional socket disconnection on exit, error, or cancellation.
    2. Resilient fallback across a pool of client IDs (base_id + 0..max_retries-1)
       to prevent 'Error 326: client id is already in use' from locking out automated cron jobs.
    """
    def __init__(
        self,
        host: str,
        port: int,
        base_client_id: int = 1,
        max_retries: int = 5,
        timeout: float = 10.0,
        ib_instance: Optional[Any] = None
    ):
        self.host = host
        self.port = int(port)
        self.base_client_id = int(base_client_id)
        self.max_retries = max(1, int(max_retries))
        self.timeout = float(timeout)
        if ib_instance is not None:
            self.ib = ib_instance
        elif IB is not None:
            self.ib = IB()
        else:
            raise ImportError("ib_insync is not installed in current environment.")
        self.connected_client_id: Optional[int] = None

    def __enter__(self):
        last_err: Optional[Exception] = None
        for offset in range(self.max_retries):
            client_id = self.base_client_id + offset
            try:
                logger.info(f"[IBKR] Attempting connection to {self.host}:{self.port} with clientId={client_id}")
                self.ib.connect(self.host, self.port, clientId=client_id, timeout=self.timeout)
                is_conn = getattr(self.ib, "isConnected", None)
                if is_conn is None or is_conn():
                    self.connected_client_id = client_id
                    logger.info(f"[IBKR] Successfully connected to gateway with clientId={client_id}")
                    return self.ib
            except Exception as ex:
                last_err = ex
                logger.warning(f"[IBKR] Connection with clientId={client_id} failed: {ex}. Trying next ID in pool...")
                try:
                    if hasattr(self.ib, "isConnected") and self.ib.isConnected():
                        self.ib.disconnect()
                except Exception:
                    pass

        raise ConnectionError(
            f"[IBKR] Failed to connect to IB Gateway ({self.host}:{self.port}) "
            f"across client ID pool [{self.base_client_id}..{self.base_client_id + self.max_retries - 1}]. "
            f"Last error: {last_err}"
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.ib and hasattr(self.ib, "isConnected") and self.ib.isConnected():
                logger.info(f"[IBKR] Disconnecting socket (clientId={self.connected_client_id}).")
                self.ib.disconnect()
        except Exception as ex:
            logger.warning(f"[IBKR] Error during socket disconnect: {ex}")


def extract_ibkr_ticker_data(
    config: MainConfig,
    h_config: HistoryReqConfig,
    base_client_id: int = 1,
    ib_instance: Optional[Any] = None
) -> pd.DataFrame:
    """
    Extracts historical ticker bar data from Interactive Brokers Gateway.
    Uses SafeIBConnection context manager to ensure sockets are unconditionally released
    and client ID collisions are automatically resolved.
    """
    with SafeIBConnection(
        host=config.synology_main_ip,
        port=config.ibkr_gateway_port,
        base_client_id=base_client_id,
        ib_instance=ib_instance
    ) as ib:
        from common_lib.utility.market_datetime import get_trading_day_count
        duration_str = str(get_trading_day_count(h_config.startDateStr, h_config.endDateStr)) + " D"
        end_datetime = datetime.datetime.strptime(h_config.endDateStr, "%Y-%m-%d")

        contract = _define_contract(ib, h_config.symbol, h_config.exchange)

        current_error = {}

        def capture_error(reqId, errorCode, errorString, contract):
            current_error['code'] = errorCode
            current_error['msg'] = errorString

        ib.errorEvent += capture_error

        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_datetime,
            durationStr=duration_str,
            barSizeSetting=h_config.barSizeSetting,
            whatToShow=h_config.whatToShow,
            useRTH=h_config.useRTH
        )

        if not bars:
            err_code = current_error.get('code', 'UNKNOWN')
            err_msg = current_error.get('msg', 'No historical data returned')
            raise PermissionError(f"Request returned error code: {err_code} Reason: {err_msg}")

        if util is None:
            raise ImportError("ib_insync.util is required to format bars DataFrame.")
        df = util.df(bars)
        if df is None:
            return pd.DataFrame()
        return df


def _define_contract(ib: Any, symbol: str, exchange: str):
    """
    Smartly defines the contract and returns the right secType.
    """
    if Contract is None:
        raise ImportError("ib_insync.Contract is required to define contract.")
    contract = Contract(symbol=symbol, secType='STK', exchange=exchange, currency='USD')

    if ib.qualifyContracts(contract):
        return contract

    contract.secType = 'IND'
    if ib.qualifyContracts(contract):
        return contract

    raise ValueError(f"Could not resolve '{symbol}' on '{exchange}' as either Stock or Index.")


def _connect_to_gateway(config: MainConfig, client_id: int = 1):
    """Legacy compatibility helper."""
    if IB is None:
        raise ImportError("ib_insync is required for _connect_to_gateway.")
    ib = IB()
    ib.connect(config.synology_main_ip, config.ibkr_gateway_port, clientId=client_id)
    return ib
