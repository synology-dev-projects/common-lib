import logging
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.exc import NoSuchTableError
from common_lib.config.main_config import MainConfig
import yaml


# --- INTERNAL HELPER: CONNECTION FACTORY & POOLING ---
@lru_cache(maxsize=8)
def _get_engine_cached(oracle_user: str, oracle_pass_secret: str, host: str, service: str, port: int = 1521) -> sa.Engine:
    """
    Creates and caches a pooled SQLAlchemy engine.
    """
    dsn = f"oracle+oracledb://{oracle_user}:{oracle_pass_secret}@{host}:{port}/?service_name={service}"
    return sa.create_engine(
        dsn,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True
    )


def _get_engine(config: MainConfig) -> sa.Engine:
    """
    Retrieves the cached SQLAlchemy engine pool for the given configuration.
    """
    return _get_engine_cached(
        config.oracle_user,
        config.oracle_pass.get_secret_value(),
        config.synology_main_ip,
        config.oracle_service
    )


# ==============================================================================
# PUBLIC API (These take 'config' as the entry point)
# ==============================================================================

def execute(config: MainConfig, sql_statement: str) -> None:
    """
    Executes a SQL statement that does not return rows (DELETE, UPDATE, etc.)
    and automatically commits.
    """
    start_time = time.time()

    engine = _get_engine(config)
    # engine.begin() automatically starts a transaction and commits at the end
    with engine.begin() as conn:
        conn.execute(sa.text(sql_statement))

    end_time = time.time()
    logging.info(f"Query time: {end_time - start_time:.4f} seconds")



def sql(config: MainConfig, sql_query: str) -> pd.DataFrame:
    """
    Executes a read-only SQL query and returns a DataFrame.
    """
    start_time = time.time()

    engine = _get_engine(config)
    # read_sql_query manages connection open/close automatically with a pooled engine
    df = pd.read_sql_query(sql_query, engine, parse_dates={"DATETIME": '%Y-%m-%d'})
    df.columns = df.columns.str.upper()

    end_time = time.time()
    logging.info(f"Execution time: {end_time - start_time:.4f} seconds")
    return df



def drop_table_if_exists(config: MainConfig, table_name: str) -> None:
    """
    Public wrapper to drop a table using a connection from the pool.
    """
    start_time = time.time()

    table_name = table_name.upper()
    engine = _get_engine(config)
    _drop_table_internal(engine, table_name)



def insert_into_table(config: MainConfig, df: pd.DataFrame, table_name: str, write_mode: str,
                      primary_keys: list[str]) -> None:
    """
    Main interface to insert df into oracle.
    :param df:
    :param table_name:
    :param primary_keys:
    :param write_mode: 'ignore', 'upsert', or 'overwrite'
    """
    start_time = time.time()
    write_mode = write_mode.lower()
    table_name = table_name.upper()
    engine = _get_engine(config)

    if write_mode == 'ignore':
        _df_to_oracle_insert_ignore(engine, df, table_name, primary_keys)
    elif write_mode == 'upsert':
        _df_to_oracle_upsert(engine, df, table_name, primary_keys)
    elif write_mode == 'overwrite':
        _df_to_oracle_overwrite(engine, df, table_name, primary_keys)
    else:
        raise ValueError("Invalid write mode. Use: ignore, upsert, or overwrite")

    end_time = time.time()
    logging.info(f"Execution time for {table_name}: {end_time - start_time:.4f} seconds")


def get_unusual_flow(
    config: MainConfig,
    symbol: str,
    lookback_days: int = 30,
    min_premium: float = 0.0,
    limit: int = 50
) -> pd.DataFrame:
    """
    Queries UNUSUAL_OPTION_FLOW_TE for records matching SYMBOL = :symbol and
    TRADE_DATE >= CURRENT_DATE - :lookback_days ordered by TRADE_DATE DESC, PREMIUM DESC.
    Safe fallback if table does not exist or has zero rows.
    """
    table_name = getattr(config, "oracle_unusual_flow_table_name", "UNUSUAL_OPTION_FLOW_TE")
    clean_symbol = str(symbol).strip().upper().replace("$", "")
    if not clean_symbol:
        return pd.DataFrame()

    query = f"""
        SELECT FLOW_ID, TRADE_DATE, SYMBOL, ORDER_TYPE, STRIKE_PRICE, STRIKE_OTM_PCT,
               EXPIRATION_DATE, OPEN_INTEREST, IS_UNUSUAL_OI, PREMIUM, NET_SCORE, CREATED_AT
        FROM {table_name}
        WHERE SYMBOL = :symbol
          AND TRADE_DATE >= CURRENT_DATE - :lookback_days
          AND PREMIUM >= :min_premium
        ORDER BY TRADE_DATE DESC, PREMIUM DESC
    """
    try:
        engine = _get_engine(config)
        df = pd.read_sql_query(
            sa.text(query),
            engine,
            params={
                "symbol": clean_symbol,
                "lookback_days": int(lookback_days),
                "min_premium": float(min_premium),
            }
        )
        df.columns = df.columns.str.upper()
        if limit is not None and limit > 0:
            df = df.head(limit)
        return df
    except Exception as ex:
        logging.warning(f"Error querying unusual flow from {table_name} for symbol {clean_symbol}: {ex}")
        return pd.DataFrame()


