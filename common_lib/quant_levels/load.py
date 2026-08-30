import logging
from typing import Optional
from datetime import datetime, timezone
import pandas as pd
from common_lib.connectors import postgres
from common_lib.config.main_config import MainConfig as Config

class CutoffDateNotFoundError(Exception):
    """Raised when the database query returns no valid max date."""
    pass

logger = logging.getLogger(__name__)


def run(config: Config, write_mode: str, df: pd.DataFrame) -> int:
    table_name = getattr(config, "postgres_quant_table_name", "quant_lvl_data_te")
    primary_keys = ["datetime", "ticker", "start_lvl_price"]

    if df is None or df.empty:
        logger.info("DataFrame is empty. Skipping DB push.")
        return 0

    logger.info(f"Pushing {len(df)} rows to '{table_name}' with mode='{write_mode}'...")

    try:
        postgres.insert_into_table(
            config=config,
            df=df,
            table_name=table_name,
            write_mode=write_mode,
            primary_keys=primary_keys
        )
        logger.info("Push successful.")
        return len(df)
    except Exception as e:
        logger.error(f"Failed to push to PostgreSQL: {e}")
        raise e


def _get_latest_recorded_date(config: Config) -> datetime:
    """
    Gets the latest record date of the quant_lvl_table for cutoff date.
    """
    table_name = getattr(config, "postgres_quant_table_name", "quant_lvl_data_te")
    query = f'SELECT MAX(datetime) FROM {table_name}'

    try:
        df = postgres.sql(config, query)

        if df.empty:
            raise CutoffDateNotFoundError("Query returned no rows.")

        last_date = df.iloc[0, 0]
        if pd.isna(last_date):
            raise CutoffDateNotFoundError(f"Table '{table_name}' is empty; no max date found.")

        if isinstance(last_date, str):
            last_date = pd.to_datetime(last_date).to_pydatetime()
        elif isinstance(last_date, pd.Timestamp):
            last_date = last_date.to_pydatetime()

        if last_date.tzinfo is None:
            last_date = last_date.replace(tzinfo=timezone.utc)
        else:
            last_date = last_date.astimezone(timezone.utc)

        logger.info(f"Last checkpoint found: {last_date}")
        return last_date

    except CutoffDateNotFoundError:
        raise
    except Exception as e:
        logger.warning(f"Database error while fetching date: {e}")
        raise CutoffDateNotFoundError(f"Could not retrieve cutoff date due to DB error: {e}")


def _quant_lvl_df_to_string(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "QUANT LVL: No records found."
    date = df['DATETIME'].iloc[0].date()
    header = f"QUANT LVL FOR DATE: {date}"
    cols = [c for c in ['START_LVL_PRICE', 'END_LVL_PRICE', 'BUY_SELL_IND', 'COMMENTS'] if c in df.columns]
    df_sorted = df.sort_values(by='START_LVL_PRICE', ascending=False) if 'START_LVL_PRICE' in df.columns else df
    df_str = (df_sorted[cols]
              .to_string(index=False, header=False))

    msg_str = f"{header}\n{df_str}"
    return msg_str
