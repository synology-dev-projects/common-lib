"""
Runner module for Economic Events Ingestion.
Coordinates extract -> transform -> load cycle.
"""

import time
import logging
from typing import Dict, Any, Optional
import sqlalchemy as sa

from common_lib.config.main_config import MainConfig, load_config
from common_lib.database.postgres import get_postgres_engine
from common_lib.economic_events.extract import extract_all_upcoming_events
from common_lib.economic_events.transform import transform_raw_events
from common_lib.economic_events.load import load_events_to_postgres

logger = logging.getLogger("quant.common_lib.economic_events.runner")


def run_economic_events_sync(
    config_or_engine: Optional[Any] = None,
    force_refresh: bool = False,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes a complete ingestion run:
    1. Extract calendar feeds with rate-limiting and caching.
    2. Transform and validate events into relational records.
    3. Idempotently upsert records into PostgreSQL.
    """
    start_time = time.time()
    logger.info("Starting Economic Events ingestion cycle via common_lib runner...")

    if isinstance(config_or_engine, sa.Engine):
        engine = config_or_engine
    else:
        cfg = config_or_engine or load_config()
        engine = get_postgres_engine(cfg)

    # 1. Extract
    raw_events = extract_all_upcoming_events(force_refresh=force_refresh)
    extract_count = len(raw_events)

    # 2. Transform
    records = transform_raw_events(raw_events)
    transform_count = len(records)

    # 3. Load
    loaded_count = load_events_to_postgres(records, engine=engine)

    duration = time.time() - start_time
    logger.info(f"Ingestion cycle completed in {duration:.2f}s: extracted={extract_count}, transformed={transform_count}, loaded={loaded_count}")

    return {
        "status": "success",
        "duration_seconds": round(duration, 3),
        "raw_extracted": extract_count,
        "records_transformed": transform_count,
        "records_loaded": loaded_count,
    }
