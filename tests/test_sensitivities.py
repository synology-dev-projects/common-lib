import pytest
from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy.sql import text

from common_lib.database.schemas import ensure_all_schemas
from common_lib.economic_events.sensitivities import (
    calculate_factor_betas,
    fetch_sec_quarterly_facts,
    classify_macro_profile,
    get_or_compute_sensitivity
)

@pytest.fixture(scope="module")
def sqlite_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    # manually create the tables we need for the test because pgvector syntax will crash sqlite
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ibkr_historical_te (
            symbol VARCHAR(16) NOT NULL,
            datetime TIMESTAMP WITH TIME ZONE NOT NULL,
            close NUMERIC(10, 4)
        )
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS company_macro_sensitivities (
            ticker VARCHAR(16) PRIMARY KEY,
            beta_rates NUMERIC(8, 4),
            beta_oil NUMERIC(8, 4),
            beta_usd NUMERIC(8, 4),
            beta_market NUMERIC(8, 4),
            debt_to_equity NUMERIC(10, 4),
            interest_coverage NUMERIC(10, 4),
            net_debt_ebitda NUMERIC(10, 4),
            thematic_tags TEXT,
            primary_catalysts TEXT,
            query_expansion TEXT,
            last_filing_date DATE,
            last_calculated_at TIMESTAMP WITH TIME ZONE
        )
        """))
    
    # Insert mock data into ibkr_historical_te
    with engine.begin() as conn:
        for i in range(10):
            conn.execute(
                text("INSERT INTO ibkr_historical_te (symbol, datetime, close) VALUES (:symbol, :dt, :close)"),
                {"symbol": "AAPL", "dt": datetime.now(timezone.utc), "close": 150.0 + i}
            )
            
    return engine

def test_calculate_factor_betas(sqlite_engine):
    # With data
    betas = calculate_factor_betas("AAPL", sqlite_engine)
    assert isinstance(betas, dict)
    assert "beta_rates" in betas
    
    # Without data
    betas_no_data = calculate_factor_betas("XYZ", sqlite_engine)
    assert betas_no_data["beta_rates"] == -0.1 # default

def test_fetch_sec_quarterly_facts():
    facts = fetch_sec_quarterly_facts("AAPL")
    assert isinstance(facts, dict)
    assert "debt_to_equity" in facts

def test_classify_macro_profile():
    betas = {"beta_rates": -0.1, "beta_oil": 0.2, "beta_market": 1.5}
    fundamentals = {"debt_to_equity": 1.5}
    
    profile = classify_macro_profile("AAPL", betas, fundamentals)
    assert "rate-sensitive" in profile["thematic_tags"]
    assert "energy-sensitive" in profile["thematic_tags"]
    assert "high-beta" in profile["thematic_tags"]
    assert "AAPL macro catalysts" in profile["query_expansion"]

def test_get_or_compute_sensitivity_caching(sqlite_engine):
    # Compute first time
    sens1 = get_or_compute_sensitivity(sqlite_engine, "AAPL")
    assert sens1["ticker"] == "AAPL"
    
    # Fetch second time (should hit cache)
    sens2 = get_or_compute_sensitivity(sqlite_engine, "AAPL")
    dt1 = sens1["last_calculated_at"]
    if isinstance(dt1, str):
        dt1 = datetime.fromisoformat(dt1)
    dt2 = sens2["last_calculated_at"]
    if isinstance(dt2, str):
        dt2 = datetime.fromisoformat(dt2)
    assert dt1 == dt2
    
    # Force refresh
    sens3 = get_or_compute_sensitivity(sqlite_engine, "AAPL", force_refresh=True)
    dt3 = sens3["last_calculated_at"]
    if isinstance(dt3, str):
        dt3 = datetime.fromisoformat(dt3)
    assert dt3 >= dt1
