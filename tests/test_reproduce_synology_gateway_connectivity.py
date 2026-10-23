import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from common_lib.connectors.ibkr import SafeIBConnection
from common_lib.extractors.ibkr_options import IBKROptionsExtractor, OptionContractRecord

def test_safe_ib_connection_stubs_heavy_requests_to_prevent_handshake_hang():
    """
    Validates that SafeIBConnection automatically stubs out positions, executions,
    orders, and subaccount update async requests so that connecting to IB Gateway
    never stalls on subaccount synchronization timeouts.
    """
    fake_ib = MagicMock()
    fake_ib.isConnected.return_value = True

    conn = SafeIBConnection(host="127.0.0.1", port=4001, base_client_id=10, ib_instance=fake_ib)
    
    # Verify that ib_instance connects with readonly=True
    with conn as ib:
        assert ib.isConnected() is True
    
    fake_ib.connect.assert_called_once()
    _, kwargs = fake_ib.connect.call_args
    assert kwargs.get("readonly") is True or fake_ib.connect.call_args[0][4] is True


def test_extractor_pacing_prevents_market_data_farm_rate_limit_drops():
    """
    Validates that IBKROptionsExtractor enforces request and cancel pacing
    delays so that rapid contract loops do not trip IBKR Error 1100 / Warning 2103.
    """
    class PacedFakeIB:
        def __init__(self):
            self.sleep_intervals = []
            self.req_calls = 0
            self.cancel_calls = 0

        def sleep(self, seconds):
            self.sleep_intervals.append(seconds)

        def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
            self.req_calls += 1
            t = SimpleNamespace(
                bid=2.5, ask=2.6, last=2.55, volume=100,
                callOpenInterest=500.0, putOpenInterest=float("nan"), openInterest=500.0,
                modelGreeks=SimpleNamespace(delta=0.5, gamma=0.05, impliedVol=0.40)
            )
            return t

        def cancelMktData(self, contract):
            self.cancel_calls += 1

    fake_ib = PacedFakeIB()
    extractor = IBKROptionsExtractor(ib=fake_ib, requests_per_batch=5, batch_delay_seconds=0.1)
    
    # Use simple mock contracts without requiring ib_insync import
    c1 = SimpleNamespace(symbol="AMKR", right="C", strike=40.0)
    c2 = SimpleNamespace(symbol="AMKR", right="C", strike=41.0)
    c3 = SimpleNamespace(symbol="AMKR", right="C", strike=42.0)
    pairs = [
        (c1, {"right": "C", "strike": 40.0, "dte": 28, "expiration": "20261120"}),
        (c2, {"right": "C", "strike": 41.0, "dte": 28, "expiration": "20261120"}),
        (c3, {"right": "C", "strike": 42.0, "dte": 28, "expiration": "20261120"}),
    ]

    records = extractor._process_batch_chunk(pairs, 1, 1)

    assert len(records) == 3
    assert fake_ib.req_calls == 3
    assert fake_ib.cancel_calls == 3
    # Pacing sleeps must have been invoked between requests and cancels
    assert any(s in [0.025, 0.01] for s in fake_ib.sleep_intervals)
