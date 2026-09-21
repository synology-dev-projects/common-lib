"""
Unit tests for common_lib.economic_events module.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from common_lib.economic_events import (
    EconomicEventRecord,
    generate_rag_summary,
    transform_raw_event,
    transform_raw_events,
    fetch_calendar_feed,
    get_economic_events_table,
    load_events_to_postgres,
    run_economic_events_sync,
)


def test_economic_event_record_transform():
    raw = {
        "title": "Fed Interest Rate Decision",
        "country": "USD",
        "date": "2026-09-23T14:00:00-04:00",
        "impact": "High",
        "forecast": "5.25%",
        "previous": "5.50%"
    }
    rec = transform_raw_event(raw)
    assert rec is not None
    assert rec.country == "USD"
    assert rec.title == "Fed Interest Rate Decision"
    assert rec.impact_tier == "High"
    assert rec.event_timestamp.tzinfo == timezone.utc
    assert "Macroeconomic Event: [USD] Fed Interest Rate Decision" in rec.synthetic_summary


def test_economic_events_table_schema():
    tbl = get_economic_events_table()
    assert tbl.name == "economic_events"
    col_names = [c.name for c in tbl.columns]
    assert "event_id" in col_names
    assert "event_timestamp" in col_names
    assert "synthetic_summary" in col_names


def test_run_economic_events_sync_runner():
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    mock_raw = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-09-23T08:30:00-04:00", "impact": "High"}
    ]

    with patch("common_lib.economic_events.runner.extract_all_upcoming_events", return_value=mock_raw), \
         patch("common_lib.economic_events.runner.load_events_to_postgres", return_value=1):

        res = run_economic_events_sync(config_or_engine=mock_engine)
        assert res["status"] == "success"
        assert res["raw_extracted"] == 1
        assert res["records_transformed"] == 1
        assert res["records_loaded"] == 1
