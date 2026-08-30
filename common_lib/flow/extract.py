import logging
import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
from bs4 import BeautifulSoup
from common_lib.config.main_config import MainConfig

logger = logging.getLogger("quant.pipeline.flow.extract")

def get_authenticated_flow_session(config: MainConfig) -> requests.Session:
    """
    Creates an authenticated requests.Session for TradingEdge Flow using ASP.NET ViewState & m_userName.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": getattr(config, "te_user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        "Referer": "https://flow.tradingedge.club/"
    })
    
    login_url = getattr(config, "te_option_login_gate", "https://flow.tradingedge.club/Login.aspx?ReturnUrl=%2fdefault.aspx")
    te_pass = config.te_pass.get_secret_value() if hasattr(config.te_pass, "get_secret_value") else str(config.te_pass)
    
    try:
        r_get = session.get(login_url, timeout=15)
        soup = BeautifulSoup(r_get.text, "html.parser")
        
        viewstate = soup.find("input", id="__VIEWSTATE")
        viewstate_gen = soup.find("input", id="__VIEWSTATEGENERATOR")
        event_val = soup.find("input", id="__EVENTVALIDATION")
        
        payload = {
            "__VIEWSTATE": viewstate.get("value", "") if viewstate else "",
            "__VIEWSTATEGENERATOR": viewstate_gen.get("value", "") if viewstate_gen else "",
            "__EVENTVALIDATION": event_val.get("value", "") if event_val else "",
            "m_userName": te_pass,
            "m_btnLogin": "Confirm Identity"
        }
        
        post_url = "https://flow.tradingedge.club" + r_get.url.split("tradingedge.club")[-1]
        session.post(post_url, data=payload, timeout=15)
        logger.info("Successfully authenticated with TradingEdge Flow gate.")
    except Exception as ex:
        logger.warning(f"Flow login gate encounter: {ex}")
        
    return session

def parse_html_flow_table(html_content: str, symbol: Optional[str] = None) -> tuple[List[Dict[str, Any]], Optional[float]]:
    """
    Parses TradingEdge Flow HTML table into raw record dictionaries.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    records = []
    net_score = None
    
    score_elem = soup.find(class_=re.compile(r"net.*score|score", re.I)) or soup.find(string=re.compile(r"Net Score", re.I))
    if score_elem:
        parent = score_elem.parent
        match = re.search(r"([\+\-]?\d+(?:\.\d+)?)", parent.get_text() if parent else "")
        if match:
            try:
                net_score = float(match.group(1))
            except Exception:
                pass
                
    table = soup.find("table")
    if not table:
        return records, net_score
        
    rows = table.find_all("tr")
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
            
        texts = [td.get_text(strip=True) for td in tds]
        
        raw_trade_date = texts[0]
        raw_order_type = texts[1]
        # Clean prefix like 'CBuy Call' -> 'Buy Call' or 'PBuy Put' -> 'Buy Put'
        clean_order_type = re.sub(r"^[CP]", "", raw_order_type).strip() if raw_order_type else "Buy Call"
        
        raw_symbol = texts[2] if len(texts) > 2 else (symbol or "")
        raw_strike = texts[3] if len(texts) > 3 else ""
        raw_exp = texts[4] if len(texts) > 4 else ""
        raw_oi = texts[5] if len(texts) > 5 else ""
        raw_premium = texts[6] if len(texts) > 6 else ""
        
        is_unusual_oi = 1 if (tds[5].find("svg") or "▲" in raw_oi or "⚠️" in raw_oi) else 0
        
        records.append({
            "trade_date": raw_trade_date,
            "order_type": clean_order_type,
            "symbol": raw_symbol or symbol,
            "strike": raw_strike,
            "exp": raw_exp,
            "oi": raw_oi,
            "is_unusual_oi": is_unusual_oi,
            "premium": raw_premium,
            "net_score": net_score
        })
        
    return records, net_score

def extract_flow_for_symbol(
    config: MainConfig, 
    symbol: str, 
    cutoff_date: Optional[date] = None, 
    days: Optional[int] = None,
    session: Optional[requests.Session] = None
) -> List[Dict[str, Any]]:
    """
    Fetches raw flow records for a single symbol from TradingEdge Flow (Symbol.aspx?Id={symbol}&Days={lookback_days}).
    """
    sess = session or get_authenticated_flow_session(config)
    sym = symbol.strip().upper()
    
    if days is not None:
        lookback_days = days
    elif cutoff_date is not None:
        delta = (date.today() - cutoff_date).days
        lookback_days = max(delta + 5, 30)
    else:
        lookback_days = 730
        
    url = f"https://flow.tradingedge.club/Symbol.aspx?Id={sym}&Days={lookback_days}"
    
    try:
        resp = sess.get(url, timeout=20)
        if resp.status_code == 200:
            records, score = parse_html_flow_table(resp.text, symbol=sym)
            
            if cutoff_date:
                filtered = []
                for rec in records:
                    try:
                        rec_date = pd.to_datetime(rec["trade_date"]).date()
                        if rec_date > cutoff_date:
                            filtered.append(rec)
                    except Exception:
                        filtered.append(rec)
                return filtered
            return records
    except Exception as ex:
        logger.error(f"Error fetching flow for {symbol}: {ex}")
    return []

BENCHMARK_UNIVERSE: List[str] = [
    "SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "META", "AMZN", "GOOGL", "MSFT",
    "PLTR", "AMD", "TSM", "SMCI", "COIN", "CRWD", "NFLX", "AVGO", "ARM", "MSTR"
]

def extract_all_displayed_symbols(config: MainConfig, session: Optional[requests.Session] = None) -> List[str]:
    """
    Scrapes TradingEdge Flow default and stats pages to extract all currently displayed ticker symbols.
    Combines with BENCHMARK_UNIVERSE and returns a deduplicated, sorted list of upper-cased tickers.
    """
    sess = session or get_authenticated_flow_session(config)
    symbols_set = set(BENCHMARK_UNIVERSE)
    
    scrape_urls = [
        "https://flow.tradingedge.club/default.aspx",
        "https://flow.tradingedge.club/Stats.aspx?Stat=Premium&Days=30",
        "https://flow.tradingedge.club/Stats.aspx?Stat=Bull&Days=30",
        "https://flow.tradingedge.club/Stats.aspx?Stat=Bear&Days=30",
    ]
    
    for url in scrape_urls:
        try:
            resp = sess.get(url, timeout=20)
            if resp.status_code == 200:
                matches = re.findall(r"Symbol\.aspx\?Id=([A-Za-z0-9_\-\.]+)", resp.text, flags=re.IGNORECASE)
                for m in matches:
                    clean = m.strip().upper().replace("$", "")
                    if clean:
                        symbols_set.add(clean)
            else:
                logger.warning(f"Failed to fetch {url} (status code: {resp.status_code})")
        except Exception as ex:
            logger.warning(f"Error scraping symbols from {url}: {ex}")
            
    return sorted(list(symbols_set))

