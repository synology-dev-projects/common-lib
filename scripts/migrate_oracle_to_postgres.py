"""
Migration Script: Oracle DB -> PostgreSQL / TimescaleDB
Extracts institutional flow and quant levels from Oracle and upserts into PostgreSQL.
"""
import sys
import logging
from pathlib import Path
from typing import List

import pandas as pd

# Add parent directory to sys.path so common_lib is importable
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from common_lib.config.main_config import load_config, MainConfig
import common_lib.connectors.oracle as oracle_conn
import common_lib.connectors.postgres as postgres_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("quant.migration.oracle_to_postgres")


def migrate_table(
    config: MainConfig,
    oracle_table: str,
    postgres_table: str,
    pks: List[str],
    batch_size: int = 5000
) -> int:
    """
    Reads all records from Oracle table and writes/upserts into PostgreSQL in batches.
    """
    logger.info(f"Starting migration: Oracle '{oracle_table}' -> PostgreSQL '{postgres_table}'...")
    try:
        query = f"SELECT * FROM {oracle_table}"
        df = oracle_conn.sql(config, query)
        if df.empty:
            logger.info(f"Oracle table '{oracle_table}' is empty. 0 rows migrated.")
            return 0

        logger.info(f"Extracted {len(df)} rows from Oracle '{oracle_table}'. Writing to PostgreSQL...")
        
        # Lowercase columns for PostgreSQL compatibility
        df_clean = df.copy()
        df_clean.columns = df_clean.columns.str.lower()
        pk_cols = [p.lower() for p in pks]

        total_rows = len(df_clean)
        for i in range(0, total_rows, batch_size):
            batch_df = df_clean.iloc[i : i + batch_size]
            postgres_conn.write_to_postgres_upsert(config, batch_df, postgres_table, pk_cols)
            logger.info(f"Upserted batch {i // batch_size + 1} ({len(batch_df)} rows) into '{postgres_table}'.")

        logger.info(f"Successfully migrated {total_rows} rows from '{oracle_table}' to '{postgres_table}'.")
        return total_rows
    except Exception as ex:
        logger.error(f"Failed migrating '{oracle_table}' to '{postgres_table}': {ex}")
        raise


def run_migration() -> None:
    """
    Executes full migration for standard Quant System tables.
    """
    config = load_config()
    logger.info(f"Loaded config. PostgreSQL target: {config.postgres_host}:{config.postgres_port}/{config.postgres_db}")

    migration_specs = [
        {
            "oracle_table": getattr(config, "oracle_unusual_flow_table_name", "UNUSUAL_OPTION_FLOW_TE"),
            "postgres_table": getattr(config, "postgres_unusual_flow_table_name", "unusual_option_flow_te"),
            "pks": getattr(config, "oracle_unusual_flow_pks", ["flow_id"]),
        },
        {
            "oracle_table": getattr(config, "oracle_quant_table_name", "QUANT_LVL_DATA_TE"),
            "postgres_table": getattr(config, "postgres_quant_table_name", "quant_lvl_data_te"),
            "pks": getattr(config, "oracle_quant_pks", ["datetime", "ticker", "start_lvl_price"]),
        },
        {
            "oracle_table": getattr(config, "oracle_ibkr_ticker_table_name", "ticker_data_ibkr"),
            "postgres_table": "ticker_data_ibkr",
            "pks": ["symbol", "datetime"],
        }
    ]

    total_migrated = 0
    for spec in migration_specs:
        try:
            migrated = migrate_table(
                config=config,
                oracle_table=spec["oracle_table"],
                postgres_table=spec["postgres_table"],
                pks=spec["pks"]
            )
            total_migrated += migrated
        except Exception as e:
            logger.warning(f"Skipping or encountered issue on {spec['oracle_table']}: {e}")

    # Create composite indexes for high-frequency query paths
    logger.info("Applying high-performance composite indexes on PostgreSQL / TimescaleDB...")
    index_queries = [
        "CREATE INDEX IF NOT EXISTS idx_flow_sym_date_prem ON unusual_option_flow_te (symbol, trade_date DESC, premium DESC);",
        "CREATE INDEX IF NOT EXISTS idx_quant_lvl_lookup ON quant_lvl_data_te (ticker, datetime DESC);"
    ]
    for idx_sql in index_queries:
        try:
            postgres_conn.execute(config, idx_sql)
            logger.info(f"Applied index: {idx_sql.split()[5]}")
        except Exception as idx_err:
            logger.debug(f"Index notice: {idx_err}")

    logger.info(f"Migration completed. Total records processed: {total_migrated}")


if __name__ == "__main__":
    run_migration()
