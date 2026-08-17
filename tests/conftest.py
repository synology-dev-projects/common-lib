# tests/conftest.py
import pytest
from sqlalchemy import true
import common_lib.config.main_config as config
import common_lib.connectors.tradingedge.dexgex as te_gd
import common_lib.connectors.tradingedge.optionflow as te_of

import logging

@pytest.fixture(scope="session")
def env_config():
    """Load config once for the whole session."""
    return config.load_config()

@pytest.fixture(scope="module")
def dex_gex_data_extract(env_config):
    """Runs exactly once per test file and caches the returned dictionary."""
    logging.info("[Setup] Running TE dexgex extraction ...")
    
    # Run your sequence sequentially
    cookie=te_gd.authenticate_and_get_cookie(env_config)
    raw_data = te_gd.extract_raw_data(env_config, cookie, "AAPL") 
    clean_data = te_gd.convert_raw_to_df(raw_data) 

    # Package everything into a dictionary so tests can inspect any stage
    shared_context = {
        "cookie": cookie,
        "raw_data": raw_data,
        "clean_data": clean_data
    }

    logging.debug(f"[Setup] Shared Context: {shared_context}")
    return shared_context

@pytest.fixture(scope="module")
def option_flow_data_extract(env_config):
    """Runs exactly once per test file and caches the returned dictionary."""
    logging.info("[Setup] Running TE dexgex extraction ...")
    
    # Run your sequence sequentially
    cookie= te_of.authenticate_and_get_cookie(env_config)
    raw_data = te_of.extract_option_flow_html(env_config, cookie) 
    clean_data = te_of.convert_html_to_df(raw_data) 
    
    # Package everything into a dictionary so tests can inspect any stage
    shared_context = {
        "cookie": cookie,
        "raw_data": raw_data,
        "clean_data": clean_data
      
    }

    logging.debug(f"[Setup] Shared Context: {shared_context}")
    return shared_context


