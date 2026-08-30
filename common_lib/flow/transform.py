import hashlib
import logging
import re
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import pandas as pd

logger = logging.getLogger("quant.pipeline.flow.transform")

def parse_premium_str(val: Any) -> float:
    """
    Parses premium strings like '14.2M', '780K', '$1.85M', '$450,000' into raw float numbers.
    """
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    
    clean_val = str(val).strip().replace("$", "").replace(",", "")
    multiplier = 1.0
    
    if clean_val.upper().endswith("M"):
        multiplier = 1_000_000.0
        clean_val = clean_val[:-1].strip()
    elif clean_val.upper().endswith("K"):
        multiplier = 1_000.0
        clean_val = clean_val[:-1].strip()
    elif clean_val.upper().endswith("B"):
        multiplier = 1_000_000_000.0
        clean_val = clean_val[:-1].strip()
        
    try:
        return float(clean_val) * multiplier
    except Exception:
        return 0.0

def parse_strike_and_otm(strike_str: Any) -> tuple[float, Optional[float]]:
    """
    Parses strike string like '185.00 (7 %)' or '140.00 (-20 %)' into (strike_price, otm_pct).
    """
    if strike_str is None or pd.isna(strike_str):
        return 0.0, None
    s = str(strike_str).strip()
    match = re.search(r"([\d\.]+)\s*(?:\(([\+\-\d\.]+)\s*%\))?", s)
    if match:
        stk = float(match.group(1))
        otm = float(match.group(2)) if match.group(2) is not None else None
        return stk, otm
    try:
        return float(s), None
    except Exception:
        return 0.0, None

def generate_flow_id(row: Dict[str, Any]) -> str:
    """
    Generates a deterministic SHA-256 hash for idempotent database upsert.
    """
    raw_str = (
        f"{row.get('trade_date')}_"
        f"{row.get('symbol')}_"
        f"{row.get('order_type')}_"
        f"{row.get('strike_price')}_"
        f"{row.get('expiration_date')}_"
        f"{row.get('premium')}"
    )
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:32]

def parse_date_val(raw_val: Any, default_val: Optional[date] = None) -> date:
    if raw_val is None or pd.isna(raw_val):
        return default_val or datetime.now().date()
    if isinstance(raw_val, (datetime, pd.Timestamp)):
        return raw_val.date()
    if isinstance(raw_val, date):
        return raw_val
    
    s = str(raw_val).strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return default_val or datetime.now().date()
        
    # Handle Excel serial day numbers (e.g. 46261 -> 2026-08-27)
    try:
        num = float(s)
        if 35000 <= num <= 60000:
            return (datetime(1899, 12, 30) + timedelta(days=num)).date()
    except (ValueError, TypeError):
        pass

    try:
        parsed = pd.to_datetime(s, errors="coerce")
        if pd.notna(parsed):
            d = parsed.date()
            if 2000 <= d.year <= 2100:
                return d
            elif d.year > 2100 or d.year < 2000:
                return d.replace(year=2000 + (d.year % 100))
    except Exception:
        pass

    return default_val or datetime.now().date()

def transform_flow_records(raw_records: List[Dict[str, Any]], net_score: Optional[float] = None) -> pd.DataFrame:
    """
    Transforms raw scraped flow items into the normalized UNUSUAL_OPTION_FLOW_TE DataFrame.
    """
    if not raw_records:
        return pd.DataFrame(columns=[
            "flow_id", "trade_date", "symbol", "order_type", "strike_price",
            "strike_otm_pct", "expiration_date", "open_interest", "is_unusual_oi",
            "premium", "net_score", "created_at"
        ])
    
    rows = []
    now_ts = datetime.now()
    
    for item in raw_records:
        symbol = str(item.get("symbol", "")).strip().upper().replace("$", "")
        if not symbol:
            continue
        
        # Trade date parsing
        trade_date_val = parse_date_val(item.get("trade_date"))
        
        # Expiration date parsing
        raw_exp = item.get("exp") or item.get("expiration_date")
        exp_date_val = parse_date_val(raw_exp, default_val=trade_date_val)
        
        # Strike & OTM
        stk_val, otm_pct = parse_strike_and_otm(item.get("strike") or item.get("strike_price"))
        
        # Order Type
        order_type_val = str(item.get("order_type", "")).strip().upper().replace(" ", "_")
        
        # Open interest & unusual flag
        raw_oi = str(item.get("oi") or item.get("open_interest", "0")).strip()
        is_unusual = 1 if ("⚠️" in raw_oi or item.get("is_unusual_oi") or "▲" in raw_oi) else 0
        clean_oi_str = re.sub(r"[^\d]", "", raw_oi)
        open_interest_val = int(clean_oi_str) if clean_oi_str else 0
        
        # Premium
        premium_val = parse_premium_str(item.get("premium"))
        
        raw_score = item.get("net_score") if item.get("net_score") is not None else net_score
        net_score_val = float(raw_score) if raw_score is not None else 0.0
        
        # Skip empty/footer rows
        if stk_val <= 0 and premium_val <= 0:
            continue

        row_dict = {
            "trade_date": trade_date_val,
            "symbol": symbol,
            "order_type": order_type_val,
            "strike_price": stk_val,
            "strike_otm_pct": otm_pct,
            "expiration_date": exp_date_val,
            "open_interest": open_interest_val,
            "is_unusual_oi": is_unusual,
            "premium": premium_val,
            "net_score": net_score_val,
            "created_at": now_ts
        }
        row_dict["flow_id"] = generate_flow_id(row_dict)
        rows.append(row_dict)
        
    df = pd.DataFrame(rows)
    return df
