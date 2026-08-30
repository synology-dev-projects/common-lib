import pytest
from common_lib.connectors.tradingedge.optionflow import extract_option_flow_html, authenticate_and_get_cookie, convert_html_to_df
import logging
import pandas as pd


@pytest.mark.integration
def test_auth_is_valid(option_flow_data_extract):
    cookie_val = option_flow_data_extract["cookie"]
    assert cookie_val is not None


@pytest.mark.integration
def test_raw_data_contains_ticker(option_flow_data_extract):
    raw_data = option_flow_data_extract["raw_data"]
    assert raw_data is not None


@pytest.mark.integration
def test_clean_data_math(option_flow_data_extract):
    clean_data = option_flow_data_extract["clean_data"]
    assert isinstance(clean_data, pd.DataFrame) and not clean_data.empty, "clean_data must be a non-empty DataFrame"


def test_convert_html_to_df_offline():
    mock_html = """
    <html><body>
        <table><tbody>
            <tr>
                <td>NVDA</td>
                <td data-value="135.0"><span value="5.0">+5%</span></td>
                <td data-value="26-09-18">9/18/26</td>
                <td data-value="5000000">5.0M</td>
            </tr>
        </tbody></table>
        <table><tbody></tbody></table>
        <table><tbody></tbody></table>
        <table><tbody></tbody></table>
    </body></html>
    """
    df = convert_html_to_df(mock_html)
    assert df is not None
    assert len(df) == 1
    assert df.iloc[0]['ticker'] == 'NVDA'
    assert df.iloc[0]['strike'] == 135.0
    assert df.iloc[0]['option_type'] == 'Call'
    assert df.iloc[0]['action'] == 'Buy'


def test_convert_html_to_df_none():
    assert convert_html_to_df(None) is None