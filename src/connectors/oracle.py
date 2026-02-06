import logging
import time
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.exc import NoSuchTableError
from config.main_config import MainConfig


# --- INTERNAL HELPER: CONNECTION FACTORY ---
def _get_engine(config: MainConfig) -> sa.Engine:
    """
    Creates a SQLAlchemy engine on demand using config credentials.
    """
    dsn = f"oracle+oracledb://{config.oracle_user}:{config.oracle_pass.get_secret_value()}@{config.oracle_host_ip}:1521/?service_name={config.oracle_service}"
    return sa.create_engine(dsn)


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
    try:
        # engine.begin() automatically starts a transaction and commits at the end
        with engine.begin() as conn:
            conn.execute(sa.text(sql_statement))
    finally:
        engine.dispose()

    end_time = time.time()
    logging.info(f"Query time: {end_time - start_time:.4f} seconds")



def sql(config: MainConfig, sql_query: str) -> pd.DataFrame:
    """
    Executes a read-only SQL query and returns a DataFrame.
    """
    start_time = time.time()

    engine = _get_engine(config)
    try:
        # read_sql_query manages connection open/close automatically with an engine
        df = pd.read_sql_query(sql_query, engine, parse_dates={"DATETIME": '%Y-%m-%d'})
        df.columns = df.columns.str.upper()
        return df
    finally:
        engine.dispose()

    end_time = time.time()
    logging.info(f"Execution time: {end_time - start_time:.4f} seconds")


def drop_table_if_exists(config: MainConfig, table_name: str) -> None:
    """
    Public wrapper to drop a table using a fresh connection.
    """
    start_time = time.time()

    table_name = table_name.upper()
    engine = _get_engine(config)
    try:
        _drop_table_internal(engine, table_name)
    finally:
        engine.dispose()


def insert_into_table(config: MainConfig, df: pd.DataFrame, table_name: str, write_mode: str,
                      primary_keys: [str]) -> None:
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


    try:
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

    finally:
        engine.dispose()


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


def _df_to_oracle_overwrite(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: [str]) -> int:
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


def _df_to_oracle_upsert(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: [str]) -> None:
    """
    Inserts and updates any records based off pk.
    """
    temp_table_name = "TEMP_" + table_name[:20]  # Shorten to ensure valid Oracle ID

    # 1. Write to Temp Table
    _df_to_oracle_overwrite(engine, df, temp_table_name, primary_keys)
    # 2. Create Merge SQL
    merge_sql = _create_merge_statement(engine, temp_table_name, table_name, "upsert")
    logging.info(f"Executing MERGE (Upsert)")

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


def _df_to_oracle_insert_ignore(engine: sa.Engine, df: pd.DataFrame, table_name: str, primary_keys: [str]) -> None:
    """
    Will not insert any records that violate primary_id constraints.
    """
    temp_table_name = "TEMP_" + table_name[:20]

    # 1. Write to Temp Table
    _df_to_oracle_overwrite(engine, df, temp_table_name, primary_keys)

    # 2. Create Merge SQL
    merge_sql = _create_merge_statement(engine, temp_table_name, table_name, "ignore")
    logging.info(f"Executing MERGE (Ignore Duplicates)")

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