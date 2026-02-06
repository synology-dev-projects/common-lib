import logging

import pandas as pd
import unittest

import src.connectors.oracle as oracle


def test_quant_oracle_schema(env_config):
    """
        Validates that the DataFrame matches the required schema.
        Raises an AssertionError if the schema is incorrect.
        """
    # 1. Define Expected Columns in order

    expected_cols = [
        "DATETIME",
        "TICKER",
        "START_LVL_PRICE",
        "END_LVL_PRICE",
        "COMMENTS",
        "BUY_SELL_IND",
        "WEB_LINK"
    ]

    df = oracle.sql(env_config, f"SELECT * FROM {env_config.oracle_quant_table_name}")
    logging.debug(df.info())

    # Check 1: Are columns exactly right?
    cols_match = sorted(list(df.columns)) == sorted(expected_cols)
    assert cols_match, f"Column mismatch.\nExpected: {expected_cols}\nGot: {list(df.columns)}"

    # Check 2: Are data types correct? (If df is not empty)

    assert pd.api.types.is_datetime64_any_dtype(df["DATETIME"]), "DATETIME column is not datetime type"
    assert pd.api.types.is_string_dtype(df["TICKER"]), "TICKER column is not string/object type"
    assert pd.api.types.is_float_dtype(df["START_LVL_PRICE"]), "START_LVL_PRICE is not float"
    assert pd.api.types.is_float_dtype(df["END_LVL_PRICE"]), "END_LVL_PRICE is not float"
    # Strings are often stored as 'object' or 'string' in pandas
    assert pd.api.types.is_string_dtype(df["COMMENTS"]) or pd.api.types.is_string_dtype(
        df["TICKER"]), "TICKER is not string/object"
    assert pd.api.types.is_string_dtype(df["BUY_SELL_IND"]) or pd.api.types.is_string_dtype(
        df["TICKER"]), "TICKER is not string/object"
    assert pd.api.types.is_string_dtype(df["WEB_LINK"]) or pd.api.types.is_string_dtype(
        df["TICKER"]), "TICKER is not string/object"



def test_write_to_oracle_upsert(env_config):
    """
    Checks if no constraints exceptions raised when inserting
    Passes if new records are found
    """

    df1 = pd.DataFrame({
        'symbol': ['SPY', 'SPY', 'SPY'],
        'datetime': pd.to_datetime(['2023-01-15', '2023-01-16', '2023-01-17']),
        'price': [12.50, 12.75, 13.00]
    })
    df2 = pd.DataFrame({
        'symbol': ['SPY', 'SPY', 'SPY'],
        'datetime': pd.to_datetime(['2023-01-15', '2023-01-16', '2023-01-17']),
        'price': [1.00, 2.00, 3.00]
    })


    oracle.insert_into_table(env_config, df1, "ticker_test", "overwrite", ["DATETIME", "SYMBOL"])
    oracle.insert_into_table(env_config, df2, "ticker_test", "upsert", ["DATETIME", "SYMBOL"])

    returned_df = oracle.sql(env_config, "SELECT * FROM ticker_test")
    oracle.drop_table_if_exists(env_config, "ticker_test")

    print(returned_df)

    assert (pd.DataFrame.equals(df1, returned_df)) is False


def test_write_to_oracle_insert_ignore(env_config):
    """
    Checks if records are being ignored when inserting
    Passes if no exception raised and df remains the same
    """
    df1 = pd.DataFrame({
        'SYMBOL': ['SPY', 'SPY', 'SPY'],
        'DATETIME': pd.to_datetime(['2023-01-15', '2023-01-16', '2023-01-17']),
        'PRICE': [12.50, 12.75, 13.00]
    })

    df2 = pd.DataFrame({
        'SYMBOL': ['SPY', 'SPY', 'SPY', 'SPY'],
        'DATETIME': pd.to_datetime(['2023-01-15', '2023-01-16', '2023-01-17', '2023-01-18']),
        'PRICE': [1.00, 2.00, 3.00, 4.00]
    })

    oracle.insert_into_table(env_config, df1, "ticker_test", "overwrite", ["DATETIME", "SYMBOL"])
    oracle.insert_into_table(env_config, df2, "ticker_test", "ignore", ["DATETIME", "SYMBOL"])

    returned_df = oracle.sql(env_config, "SELECT * FROM ticker_test")
    oracle.drop_table_if_exists(env_config, "ticker_test")


    print(returned_df)

    merged_df = returned_df.merge(df1, on=['SYMBOL', 'DATETIME', 'PRICE'], how='left', indicator=True)

    assert len(returned_df) == 4
    assert (pd.DataFrame.equals(df1, merged_df[merged_df['_merge'] == 'both'].drop('_merge', axis=1)))

# TODO: List of possbile test: datatypes dont change from df to oracle (and vice versa), integrity checks before hand

if __name__ == '__main__':
    unittest.main()