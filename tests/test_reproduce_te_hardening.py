import inspect
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Allow running isolated unit tests even in environments without beautifulsoup4
if "bs4" not in sys.modules:
    class _FakeInput:
        def __init__(self, value="fake-csrf-token"):
            self._value = value

        def get(self, key, default=""):
            return self._value if key == "value" else default

    class _FakeSoup:
        def __init__(self, html, parser="html.parser"):
            self.html = html or ""

        def find(self, tag, attrs=None, **kwargs):
            if tag == "input" and isinstance(attrs, dict) and attrs.get("name") == "_token":
                if "_token" in self.html:
                    return _FakeInput("csrf123")
                return None
            return _FakeInput("")

    fake_bs4 = types.ModuleType("bs4")
    fake_bs4.BeautifulSoup = _FakeSoup
    sys.modules["bs4"] = fake_bs4


def test_main_config_user_agent_is_not_truncated():
    """MainConfig.te_user_agent must be a valid full browser User-Agent without literal '...'."""
    from common_lib.config.main_config import MainConfig

    default_ua = MainConfig.model_fields["te_user_agent"].default
    assert isinstance(default_ua, str)
    assert "..." not in default_ua, f"Malformed truncated User-Agent: {default_ua}"
    assert "(KHTML, like Gecko)" in default_ua
    assert "Chrome/" in default_ua
    assert "Safari/537.36" in default_ua


def test_flow_extract_fallback_user_agent_is_not_truncated():
    """common_lib.flow.extract fallback User-Agent must be a complete browser User-Agent."""
    from common_lib.flow import extract as flow_extract

    src = inspect.getsource(flow_extract.get_authenticated_flow_session)
    assert "(KHTML, like Gecko)" in src and "Chrome/" in src, (
        "flow/extract.py fallback User-Agent is truncated and missing Chrome/Safari tokens"
    )


def test_get_authenticated_session_rejects_login_redirect():
    """POST /login redirecting back to /login with credential error must return None, not cache session."""
    from common_lib.connectors.tradingedge import dexgex

    dexgex._cached_session = None
    dexgex._last_auth_time = 0.0

    mock_cfg = MagicMock()
    mock_cfg.te_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    mock_cfg.te_login_gate = "https://tools.tradingedge.club/login"
    mock_cfg.te_email.get_secret_value.return_value = "user@example.com"
    mock_cfg.te_pass.get_secret_value.return_value = "wrongpass"

    mock_get = MagicMock(status_code=200, text='<input name="_token" value="csrf123">')
    mock_post = MagicMock(
        status_code=200,
        url="https://tools.tradingedge.club/login",
        text="<html><title>Sign In</title>These credentials do not match our records.</html>",
    )

    mock_session = MagicMock()
    mock_session.get.return_value = mock_get
    mock_session.post.return_value = mock_post

    with patch("common_lib.connectors.tradingedge.dexgex.requests.Session", return_value=mock_session), \
         patch("common_lib.connectors.tradingedge.dexgex.SESSION_CACHE_FILE") as mock_cache_file:
        mock_cache_file.exists.return_value = False
        sess = dexgex.get_authenticated_session(mock_cfg, force_refresh=True)

    assert sess is None, "get_authenticated_session falsely treated failed /login redirect as authenticated"
    assert dexgex._cached_session is None


def test_extract_raw_data_backs_off_and_retries_once_on_429():
    """extract_raw_data must honor Retry-After on HTTP 429, sleep once, and retry once."""
    from common_lib.connectors.tradingedge import dexgex

    mock_cfg = MagicMock()
    mock_cfg.te_dex_gex_url = "https://tools.tradingedge.club/api/dex/data"

    resp_429 = MagicMock(status_code=429, headers={"Retry-After": "12"})
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ticker": "SPY", "spot_price": 580.0, "strikes": []}

    mock_session = MagicMock()
    mock_session.get.side_effect = [resp_429, resp_200]

    with patch("common_lib.connectors.tradingedge.dexgex.time.sleep") as mock_sleep, \
         patch("common_lib.connectors.tradingedge.dexgex.get_authenticated_session") as mock_reauth:
        result = dexgex.extract_raw_data(mock_cfg, mock_session, "SPY")

    assert result is not None and result.get("ticker") == "SPY", (
        "extract_raw_data dropped HTTP 429 immediately instead of backing off and retrying once"
    )
    assert mock_sleep.called, "extract_raw_data did not sleep on HTTP 429"
    assert not mock_reauth.called, "HTTP 429 must never trigger /login re-authentication"


def test_prefect_snapshot_retry_strategy_avoids_rate_limit_storm():
    """execute_snapshot must not auto-retry full 45-ticker scrapes on loud failure (retries == 0)."""
    from common_lib.orchestration import prefect_flow

    assert prefect_flow.execute_snapshot.retries == 0, (
        f"execute_snapshot retries={prefect_flow.execute_snapshot.retries} must be 0 to prevent whole-pipeline retry storms"
    )
