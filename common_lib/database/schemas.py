"""
Canonical Database Schemas & Auto-Migration Engine for Quant System (PostgreSQL 16).
Provides centralized DDL definitions and idempotent schema verification on startup.
"""

import logging
from typing import Any, Dict, Optional, Union
import sqlalchemy as sa

logger = logging.getLogger("quant.common_lib.database.schemas")

# ==============================================================================
# CANONICAL POSTGRESQL 16 SCHEMAS
# ==============================================================================

SCHEMAS: Dict[str, str] = {
    "unusual_whales_flow_te": """
CREATE TABLE IF NOT EXISTS unusual_whales_flow_te (
    symbol VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    strike NUMERIC(10, 2) NOT NULL,
    call_put VARCHAR(4) NOT NULL,
    exp_date DATE NOT NULL,
    spot_price NUMERIC(10, 2),
    otm_pct NUMERIC(6, 2),
    premium NUMERIC(14, 2) NOT NULL,
    order_type VARCHAR(16),
    sentiment VARCHAR(16),
    is_otm BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date, strike, call_put, exp_date, premium)
);
CREATE INDEX IF NOT EXISTS idx_flow_symbol_date ON unusual_whales_flow_te(symbol, trade_date);
""",
    "quant_lvl_data_te": """
CREATE TABLE IF NOT EXISTS quant_lvl_data_te (
    symbol VARCHAR(16) NOT NULL,
    datetime TIMESTAMP WITH TIME ZONE NOT NULL,
    quant_level_type VARCHAR(32) NOT NULL,
    price_level NUMERIC(10, 2) NOT NULL,
    buy_zone_low NUMERIC(10, 2),
    buy_zone_high NUMERIC(10, 2),
    sell_zone_low NUMERIC(10, 2),
    sell_zone_high NUMERIC(10, 2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, datetime, quant_level_type, price_level)
);
CREATE INDEX IF NOT EXISTS idx_quant_lvl_symbol_dt ON quant_lvl_data_te(symbol, datetime);
""",
    "ibkr_historical_te": """
CREATE TABLE IF NOT EXISTS ibkr_historical_te (
    symbol VARCHAR(16) NOT NULL,
    datetime TIMESTAMP WITH TIME ZONE NOT NULL,
    open NUMERIC(10, 4),
    high NUMERIC(10, 4),
    low NUMERIC(10, 4),
    close NUMERIC(10, 4),
    volume BIGINT,
    bar_count INTEGER,
    average NUMERIC(10, 4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, datetime)
);
CREATE INDEX IF NOT EXISTS idx_ibkr_symbol_dt ON ibkr_historical_te(symbol, datetime);
""",
    "chat_history": """
CREATE TABLE IF NOT EXISTS chat_history (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_session_id ON chat_history(session_id);
""",
    "economic_events": """
CREATE TABLE IF NOT EXISTS economic_events (
    event_id VARCHAR(64) NOT NULL,
    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    country VARCHAR(8) NOT NULL,
    title VARCHAR(256) NOT NULL,
    impact_tier VARCHAR(16) NOT NULL,
    forecast VARCHAR(32),
    previous VARCHAR(32),
    actual VARCHAR(32),
    synthetic_summary TEXT,
    raw_payload JSONB,
    embedding VECTOR(768),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, event_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_econ_ts ON economic_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_econ_country_impact ON economic_events(country, impact_tier);
CREATE INDEX IF NOT EXISTS idx_econ_embedding ON economic_events USING hnsw (embedding vector_cosine_ops);
""",
    "company_macro_sensitivities": """
CREATE TABLE IF NOT EXISTS company_macro_sensitivities (
    ticker VARCHAR(16) PRIMARY KEY,
    beta_rates NUMERIC(8, 4),
    beta_oil NUMERIC(8, 4),
    beta_usd NUMERIC(8, 4),
    beta_market NUMERIC(8, 4),
    debt_to_equity NUMERIC(10, 4),
    interest_coverage NUMERIC(10, 4),
    net_debt_ebitda NUMERIC(10, 4),
    thematic_tags TEXT[],
    primary_catalysts TEXT[],
    query_expansion TEXT,
    last_filing_date DATE,
    last_calculated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_macro_sens_ticker ON company_macro_sensitivities(ticker);
"""
}


def ensure_pgvector_extension(engine: sa.Engine) -> bool:
    """Ensures pgvector extension is enabled in the database idempotently."""
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector;"))
        logger.info("pgvector extension verified/enabled successfully.")
        return True
    except Exception as e:
        logger.warning(f"Could not enable pgvector extension: {e}")
        return False


# ==============================================================================
# AUTO-MIGRATION & VERIFICATION ENGINE
# ==============================================================================

def ensure_all_schemas(engine_or_config: Optional[Union[sa.Engine, Any]] = None) -> Dict[str, str]:
    """
    Ensures all canonical schemas (tables, indices, constraints) exist in PostgreSQL.
    Executes DDL statements idempotently inside a transaction.

    Args:
        engine_or_config: Optional SQLAlchemy Engine, MainConfig instance, dict, or None.
                          When None or config, resolved via get_postgres_engine().

    Returns:
        Dict[str, str]: Mapping table name to verification status (e.g. {"unusual_whales_flow_te": "verified", ...}).
    """
    if isinstance(engine_or_config, sa.Engine):
        engine = engine_or_config
    else:
        from common_lib.database.postgres import get_postgres_engine
        engine = get_postgres_engine(engine_or_config)

    results: Dict[str, str] = {}

    with engine.begin() as conn:
        for table_name, ddl_block in SCHEMAS.items():
            # Split multi-statement DDL by semicolon
            statements = [s.strip() for s in ddl_block.strip().split(";") if s.strip()]
            for statement in statements:
                conn.execute(sa.text(statement))
            results[table_name] = "verified"
            logger.info(f"Schema for table '{table_name}' verified/auto-migrated successfully.")

    return results
