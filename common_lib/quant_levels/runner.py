import logging
from typing import Optional
from datetime import datetime, date
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


def run_target_date_extraction(target_date: date, config: Optional[MainConfig] = None) -> int:
    """
    Runs on-demand targeted quant levels ingestion for a specific date:
    1. Loads config if not provided.
    2. Queries/scans posts from Mighty feed via extract matching target_date.
    3. Transforms with transform.run(config, matching_posts).
    4. Upserts into PostgreSQL quant_lvl_data_te with load.run(config, "upsert", clean_df).
    5. Returns count of rows upserted.
    """
    if config is None:
        config = load_config()

    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    logger.info(f"Starting targeted quant levels extraction for date: {target_date}")

    # 1. Fetch matching posts for target_date
    matching_posts = extract.fetch_posts_for_date(config, target_date)
    if not matching_posts:
        logger.info(f"No posts found for target_date: {target_date}.")
        return 0

    # 2. Transform unstructured data to structured df
    clean_df = transform.run(config, matching_posts)
    if clean_df is None or clean_df.empty:
        logger.info(f"Parsed DataFrame is empty for target_date: {target_date}. 0 rows upserted.")
        return 0

    # 3. Load df to postgres
    load.run(config, "upsert", clean_df)
    rows_upserted = len(clean_df)
    logger.info(f"Target date {target_date} quant levels loaded successfully. {rows_upserted} rows upserted.")
    return rows_upserted
