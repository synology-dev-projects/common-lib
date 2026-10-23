from datetime import date
import math
from types import SimpleNamespace
import pytest

from common_lib.extractors.ibkr_options import IBKROptionsExtractor, OptionContractRecord
from common_lib.calculators.gexdex import calculate_gexdex_distribution
from common_lib.calculators.shadow_comparator import compare_gexdex_payloads


class _FakeIB:
    """Simulates IBKR where OI (tick 101) arrives immediately, and modelGreeks (tick 106) arrives after 3 sleep ticks."""
    def __init__(self):
        self.sleep_calls = 0
        self.tickers = []

    def reqSecDefOptParams(self, symbol, futFopExchange, underlyingSecType, underlyingConId):
        return [
            SimpleNamespace(
                exchange="SMART",
                tradingClass="AMKR",
                multiplier="100",
                expirations=["20261023", "20261120"],
                strikes=[39.59, 40.0, 41.59, 42.0, 44.59, 45.0]
            )
        ]

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        t = SimpleNamespace(
            bid=1.0,
            ask=1.2,
            last=1.1,
            volume=50,
            callOpenInterest=1200.0 if contract.right == "C" else float("nan"),
            putOpenInterest=1655.0 if contract.right == "P" else float("nan"),
            openInterest=1200.0,
            modelGreeks=None
        )
        self.tickers.append(t)
        return t

    def sleep(self, seconds):
        self.sleep_calls += 1
        if self.sleep_calls >= 3:
            for t in self.tickers:
                t.modelGreeks = SimpleNamespace(
                    impliedVol=0.65,
                    delta=0.45,
                    gamma=0.085,
                    vega=0.02,
                    theta=-0.05,
                    undPrice=41.80
                )

    def cancelMktData(self, contract):
        pass


def test_extractor_filters_corporate_action_strikes_and_waits_for_both_oi_and_greeks():
    fake_ib = _FakeIB()
    extractor = IBKROptionsExtractor(
        ib=fake_ib,
        requests_per_batch=40,
        batch_delay_seconds=0.01,
        request_timeout_seconds=1.0,
        min_wait_seconds=0.01
    )
    und = SimpleNamespace(symbol="AMKR", secType="STK", conId=38708075)
    specs = extractor.select_chains_and_strikes(
        und,
        spot_price=41.80,
        max_dte=30,
        strike_range=25,
        target_date=date(2026, 10, 22)
    )
    strikes = {s["strike"] for s in specs}
    # Corporate action .59 strikes MUST be filtered out
    assert 39.59 not in strikes
    assert 41.59 not in strikes
    assert 44.59 not in strikes
    assert strikes == {40.0, 42.0, 45.0}

    # Chunk polling MUST wait for BOTH OI and modelGreeks (not exit on tick 1 when only OI is present)
    opt_c = SimpleNamespace(symbol="AMKR", lastTradeDateOrContractMonth="20261023", strike=42.0, right="C")
    records = extractor._process_batch_chunk([(opt_c, {**specs[0], "right": "C"})], 1, 1)
    assert len(records) == 1
    assert records[0].open_interest == 1200
    assert records[0].gamma == 0.085
    assert records[0].implied_vol == 0.65


def test_gexdex_calculator_interpolates_expiration_iv_and_matches_tradingedge_schema():
    # ATM contract has implied_vol=0.68; OTM 45P has implied_vol=None, gamma=None
    contracts = [
        OptionContractRecord(
            ticker="AMKR", strike=40.0, expiration="20261023", right="P", dte=1,
            open_interest=5000, implied_vol=0.68, delta=-0.65, gamma=0.14
        ),
        OptionContractRecord(
            ticker="AMKR", strike=40.0, expiration="20261023", right="C", dte=1,
            open_interest=500, implied_vol=0.68, delta=0.35, gamma=0.14
        ),
        OptionContractRecord(
            ticker="AMKR", strike=45.0, expiration="20261023", right="P", dte=1,
            open_interest=1655, implied_vol=0.68, delta=-0.08, gamma=0.03
        ),
        OptionContractRecord(
            ticker="AMKR", strike=50.0, expiration="20261120", right="C", dte=29,
            open_interest=4000, implied_vol=0.65, delta=0.25, gamma=0.04
        ),
    ]
    payload = calculate_gexdex_distribution(
        contracts=contracts,
        spot_price=39.20,
        ticker="AMKR",
        target_date=date(2026, 10, 22)
    )

    by_strike = {s["strike"]: s for s in payload["strikes"]}
    # 1. Strike 45.0 1-DTE Put must interpolate expiration IV (~0.68 -> ~$42.6k GEX), NOT 0.20 (which yielded $0.00 GEX)
    assert abs(by_strike[45.0]["put_gex"]) > 40000.0

    # 2. TradingEdge signed convention in expirations and rolling: put_gex < 0 and put_dex < 0
    exp_40 = by_strike[40.0]["expirations"]["2026-10-23"]
    assert exp_40["call_gex"] > 0
    assert exp_40["put_gex"] < 0
    assert exp_40["call_dex"] > 0
    assert exp_40["put_dex"] < 0

    # 3. Rolling arrays MUST be cumulative sums across strikes
    rolling = payload["rolling"]
    assert len(rolling["strikes"]) == 3
    assert rolling["call_gex"][-1] == pytest.approx(payload["total_call_gex"], abs=1.0)
    assert abs(rolling["put_gex"][-1]) == pytest.approx(payload["total_put_gex"], abs=1.0)
    assert rolling["put_gex"][-1] < 0
    assert rolling["put_dex"][-1] < 0

    # 4. call_put_ratio = total_call_dex / total_put_dex; gex_wall = 40.0 (largest absolute GEX)
    expected_cp_ratio = payload["total_call_dex"] / payload["total_put_dex"]
    assert payload["call_put_ratio"] == pytest.approx(expected_cp_ratio, rel=1e-3)
    assert payload["gex_wall"] == 40.0


def test_shadow_comparator_separates_dex_and_gex():
    te_payload = {
        "ticker": "AMKR",
        "spot_price": 39.20,
        "net_gex": -5000000.0,
        "net_dex": -15000000.0,
        "call_wall": 50.0,
        "put_wall": 40.0,
        "zero_gex_level": 44.5,
        "strikes": [
            {"strike": 40.0, "call_gex": 500000.0, "put_gex": -5500000.0, "call_dex": 1000000.0, "put_dex": -16000000.0}
        ]
    }
    ibkr_payload = {
        "ticker": "AMKR",
        "spot_price": 39.20,
        "net_gex": -4950000.0,
        "net_dex": -14850000.0,
        "call_wall": 50.0,
        "put_wall": 40.0,
        "zero_gex_level": 44.5,
        "strikes": [
            {"strike": 40.0, "call_gex": 505000.0, "put_gex": -5455000.0, "call_dex": 1010000.0, "put_dex": -15860000.0}
        ]
    }
    res = compare_gexdex_payloads(te_payload, ibkr_payload)
    d = res.to_dict()
    assert "te_net_dex" in d and "ibkr_net_dex" in d and "dex_pearson_r" in d
    row = d["strike_rows"][0]
    assert "te_call_dex" in row and "ibkr_call_dex" in row
    assert "te_put_dex" in row and "ibkr_put_dex" in row
    assert "te_net_dex" in row and "ibkr_net_dex" in row
    assert "gex_diff_pct" in row and "dex_diff_pct" in row
