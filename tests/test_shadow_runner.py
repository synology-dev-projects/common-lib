import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.shadow_pipeline_runner import (
    fetch_baseline_te_payload,
    run_shadow_comparison_for_ticker,
    generate_markdown_dashboard
)


def test_generate_markdown_dashboard(tmp_path):
    results = [
        {
            "ticker": "SPY",
            "spot_price": 500.0,
            "te_net_gex": 1000000.0,
            "ibkr_net_gex": 995000.0,
            "net_gex_diff_pct": 0.5,
            "gex_pearson_r": 0.998,
            "te_call_wall": 510.0,
            "ibkr_call_wall": 510.0,
            "call_wall_matched": True,
            "te_put_wall": 490.0,
            "ibkr_put_wall": 490.0,
            "put_wall_matched": True,
            "zero_flip_diff_pct": 0.05,
            "regime_matched": True
        }
    ]
    report_file = generate_markdown_dashboard(results, datetime.date(2026, 10, 22), tmp_path)
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "SPY" in content
    assert "0.998" in content
    assert "🟢 PASS" in content


def test_fetch_baseline_te_payload():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_row = MagicMock()
    mock_row.raw_payload = {"ticker": "SPY", "net_gex": 123.0}
    mock_conn.execute.return_value.fetchone.return_value = mock_row

    payload = fetch_baseline_te_payload(mock_engine, "SPY", datetime.date(2026, 10, 22))
    assert payload == {"ticker": "SPY", "net_gex": 123.0}


def test_run_shadow_comparison_for_ticker_mocked(tmp_path):
    mock_extractor = MagicMock()
    from common_lib.extractors.ibkr_options import OptionContractRecord, OptionsChainExtractionResult
    
    mock_extractor.extract_chain_snapshot.return_value = OptionsChainExtractionResult(
        ticker="SPY",
        spot_price=500.0,
        timestamp="2026-10-22T00:00:00Z",
        total_contracts_extracted=2,
        contracts=[
            OptionContractRecord(
                ticker="SPY", strike=500.0, right="C", expiration="20261030",
                dte=8, implied_vol=0.15, delta=0.5, gamma=0.03, open_interest=1000
            ),
            OptionContractRecord(
                ticker="SPY", strike=500.0, right="P", expiration="20261030",
                dte=8, implied_vol=0.15, delta=-0.5, gamma=0.03, open_interest=1000
            )
        ]
    )

    res = run_shadow_comparison_for_ticker(
        extractor=mock_extractor,
        ticker="SPY",
        target_date=datetime.date(2026, 10, 22),
        te_payload=None,
        output_dir=tmp_path
    )
    assert res is not None
    assert res["ticker"] == "SPY"
    assert res["spot_price"] == 500.0
    assert (tmp_path / "SPY_comparison.json").exists()
