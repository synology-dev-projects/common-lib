import logging
import urllib.parse
import requests
from bs4 import BeautifulSoup
import pandas as pd
from common_lib.config.main_config import MainConfig, load_config

_cached_session: requests.Session | None = None


def get_authenticated_session(config: MainConfig) -> requests.Session | None:
    """
    Returns a cached, authenticated requests.Session instance to enable
    TCP Connection pooling (Keep-Alive) and prevent re-authenticating on every API call.
    """
    global _cached_session
    if _cached_session is not None:
        return _cached_session

    session = requests.Session()
    session.headers.update({
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-CA,en;q=0.9,zh-CN;q=0.8,zh;q=0.7,en-GB;q=0.6,en-US;q=0.5",
        "User-Agent": config.te_user_agent
    })

    # 1. GET request to load the login page and grab the CSRF token
    try:
        get_response = session.get(config.te_login_gate)
        soup = BeautifulSoup(get_response.text, 'html.parser')
        token_input = soup.find('input', {'name': '_token'})

        if not token_input:
            logging.error("Failed to find _token on the page.")
            return None

        fresh_token = token_input.get('value')
        payload = {
            '_token': fresh_token,
            'password': config.te_pass.get_secret_value()
        }

        # 2. POST the payload to authenticate
        post_response = session.post(config.te_login_gate, data=payload)

        if post_response.status_code in [200, 302] and "Sessions expire" not in post_response.text:
            logging.info("Authentication successful! Session cached.")
            _cached_session = session
            return _cached_session
        else:
            logging.error(f"Authentication failed. Status: {post_response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Session authentication error: {e}")
        return None


def get_mm_dex_gex_data(ticker: str, max_dte: int = 50, strike_range: int = 25) -> str | dict:
    """
    Retrieves flattened Market Maker Gamma Exposure (GEX) and Delta Exposure (DEX) option chain data.

    Args:
        ticker: The equity or ETF ticker symbol (e.g., 'AAPL', 'SPY').
        max_dte: The maximum days to expiration to include in the chain. Defaults to 50.
        strike_range: The number of strikes above and below the spot price to include. Defaults to 25.
    """
    config = load_config()
    session = get_authenticated_session(config)

    raw_data = extract_raw_data(config, session, ticker, max_dte, strike_range)
    clean_df = convert_raw_to_df(raw_data)

    if clean_df is None or clean_df.empty:
        return {
            "error": f"No data returned for ticker {ticker}",
            "status": "unavailable"
        }
    else:
        columns_to_keep = ['strike', 'expiration', 'exp_call_gex', 'exp_put_gex']
        df_filtered = clean_df[columns_to_keep]
        return df_filtered.to_csv(index=False)


def extract_raw_data(
    config: MainConfig,
    session_or_cookie: requests.Session | str | None,
    ticker: str,
    max_dte=50,
    strike_range=25
):
    dex_gex_base_url = config.te_dex_gex_url
    url_params = {
        "ticker": ticker,
        "max_dte": max_dte,
        "strike_range": strike_range
    }
    query_string = urllib.parse.urlencode(url_params)
    dex_gex_url = f"{dex_gex_base_url}?{query_string}"

    if isinstance(session_or_cookie, requests.Session):
        response = session_or_cookie.get(dex_gex_url)
    else:
        headers = {
            "Cookie": session_or_cookie,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-CA,en;q=0.9,zh-CN;q=0.8,zh;q=0.7,en-GB;q=0.6,en-US;q=0.5"
        }
        response = requests.get(dex_gex_url, headers=headers)

    if response.status_code == 200:
        logging.info(f"Success: {response.status_code} retrieved data for {ticker}")
        raw_data = response.json()
        logging.debug(f"Raw Data: {raw_data}")
        return raw_data
    else:
        logging.error(f"Failed: {response.status_code} - {response.text}")
        return None


def authenticate_and_get_cookie(config: MainConfig) -> str | None:
    session = get_authenticated_session(config)
    if session:
        # Extract cookie string for callers requiring string format
        return "; ".join([f"{k}={v}" for k, v in session.cookies.items()])
    return None


def convert_raw_to_df(data: dict | None) -> pd.DataFrame | None:
    if data is None:
        return None

    rows = []
    for strike_node in data.get('strikes', []):
        strike = strike_node['strike']
        for exp_date, metrics in strike_node['expirations'].items():
            rows.append({
                'strike': strike,
                'expiration': pd.to_datetime(exp_date),
                'exp_call_dex': metrics.get('call_dex', 0),
                'exp_put_dex': metrics.get('put_dex', 0),
                'exp_call_gex': metrics.get('call_gex', 0),
                'exp_put_gex': metrics.get('put_gex', 0)
            })

    df_granular = pd.DataFrame(rows)

    df_rolling = pd.DataFrame(data.get('rolling', {}))
    df_rolling.rename(columns={
        'strikes': 'strike',
        'call_dex': 'roll_call_dex',
        'put_dex': 'roll_put_dex',
        'call_gex': 'roll_call_gex',
        'put_gex': 'roll_put_gex'
    }, inplace=True)

    df = pd.merge(df_granular, df_rolling, on='strike', how='left')

    global_scalars = [
        'ticker', 'spot_price', 'call_put_ratio',
        'call_split_ratio', 'gex_wall', 'call_wall', 'put_wall'
    ]
    for key in global_scalars:
        df[key] = data.get(key)

    df = df.sort_values(by=['expiration', 'strike']).reset_index(drop=True)
    logging.debug(f"Clean data: {df}")

    df_filtered = df[(df['exp_call_gex'] != 0) | (df['exp_put_gex'] != 0)]
    if isinstance(df_filtered, pd.DataFrame):
        return df_filtered
    return pd.DataFrame(df_filtered)
