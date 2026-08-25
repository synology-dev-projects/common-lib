import pytest
from unittest.mock import MagicMock, patch, ANY
from datetime import date, datetime
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import SecretStr

from common_lib.config.main_config import MainConfig
import common_lib.connectors.postgres as pg_conn


@pytest.fixture
def sample_config():
    """Mock configuration for testing postgres connector without requiring live DB."""
    config = MagicMock(spec=MainConfig)
    config.db_type = "postgres"
    config.postgres_host = "localhost"
    config.postgres_port = 5432
    config.postgres_db = "test_quant_db"
    config.postgres_user = "test_user"
    config.postgres_pass = SecretStr("secret_password")
    config.postgres_unusual_flow_table_name = "unusual_option_flow_te"
    config.postgres_quant_table_name = "quant_lvl_data_te"
    return config


def test_get_postgres_engine_caching(sample_config):
    """Verifies that engine creation uses lru_cache and builds valid postgresql+psycopg DSN."""
    with patch("sqlalchemy.create_engine") as mock_create:
        mock_engine = MagicMock(spec=sa.Engine)
        mock_create.return_value = mock_engine

        # Clear cache first
        pg_conn._get_postgres_engine_cached.cache_clear()

        engine1 = pg_conn._get_postgres_engine(sample_config)
        engine2 = pg_conn._get_postgres_engine(sample_config)

        assert engine1 is engine2
        assert mock_create.call_count == 1
        call_args = mock_create.call_args[0]
        dsn = call_args[0]
        assert dsn.drivername == "postgresql+psycopg"
        assert dsn.username == "test_user"
        assert dsn.password == "secret_password"
        assert dsn.host in ["localhost", "127.0.0.1"]
        assert dsn.port == 5432
        assert dsn.database == "test_quant_db"


def test_write_to_postgres_upsert_generates_on_conflict(sample_config):
    """Verifies write_to_postgres_upsert constructs ON CONFLICT DO UPDATE statement."""
    df = pd.DataFrame([
        {
            "FLOW_ID": "flow_123",
            "SYMBOL": "NVDA",
            "PREMIUM": 500000.0,
            "TRADE_DATE": "2026-08-20"
        }
    ])

    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine):
        pg_conn.write_to_postgres_upsert(
            config=sample_config,
            df=df,
            table_name="unusual_option_flow_te",
            pks=["FLOW_ID"]
        )

        assert mock_conn.execute.call_count >= 1
        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(executed_stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "ON CONFLICT (flow_id) DO UPDATE" in compiled_sql


def test_write_to_postgres_upsert_empty_dataframe(sample_config):
    """Verifies write_to_postgres_upsert safely handles empty DataFrame without error."""
    with patch("common_lib.connectors.postgres._get_postgres_engine") as mock_engine:
        pg_conn.write_to_postgres_upsert(sample_config, pd.DataFrame(), "test_table", ["id"])
        assert mock_engine.call_count == 0


def test_get_unusual_flow_single_ticker_query(sample_config):
    """Verifies get_unusual_flow generates single-ticker WHERE symbol = :symbol with interval."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_df = pd.DataFrame([{
        "flow_id": "f1",
        "symbol": "AAPL",
        "premium": 1000000.0,
        "trade_date": "2026-08-21"
    }])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        result = pg_conn.get_unusual_flow(
            config=sample_config,
            symbols="AAPL",
            lookback_days=14,
            min_premium=250000.0
        )

        assert mock_read_sql.call_count == 1
        query_arg = mock_read_sql.call_args[0][0]
        params_arg = mock_read_sql.call_args[1]["params"]

        assert "WHERE symbol = :symbol" in str(query_arg)
        assert "trade_date >= :cutoff_date" in str(query_arg)
        assert params_arg["symbol"] == "AAPL"
        assert "cutoff_date" in params_arg
        assert params_arg["min_premium"] == 250000.0
        assert "FLOW_ID" in result.columns
        assert "SYMBOL" in result.columns


def test_get_unusual_flow_batch_tickers_single_flight(sample_config):
    """Verifies get_unusual_flow batches multiple tickers into a single IN (:sym_0, :sym_1) query."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_df = pd.DataFrame([
        {"flow_id": "f1", "symbol": "NVDA", "premium": 2000000.0},
        {"flow_id": "f2", "symbol": "AMD", "premium": 800000.0}
    ])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        result = pg_conn.get_unusual_flow(
            config=sample_config,
            symbols=["NVDA", "AMD", "MSFT"],
            lookback_days=30,
            min_premium=10000.0
        )

        assert mock_read_sql.call_count == 1
        query_arg = str(mock_read_sql.call_args[0][0])
        params_arg = mock_read_sql.call_args[1]["params"]

        assert "WHERE symbol IN (:sym_0, :sym_1, :sym_2)" in query_arg
        assert params_arg["sym_0"] == "NVDA"
        assert params_arg["sym_1"] == "AMD"
        assert params_arg["sym_2"] == "MSFT"
        assert len(result) == 2


def test_get_unusual_flow_empty_and_fallback(sample_config):
    """Verifies empty symbol input or query failure returns empty DataFrame safely."""
    # Empty input
    assert pg_conn.get_unusual_flow(sample_config, symbols=None).empty
    assert pg_conn.get_unusual_flow(sample_config, symbols="").empty
    assert pg_conn.get_unusual_flow(sample_config, symbols=[]).empty

    # DB Exception fallback
    with patch("common_lib.connectors.postgres._get_postgres_engine", side_effect=Exception("DB Down")):
        res = pg_conn.get_unusual_flow(sample_config, symbols="TSLA")
        assert isinstance(res, pd.DataFrame)
        assert res.empty


