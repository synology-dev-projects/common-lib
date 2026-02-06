import logging
import requests


def send_ntfy_notification(endpoint:str
                           , topic:str
                           , title:str 
                           , message:str
                           , priority:int=3
                           , tags=None
                           , auth=None) -> requests.Response:
    """
    :param title: The title of the notification (e.g., 'Backup Complete').
    :param message: The main text body of the notification.
    :param priority: The urgency level (1=Min, 3=Default, 5=Max/Urgent).
    :param tags: Optional comma-separated string of tags (e.g., 'python,success').
    :param auth: Optional tuple (username, password) if authentication is enabled.
    """

    endpoint = f"{endpoint}/{topic}"

    # HTTP Headers define the notification's appearance and behavior
    headers = {
        'Title': title,
        'Priority': str(priority),
    }

    if tags:
        headers['Tags'] = tags

    try:
        response = requests.post(
            endpoint,
            data=message.encode('utf-8'),  # The main message body
            headers=headers,
            auth=auth,  # Passes authentication if provided
            verify=True  # Ensures secure SSL/HTTPS connection
        )

        # Check for HTTP errors (like 404, 500, or 401 Unauthorized)
        response.raise_for_status()

        logging.info(f"Notification sent successfully to topic: {topic}")
        logging.info(f"Server Response Status: {response.status_code}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending notification: {e}")
        raise e

    return response

    #----------------------------------------PRIVATE-------------------------------------------------------


def _validate_connection(endpoint: str, topic: str, auth = None) -> requests.Response:
    """
    Checks if a topic exists and is accessible by performing a simple GET request.

    :param topic: The topic name to check (e.g., 'server_alerts_12345').
    :param auth: Optional tuple (username, password) if authentication is enabled.
    :return: True if the topic is accessible (HTTP 200), False otherwise.
    """

    # 1. Check Base URL Connectivity
    logging.info(f"Validating {endpoint}...")
    try:
        base_response = requests.get(endpoint, verify=True, timeout=5)
        if base_response.status_code != 200:
            logging.warn(f"WARNING: URL {endpoint} returned status code: {base_response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"ERROR: Cannot connect to base URL '{endpoint}': {e}")
        raise e

    return base_response


















