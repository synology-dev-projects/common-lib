

from unittest.mock import MagicMock, patch
import pytest
import common_lib.connectors.nfty as nfty


def test_publish_message_mocked(env_config):
    """
    Unit test: Validates payload formatting without sending real push alerts.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("requests.post", return_value=mock_response) as mock_post:
        result = nfty.send_ntfy_notification(env_config.ntfy_endpoint, "quant_alerts", "TEST_MESSAGE", "HELLO WORLD", 5)
        assert result.status_code == 200
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["Title"] == "TEST_MESSAGE"
        assert kwargs["headers"]["Priority"] == "5"


@pytest.mark.integration
def test_publish_message_live(env_config):
    """
    Integration test: Validates real push notification delivery against live server.
    """
    result = nfty.send_ntfy_notification(env_config.ntfy_endpoint, "quant_alerts", "TEST_MESSAGE", "HELLO WORLD", 1)
    assert result.status_code == 200