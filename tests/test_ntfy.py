

import pytest
import unittest
from common_lib.connectors.nfty import NtfyConnector


def test_validate_server_and_topic_with_real_credentials(env_config):
    """
    Validates Connections, passes if status code is 200
    :return:
    """

    ntfy_conn = NtfyConnector(env_config.ntfy_endpoint)

    result = ntfy_conn._validate_connection("quant_alerts")
    print(f"\nResponse Status Code: {result.status_code}")
    assert 200 == result.status_code



def test_02_publish_message_success(env_config):
    """
    Validates Connections, passes if status code is 200
    Please check physically if notification is received
    :return:
    """

    ntfy_conn = NtfyConnector(env_config.ntfy_endpoint)

    result = ntfy_conn.send_ntfy_notification("quant_alerts", "TEST_MESSAGE", "HELLO WORLD", 5)
    print(f"\nResponse Status Code: {result.status_code}")
    assert 200 == result.status_code



if __name__ == '__main__':
    unittest.main()