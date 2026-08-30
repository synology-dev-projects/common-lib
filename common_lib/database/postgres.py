"""
PostgreSQL Database Connector & Schema Interface for Quant System.
Provides engine factory, pooling, database operations, and schema auto-migration exports.
"""

import os
import logging
from typing import Any, Optional, Union
import sqlalchemy as sa
from pydantic import SecretStr

from common_lib.config.main_config import MainConfig, load_config
from common_lib.connectors.postgres import (
    _get_postgres_engine_cached,
    _get_postgres_engine,
    _get_engine,
    execute,
    sql,
    drop_table_if_exists,
    write_to_postgres_upsert,
    insert_into_table,
    get_unusual_flow,
    get_quant_levels,
    ensure_flow_indexes,
)
from common_lib.database.schemas import ensure_all_schemas

logger = logging.getLogger("quant.common_lib.database.postgres")


def get_postgres_engine(engine_or_config: Optional[Union[sa.Engine, MainConfig, dict]] = None) -> sa.Engine:
    """
    Public factory function to retrieve or build a cached SQLAlchemy PostgreSQL engine.
    
    Accepts:
        - sa.Engine: returns as-is.
        - MainConfig: builds pooled engine using config credentials.
        - dict: builds engine using dictionary configuration.
        - None: loads MainConfig from environment or falls back to standard POSTGRES_* env vars.
    """
    if isinstance(engine_or_config, sa.Engine):
        return engine_or_config

    if isinstance(engine_or_config, MainConfig):
        return _get_postgres_engine(engine_or_config)

    if isinstance(engine_or_config, dict):
        user = engine_or_config.get("user") or engine_or_config.get("postgres_user", "quant_admin")
        pw = engine_or_config.get("password") or engine_or_config.get("postgres_pass", "quant_secure_pass")
        if isinstance(pw, SecretStr):
            pw = pw.get_secret_value()
        host = engine_or_config.get("host") or engine_or_config.get("postgres_host", "localhost")
        port = int(engine_or_config.get("port") or engine_or_config.get("postgres_port", 5432))
        db = engine_or_config.get("db") or engine_or_config.get("database") or engine_or_config.get("postgres_db", "quant_db")
        return _get_postgres_engine_cached(user, str(pw), host, port, db)

    # If engine_or_config is None, attempt loading via MainConfig first
    try:
        cfg = load_config()
        return _get_postgres_engine(cfg)
    except Exception as e:
        logger.debug(f"Could not load full MainConfig ({e}), falling back to direct POSTGRES_* env vars.")
        user = os.getenv("POSTGRES_USER", "quant_admin")
        pw = os.getenv("POSTGRES_PASSWORD", os.getenv("POSTGRES_PASS", "quant_secure_pass"))
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        db = os.getenv("POSTGRES_DB", "quant_db")
        return _get_postgres_engine_cached(user, pw, host, port, db)


__all__ = [
    "get_postgres_engine",
    "ensure_all_schemas",
    "_get_postgres_engine_cached",
    "_get_postgres_engine",
    "_get_engine",
    "execute",
    "sql",
    "drop_table_if_exists",
    "write_to_postgres_upsert",
    "insert_into_table",
    "get_unusual_flow",
    "get_quant_levels",
    "ensure_flow_indexes",
]