def get_table_metadata(m_config: MainConfig, table_name: str) :
    """
    Loads the YAML and extracts the metadata for a specific table.
    """
    tables = _get_metadata_catalog(m_config)
    if table_name not in tables:
        raise KeyError(f"Table '{table_name}' not found in the db catalogue.")

    table_meta = tables[table_name]
    return table_meta

def generate_metadata_from_oracle(m_config: MainConfig) -> None:
    """
    Reads table schemas from Oracle and generates a YAML configuration file.
    Writes in project root.
    """
    # Create an inspector to look at the database metadata
    engine = _get_engine(m_config)
    inspector = sa.inspect(engine)

    # 1. Ask Oracle for a list of ALL tables in the default schema
    table_names = inspector.get_table_names()

    if not table_names:
        logging.error("ERROR: No tables found in the database.")
        return

    logging.info(f"Found {len(table_names)} tables. Generating YAML...")
    schema_config = {"tables": {}}

    for table in table_names:
        # 1. Verify the table actually exists in Oracle
        if not inspector.has_table(table):
            logging.warning(f"Warning: Table '{table}' not found in Oracle. Skipping.")
            continue

        logging.info(f"Inspecting table: {table}...")

        # 2. Extract Primary Keys
        pk_constraint = inspector.get_pk_constraint(table)
        primary_keys = pk_constraint.get('constrained_columns', [])

        # 3. Extract Columns
        columns_info = inspector.get_columns(table)

        # Build a default 1-to-1 mapping (lowercase key : UPPERCASE ORACLE NAME)
        # e.g., 'start_lvl_price': 'START_LVL_PRICE'
        column_mapping = {}
        for col in columns_info:
            oracle_col_name = col['name']
            python_key_name = oracle_col_name.lower()
            column_mapping[python_key_name] = oracle_col_name

        # 4. Construct the dictionary for this specific table
        # We use a friendly lowercase name for the YAML key (e.g., 'quant_lvl_data_te')
        table_key = table.lower()
        schema_config["tables"][table_key] = {
            "table_name": table,
            "primary_keys": primary_keys,
            "columns": column_mapping
        }

    # 5. Write the dictionary to a YAML file
    with open(m_config.db_catalog_file_path, "w") as yaml_file:
        # default_flow_style=False ensures it writes as a clean block format
        # sort_keys=False keeps the columns in the order Oracle returned them
        yaml.dump(schema_config, yaml_file, default_flow_style=False, sort_keys=False)

    logging.info(f"Successfully wrote schema to {m_config.common_config_path}")





# ==============================================================================
# PRIVATE IMPLEMENTATION (These take 'engine' to reuse connections)
# ==============================================================================




def _drop_table_internal(engine: sa.Engine, table_name: str) -> None:
    """
    Internal helper that uses an existing engine to drop a table.
    """

    try:
        # Reflect table to see if it exists
        meta = sa.MetaData()
        tbl = sa.Table(table_name.lower(), meta, autoload_with=engine)

        logging.info(f"Table '{table_name}' found. Dropping it now...")
        with engine.begin() as conn:  # 'begin' automatically commits
            tbl.drop(conn)
        logging.info(f"Table '{table_name}' successfully dropped.")

    except NoSuchTableError:
        logging.warning(f"Table '{table_name}' not found in schema. Skipping drop.")
    except Exception as e:
        logging.error(f"Error dropping table {table_name}: {e}")


def _df_to_oracle_overwrite(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: list[str]) -> int:
    """
    Writes table to oracle / Will overwrite if there's anything of the same name.
    """
    # 1. Drop table if exists
    _drop_table_internal(engine, table_name)

    # 2. Prepare DataFrame
    df_clean = _lowercase_col_df(df.copy())
    sa_type_dict = _df_to_sa_types(df_clean)

    # 3. Define Schema (Columns + PKs)
    columns = []
    for col_name, sql_type in sa_type_dict.items():
        is_pk = (col_name.lower() in (s.lower() for s in primary_keys))
        columns.append(sa.Column(col_name, sql_type, primary_key=is_pk))

    tbl = sa.Table(table_name, sa.MetaData(), *columns)

    # 4. Create and Insert
    with engine.begin() as conn:
        tbl.create(conn)
        logging.info(f"Table '{table_name}' structure created.")

        #oracle doesnt accept nan, must convert to NONE
        df_payload = df_clean.replace({float('nan'): None})

        data_to_insert = df_payload.to_dict(orient='records')
        if data_to_insert:
            conn.execute(sa.insert(tbl), data_to_insert)

    return len(df.index)


