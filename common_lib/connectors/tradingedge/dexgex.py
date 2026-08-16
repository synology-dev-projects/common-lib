import logging
import urllib.parse
import io
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FuncFormatter

from common_lib.config.main_config import MainConfig, load_config

_cached_session: requests.Session | None = None


def get_authenticated_session(config: MainConfig, force_refresh: bool = False) -> requests.Session | None:
    """
    Returns a cached, authenticated requests.Session instance to enable
    TCP Connection pooling (Keep-Alive) and prevent re-authenticating on every API call.
    If force_refresh is True or the session expired, re-authenticates with TradingEdge login gate.
    """
    global _cached_session
    if _cached_session is not None and not force_refresh:
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
        get_response = session.get(config.te_login_gate, timeout=10.0)
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
        post_response = session.post(config.te_login_gate, data=payload, timeout=10.0)

        if post_response.status_code in [200, 302] and "Sessions expire" not in post_response.text:
            logging.info("TradingEdge authentication successful! Session cached.")
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
    Main entry point function to retrieve clean GEX & DEX options data as a pandas DataFrame or raw dictionary.
    Defaults to max_dte=50 and strike_range=25.
    """
    config = load_config()
    session = get_authenticated_session(config)
    if not session:
        logging.error("Authentication failed. Cannot fetch data.")
        return {}

    raw_data = extract_raw_data(config, session, ticker, max_dte=max_dte, strike_range=strike_range)
    if not raw_data:
        logging.error(f"Failed to fetch raw data for ticker: {ticker}")
        return {}

    clean_df = convert_raw_to_df(raw_data)
    if clean_df is not None and not clean_df.empty:
        columns_to_keep = ['strike', 'expiration', 'exp_call_gex', 'exp_put_gex']
        df_filtered = clean_df[columns_to_keep]
        return df_filtered.to_csv(index=False)

    return raw_data


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

    response = None
    try:
        if isinstance(session_or_cookie, requests.Session):
            response = session_or_cookie.get(dex_gex_url, timeout=12.0)
        else:
            headers = {
                "Cookie": session_or_cookie,
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-CA,en;q=0.9,zh-CN;q=0.8,zh;q=0.7,en-GB;q=0.6,en-US;q=0.5"
            }
            response = requests.get(dex_gex_url, headers=headers, timeout=12.0)

        if response.status_code == 200:
            try:
                raw_data = response.json()
                if isinstance(raw_data, dict) and ("ticker" in raw_data or "spot_price" in raw_data or "spotPrice" in raw_data):
                    logging.info(f"Success: 200 retrieved live data for {ticker}")
                    return raw_data
            except Exception:
                pass  # Fall through to automatic session recovery
    except Exception as req_err:
        logging.warning(f"Initial request to TradingEdge failed for {ticker}: {req_err}")

    # Session recovery: Automatically re-authenticate and retry once
    logging.warning(f"TradingEdge session expired or returned invalid payload for {ticker}. Triggering automatic re-authentication...")
    fresh_session = get_authenticated_session(config, force_refresh=True)
    if fresh_session:
        try:
            retry_resp = fresh_session.get(dex_gex_url, timeout=15.0)
            if retry_resp.status_code == 200:
                raw_data = retry_resp.json()
                if isinstance(raw_data, dict):
                    logging.info(f"Success: 200 retrieved live data for {ticker} after re-authentication!")
                    return raw_data
        except Exception as retry_err:
            logging.error(f"Failed to fetch live data on retry after re-authentication for {ticker}: {retry_err}")

    return None


def authenticate_and_get_cookie(config: MainConfig) -> str | None:
    session = get_authenticated_session(config)
    if session:
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


def generate_gexdex_chart(
    df: pd.DataFrame,
    ticker: str | None = None,
    spot_price: float | None = None,
    call_wall: float | None = None,
    put_wall: float | None = None,
    call_put_ratio: float | None = None,
    format: str = "png"
) -> bytes:
    """
    Generates a high-definition, dark-mode double-sided bi-directional horizontal bar chart
    visualizing Gamma Exposure (GEX) and Delta Exposure (DEX) by strike and expiration.
    
    CALLS extend to the LEFT of zero (-x), PUTS extend to the RIGHT of zero (+x).
    
    :param df: DataFrame containing options chain exposure metrics.
    :param ticker: Stock ticker symbol (optional override).
    :param spot_price: Spot price (optional override).
    :param call_wall: Major call wall strike (optional override).
    :param put_wall: Major put wall strike (optional override).
    :param call_put_ratio: Call/Put ratio (optional override).
    :return: PNG image binary bytes.
    """
    if df is None or df.empty:
        raise ValueError("Cannot generate GEX/DEX chart from an empty DataFrame.")

    df_copy = df.copy()

    # Extract scalar metadata from DataFrame columns if present, otherwise fallback to defaults
    symbol = ticker or (df_copy['ticker'].iloc[0] if 'ticker' in df_copy.columns and not pd.isna(df_copy['ticker'].iloc[0]) else "AAPL")
    symbol = str(symbol).upper().strip()

    spot = spot_price if spot_price is not None else (float(df_copy['spot_price'].iloc[0]) if 'spot_price' in df_copy.columns and not pd.isna(df_copy['spot_price'].iloc[0]) else 311.21)
    c_wall = call_wall if call_wall is not None else (float(df_copy['call_wall'].iloc[0]) if 'call_wall' in df_copy.columns and not pd.isna(df_copy['call_wall'].iloc[0]) else spot * 1.03)
    p_wall = put_wall if put_wall is not None else (float(df_copy['put_wall'].iloc[0]) if 'put_wall' in df_copy.columns and not pd.isna(df_copy['put_wall'].iloc[0]) else spot * 0.97)
    cp_ratio = call_put_ratio if call_put_ratio is not None else (float(df_copy['call_put_ratio'].iloc[0]) if 'call_put_ratio' in df_copy.columns and not pd.isna(df_copy['call_put_ratio'].iloc[0]) else 0.47)

    # Standardize expiration string column
    if 'exp_str' not in df_copy.columns:
        if 'expiration' in df_copy.columns:
            df_copy['exp_str'] = pd.to_datetime(df_copy['expiration']).dt.strftime('%Y-%m-%d')
        else:
            df_copy['exp_str'] = '2026-08-01'

    strikes = np.array(sorted(df_copy['strike'].unique())) if 'strike' in df_copy.columns else np.arange(250, 375, 2.5)
    expirations = sorted(df_copy['exp_str'].unique())[:11]

    palette = [
        '#d90429', '#ef233c', '#f77f00', '#fcbf49', '#e0a96d',
        '#1d3557', '#457b9d', '#3a86ff', '#2a9d8f', '#495057', '#6c757d'
    ]
    colors = palette[:len(expirations)]

    def get_series_values(sub_df: pd.DataFrame, col1: str, col2: str, strikes_arr: np.ndarray) -> np.ndarray:
        if col1 in sub_df.columns:
            return sub_df[col1].fillna(0).values
        elif col2 in sub_df.columns:
            return sub_df[col2].fillna(0).values
        return np.zeros(len(strikes_arr))

    # --- Pre-calculate Raw Exposure Magnitudes for Dynamic Scaling ---
    raw_call_gex_tot = np.zeros(len(strikes))
    raw_put_gex_tot = np.zeros(len(strikes))
    raw_call_dex_tot = np.zeros(len(strikes))
    raw_put_dex_tot = np.zeros(len(strikes))

    for exp in expirations:
        sub_df = df_copy[df_copy["exp_str"] == exp].set_index("strike").reindex(strikes).fillna(0)
        raw_call_gex_tot += np.abs(get_series_values(sub_df, "call_gex", "exp_call_gex", strikes))
        raw_put_gex_tot += np.abs(get_series_values(sub_df, "put_gex", "exp_put_gex", strikes))
        raw_call_dex_tot += np.abs(get_series_values(sub_df, "call_dex", "exp_call_dex", strikes))
        raw_put_dex_tot += np.abs(get_series_values(sub_df, "put_dex", "exp_put_dex", strikes))

    max_gex_val = max(raw_call_gex_tot.max(), raw_put_gex_tot.max(), 1.0)
    max_dex_val = max(raw_call_dex_tot.max(), raw_put_dex_tot.max(), 1.0)

    # Dynamic unit scale determination (Billions >= 1e9, Millions < 1e9)
    gex_unit_scale = 1e9 if max_gex_val >= 1e9 else 1e6
    gex_unit_label = "B" if max_gex_val >= 1e9 else "M"

    dex_unit_scale = 1e9 if max_dex_val >= 1e9 else 1e6
    dex_unit_label = "B" if max_dex_val >= 1e9 else "M"

    # Setup Dark Theme Figure Layout
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(15, 9), facecolor='#0f141d')
    
    gs = fig.add_gridspec(1, 2, left=0.10, right=0.82, top=0.84, bottom=0.10, wspace=0.18)
    ax1 = fig.add_subplot(gs[0, 0], facecolor='#151a24')
    ax2 = fig.add_subplot(gs[0, 1], facecolor='#151a24', sharey=ax1)

    # Calculate bar height based on strike spacing
    strike_diffs = np.diff(strikes)
    min_diff = strike_diffs.min() if len(strike_diffs) > 0 else 2.5
    bar_height = min_diff * 0.75

    # --- PLOT 1: GEX ---
    bottom_call_gex = np.zeros(len(strikes))
    bottom_put_gex = np.zeros(len(strikes))

    for exp, color in zip(expirations, colors):
        sub_df = df_copy[df_copy["exp_str"] == exp].set_index("strike").reindex(strikes).fillna(0)
        c_gex = np.abs(get_series_values(sub_df, "call_gex", "exp_call_gex", strikes)) / gex_unit_scale
        p_gex = np.abs(get_series_values(sub_df, "put_gex", "exp_put_gex", strikes)) / gex_unit_scale

        ax1.barh(strikes, -c_gex, left=-bottom_call_gex, color=color, height=bar_height, edgecolor='none')
        ax1.barh(strikes, p_gex, left=bottom_put_gex, color=color, height=bar_height, edgecolor='none')

        bottom_call_gex += c_gex
        bottom_put_gex += p_gex

    # --- PLOT 2: DEX ---
    bottom_call_dex = np.zeros(len(strikes))
    bottom_put_dex = np.zeros(len(strikes))

    for exp, color in zip(expirations, colors):
        sub_df = df_copy[df_copy["exp_str"] == exp].set_index("strike").reindex(strikes).fillna(0)
        c_dex = np.abs(get_series_values(sub_df, "call_dex", "exp_call_dex", strikes)) / dex_unit_scale
        p_dex = np.abs(get_series_values(sub_df, "put_dex", "exp_put_dex", strikes)) / dex_unit_scale

        ax2.barh(strikes, -c_dex, left=-bottom_call_dex, color=color, height=bar_height, edgecolor='none')
        ax2.barh(strikes, p_dex, left=bottom_put_dex, color=color, height=bar_height, edgecolor='none')

        bottom_call_dex += c_dex
        bottom_put_dex += p_dex

    max_gex_scaled = max(bottom_call_gex.max(), bottom_put_gex.max(), 1.0) * 1.15
    ax1.set_xlim(-max_gex_scaled, max_gex_scaled)

    max_dex_scaled = max(bottom_call_dex.max(), bottom_put_dex.max(), 1.0) * 1.15
    ax2.set_xlim(-max_dex_scaled, max_dex_scaled)

    # Strike Y-Tick Selection: Clean integer tick spacing every 2 or 5 strikes
    if len(strikes) > 0:
        step = max(1, len(strikes) // 20)
        ytick_positions = strikes[::step]
        min_s, max_s = strikes.min(), strikes.max()
        padding = (max_s - min_s) * 0.04 if max_s > min_s else 2.5
        ax1.set_ylim(min_s - padding, max_s + padding)
    else:
        ytick_positions = strikes

    for ax in (ax1, ax2):
        ax.axhline(spot, color='#3a86ff', linestyle='--', linewidth=1.5, zorder=5)
        ax.axhline(c_wall, color='#00f5d4', linestyle='--', linewidth=1.5, zorder=5)
        ax.axhline(p_wall, color='#ff006e', linestyle='--', linewidth=1.5, zorder=5)
        ax.axvline(0, color='#6c757d', linestyle='-', linewidth=1.2, zorder=4)
        ax.grid(True, color='#212836', linestyle=':', linewidth=0.8)
        ax.set_yticks(ytick_positions)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{int(y)}" if y == int(y) else f"{y:.1f}"))
        ax.tick_params(colors='#a0aec0', labelsize=9)

    def gex_formatter(val_x, pos):
        val = abs(val_x)
        if val == 0:
            return "0"
        return f"{val:.0f}{gex_unit_label}"

    def dex_formatter(val_x, pos):
        val = abs(val_x)
        if val == 0:
            return "0"
        return f"{val:.0f}{dex_unit_label}"

    ax1.xaxis.set_major_formatter(FuncFormatter(gex_formatter))
    ax2.xaxis.set_major_formatter(FuncFormatter(dex_formatter))

    ax1.text(0.18, 0.94, 'CALLS', transform=ax1.transAxes, color='#ffffff', fontsize=9, fontweight='bold', bbox=dict(boxstyle='square,pad=0.3', facecolor='#212836', edgecolor='none'))
    ax1.text(0.78, 0.94, 'PUTS', transform=ax1.transAxes, color='#ffffff', fontsize=9, fontweight='bold', bbox=dict(boxstyle='square,pad=0.3', facecolor='#212836', edgecolor='none'))
    
    ax2.text(0.18, 0.94, 'CALLS', transform=ax2.transAxes, color='#ffffff', fontsize=9, fontweight='bold', bbox=dict(boxstyle='square,pad=0.3', facecolor='#212836', edgecolor='none'))
    ax2.text(0.78, 0.94, 'PUTS', transform=ax2.transAxes, color='#ffffff', fontsize=9, fontweight='bold', bbox=dict(boxstyle='square,pad=0.3', facecolor='#212836', edgecolor='none'))

    ax1.set_xlabel("Gamma Exposure (GEX)", color='#cbd5e0', fontsize=11, fontweight='bold', labelpad=10)
    ax2.set_xlabel("Delta Exposure (DEX)", color='#cbd5e0', fontsize=11, fontweight='bold', labelpad=10)
    ax1.set_ylabel("Strike Price", color='#cbd5e0', fontsize=11, fontweight='bold')

    fig.suptitle(f"{symbol} GEX DEX Chart", color='#ffffff', fontsize=16, fontweight='bold', y=0.91)

    fig.text(0.10, 0.94, f"Spot Price\n${spot:.2f}", color='#ffffff', fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.5', facecolor='#181e2a', edgecolor='#2d3748'))
    fig.text(0.48, 0.94, f"Call/Put Ratio\n{cp_ratio:.2f}", color='#ff758f', fontsize=12, fontweight='bold', bbox=dict(boxstyle='round,pad=0.5', facecolor='#181e2a', edgecolor='#2d3748'))

    legend_patches = [mpatches.Patch(color=c, label=exp) for exp, c in zip(expirations, colors)]
    leg1 = fig.legend(handles=legend_patches, title="Expiries", loc="upper right", bbox_to_anchor=(0.96, 0.85), facecolor='#151a24', edgecolor='#2d3748', fontsize=8, title_fontsize=9)
    plt.setp(leg1.get_title(), color='#ffffff', fontweight='bold')
    for text in leg1.get_texts():
        text.set_color('#cbd5e0')

    summary_text = f"Spot: ${spot:.2f}\nCall Wall: ${c_wall:.2f}\nPut Wall: ${p_wall:.2f}"
    fig.text(0.84, 0.12, summary_text, color='#ffffff', fontsize=9, bbox=dict(boxstyle='square,pad=0.6', facecolor='#151a24', edgecolor='#2d3748'), family='monospace')

    buf = io.BytesIO()
    img_fmt = format.lower() if format else 'png'
    try:
        plt.savefig(buf, format=img_fmt, dpi=130, bbox_inches='tight', facecolor=fig.get_facecolor())
    except Exception:
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
