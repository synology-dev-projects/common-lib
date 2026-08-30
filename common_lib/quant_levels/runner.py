import logging
from typing import Optional
from datetime import datetime
import pandas as pd

from common_lib.config.main_config import load_config, MainConfig
from common_lib.connectors import nfty
from common_lib.quant_levels import extract, transform, load
from common_lib.quant_levels.load import CutoffDateNotFoundError

logger = logging.getLogger("quant.quant_levels.runner")


def run_daily_incremental(config: Optional[MainConfig] = None) -> int:
    """
    Runs the daily incremental quant levels pipeline in-process:
    1. Loads config.
    2. Queries latest cutoff date from quant_lvl_data_te using load._get_latest_recorded_date(config).
    3. Calls extract.run(config, cutoff_date=cutoff_date).
    4. If raw data exists, transforms with transform.run(config, raw_post_json).
    5. Dispatches NTFY alert with load._quant_lvl_df_to_string(clean_df).
    6. Upserts into PostgreSQL with load.run(config, "upsert", clean_df).
    7. Returns count of rows upserted (or 0 if no new posts).
    """
    if config is None:
        config = load_config()

    try:
        cutoff_date = load._get_latest_recorded_date(config)
        logger.info(f"Latest cutoff date found in DB: {cutoff_date}")
    except CutoffDateNotFoundError as e:
        logger.info(f"No cutoff date found in DB ({e}). Running without cutoff date.")
        cutoff_date = None
    except Exception as e:
        logger.warning(f"Error querying cutoff date ({e}). Running without cutoff date.")
        cutoff_date = None

    # 1. Fetch raw data from feed
    raw_post_json = extract.run(config, cutoff_date=cutoff_date)
    if not raw_post_json:
        logger.info(f"No new posts found after cutoff_date: {cutoff_date}.")
        return 0

    # 2. Transform unstructured data to structured df
    clean_df = transform.run(config, raw_post_json)
    if clean_df is None or clean_df.empty:
        logger.info("Parsed/cleaned DataFrame is empty. 0 rows upserted.")
        return 0

    # 3. Dispatch NTFY alert
    try:
        df_str = load._quant_lvl_df_to_string(clean_df)
        nfty.send_ntfy_notification(
            config.ntfy_endpoint,
            "quant_alerts",
            "NEW QUANT LVLS",
            df_str,
            3
        )
    except Exception as alert_err:
        logger.warning(f"Failed to dispatch NTFY alert: {alert_err}")

    # 4. Load df to postgres
    load.run(config, "upsert", clean_df)
    rows_upserted = len(clean_df)
    logger.info(f"Daily incremental quant levels loaded successfully. {rows_upserted} rows upserted.")
    return rows_upserted