def _df_to_oracle_upsert(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: list[str]) -> None:
    """
    Inserts and updates any records based off pk.
    """
    unique_suffix = uuid.uuid4().hex[:8].upper()
    temp_table_name = f"TMP_{table_name[:12]}_{unique_suffix}"

    # 1. Write to Temp Table
    _df_to_oracle_overwrite(engine, df, temp_table_name, primary_keys)
    # 2. Create Merge SQL
    merge_sql = _create_merge_statement(engine, temp_table_name, table_name, "upsert")
    logging.info(f"Executing MERGE (Upsert) via staging table {temp_table_name}")

    # 3. Execute Merge
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(merge_sql))
        logging.info("MERGE statement executed successfully.")
    except Exception as e:
        logging.error(f"Upsert failed: {e}")
        raise
    finally:
        _drop_table_internal(engine, temp_table_name)


def _df_to_oracle_insert_ignore(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: list[str]) -> None:
    """
    Will not insert any records that violate primary_id constraints.
    """
    unique_suffix = uuid.uuid4().hex[:8].upper()
    temp_table_name = f"TMP_{table_name[:12]}_{unique_suffix}"

    # 1. Write to Temp Table
    _df_to_oracle_overwrite(engine, df, temp_table_name, primary_keys)

    # 2. Create Merge SQL
    merge_sql = _create_merge_statement(engine, temp_table_name, table_name, "ignore")
    logging.info(f"Executing MERGE (Ignore Duplicates) via staging table {temp_table_name}")

    # 3. Execute Merge
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(merge_sql))
        logging.info("MERGE statement executed successfully.")
    except Exception as e:
        logging.error(f"Insert Ignore failed: {e}")
        raise
    finally:
        _drop_table_internal(engine, temp_table_name)

def _get_metadata_catalog(m_config: MainConfig) -> dict[str, Any]:
    """
    Reads a YAML file and checks if it contains any table definitions.
    """
    yaml_file = Path(m_config.db_catalog_file_path)

    # 1. Verify the file actually exists before trying to open it
    if not yaml_file.exists():
        raise FileNotFoundError(f"Could not find the file at {yaml_file.resolve()}")

    # 2. Read and parse the YAML
    with open(yaml_file, "r") as file:
        try:
            db_catalog_data = yaml.safe_load(file)
        except yaml.YAMLError as e:
            error_msg = f"Error: The file is not valid YAML. Details: {e}"
            logging.error(error_msg)
            raise FileNotFoundError(error_msg)

    # 4. Check for the 'tables' dictionary and see if it has contents
    tables = db_catalog_data.get("tables", {})

    if not tables:
        error_msg = "The YAML file is valid, but no tables were found inside it."
        raise FileNotFoundError(error_msg)

    # 5. Success!
    table_count = len(tables)
    table_names = list(tables.keys())
    logging.info(f"Found {table_count} table(s): {table_names}")

    return tables


# ==============================================================================
# HELPER FUNCTIONS (Stateless)
# ==============================================================================

def _create_merge_statement(engine: sa.Engine, src_table: str, tgt_table: str, mode: str) -> str:
    """
    Reflects the Target Table to build a dynamic MERGE statement.
    """
    # Reflect target table to get columns
    inspector = sa.inspect(engine)
    if not inspector.has_table(tgt_table.lower()):
        raise NoSuchTableError(f"Target table {tgt_table} does not exist for merge.")

    # Get columns and PKs
    col_list = [col['name'].upper() for col in inspector.get_columns(tgt_table.lower())]
    pk_list = [col.upper() for col in inspector.get_pk_constraint(tgt_table.lower())['constrained_columns']]

    if not pk_list:
        raise ValueError(f"Table {tgt_table} has no primary keys defined in Oracle.")

    # Logic to build strings
    set_clauses = [f"T.{col} = S.{col}" for col in col_list if col not in pk_list]
    set_clause_str = ", ".join(set_clauses)

    insert_cols_str = ", ".join(col_list)
    insert_values_str = ", ".join([f"S.{col}" for col in col_list])

    on_clause_str = " AND ".join([f"S.{key} = T.{key}" for key in pk_list])

    mode = mode.lower()
    if mode == "upsert":
        # Oracle MERGE UPDATE clause cannot update columns used in the ON clause
        update_part = f"WHEN MATCHED THEN UPDATE SET {set_clause_str}" if set_clauses else ""
    elif mode == "ignore":
        update_part = ""
    else:
        raise ValueError(f"Invalid mode: {mode}")

    sql = f"""
    MERGE INTO {tgt_table.upper()} T
    USING {src_table.upper()} S
    ON ({on_clause_str})
    {update_part}
    WHEN NOT MATCHED THEN
        INSERT ({insert_cols_str})
        VALUES ({insert_values_str})
    """
    return sql


def _lowercase_col_df(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = df.columns.str.lower()
    return df


def _df_to_sa_types(df: pd.DataFrame, default_string_length: int = 255) -> dict:
    """
    takes df and converts the
    :param df:
    :param default_string_length:
    :return:
    """
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