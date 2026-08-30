"""
Unit tests for Universal Database Schema Auto-Migration Engine (DB-01).
Verifies canonical DDL definitions, transactional execution, idempotency, error handling, and engine resolution.
"""

import pytest
from unittest.mock import MagicMock, patch, call
import sqlalchemy as sa
from pydantic import SecretStr

from common_lib.config.main_config import MainConfig
from common_lib.database.schemas import SCHEMAS, ensure_all_schemas
from common_lib.database.postgres import get_postgres_engine, ensure_all_schemas as pg_ensure_all_schemas


@pytest.fixture
def mock_engine():
    """Provides a mocked SQLAlchemy engine with a mock connection context."""
    engine = MagicMock(spec=sa.Engine)
    conn = MagicMock()
    # Configure context manager for engine.begin()
    engine.begin.return_value.__enter__.return_value = conn
    return engine, conn


@pytest.fixture
def sample_config():
    """Mock configuration for testing schema engine with MainConfig."""
    config = MagicMock(spec=MainConfig)
    config.db_type = "postgres"
    config.postgres_host = "localhost"
    config.postgres_port = 5432
    config.postgres_db = "test_quant_db"
    config.postgres_user = "test_user"
    config.postgres_pass = SecretStr("secret_password")
    return config


# ==============================================================================
# 1. CANONICAL SCHEMAS DDL INTEGRITY TESTS
# ==============================================================================

def test_schemas_contains_all_four_canonical_tables():
    """Verifies SCHEMAS dictionary contains exactly the 4 required production tables."""
    expected_tables = {
        "unusual_whales_flow_te",
        "quant_lvl_data_te",
        "ibkr_historical_te",
        "chat_history"
    }
    assert set(SCHEMAS.keys()) == expected_tables

    for table_name in expected_tables:
        ddl = SCHEMAS[table_name]
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in ddl
        assert "CREATE INDEX IF NOT EXISTS" in ddl


def test_schemas_ddl_specific_column_definitions():
    """Verifies critical columns and primary keys in each canonical table DDL."""
    # unusual_whales_flow_te
    flow_ddl = SCHEMAS["unusual_whales_flow_te"]
    assert "symbol VARCHAR(16) NOT NULL" in flow_ddl
    assert "trade_date DATE NOT NULL" in flow_ddl
    assert "strike NUMERIC(10, 2) NOT NULL" in flow_ddl
    assert "call_put VARCHAR(4) NOT NULL" in flow_ddl
    assert "exp_date DATE NOT NULL" in flow_ddl
    assert "spot_price NUMERIC(10, 2)" in flow_ddl
    assert "otm_pct NUMERIC(6, 2)" in flow_ddl
    assert "premium NUMERIC(14, 2) NOT NULL" in flow_ddl
    assert "order_type VARCHAR(16)" in flow_ddl
    assert "sentiment VARCHAR(16)" in flow_ddl
    assert "is_otm BOOLEAN DEFAULT FALSE" in flow_ddl
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP" in flow_ddl
    assert "PRIMARY KEY (symbol, trade_date, strike, call_put, exp_date, premium)" in flow_ddl
    assert "idx_flow_symbol_date" in flow_ddl

    # quant_lvl_data_te
    quant_ddl = SCHEMAS["quant_lvl_data_te"]
    assert "symbol VARCHAR(16) NOT NULL" in quant_ddl
    assert "datetime TIMESTAMP WITH TIME ZONE NOT NULL" in quant_ddl
    assert "quant_level_type VARCHAR(32) NOT NULL" in quant_ddl
    assert "price_level NUMERIC(10, 2) NOT NULL" in quant_ddl
    assert "buy_zone_low NUMERIC(10, 2)" in quant_ddl
    assert "buy_zone_high NUMERIC(10, 2)" in quant_ddl
    assert "sell_zone_low NUMERIC(10, 2)" in quant_ddl
    assert "sell_zone_high NUMERIC(10, 2)" in quant_ddl
    assert "PRIMARY KEY (symbol, datetime, quant_level_type, price_level)" in quant_ddl
    assert "idx_quant_lvl_symbol_dt" in quant_ddl

    # ibkr_historical_te
    ibkr_ddl = SCHEMAS["ibkr_historical_te"]
    assert "symbol VARCHAR(16) NOT NULL" in ibkr_ddl
    assert "datetime TIMESTAMP WITH TIME ZONE NOT NULL" in ibkr_ddl
    assert "open NUMERIC(10, 4)" in ibkr_ddl
    assert "high NUMERIC(10, 4)" in ibkr_ddl
    assert "low NUMERIC(10, 4)" in ibkr_ddl
    assert "close NUMERIC(10, 4)" in ibkr_ddl
    assert "volume BIGINT" in ibkr_ddl
    assert "bar_count INTEGER" in ibkr_ddl
    assert "average NUMERIC(10, 4)" in ibkr_ddl
    assert "PRIMARY KEY (symbol, datetime)" in ibkr_ddl
    assert "idx_ibkr_symbol_dt" in ibkr_ddl

    # chat_history
    chat_ddl = SCHEMAS["chat_history"]
    assert "id SERIAL PRIMARY KEY" in chat_ddl
    assert "session_id VARCHAR(64) NOT NULL" in chat_ddl
    assert "role VARCHAR(16) NOT NULL" in chat_ddl
    assert "content TEXT NOT NULL" in chat_ddl
    assert "metadata JSONB" in chat_ddl
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP" in chat_ddl
    assert "idx_chat_session_id" in chat_ddl


