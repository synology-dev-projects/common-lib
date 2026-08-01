from common_lib.connectors.tradingedge.dexgex import extract_raw_data, authenticate_and_get_cookie, convert_raw_to_df
import pytest
import logging
import pandas as pd



def test_auth_is_valid(dex_gex_data_extract):
    cookie_val = dex_gex_data_extract["cookie"]
    assert cookie_val is not None

def test_raw_data_contains_ticker(dex_gex_data_extract):
    raw_data = dex_gex_data_extract["raw_data"]
    assert "ticker" in raw_data

def test_clean_data_math(dex_gex_data_extract):
    clean_data = dex_gex_data_extract["clean_data"]
    assert isinstance(clean_data, pd.DataFrame) and not clean_data.empty, "clean_data must be a non-empty DataFrame"