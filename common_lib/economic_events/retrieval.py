"""
Retrieval module for Economic Events.
Performs deterministic, ultra-fast (<2ms) relational SQL filtering by country,
impact tier, and temporal window.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import sqlalchemy as sa

logger = logging.getLogger("quant.common_lib.economic_events.retrieval")

# Universal mapping of tickers to primary base economy/currency
TICKER_CURRENCY_MAP = {
    # US Major Indices & Mega Caps
    "SPY": "USD", "QQQ": "USD", "IWM": "USD", "DIA": "USD",
    "NVDA": "USD", "AAPL": "USD", "MSFT": "USD", "AMZN": "USD",
    "GOOGL": "USD", "META": "USD", "TSLA": "USD", "AMD": "USD",
    "SOFI": "USD", "PLTR": "USD", "UPST": "USD", "COIN": "USD",
    # International & Country ETFs
    "EWJ": "JPY", "DXJ": "JPY",
    "EWG": "EUR", "EZU": "EUR", "VGK": "EUR",
    "EWU": "GBP",
    "FXI": "CNY", "KWEB": "CNY", "MCHI": "CNY",
    "EWZ": "BRL",
    "EEM": "USD", "EFA": "USD",
}

# Deprecated: Static profiles
TICKER_THEMATIC_PROFILES = {}

def get_ticker_currency(ticker: str) -> str:
    """Resolves primary currency for ticker, defaulting to USD."""
    return TICKER_CURRENCY_MAP.get(ticker.upper(), "USD")


def get_thematic_expansion(ticker: str, engine: Optional[sa.Engine] = None) -> str:
    """Returns general macro keywords for ticker."""
    clean = ticker.upper()
    return f"{clean} macro catalysts, monetary policy, economic data"


def retrieve_relevant_events(
    engine: sa.Engine,
    ticker: str = "SPY",
    semantic_query: Optional[str] = None,
    api_key: Optional[str] = None,
    country: Optional[str] = None,
    top_k: int = 5,
    time_window_days: int = 7,
    min_similarity_threshold: float = 0.35,
    table_name: str = "economic_events"
) -> List[Dict[str, Any]]:
    """
    Retrieves relevant macroeconomic events for a ticker using pure relational SQL.
    Filters by country/currency and temporal window, ordering by impact tier and timestamp.
    Executes in < 2ms with zero external API calls or vector overhead.
    """
    clean_ticker = ticker.upper().strip()
    target_country = country or get_ticker_currency(clean_ticker)

    sql = sa.text(f"""
        SELECT 
            event_id,
            event_timestamp,
            country,
            title,
            impact_tier,
            forecast,
            previous,
            actual,
            synthetic_summary
        FROM {table_name}
        WHERE country = :country
          AND event_timestamp >= CURRENT_TIMESTAMP - INTERVAL '1 day'
          AND event_timestamp <= CURRENT_TIMESTAMP + INTERVAL '{time_window_days} days'
        ORDER BY 
            CASE impact_tier 
                WHEN 'High' THEN 1 
                WHEN 'Medium' THEN 2 
                WHEN 'Low' THEN 3 
                ELSE 4 
            END ASC,
            event_timestamp ASC
        LIMIT :top_k
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "country": target_country,
            "top_k": top_k
        }).fetchall()

    events = []
    for r in rows:
        events.append({
            "event_id": r[0],
            "event_timestamp": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
            "country": r[2],
            "title": r[3],
            "impact_tier": r[4],
            "forecast": r[5],
            "previous": r[6],
            "actual": r[7],
            "synthetic_summary": r[8],
            "similarity_score": 1.0 if r[4] == "High" else 0.7,
            "status": "RELEASED" if r[7] else "UPCOMING"
        })

    return events


def format_rag_context_block(events: List[Dict[str, Any]]) -> str:
    """
    Formats retrieved events into a concise, telegraphic bullet list
    optimized for LLM prompt augmentation (0% fluff, strictly objective).
    """
    if not events:
        return "• No major upcoming macroeconomic catalysts in the active 7-day window."

    lines = []
    for ev in events:
        ts_str = ev.get("event_timestamp", "")[:16].replace("T", " ")
        status = ev.get("status", "UPCOMING")
        title = ev.get("title", "")
        country = ev.get("country", "")
        tier = ev.get("impact_tier", "")
        actual = ev.get("actual")
        forecast = ev.get("forecast")
        previous = ev.get("previous")

        if status == "RELEASED" and actual:
            details = f"ACTUAL: {actual} (Exp: {forecast or 'N/A'}, Prev: {previous or 'N/A'})"
        else:
            details = f"EXPECTED: {forecast or 'N/A'} (Prev: {previous or 'N/A'})"

        lines.append(f"• [{country}] {title} ({tier}) | {ts_str} UTC | {status}: {details}")

    return "\n".join(lines)