# ==============================================================================
# 2. DDL EXECUTION & TRANSACTION VERIFICATION TESTS
# ==============================================================================

def test_ensure_all_schemas_executes_ddl_for_all_tables(mock_engine):
    """Verifies ensure_all_schemas opens a transaction and executes DDL for all 4 tables."""
    engine, conn = mock_engine

    result = ensure_all_schemas(engine)

    # Verify transaction context was opened
    engine.begin.assert_called_once()

    # Verify return dictionary contains all 4 verified statuses
    expected_result = {
        "unusual_whales_flow_te": "verified",
        "quant_lvl_data_te": "verified",
        "ibkr_historical_te": "verified",
        "chat_history": "verified"
    }
    assert result == expected_result

    # Total statements across 4 tables: each table has CREATE TABLE + CREATE INDEX (at least 8 statements)
    assert conn.execute.call_count >= 8

    # Extract all executed SQL strings
    executed_sqls = [str(call_args[0][0]) for call_args in conn.execute.call_args_list]

    for table in ["unusual_whales_flow_te", "quant_lvl_data_te", "ibkr_historical_te", "chat_history"]:
        assert any(f"CREATE TABLE IF NOT EXISTS {table}" in sql for sql in executed_sqls)


# ==============================================================================
# 3. IDEMPOTENCY TESTS
# ==============================================================================

def test_ensure_all_schemas_idempotency(mock_engine):
    """Verifies that running ensure_all_schemas multiple times executes cleanly without side effects."""
    engine, conn = mock_engine

    # Run 1
    result1 = ensure_all_schemas(engine)
    assert result1 == {
        "unusual_whales_flow_te": "verified",
        "quant_lvl_data_te": "verified",
        "ibkr_historical_te": "verified",
        "chat_history": "verified"
    }
    call_count_first_run = conn.execute.call_count

    # Run 2
    result2 = ensure_all_schemas(engine)
    assert result2 == result1
    assert conn.execute.call_count == call_count_first_run * 2
    assert engine.begin.call_count == 2


# ==============================================================================
# 4. ERROR HANDLING TESTS
# ==============================================================================

def test_ensure_all_schemas_connection_failure():
    """Verifies ensure_all_schemas raises appropriate error when engine fails to connect."""
    failing_engine = MagicMock(spec=sa.Engine)
    failing_engine.begin.side_effect = sa.exc.OperationalError(
        "could not connect to server: Connection refused",
        params={},
        orig=Exception("Connection refused")
    )

    with pytest.raises(sa.exc.OperationalError) as exc_info:
        ensure_all_schemas(failing_engine)

    assert "Connection refused" in str(exc_info.value)


def test_ensure_all_schemas_statement_execution_failure(mock_engine):
    """Verifies that a failure during DDL statement execution raises exception and terminates."""
    engine, conn = mock_engine
    conn.execute.side_effect = sa.exc.ProgrammingError(
        "syntax error at or near 'INVALID'",
        params={},
        orig=Exception("syntax error")
    )

    with pytest.raises(sa.exc.ProgrammingError) as exc_info:
        ensure_all_schemas(engine)

    assert "syntax error" in str(exc_info.value)


# ==============================================================================
# 5. ENGINE RESOLUTION & CONFIG INTERFACE TESTS
# ==============================================================================

def test_ensure_all_schemas_resolves_default_engine_when_none(mock_engine):
    """Verifies ensure_all_schemas() with None resolves via get_postgres_engine()."""
    engine, _ = mock_engine
    with patch("common_lib.database.postgres.get_postgres_engine", return_value=engine) as mock_get_engine:
        result = ensure_all_schemas()
        assert mock_get_engine.call_count == 1
        assert result["unusual_whales_flow_te"] == "verified"


def test_ensure_all_schemas_resolves_main_config(sample_config, mock_engine):
    """Verifies ensure_all_schemas(sample_config) resolves via get_postgres_engine(config)."""
    engine, _ = mock_engine
    with patch("common_lib.database.postgres.get_postgres_engine", return_value=engine) as mock_get_engine:
        result = ensure_all_schemas(sample_config)
        assert mock_get_engine.call_count == 1
        assert mock_get_engine.call_args[0][0] is sample_config
        assert result["chat_history"] == "verified"


def test_get_postgres_engine_with_dict():
    """Verifies get_postgres_engine builds engine from dict config."""
    dict_config = {
        "user": "custom_user",
        "password": "custom_password",
        "host": "192.168.1.50",
        "port": 5432,
        "database": "custom_quant_db"
    }
    with patch("sqlalchemy.create_engine") as mock_create:
        mock_create.return_value = MagicMock(spec=sa.Engine)
        engine = get_postgres_engine(dict_config)
        assert engine is not None
        assert mock_create.call_count == 1
        dsn = mock_create.call_args[0][0]
        assert dsn.username == "custom_user"
        assert dsn.password == "custom_password"
        assert dsn.host == "192.168.1.50"
        assert dsn.database == "custom_quant_db"


def test_postgres_module_re_exports():
    """Verifies that common_lib.database.postgres re-exports ensure_all_schemas and get_postgres_engine."""
    from common_lib.database.postgres import ensure_all_schemas as reexported_ensure
    assert reexported_ensure is ensure_all_schemas
