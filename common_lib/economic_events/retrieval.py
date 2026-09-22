"""
Retrieval module for Economic Events RAG.
Performs semantic vector similarity search via pgvector combined with
country, temporal, and thematic risk profile filters.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import sqlalchemy as sa

from common_lib.economic_events.embeddings import generate_embeddings, get_gemini_api_key

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

# Thematic macro sensitivity presets for query expansion
TICKER_THEMATIC_PROFILES = {
    "SOFI": "variable-rate debt financing, personal loan securitization, fed funds rate cuts, net interest margin, bank deposit costs",
    "UPST": "consumer credit default delinquencies, prime borrowing rates, fed liquidity, loan volume demand",
    "PLTR": "defense spending budgets, government procurement, treasury debt ceiling, interest rate discounting",
    "NVDA": "semiconductor export trade policy, artificial intelligence capital expenditure, tech valuation multiple, bond yields",
    "AMD": "semiconductor capital expenditure, cloud computing demand, treasury yields, tech valuation multiple",
    "TSLA": "auto loan financing rates, consumer vehicle demand, retail interest rates, discretionary spending",
    "COIN": "monetary policy liquidity, regulatory oversight, interest rates, dollar index strength",
    "SPY": "broad market monetary policy, FOMC rate decisions, core CPI inflation, non-farm payrolls, GDP growth",
    "QQQ": "nasdaq 100 duration risk, 10-year treasury yield, tech multiples, core inflation, fed dot plot",
    "IWM": "small cap debt refinancing, regional bank credit availability, domestic US economic growth, interest rate cuts",
}


def get_ticker_currency(ticker: str) -> str:
    """Resolves primary currency for ticker, defaulting to USD."""
    return TICKER_CURRENCY_MAP.get(ticker.upper(), "USD")


def get_thematic_expansion(ticker: str) -> str:
    """Returns preset thematic risk keywords for ticker if available."""
    clean = ticker.upper()
    return TICKER_THEMATIC_PROFILES.get(clean, f"{clean} macro catalysts, monetary policy, economic data")


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
    Retrieves semantically relevant macroeconomic events for a ticker.
    Combines hard relational constraints (country, temporal window) with
    HNSW vector cosine similarity search via pgvector.
    """
    clean_ticker = ticker.upper().strip()
    target_country = country or get_ticker_currency(clean_ticker)

    # 1. Determine query text
    query_text = semantic_query or get_thematic_expansion(clean_ticker)

    # 2. Check if vector search is possible
    query_vector: Optional[List[float]] = None
    try:
        key = get_gemini_api_key(api_key)
        emb_res = generate_embeddings([query_text], api_key=key)
        if emb_res and len(emb_res[0]) == 768:
            query_vector = emb_res[0]
    except Exception as e:
        logger.debug(f"Vector search bypassed for {clean_ticker}: {e}")

    # 3. Query execution
    if query_vector is not None:
        vec_str = "[" + ",".join(str(f) for f in query_vector) + "]"
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
                synthetic_summary,
                1 - (embedding <=> :query_vector) AS similarity_score
            FROM {table_name}
            WHERE country = :country
              AND event_timestamp >= CURRENT_TIMESTAMP - INTERVAL '1 day'
              AND event_timestamp <= CURRENT_TIMESTAMP + INTERVAL '{time_window_days} days'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :query_vector ASC
            LIMIT :top_k
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {
                "query_vector": vec_str,
                "country": target_country,
                "top_k": top_k
            }).fetchall()

        events = []
        for r in rows:
            score = float(r[9]) if r[9] is not None else 0.0
            if score >= min_similarity_threshold:
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
                    "similarity_score": round(score, 4),
                    "status": "RELEASED" if r[7] else "UPCOMING"
                })
        if events:
            return events

    # 4. Fallback: Relational query sorted by impact and timestamp
    logger.info(f"Using deterministic relational fallback retrieval for {clean_ticker}...")
    fallback_sql = sa.text(f"""
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
        rows = conn.execute(fallback_sql, {
            "country": target_country,
            "top_k": top_k
        }).fetchall()

    fallback_events = []
    for r in rows:
        fallback_events.append({
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

    return fallback_events


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
