import logging
from datetime import datetime, date
from typing import Optional
import pandas as pd
import sqlalchemy as sa
from common_lib.config.main_config import MainConfig
import common_lib.connectors.postgres as postgres

logger = logging.getLogger("quant.pipeline.flow.load")

def get_latest_recorded_date(config: MainConfig) -> Optional[date]:
    """
    Queries PostgreSQL to find the highest trade_date in unusual_option_flow_te.
    Returns None if the table is empty or does not exist.
    """
    table_name = "unusual_option_flow_te"
    query = f"SELECT MAX(trade_date) AS max_date FROM {table_name}"
    try:
        df = postgres.sql(config, query)
        if not df.empty and ("max_date" in df.columns or "MAX_DATE" in df.columns):
            col = "max_date" if "max_date" in df.columns else "MAX_DATE"
            val = df[col].iloc[0]
            if pd.notna(val):
                if isinstance(val, (datetime, pd.Timestamp)):
                    return val.date()
                elif isinstance(val, date):
                    return val
                return pd.to_datetime(val).date()
    except Exception as ex:
        logger.warning(f"Could not retrieve MAX(trade_date) from {table_name}: {ex}")
    return None

def run(config: MainConfig, df: pd.DataFrame, write_mode: str = "upsert") -> int:
    """
    Persists flow records to PostgreSQL table 'unusual_option_flow_te'.
    """
    table_name = "unusual_option_flow_te"
    primary_keys = ["flow_id"]
    
    if df.empty:
        logger.info("DataFrame is empty. Skipping DB insert.")
        return 0
        
    logger.info(f"Pushing {len(df)} flow records to '{table_name}' on PostgreSQL with mode='{write_mode}'...")
    postgres.write_to_postgres_upsert(
        config=config,
        df=df,
        table_name=table_name,
        pks=primary_keys
    )
    logger.info(f"Successfully upserted {len(df)} records into {table_name}.")
    return len(df)

