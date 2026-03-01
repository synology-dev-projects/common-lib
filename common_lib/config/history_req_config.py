from dataclasses import dataclass


@dataclass
class HistoryReqConfig:
    # --- Required Arguments ---
    symbol: str
    exchange: str
    startDateStr: str
    endDateStr: str

    # --- Optional Arguments (with Defaults) ---
    barSizeSetting: str = "1 day"
    whatToShow: str = "TRADES"
    useRTH: bool = False
    currency: str = "USD"