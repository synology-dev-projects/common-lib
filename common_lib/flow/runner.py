import logging
from datetime import datetime, date, timedelta
from typing import Optional
import pandas as pd

from common_lib.config.main_config import load_config, MainConfig
from common_lib.flow import extract, transform, load

logger = logging.getLogger("quant.gateway.flow_pipeline.runner")


def run_daily_incremental(config: Optional[MainConfig] = None) -> int:
    if config is None:
        config = load_config()

    latest_date = load.get_latest_recorded_date(config)
    if latest_date:
        cutoff_date = latest_date
        logger.info(f"Found latest recorded date in DB: {latest_date}")
    else:
        cutoff_date = (datetime.now() - timedelta(days=30)).date()
        logger.info(f"No previous records found in DB. Ingesting last 30 days (cutoff: {cutoff_date}).")

    session = extract.get_authenticated_flow_session(config)
    symbols = extract.extract_all_displayed_symbols(config, session)
    if not symbols:
        logger.warning("Could not extract active symbol list, using fallback watchlist.")
        symbols = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "AMZN", "META", "GOOGL", "IWM", "COIN"]

    logger.info(f"Targeting {len(symbols)} universe symbols.")
    all_raw_records = []
    for sym in symbols:
        recs = extract.extract_flow_for_symbol(config, sym, cutoff_date=cutoff_date, session=session)
        if recs:
            all_raw_records.extend(recs)

    if not all_raw_records:
        logger.info("Zero new flow records detected beyond cutoff date.")
        return 0

    df_clean = transform.transform_flow_records(all_raw_records)
    rows_inserted = load.run(config, df_clean, write_mode="upsert")
    logger.info(f"Incremental flow sync completed. Rows inserted: {rows_inserted}")
    return rows_inserted
