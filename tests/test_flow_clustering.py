"""
Unit tests for Options Flow Thematic Semantic Clustering & SEC 10-K Ingestion Engine.
"""

import math
from datetime import date
from unittest.mock import MagicMock, patch
import pytest
import sqlalchemy as sa

from common_lib.flow.clustering import (
    cosine_distance,
    get_semantic_profile,
    upsert_semantic_profile,
    seed_core_watchlist_profiles,
    cluster_thematic_flow,
    CORE_10K_SUMMARIES,
)


def _unit_vector(base: list) -> list:
    norm = math.sqrt(sum(x * x for x in base))
    return [x / norm for x in base] if norm > 0 else base


def test_cosine_distance_math():
    """Mathematical verification of cosine distance properties."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v_ortho = [0.0, 1.0, 0.0]
    v_opp = [-1.0, 0.0, 0.0]

    assert cosine_distance(v1, v2) == 0.0
    assert cosine_distance(v1, v_ortho) == 1.0
    assert cosine_distance(v1, v_opp) == 2.0

    # Dimension mismatch or empty vector fallback
    assert cosine_distance([], [1.0, 2.0]) == 1.0
    assert cosine_distance([1.0], [1.0, 2.0]) == 1.0


def test_core_10k_summaries_coverage():
    """Verifies standard watchlist tickers exist in CORE_10K_SUMMARIES."""
    required = {"SOFI", "AFRM", "UPST", "NVDA", "AMD", "TSLA", "PLTR", "COIN", "XOM", "CVX", "SPY", "QQQ"}
    assert required.issubset(set(CORE_10K_SUMMARIES.keys()))
    for t in required:
        item = CORE_10K_SUMMARIES[t]
        assert len(item["business_summary"]) > 50
        assert item["sector"]


def test_get_semantic_profile_mocked():
    """Tests retrieval and vector parsing of an existing semantic profile."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # Mock DB row
    mock_row = {
        "ticker": "SOFI",
        "cik": "0001818874",
        "company_name": "SoFi Technologies, Inc.",
        "sector": "Fintech",
        "business_summary": "Digital banking and consumer lending.",
        "embedding": "[0.1, 0.2, 0.3]",
        "last_filing_date": date(2026, 2, 15),
        "updated_at": None,
    }
    mock_conn.execute.return_value.mappings.return_value.first.return_value = mock_row

    profile = get_semantic_profile(mock_engine, "SOFI")
    assert profile is not None
    assert profile["ticker"] == "SOFI"
    assert profile["embedding"] == [0.1, 0.2, 0.3]
    assert profile["last_filing_date"] == "2026-02-15"


def test_cluster_thematic_flow_multi_ticker_success():
    """
    Tests full flow clustering workflow:
    - 3 fintech consumer credit tickers (SOFI, AFRM, UPST) with combined premium >= $1.0M.
    - 1 semiconductor ticker (NVDA) with high premium but no semantic partner.
    - Proves SOFI, AFRM, and UPST cluster together, while NVDA is isolated and filtered out.
    """
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # 1. Flow prints in unusual_whales_flow_te
    flow_rows = [
        ("SOFI", 1500000.0, 1200000.0, 300000.0, 42),   # Bullish Fintech ($1.5M)
        ("AFRM", 1200000.0, 1000000.0, 200000.0, 35),   # Bullish Fintech ($1.2M)
        ("UPST", 800000.0, 600000.0, 200000.0, 20),     # Bullish Fintech ($0.8M)
        ("NVDA", 4000000.0, 3500000.0, 500000.0, 110),  # Semiconductor ($4.0M, isolated)
    ]

    # Create synthetic 768-dim vectors
    # Fintech vector base: mostly dimension 0 & 1
    v_fintech = [0.0] * 768
    v_fintech[0] = 0.95
    v_fintech[1] = 0.10

    v_sofi = _unit_vector(list(v_fintech))
    
    v_afrm_raw = list(v_fintech)
    v_afrm_raw[1] = 0.15
    v_afrm = _unit_vector(v_afrm_raw)

    v_upst_raw = list(v_fintech)
    v_upst_raw[1] = 0.20
    v_upst = _unit_vector(v_upst_raw)

    # Semiconductor vector base: mostly dimension 500 (orthogonal to fintech)
    v_semi_raw = [0.0] * 768
    v_semi_raw[500] = 1.0
    v_nvda = _unit_vector(v_semi_raw)

    profile_rows = [
        ("SOFI", "SoFi Technologies", "Consumer Fintech", v_sofi),
        ("AFRM", "Affirm Holdings", "Consumer Fintech", v_afrm),
        ("UPST", "Upstart Holdings", "Consumer Fintech", v_upst),
        ("NVDA", "NVIDIA Corporation", "Semiconductors", v_nvda),
    ]

    # Configure mock connection to return flow rows on first query, profile rows on second query
    mock_conn.execute.side_effect = [
        MagicMock(fetchall=lambda: flow_rows),
        MagicMock(fetchall=lambda: profile_rows),
    ]

    clusters = cluster_thematic_flow(
        engine=mock_engine,
        trade_date=date(2026, 9, 21),
        min_cluster_premium=1000000.0,
        max_distance=0.25
    )

    assert len(clusters) == 1
    c = clusters[0]
    assert c["ticker_count"] == 3
    cluster_symbols = {t["ticker"] for t in c["tickers"]}
    assert cluster_symbols == {"SOFI", "AFRM", "UPST"}
    assert c["combined_premium"] == 3500000.0
    assert c["net_sentiment"] == "BULLISH"
    assert c["avg_cosine_distance"] <= 0.25
    assert c["dominant_sector"] == "Consumer Fintech"


def test_cluster_thematic_flow_below_premium_threshold():
    """Tests that clusters with >=2 tickers but below $1.0M combined premium are filtered out."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    flow_rows = [
        ("SOFI", 300000.0, 200000.0, 100000.0, 10),
        ("AFRM", 200000.0, 150000.0, 50000.0, 8),
    ]

    v = _unit_vector([1.0] + [0.0] * 767)
    profile_rows = [
        ("SOFI", "SoFi", "Fintech", v),
        ("AFRM", "Affirm", "Fintech", v),
    ]

    mock_conn.execute.side_effect = [
        MagicMock(fetchall=lambda: flow_rows),
        MagicMock(fetchall=lambda: profile_rows),
    ]

    clusters = cluster_thematic_flow(
        engine=mock_engine,
        trade_date=date(2026, 9, 21),
        min_cluster_premium=1000000.0,  # $1M hurdle
        max_distance=0.25
    )

    assert len(clusters) == 0, "Cluster with only $500K total premium should be filtered out"


def test_cluster_thematic_flow_empty():
    """Tests behavior when no flow prints exist for date."""
    mock_engine = MagicMock(spec=sa.Engine)
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = []

    clusters = cluster_thematic_flow(engine=mock_engine, trade_date=date(2026, 9, 21))
    assert clusters == []
