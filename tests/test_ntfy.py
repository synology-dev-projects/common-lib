

import pytest
import unittest
import common_lib.connectors.nfty as nfty


def test_validate_server_and_topic_with_real_credentials(env_config):
    """
    Validates Connections, passes if status code is 200
    :return:
    """


    result = nfty._validate_connection(env_config.ntfy_endpoint,"quant_alerts")
    print(f"\nResponse Status Code: {result.status_code}")
    assert 200 == result.status_code



def test_02_publish_message_success(env_config):
    """
    Validates Connections, passes if status code is 200
    Please check physically if notification is received
    :return:
    """

    result = nfty.send_ntfy_notification(env_config.ntfy_endpoint,"quant_alerts", "TEST_MESSAGE", "HELLO WORLD", 5)
    print(f"\nResponse Status Code: {result.status_code}")
    assert 200 == result.status_code



if __name__ == '__main__':
    unittest.main()