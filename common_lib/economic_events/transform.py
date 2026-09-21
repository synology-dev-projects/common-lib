"""
Transformation module for Economic Events.
Normalizes raw calendar events into canonical Pydantic models with RAG-ready chunk summaries.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("quant.common_lib.economic_events.transform")


class EconomicEventRecord(BaseModel):
    event_id: str = Field(description="Deterministic unique ID computed from country, title, and timestamp")
    event_timestamp: datetime = Field(description="Event datetime normalized to UTC")
    country: str = Field(description="Country or currency code, e.g. USD, EUR, JPY")
    title: str = Field(description="Name or title of the macroeconomic event")
    impact_tier: str = Field(description="Impact classification: High, Medium, Low, Holiday, or None")
    forecast: Optional[str] = Field(default=None, description="Consensus or forecast estimate")
    previous: Optional[str] = Field(default=None, description="Prior release value")
    actual: Optional[str] = Field(default=None, description="Actual reported value once released")
    synthetic_summary: str = Field(description="Pre-rendered text summary optimized for RAG embedding context")
    raw_payload: Dict[str, Any] = Field(default_factory=dict, description="Raw source JSON payload")


def _compute_event_id(country: str, title: str, date_str: str) -> str:
    """Generates a deterministic 16-character SHA-256 hash for deduplication."""
    norm_key = f"{country.strip().upper()}|{title.strip().lower()}|{date_str.strip()}"
    return hashlib.sha256(norm_key.encode("utf-8")).hexdigest()[:16]


def _normalize_impact(raw_impact: Optional[str]) -> str:
    """Normalizes raw impact strings to standard casing."""
    if not raw_impact:
        return "Low"
    val = raw_impact.strip().capitalize()
    if val in ("High", "Medium", "Low", "Holiday"):
        return val
    return "Low"


def _parse_iso_datetime(date_str: str) -> datetime:
    """Parses ISO 8601 string (e.g. 2026-09-23T03:15:00-04:00) into UTC datetime."""
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_rag_summary(
    country: str,
    title: str,
    utc_dt: datetime,
    impact: str,
    forecast: Optional[str],
    previous: Optional[str],
    actual: Optional[str]
) -> str:
    """
    Generates a dense, self-contained semantic summary string ideal for RAG vector embeddings.
    """
    formatted_time = utc_dt.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Macroeconomic Event: [{country}] {title}",
        f"Date & Time: {formatted_time}",
        f"Impact Tier: {impact}",
    ]

    details = []
    if forecast:
        details.append(f"Forecast: {forecast}")
    if previous:
        details.append(f"Previous: {previous}")
    if actual:
        details.append(f"Actual: {actual} (Status: Released)")
    else:
        details.append("Status: Upcoming / Pending Release")

    lines.append(", ".join(details) + ".")

    if actual and forecast:
        lines.append(f"Comparison: Actual was {actual} vs Consensus Forecast of {forecast}.")

    return " | ".join(lines)


def transform_raw_event(raw: Dict[str, Any]) -> Optional[EconomicEventRecord]:
    """Transforms a single raw calendar item into a validated EconomicEventRecord."""
    try:
        title = str(raw.get("title", "")).strip()
        country = str(raw.get("country", "")).strip().upper()
        date_str = str(raw.get("date", "")).strip()

        if not title or not country or not date_str:
            return None

        event_timestamp = _parse_iso_datetime(date_str)
        event_id = _compute_event_id(country, title, date_str)
        impact = _normalize_impact(raw.get("impact"))

        forecast = str(raw.get("forecast", "")).strip() or None
        previous = str(raw.get("previous", "")).strip() or None
        actual = str(raw.get("actual", "")).strip() or None

        summary = generate_rag_summary(
            country=country,
            title=title,
            utc_dt=event_timestamp,
            impact=impact,
            forecast=forecast,
            previous=previous,
            actual=actual
        )

        return EconomicEventRecord(
            event_id=event_id,
            event_timestamp=event_timestamp,
            country=country,
            title=title,
            impact_tier=impact,
            forecast=forecast,
            previous=previous,
            actual=actual,
            synthetic_summary=summary,
            raw_payload=raw
        )
    except Exception as ex:
        logger.debug(f"Failed to transform event {raw.get('title')}: {ex}")
        return None


def transform_raw_events(raw_events: List[Dict[str, Any]]) -> List[EconomicEventRecord]:
    """Transforms and deduplicates a list of raw calendar events."""
    seen_ids = set()
    records: List[EconomicEventRecord] = []

    for raw in raw_events:
        rec = transform_raw_event(raw)
        if rec and rec.event_id not in seen_ids:
            seen_ids.add(rec.event_id)
            records.append(rec)

    records.sort(key=lambda x: x.event_timestamp)
    logger.info(f"Transformed {len(raw_events)} raw events into {len(records)} validated records")
    return records
