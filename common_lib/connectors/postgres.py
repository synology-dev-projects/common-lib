import logging
import re
import time
from functools import lru_cache
from datetime import date, datetime, timedelta
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
    date_input: Optional[Union[str, date]] = None,
    date: Optional[Union[str, date]] = None,
    trade_date: Optional[Union[str, date]] = None,
    start_date: Optional[Union[str, date]] = None,
    end_date: Optional[Union[str, date]] = None,
    symbols: Optional[Union[str, List[str]]] = None,
    symbol: Optional[str] = None,
    lookback_days: int = 30,
    min_premium: float = 0.0,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """Queries unusual options flow prints from PostgreSQL table (unusual_option_flow_te).
    
    Supports:
    - Single trade date: e.g. '2026-08-21', 'Friday', 'yesterday', 'today', 'latest'.
    - Date ranges: e.g. '2026-08-17 to 2026-08-21' or start_date/end_date arguments.
    - Default (no date provided & no symbols): automatically defaults to the latest recorded trading day in DB.
    - Historical lookback (symbols provided without date): queries trade_date >= cutoff_date.
    - Completeness: 100% of ALL entries for the requested session or range when limit is omitted.
    """
    from datetime import date as dt_date, timedelta as dt_timedelta
    table_name = getattr(config, "postgres_unusual_flow_table_name", "unusual_option_flow_te").lower()

    raw_date_str = str(date_input or date or trade_date or "").strip()
    is_range = False
    range_start_obj: Optional[dt_date] = None
    range_end_obj: Optional[dt_date] = None
    target_date_obj: Optional[dt_date] = None

    # 1. Clean and deduplicate symbols if provided
    raw_symbols = symbols if symbols is not None else symbol
    list_of_symbols = []
    if raw_symbols is not None:
        if isinstance(raw_symbols, str):
            raw_list = raw_symbols.split(",")
        elif isinstance(raw_symbols, (list, tuple, set)):
            raw_list = list(raw_symbols)
        else:
            raw_list = [str(raw_symbols)]

        seen = set()
        for s in raw_list:
            clean_s = str(s).strip().upper().replace("$", "")
            if clean_s and clean_s not in seen and clean_s not in ("MARKET", "ALL"):
                seen.add(clean_s)
                list_of_symbols.append(clean_s)

    # 2. Check for explicit start_date / end_date arguments or date range strings
    if start_date is not None and end_date is not None:
        is_range = True
        range_start_obj = pd.to_datetime(start_date).date()
        range_end_obj = pd.to_datetime(end_date).date()
        if range_start_obj > range_end_obj:
            range_start_obj, range_end_obj = range_end_obj, range_start_obj
    elif raw_date_str:
        range_match = re.split(r"\s+(?:to|-|through)\s+|[:\.]{2,}|(?<=\d):(?=\d)", raw_date_str, flags=re.IGNORECASE)
        if len(range_match) == 2 and range_match[0] and range_match[1]:
            try:
                range_start_obj = pd.to_datetime(range_match[0].strip()).date()
                range_end_obj = pd.to_datetime(range_match[1].strip()).date()
                if range_start_obj > range_end_obj:
                    range_start_obj, range_end_obj = range_end_obj, range_start_obj
                is_range = True
            except Exception:
                is_range = False

        if not is_range:
            WEEKDAY_MAP = {
                "monday": 0, "mon": 0,
                "tuesday": 1, "tue": 1, "tues": 1,
                "wednesday": 2, "wed": 2,
                "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
                "friday": 4, "fri": 4,
                "saturday": 5, "sat": 5,
                "sunday": 6, "sun": 6,
            }
            td_clean = raw_date_str.lower()
            clean_weekday = re.sub(r"^(last|this)\s+", "", td_clean).strip()
            if td_clean == "latest":
                target_date_obj = None  # Will resolve to MAX(trade_date)
            elif td_clean in ("yesterday", "prev", "previous"):
                today_dt = dt_date.today()
                if today_dt.weekday() == 0:  # Monday -> Friday
                    target_date_obj = today_dt - dt_timedelta(days=3)
                elif today_dt.weekday() == 6:  # Sunday -> Friday
                    target_date_obj = today_dt - dt_timedelta(days=2)
                else:
                    target_date_obj = today_dt - dt_timedelta(days=1)
            elif td_clean == "today":
                target_date_obj = dt_date.today()
            elif clean_weekday in WEEKDAY_MAP:
                today_dt = dt_date.today()
                target_weekday = WEEKDAY_MAP[clean_weekday]
                offset = (today_dt.weekday() - target_weekday) % 7
                if offset == 0 and "this" not in td_clean:
                    offset = 7
                target_date_obj = today_dt - dt_timedelta(days=offset)
            else:
                try:
                    target_date_obj = pd.to_datetime(raw_date_str).date()
                except Exception:
                    target_date_obj = None

    # 3. Check if this is a historical lookback query for specific tickers (no trade_date specified)
    is_lookback_mode = bool(list_of_symbols and not raw_date_str and start_date is None and end_date is None)

    params: dict[str, Any] = {"min_premium": float(min_premium)}
    where_clauses = []

    if is_lookback_mode:
        cutoff_date = (dt_date.today() - dt_timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")
        params["cutoff_date"] = cutoff_date
        if len(list_of_symbols) == 1:
            where_clauses.append("symbol = :symbol")
            params["symbol"] = list_of_symbols[0]
        else:
            bind_placeholders = ", ".join([f":sym_{i}" for i in range(len(list_of_symbols))])
            where_clauses.append(f"symbol IN ({bind_placeholders})")
            for i, s in enumerate(list_of_symbols):
                params[f"sym_{i}"] = s
        where_clauses.append("trade_date >= :cutoff_date")
        where_clauses.append("premium >= :min_premium")
    else:
        # Date / Range / Latest Session Mode
        if not is_range and target_date_obj is None:
            try:
                engine = _get_postgres_engine(config)
                with engine.connect() as conn:
                    max_res = conn.execute(sa.text(f"SELECT MAX(trade_date) FROM {table_name}")).scalar()
                if max_res:
                    target_date_obj = pd.to_datetime(max_res).date()
                else:
                    return pd.DataFrame()
            except Exception as ex:
                logger.warning(f"Error querying MAX(trade_date) from {table_name}: {ex}")
                return pd.DataFrame()

        if list_of_symbols:
            if len(list_of_symbols) == 1:
                where_clauses.append("symbol = :symbol")
                params["symbol"] = list_of_symbols[0]
            else:
                bind_placeholders = ", ".join([f":sym_{i}" for i in range(len(list_of_symbols))])
                where_clauses.append(f"symbol IN ({bind_placeholders})")
                for i, s in enumerate(list_of_symbols):
                    params[f"sym_{i}"] = s

        if is_range and range_start_obj and range_end_obj:
            where_clauses.append("trade_date BETWEEN :start_date AND :end_date")
            params["start_date"] = range_start_obj.strftime("%Y-%m-%d")
            params["end_date"] = range_end_obj.strftime("%Y-%m-%d")
        elif target_date_obj is not None:
            where_clauses.append("trade_date = :target_date")
            params["target_date"] = target_date_obj.strftime("%Y-%m-%d")

        where_clauses.append("premium >= :min_premium")

    if not where_clauses:
        return pd.DataFrame()

    where_sql = "WHERE " + " AND ".join(where_clauses)
    limit_clause = "LIMIT :limit" if (limit is not None and int(limit) > 0) else ""
    if limit is not None and int(limit) > 0:
        params["limit"] = int(limit)

    query = f"""
        SELECT flow_id, trade_date, symbol, order_type, strike_price, strike_otm_pct,
               expiration_date, open_interest, is_unusual_oi, premium, net_score, created_at
        FROM {table_name}
        {where_sql}
        ORDER BY trade_date DESC, premium DESC
        {limit_clause}
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
        logger.warning(f"Error querying unusual flow from PostgreSQL {table_name}: {ex}")
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


def ensure_flow_indexes(config: MainConfig) -> None:
    """
    Idempotently creates composite B-tree indices on unusual_option_flow_te
    for ultra-fast date range and ticker scans.
    """
    engine = _get_postgres_engine(config)
    index_sqls = [
        "CREATE INDEX IF NOT EXISTS idx_flow_trade_date_prem ON unusual_option_flow_te (trade_date DESC, premium DESC);",
        "CREATE INDEX IF NOT EXISTS idx_flow_symbol_date ON unusual_option_flow_te (symbol, trade_date DESC);"
    ]
    try:
        with engine.begin() as conn:
            for sql_stmt in index_sqls:
                conn.execute(sa.text(sql_stmt))
        logger.info("Successfully verified/created composite indices on unusual_option_flow_te.")
    except Exception as ex:
        logger.warning(f"Could not create flow indices (table may not exist yet): {ex}")