def test_get_quant_levels(sample_config):
    """Verifies get_quant_levels correctly formats query and binds parameters."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_df = pd.DataFrame([{
        "datetime": "2026-08-20",
        "ticker": "SPY",
        "start_lvl_price": 550.0,
        "end_lvl_price": 555.0,
        "comments": "Support zone",
        "buy_sell_ind": "BUY",
        "web_link": "http://example.com"
    }])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        target_date = date(2026, 8, 20)
        result = pg_conn.get_quant_levels(sample_config, ticker="SPY", as_of_date=target_date)

        assert mock_read_sql.call_count == 1
        query_arg = str(mock_read_sql.call_args[0][0])
        params_arg = mock_read_sql.call_args[1]["params"]

        assert "WHERE ticker = :ticker" in query_arg
        assert "AND datetime::date <= :as_of_date" in query_arg
        assert params_arg["ticker"] == "SPY"
        assert params_arg["as_of_date"] == target_date
        assert "START_LVL_PRICE" in result.columns


def test_insert_into_table_modes(sample_config):
    """Verifies insert_into_table routes appropriately for upsert, ignore, and overwrite."""
    df = pd.DataFrame([{"symbol": "AAPL", "price": 200.0}])

    with patch("common_lib.connectors.postgres.write_to_postgres_upsert") as mock_upsert, \
         patch("common_lib.connectors.postgres._df_to_postgres_insert_ignore") as mock_ignore, \
         patch("common_lib.connectors.postgres._df_to_postgres_overwrite") as mock_overwrite:

        pg_conn.insert_into_table(sample_config, df, "test_tbl", "upsert", ["symbol"])
        assert mock_upsert.call_count == 1

        pg_conn.insert_into_table(sample_config, df, "test_tbl", "ignore", ["symbol"])
        assert mock_ignore.call_count == 1

        pg_conn.insert_into_table(sample_config, df, "test_tbl", "overwrite", ["symbol"])
        assert mock_overwrite.call_count == 1

        with pytest.raises(ValueError):
            pg_conn.insert_into_table(sample_config, df, "test_tbl", "invalid_mode", ["symbol"])


def test_get_unusual_flow_market_wide_date(sample_config):
    """Verifies get_unusual_flow generates market-wide query when trade_date is provided and symbols is None or MARKET."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_df = pd.DataFrame([
        {"flow_id": "m1", "symbol": "NVDA", "premium": 5000000.0, "trade_date": "2026-08-21"},
        {"flow_id": "m2", "symbol": "TSLA", "premium": 3000000.0, "trade_date": "2026-08-21"}
    ])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        result = pg_conn.get_unusual_flow(
            config=sample_config,
            symbols=None,
            trade_date="2026-08-21",
            min_premium=500000.0,
            limit=50
        )

        assert mock_read_sql.call_count == 1
        query_arg = str(mock_read_sql.call_args[0][0])
        params_arg = mock_read_sql.call_args[1]["params"]

        assert "WHERE trade_date = :target_date" in query_arg
        assert "symbol" not in params_arg
        assert params_arg["target_date"] == "2026-08-21"
        assert params_arg["min_premium"] == 500000.0
        assert "LIMIT :limit" in query_arg
        assert params_arg["limit"] == 50
        assert len(result) == 2
        assert "FLOW_ID" in result.columns


def test_get_unusual_flow_symbol_with_trade_date(sample_config):
    """Verifies get_unusual_flow generates symbol + trade_date query when both are provided."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_df = pd.DataFrame([
        {"flow_id": "s1", "symbol": "NVDA", "premium": 2000000.0, "trade_date": "2026-08-21"}
    ])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        result = pg_conn.get_unusual_flow(
            config=sample_config,
            symbols="NVDA",
            trade_date=date(2026, 8, 21),
            min_premium=0.0
        )

        assert mock_read_sql.call_count == 1
        query_arg = str(mock_read_sql.call_args[0][0])
        params_arg = mock_read_sql.call_args[1]["params"]

        assert "WHERE symbol = :symbol" in query_arg
        assert "trade_date = :target_date" in query_arg
        assert params_arg["symbol"] == "NVDA"
        assert params_arg["target_date"] == "2026-08-21"


def test_get_unusual_flow_trade_date_latest_and_yesterday(sample_config):
    """Verifies trade_date='latest' queries MAX(trade_date) and trade_date='yesterday' calculates weekday."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.scalar.return_value = "2026-08-21"

    mock_df = pd.DataFrame([{"flow_id": "l1", "symbol": "SPY", "premium": 1000000.0}])

    with patch("common_lib.connectors.postgres._get_postgres_engine", return_value=mock_engine), \
         patch("pandas.read_sql_query", return_value=mock_df) as mock_read_sql:

        # Test latest
        res_latest = pg_conn.get_unusual_flow(
            config=sample_config,
            symbols="MARKET",
            trade_date="latest"
        )
        assert mock_conn.execute.call_count == 1
        assert "SELECT MAX(trade_date)" in str(mock_conn.execute.call_args[0][0])
        assert not res_latest.empty

        # Test latest fallback when table empty
        mock_conn.execute.return_value.scalar.return_value = None
        res_empty = pg_conn.get_unusual_flow(
            config=sample_config,
            trade_date="latest"
        )
        assert res_empty.empty

        # Test yesterday
        mock_read_sql.reset_mock()
        res_yesterday = pg_conn.get_unusual_flow(
            config=sample_config,
            trade_date="yesterday"
        )
        assert mock_read_sql.call_count == 1
        yesterday_params = mock_read_sql.call_args[1]["params"]
        assert "target_date" in yesterday_params
