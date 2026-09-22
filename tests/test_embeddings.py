"""
Unit tests for economic events embeddings and retrieval modules in common_lib.
"""

from unittest.mock import patch, MagicMock
import pytest

from common_lib.economic_events.embeddings import (
    generate_embeddings,
    get_gemini_api_key,
)
from common_lib.economic_events.retrieval import (
    get_ticker_currency,
    get_thematic_expansion,
    format_rag_context_block,
    TICKER_CURRENCY_MAP,
)


def test_get_ticker_currency():
    assert get_ticker_currency("SPY") == "USD"
    assert get_ticker_currency("NVDA") == "USD"
    assert get_ticker_currency("EWJ") == "JPY"
    assert get_ticker_currency("EWG") == "EUR"
    assert get_ticker_currency("UNKNOWN_TICKER_XYZ") == "USD"


def test_get_thematic_expansion():
    sofi_expansion = get_thematic_expansion("SOFI")
    assert "debt" in sofi_expansion.lower() or "rate" in sofi_expansion.lower()

    nvda_expansion = get_thematic_expansion("NVDA")
    assert "semiconductor" in nvda_expansion.lower() or "yield" in nvda_expansion.lower()


def test_format_rag_context_block_empty():
    res = format_rag_context_block([])
    assert "No major upcoming" in res


def test_format_rag_context_block_populated():
    events = [
        {
            "event_timestamp": "2026-09-24T12:30:00+00:00",
            "country": "USD",
            "title": "Unemployment Claims",
            "impact_tier": "Medium",
            "forecast": "201K",
            "previous": "196K",
            "actual": None,
            "status": "UPCOMING"
        },
        {
            "event_timestamp": "2026-09-21T14:00:00+00:00",
            "country": "USD",
            "title": "Existing Home Sales",
            "impact_tier": "Low",
            "forecast": "4.00M",
            "previous": "3.95M",
            "actual": "4.05M",
            "status": "RELEASED"
        }
    ]
    formatted = format_rag_context_block(events)
    assert "[USD] Unemployment Claims (Medium)" in formatted
    assert "EXPECTED: 201K" in formatted
    assert "[USD] Existing Home Sales (Low)" in formatted
    assert "ACTUAL: 4.05M" in formatted


@patch("common_lib.economic_events.embeddings.generate_embeddings_http")
def test_generate_embeddings_mocked(mock_http):
    fake_vector = [0.1] * 768
    mock_http.return_value = [fake_vector, fake_vector]

    texts = ["Macro summary 1", "Macro summary 2"]
    vectors = generate_embeddings(texts, api_key="dummy_key", batch_size=5)

    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    assert mock_http.called
