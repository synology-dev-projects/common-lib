import logging
import time
from functools import lru_cache
from datetime import date
from typing import Any, Union, List, Optional

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from common_lib.config.main_config import MainConfig

logger = logging.getLogger("quant.common_lib.connectors.postgres")


# --- INTERNAL HELPER: CONNECTION FACTORY & POOLING ---
@lru_cache(maxsize=8)
def _get_postgres_engine_cached(
    user: str,
    password_secret: str,
    host: str,
    port: int,
    db: str
) -> sa.Engine:
    """
    Creates and caches a pooled PostgreSQL SQLAlchemy engine using psycopg.
    """
    resolved_host = "127.0.0.1" if host == "localhost" else host
    url = sa.engine.URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=password_secret,
        host=resolved_host,
        port=port,
        database=db
    )
    return sa.create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2}
    )




def _get_postgres_engine(config: MainConfig) -> sa.Engine:
    """
    Retrieves the cached SQLAlchemy engine pool for PostgreSQL given MainConfig.
    """
    return _get_postgres_engine_cached(
        config.postgres_user,
        config.postgres_pass.get_secret_value(),
        config.postgres_host,
        config.postgres_port,
        config.postgres_db
    )


def _get_engine(config: MainConfig) -> sa.Engine:
    """Convenience alias matching common connector interface."""
    return _get_postgres_engine(config)


# ==============================================================================
# PUBLIC API
# ==============================================================================

def execute(config: MainConfig, sql_statement: str, params: Optional[dict] = None) -> None:
    """
    Executes a SQL statement that does not return rows (CREATE, DROP, DELETE, etc.)
    and automatically commits.
    """
    start_time = time.time()
    engine = _get_postgres_engine(config)
    with engine.begin() as conn:
        conn.execute(sa.text(sql_statement), params or {})
    end_time = time.time()
    logger.debug(f"Execute time: {end_time - start_time:.4f} seconds")


def sql(config: MainConfig, sql_query: str, params: Optional[dict] = None) -> pd.DataFrame:
    """
    Executes a read-only SQL query and returns a DataFrame with uppercase columns.
    """
    start_time = time.time()
    engine = _get_postgres_engine(config)
    df = pd.read_sql_query(sa.text(sql_query), engine, params=params)
    df.columns = df.columns.str.upper()
    end_time = time.time()
    logger.debug(f"Query execution time: {end_time - start_time:.4f} seconds")
    return df


def drop_table_if_exists(config: MainConfig, table_name: str) -> None:
    """
    Drops a table if it exists in PostgreSQL.
    """
    table_name_lower = table_name.lower()
    engine = _get_postgres_engine(config)
    try:
        meta = sa.MetaData()
        tbl = sa.Table(table_name_lower, meta, autoload_with=engine)
        with engine.begin() as conn:
            tbl.drop(conn)
        logger.info(f"Table '{table_name_lower}' dropped successfully.")
    except NoSuchTableError:
        logger.debug(f"Table '{table_name_lower}' not found. Skipping drop.")
    except Exception as e:
        logger.error(f"Error dropping table '{table_name_lower}': {e}")


def write_to_postgres_upsert(
    config: MainConfig,
    df: pd.DataFrame,
    table_name: str,
    pks: List[str]
) -> None:
    """
    Inserts DataFrame rows into PostgreSQL table with ON CONFLICT DO UPDATE on primary keys.
    Dynamically creates table if it does not exist.
    """
    if df is None or df.empty:
        return

    table_name_lower = table_name.lower()
    pk_cols = [p.lower() for p in pks]

    df_clean = _lowercase_col_df(df.copy())
    engine = _get_postgres_engine(config)

    meta = sa.MetaData()
    try:
        tbl = sa.Table(table_name_lower, meta, autoload_with=engine)
    except Exception:
        sa_type_dict = _df_to_sa_types(df_clean)
        columns = []
        for col_name, sql_type in sa_type_dict.items():
            is_pk = col_name in pk_cols
            columns.append(sa.Column(col_name, sql_type, primary_key=is_pk))
        tbl = sa.Table(table_name_lower, meta, *columns)
        with engine.begin() as conn:
            tbl.create(conn)
        logger.info(f"Table '{table_name_lower}' structure created in PostgreSQL.")

    df_payload = df_clean.where(pd.notna(df_clean), None)
    records = df_payload.to_dict(orient='records')
    if not records:
        return

    stmt = pg_insert(tbl).values(records)
    update_dict = {
        c.name: getattr(stmt.excluded, c.name)
        for c in tbl.columns
        if c.name.lower() not in pk_cols
    }

    if update_dict and pk_cols:
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_=update_dict
        )
    elif pk_cols:
        upsert_stmt = stmt.on_conflict_do_nothing(
            index_elements=pk_cols
        )
    else:
        upsert_stmt = stmt

    with engine.begin() as conn:
        conn.execute(upsert_stmt)


