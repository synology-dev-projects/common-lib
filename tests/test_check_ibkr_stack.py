import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.check_ibkr_stack import (
    _is_valid_num,
    check_python_stack,
    check_env_config,
    check_tcp_reachability,
    run_ibkr_live_probes,
    run_stage0_diagnostics,
)


def test_check_tcp_reachability_detects_eof_and_alive():
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock

    # Case 1: Immediate EOF (b'') -> Docker/socat open, but IB Gateway logged out or rejecting IP
    mock_sock.recv.return_value = b""
    with patch("socket.create_connection", return_value=mock_sock):
        res_eof = check_tcp_reachability("192.168.1.68", 4001)
    assert res_eof["status"] == "FAIL"
    assert res_eof["wire_alive"] is False
    assert "0 bytes / EOF" in res_eof["detail"]

    # Case 2: Valid v100 wire response bytes
    mock_sock.recv.return_value = b"\x00\x00\x00\x1a176\x0020261021 18:00:00 EST\x00"
    with patch("socket.create_connection", return_value=mock_sock):
        res_ok = check_tcp_reachability("192.168.1.68", 4001)
    assert res_ok["status"] == "PASS"
    assert res_ok["wire_alive"] is True


def test_is_valid_num_filters_ibkr_sentinels():
    assert _is_valid_num(580.25) is True
    assert _is_valid_num(0.0) is False
    assert _is_valid_num(-1.0) is False
    assert _is_valid_num(float("nan")) is False
    assert _is_valid_num(float("inf")) is False
    assert _is_valid_num(1.7976931348623157e308) is False
    assert _is_valid_num(None) is False


def test_check_env_config_valid_and_missing():
    mock_cfg = MagicMock()
    mock_cfg.synology_main_ip = "192.168.1.68"
    mock_cfg.ibkr_gateway_port = 4002

    res = check_env_config(config=mock_cfg)
    assert res["status"] == "PASS"
    assert res["host"] == "192.168.1.68"
    assert res["port"] == 4002

    bad_cfg = MagicMock()
    bad_cfg.synology_main_ip = ""
    bad_cfg.ibkr_gateway_port = None
    res_bad = check_env_config(config=bad_cfg)
    assert res_bad["status"] == "FAIL"


def test_run_ibkr_live_probes_full_pass():
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True
    mock_ib.managedAccounts.return_value = ["DU123456"]
    mock_ib.errorEvent = []

    def mock_qualify(contract):
        contract.conId = 756733 if getattr(contract, "symbol", "") == "SPY" else 416904
        return [contract]

    mock_ib.qualifyContracts.side_effect = mock_qualify

    # Mock SPY Equity Ticker and Option Ticker
    spy_ticker = MagicMock()
    spy_ticker.marketPrice.return_value = 582.50
    spy_ticker.last = 582.50
    spy_ticker.close = 581.00

    opt_ticker = MagicMock()
    opt_ticker.callOpenInterest = 14520
    opt_ticker.putOpenInterest = 9800
    opt_ticker.modelGreeks = MagicMock(gamma=0.04125, delta=0.512, impliedVol=0.1485)

    def mock_req_mkt_data(contract, genericTickList="", snapshot=False, regulatorySnapshot=False):
        if genericTickList == "100,101,106":
            return opt_ticker
        return spy_ticker

    mock_ib.reqMktData.side_effect = mock_req_mkt_data

    mock_chain = MagicMock()
    mock_chain.exchange = "SMART"
    mock_chain.expirations = ["20261023", "20261030"]
    mock_chain.strikes = [575.0, 580.0, 585.0]
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    with patch("scripts.check_ibkr_stack.check_python_stack") as mock_stack:
        mock_stack.return_value = {
            "check": "1. Python & Quant Package Stack",
            "status": "PASS",
            "detail": "All packages OK",
        }
        mock_cfg = MagicMock(synology_main_ip="192.168.1.68", ibkr_gateway_port=4002)
        report = run_stage0_diagnostics(config=mock_cfg, ib_instance=mock_ib)

    assert report["overall_status"] == "PASS"
    assert report["ready_for_stage1"] is True
    statuses = {c["check"]: c["status"] for c in report["checks"]}
    assert statuses["3b. IBKR API Handshake & Account Auth"] == "PASS"
    assert statuses["4. US Equity L1 Data Plan (SPY)"] == "PASS"
    assert statuses["5. OPRA US Options Data Plan (OI & Greeks)"] == "PASS"
    assert mock_ib.cancelMktData.call_count >= 1
    mock_ib.disconnect.assert_called_once()


def test_run_ibkr_live_probes_detects_missing_opra_subscription():
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = True
    mock_ib.managedAccounts.return_value = ["DU123456"]

    class DummyEvent(list):
        def __iadd__(self, other):
            self.append(other)
            return self

    err_handlers = DummyEvent()
    mock_ib.errorEvent = err_handlers

    def mock_qualify(contract):
        contract.conId = 756733
        return [contract]

    mock_ib.qualifyContracts.side_effect = mock_qualify

    spy_ticker = MagicMock()
    spy_ticker.marketPrice.return_value = 580.0
    spy_ticker.last = 580.0
    spy_ticker.close = 580.0

    opt_ticker = MagicMock()
    opt_ticker.callOpenInterest = float("nan")
    opt_ticker.putOpenInterest = float("nan")
    opt_ticker.modelGreeks = None

    def mock_req_mkt_data(contract, genericTickList="", snapshot=False, regulatorySnapshot=False):
        if genericTickList == "100,101,106":
            for h in err_handlers:
                h(101, 10090, "Part of requested market data is not subscribed.", contract)
            return opt_ticker
        return spy_ticker

    mock_ib.reqMktData.side_effect = mock_req_mkt_data

    mock_chain = MagicMock(exchange="SMART", expirations=["20261023"], strikes=[580.0])
    mock_ib.reqSecDefOptParams.return_value = [mock_chain]

    probes = run_ibkr_live_probes("192.168.1.68", 4002, wait_seconds=0.01, ib_instance=mock_ib)
    opra_check = next(p for p in probes if "OPRA" in p["check"])
    assert opra_check["status"] == "FAIL"
    assert "10090" in opra_check["detail"]
