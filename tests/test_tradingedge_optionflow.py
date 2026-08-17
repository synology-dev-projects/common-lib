from common_lib.connectors.tradingedge.optionflow import extract_option_flow_html, authenticate_and_get_cookie
import logging
import pandas as pd



def test_auth_is_valid(option_flow_data_extract):
    cookie_val = option_flow_data_extract["cookie"]
    assert cookie_val is not None

def test_raw_data_contains_ticker(option_flow_data_extract):
    raw_data = option_flow_data_extract["raw_data"]
    assert raw_data is not None

def test_clean_data_math(option_flow_data_extract):
    clean_data = option_flow_data_extract["clean_data"]
    assert isinstance(clean_data, pd.DataFrame) and not clean_data.empty, "clean_data must be a non-empty DataFrame"