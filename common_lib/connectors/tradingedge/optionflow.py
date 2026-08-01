import requests
from bs4 import BeautifulSoup
from common_lib.config.main_config import MainConfig
import logging
import pandas as pd


def extract_option_flow_html(config: MainConfig
                             , cookie: str | None) -> str | None:
    # URL containing the date parameter from your network log
    url = config.te_option_flow_url

    # Use the exact same Cookie string from your previous successful requests
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    raw_html = str(soup)

    if response.status_code == 200:
        logging.debug(f"Raw Data: {raw_html[:500]}")
        return raw_html

    else:
        logging.error(f"Failed Raw Data Extraction: {response.status_code}")
        return None

def authenticate_and_get_cookie(config: MainConfig) -> str | None:
    session = requests.Session()
    login_url = config.te_option_login_gate
    # 1. GET request to fetch the initial ASP.NET state tokens
    response = session.get(login_url)
    soup = BeautifulSoup(response.text, 'html.parser')

    viewstate = soup.find('input', {'id': '__VIEWSTATE'})
    viewstate_gen = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
    event_validation = soup.find('input', {'id': '__EVENTVALIDATION'})

    # 2. Map the exact keys from your Form Data screenshot
    payload = {
        '__EVENTTARGET': '',
        '__EVENTARGUMENT': '',
        '__VIEWSTATE': viewstate.get('value') if viewstate else '',
        '__VIEWSTATEGENERATOR': viewstate_gen.get('value') if viewstate_gen else '',
        '__EVENTVALIDATION': event_validation.get('value') if event_validation else '',
        'm_userName': 'GoWithTheFlow',  # Matches the key holding your passcode/string
        'm_btnLogin': 'Confirm Identity' # Matches the exact text of your button
    }
    # 4. POST the complete state payload back to the login page
    post_response = session.post(login_url, data=payload)

    cookie_val = post_response.request.headers.get('Cookie')
    logging.debug(f"Cookie: {cookie_val}")

    if post_response.status_code in [200, 302]:
        logging.info("Successfully authenticated with ASP.NET server.")
        return cookie_val
    else:
        logging.error(f"Authentication failed: {post_response.status_code}")
        return None
    
def convert_html_to_df(html_content: str | None) -> pd.DataFrame | None:
    
    if html_content is None:
        return None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    
    # Map the first 4 tables to their Option Type and Action
    table_mapping = [
        (0, 'Call', 'Buy'),
        (1, 'Put',  'Sell'),
        (2, 'Put',  'Buy'),
        (3, 'Call', 'Sell')
    ]
    
    parsed_data = []
    
    for table_idx, option_type, action in table_mapping:
        table = tables[table_idx]
        tbody = table.find('tbody')
        if not tbody:
            continue
            
        for row in tbody.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) < 4:
                continue
            
            # Extract the raw numbers from the hidden HTML attributes
            ticker = cols[0].get_text(strip=True)
            strike = cols[1].get('data-value')
            span_tag = cols[1].find('span')
            moneyness = span_tag.get('value') if span_tag else None
            expiration = cols[2].get('data-value')
            premium = cols[3].get('data-value')
            
            parsed_data.append({
                'ticker': ticker,
                'option_type': option_type,
                'action': action,
                'strike': strike,
                'moneyness_pct': moneyness,
                'expiration': expiration,
                'premium': premium
            })
            
    # Build the DataFrame
    df = pd.DataFrame(parsed_data)
    
    # Convert columns to optimal numeric/datetime types
    df['strike'] = df['strike'].astype('float32')
    df['moneyness_pct'] = df['moneyness_pct'].astype('float32')
    df['premium'] = df['premium'].astype('float64')
    df['expiration'] = pd.to_datetime(df['expiration'], format='%y-%m-%d')
    
    # Optimize memory usage for low-cardinality text columns
    for col in ['ticker', 'option_type', 'action']:
        df[col] = df[col].astype('category')

    logging.debug(f"Clean Data: {df}")
        
    return df