def insert_into_table(
    config: MainConfig,
    df: pd.DataFrame,
    table_name: str,
    write_mode: str,
    primary_keys: List[str]
) -> None:
    """
    Main interface to insert DataFrame into PostgreSQL.
    write_mode: 'upsert', 'ignore', or 'overwrite'
    """
    if df is None or df.empty:
        return

    start_time = time.time()
    mode = write_mode.lower()
    table_name_lower = table_name.lower()
    pk_cols = [p.lower() for p in primary_keys]

    if mode == "upsert":
        write_to_postgres_upsert(config, df, table_name_lower, pk_cols)
    elif mode == "ignore":
        _df_to_postgres_insert_ignore(config, df, table_name_lower, pk_cols)
    elif mode == "overwrite":
        _df_to_postgres_overwrite(config, df, table_name_lower, pk_cols)
    else:
        raise ValueError(f"Invalid write mode: {write_mode}. Use: upsert, ignore, or overwrite")

    end_time = time.time()
    logger.debug(f"Execution time for {table_name_lower}: {end_time - start_time:.4f} seconds")


def get_unusual_flow(
    config: MainConfig,
    symbols: Optional[Union[str, List[str]]] = None,
    lookback_days: int = 30,
    min_premium: float = 0.0,
    symbol: Optional[Union[str, List[str]]] = None,
    limit: Optional[int] = None
) -> pd.DataFrame:
    """
    Queries unusual_option_flow_te for records matching symbol IN (...) and
    trade_date >= CURRENT_DATE - INTERVAL '1 day' * :lookback_days ordered by trade_date DESC, premium DESC.
    Accepts single symbol string, comma-separated symbols string, or list of symbols.
    Executes multi-ticker batch queries in a single flight.
    Safe fallback if table does not exist or has zero rows. Returns uppercase column names.
    """
    table_name = getattr(config, "postgres_unusual_flow_table_name", "unusual_option_flow_te").lower()
    raw_symbols = symbols if symbols is not None else symbol
    if raw_symbols is None:
        return pd.DataFrame()

    if isinstance(raw_symbols, str):
        raw_list = raw_symbols.split(",")
    elif isinstance(raw_symbols, (list, tuple, set)):
        raw_list = list(raw_symbols)
    else:
        raw_list = [str(raw_symbols)]

    # Clean and deduplicate symbols while preserving order
    seen = set()
    list_of_symbols = []
    for s in raw_list:
        clean_s = str(s).strip().upper().replace("$", "")
        if clean_s and clean_s not in seen:
            seen.add(clean_s)
            list_of_symbols.append(clean_s)

    if not list_of_symbols:
        return pd.DataFrame()

    from datetime import timedelta
    cutoff_date = (date.today() - timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")
    params: dict[str, Any] = {
        "cutoff_date": cutoff_date,
        "min_premium": float(min_premium),
    }

    if len(list_of_symbols) == 1:
        where_symbol_clause = "WHERE symbol = :symbol"
        params["symbol"] = list_of_symbols[0]
    else:
        bind_placeholders = ", ".join([f":sym_{i}" for i in range(len(list_of_symbols))])
        where_symbol_clause = f"WHERE symbol IN ({bind_placeholders})"
        for i, s in enumerate(list_of_symbols):
            params[f"sym_{i}"] = s

    query = f"""
        SELECT flow_id, trade_date, symbol, order_type, strike_price, strike_otm_pct,
               expiration_date, open_interest, is_unusual_oi, premium, net_score, created_at
        FROM {table_name}
        {where_symbol_clause}
          AND trade_date::text >= :cutoff_date
          AND premium >= :min_premium
        ORDER BY trade_date DESC, premium DESC
    """

    try:
        engine = _get_postgres_engine(config)
        df = pd.read_sql_query(
            sa.text(query),
            engine,
            params=params
        )
        df.columns = df.columns.str.upper()
        if limit is not None and limit > 0:
            df = df.head(limit)
        return df
    except Exception as ex:
        logger.warning(f"Error querying unusual flow from PostgreSQL {table_name} for symbols {list_of_symbols}: {ex}")
        return pd.DataFrame()


def get_quant_levels(
    config: MainConfig,
    ticker: str,
    as_of_date: Optional[date] = None
) -> pd.DataFrame:
    """
    Queries quant_lvl_data_te for levels matching ticker, optionally filtered by as_of_date.
    Returns uppercase columns: DATETIME, TICKER, START_LVL_PRICE, END_LVL_PRICE, COMMENTS, BUY_SELL_IND, WEB_LINK.
    """
    table_name = getattr(config, "postgres_quant_table_name", "quant_lvl_data_te").lower()
    clean_ticker = str(ticker).strip().upper().replace("$", "")
    if not clean_ticker:
        return pd.DataFrame()

    params: dict[str, Any] = {"ticker": clean_ticker}
    date_filter = ""
    if as_of_date is not None:
        date_filter = "AND datetime::date <= :as_of_date"
        params["as_of_date"] = as_of_date

    query = f"""
        SELECT datetime, ticker, start_lvl_price, end_lvl_price, comments, buy_sell_ind, web_link
        FROM {table_name}
        WHERE ticker = :ticker
        {date_filter}
        ORDER BY datetime DESC
    """

    try:
        engine = _get_postgres_engine(config)
        df = pd.read_sql_query(
            sa.text(query),
            engine,
            params=params
        )
        df.columns = df.columns.str.upper()
        return df
    except Exception as ex:
        logger.warning(f"Error querying quant levels from PostgreSQL {table_name} for {clean_ticker}: {ex}")
        return pd.DataFrame()


# ==============================================================================
# PRIVATE HELPERS
# ==============================================================================

def _df_to_postgres_overwrite(
    config: MainConfig,
    df: pd.DataFrame,
    table_name: str,
    primary_keys: List[str]
) -> None:
    drop_table_if_exists(config, table_name)
    write_to_postgres_upsert(config, df, table_name, primary_keys)


def _df_to_postgres_insert_ignore(
    config: MainConfig,
    df: pd.DataFrame,
    table_name: str,
    primary_keys: List[str]
) -> None:
    if df is None or df.empty:
        return

    table_name_lower = table_name.lower()
    pk_cols = [p.lower() for p in primary_keys]
    df_clean = _lowercase_col_df(df.copy())
    engine = _get_postgres_engine(config)

    meta = sa.MetaData()
    try:
        tbl = sa.Table(table_name_lower, meta, autoload_with=engine)
    except Exception:
        sa_type_dict = _df_to_sa_types(df_clean)
        columns = []
        for col_name, sql_type in sa_type_dict.items():
            is_pk = col_name in pk_cols
            columns.append(sa.Column(col_name, sql_type, primary_key=is_pk))
        tbl = sa.Table(table_name_lower, meta, *columns)
        with engine.begin() as conn:
            tbl.create(conn)

    df_payload = df_clean.where(pd.notna(df_clean), None)
    records = df_payload.to_dict(orient='records')
    if not records:
        return

    stmt = pg_insert(tbl).values(records)
    if pk_cols:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)

    with engine.begin() as conn:
        conn.execute(stmt)


def _lowercase_col_df(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = df.columns.str.lower()
    return df


def _df_to_sa_types(df: pd.DataFrame, default_string_length: int = 255) -> dict:
    types = {}
    for col_name, dtype in df.dtypes.items():
        if pd.api.types.is_integer_dtype(dtype):
            types[col_name] = sa.Integer
        elif pd.api.types.is_float_dtype(dtype):
            types[col_name] = sa.Float
        elif pd.api.types.is_bool_dtype(dtype):
            types[col_name] = sa.Boolean
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            types[col_name] = sa.DateTime
        else:
            types[col_name] = sa.String(default_string_length)
    return types
