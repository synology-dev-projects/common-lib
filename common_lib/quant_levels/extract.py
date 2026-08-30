from typing import Dict, Any, Optional, List
from common_lib.config.main_config import MainConfig as Config
import logging
import requests
import time
from datetime import datetime, timedelta
from dateutil import parser
from bs4 import BeautifulSoup
import re

class NoSuchElementException(Exception):
    pass


# Configure logging
logger = logging.getLogger(__name__)

def run(config: Config, cutoff_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Queries the API for mighty and gets all the posts data we need from the feed and puts it in a json
    :param config:
    :param cutoff_date: will only grab posts from current date to this date
    :return: semi-structured json containing the following properties:
     title, original_poster, date_posted, link, html_body, file_link, quant_lvl_txt

    """

    raw_json_response = _fetch_raw_feed(config, cutoff_date)
    json_response_with_html = _parse_feed_data(raw_json_response)
    json_response_with_raw_text = _extract_quant_levels_from_post_body(json_response_with_html)
    json_response_with_file = _extract_file_link(json_response_with_raw_text)

    return json_response_with_file


def _fetch_raw_feed(config: Config, cutoff_date: Optional[datetime] = None) -> list:
    """
    Grabs all the html related to the post from the hidden api
    :param config:
    :param cutoff_date: cutoff date to stop scrolling through infinite scroll
    :return: list of all raw html
    """
    base_url = config.te_base_url

    params = {
        'per_page': 20,
        'prompt_types': 'advertisement,profile_builder',
        'sort': 'newest',
        'page': 1
    }

    headers = _get_auth_headers(config)

    all_raw_items = []

    logger.info(f"Starting fetch. Cutoff date: {cutoff_date}")

    while True:
        try:
            logger.info(f"Fetching Page {params['page']}...")

            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Extract list from response
            if isinstance(data, list):
                page_items = data
            else:
                page_items = data.get('collection') or data.get('posts') or []

            if not page_items:
                logger.info("No items returned. End of feed.")
                break

            # 1. Add items to our master list
            all_raw_items.extend(page_items)

            # 2. DATE CHECK (The Logic You Requested)
            if cutoff_date:
                # We check the LAST item in this batch (since it's sorted by newest)
                last_item = page_items[-1]

                # Dig for the date string. Note: It's usually inside 'post' -> 'created_at'
                raw_date_str = last_item.get('post', {}).get('created_at')

                if raw_date_str:
                    # Parse ISO string to datetime object
                    # We use dateutil for robustness, or datetime.fromisoformat()
                    item_date = parser.isoparse(raw_date_str)

                    # Ensure cutoff_date is comparable (timezone awareness)
                    if item_date <= cutoff_date:
                        logger.info(f"Reached cutoff date ({item_date} < {cutoff_date}). Stopping.")
                        break
                else:
                    logger.warning("Could not find date in last item. Continuing safely.")

            # 3. Prepare next page
            params['page'] += 1
            time.sleep(1)  # Be polite

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error: {e}")
            break

    if cutoff_date is not None:
        all_raw_items = _prune_old_posts(all_raw_items, cutoff_date)

    return all_raw_items

def _get_auth_headers(config: Config) -> Dict[str, str]:
    """
    Constructs the necessary headers for the Trading Edge API.
    Unwraps the secure cookie using get_secret_value().
    """
    return {
        "User-Agent": config.te_user_agent,
        "Cookie": config.te_cookie.get_secret_value(),
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def _prune_old_posts(posts_list: list, cutoff_date: datetime) -> list:
    """
    Prunes all the raw html posts so that we only get the ones after the cutoff date
    :param posts_list:
    :param cutoff_date:
    :return: the raw html list with all posts after the cutoff date
    """
    if not cutoff_date or not posts_list:
        return posts_list

    keep_list = []
    dropped_count = 0

    for item in posts_list:
        # defensive coding to safely get the date
        raw_date_str = item.get('post', {}).get('created_at')

        if raw_date_str:
            item_date = parser.isoparse(raw_date_str)
            current_cutoff = cutoff_date
            if current_cutoff.tzinfo is None and item_date.tzinfo is not None:
                current_cutoff = current_cutoff.replace(tzinfo=item_date.tzinfo)
            elif current_cutoff.tzinfo is not None and item_date.tzinfo is None:
                item_date = item_date.replace(tzinfo=current_cutoff.tzinfo)

            if item_date > current_cutoff:
                keep_list.append(item)
            else:
                dropped_count += 1
        else:
            # If no date is present, usually safe to keep
            keep_list.append(item)

    if dropped_count > 0:
        logger.info(f"Pruned {dropped_count} posts older than {cutoff_date}")

    return keep_list


def _parse_feed_data(raw_data: Any) -> list:
    """
    Parses the feed data to extract key info including the HTML body.
    :param raw_data: The JSON list from the 'feed' API response
    :return: List of dicts containing: title, original_poster, date_posted, link, html_body
    """
    # Handle list vs dictionary response structure
    if isinstance(raw_data, list):
        items = raw_data
    else:
        # Safety chain to find the list of items
        items = raw_data.get('collection') or raw_data.get('posts') or raw_data.get('data') or []

    output_list = []

    for item in items:
        # Defensive coding: .get() ensures we don't crash on missing keys
        post_content = item.get('post', {})

        # Skip items that don't have post content (like simple notifications)
        if not post_content:
            continue

        user_info = post_content.get('user', {})
        sharing_meta = post_content.get('sharing_meta', {})

        # 1. Start with the text description
        html_body = post_content.get('description', '')

        # 2. Extract the 'assets' list
        assets = post_content.get('assets', [])

        # 3. Inject file links if they exist
        if assets:
            # Create a container for our injected links
            injected_html = "<div class='injected-attachments'><br><strong>Attachments:</strong>"
            has_files = False

            for asset in assets:
                # We only want files (txt, pdf, etc), not images which are usually already visible
                if asset.get('is_file'):
                    file_url = asset.get('original_url')
                    file_name = asset.get('original_filename', 'Download File')

                    if file_url:
                        has_files = True
                        # IMPORTANT: We add the specific class your extractor looks for
                        injected_html += f'<br><a class="mighty-file-attachment-link" href="{file_url}">{file_name}</a>'

            injected_html += "</div>"

            # Only append if we actually found files
            if has_files:
                html_body += injected_html

        entry = {
            "title": post_content.get('title', 'No Title'),
            "original_poster": user_info.get('name', 'Unknown'),
            "date_posted": post_content.get('created_at'),
            "link": sharing_meta.get('url'),
            # In Mighty Networks feeds, 'description' holds the actual HTML post body
            "html_body": html_body
        }
        output_list.append(entry)

    return output_list

def _extract_quant_levels_from_post_body(posts: list) -> list:
    """
    Extracts 'quant level' text by scanning the raw text content of the post.
    Captures:
    1. Lines starting with 3+ digits (e.g., "6500", "6400-6450")
    2. Separator lines (e.g., "---", "----")
    """

    # Regex 1: Quant Level (Starts with 3+ digits)
    level_pattern = re.compile(r"^\s*\d{3,}")

    # Regex 2: Separator (Starts with 3+ dashes)
    # This handles "---", "----", "------", and trailing spaces
    separator_pattern = re.compile(r"^\s*-{3,}")

    for post in posts:
        logging.info(f"Extracting post: {post.get('date_posted')}:{post.get('title')}")
        html_body = post.get('html_body')

        post['quant_lvl_text'] = None

        if not html_body:
            continue

        soup = BeautifulSoup(html_body, "html.parser")

        # 1. Convert entire HTML to text, treating <br> and </p> as newlines
        text_content = soup.get_text(separator="\n")

        # 2. Split into raw lines
        raw_lines = text_content.splitlines()

        extracted_lines = []

        for line in raw_lines:
            clean_line = line.strip()

            # 3. Check if line matches either pattern
            if level_pattern.match(clean_line) or separator_pattern.match(clean_line):
                extracted_lines.append(clean_line)

        # 4. Save result
        if extracted_lines:
            post['quant_lvl_text'] = "\n".join(extracted_lines)

    return posts

def _extract_file_link(posts: list) -> list:
    """
    Iterates through a list of post objects, parses the 'html_body',
    and adds a 'has_file' property based on the presence of 'a.mighty-file'.
    """
    for post in posts:
        html_body = post.get("html_body", "")

        soup = BeautifulSoup(html_body, "html.parser")

        file_tag = soup.select_one("a.mighty-file, a.mighty-file-attachment-link")

        if file_tag:
            file_url = file_tag.get("href")
            post["file_link"] = file_url
            content = _get_file_content(file_url)
            if content:
                post["quant_lvl_text"] = content
        else:
            post["file_link"] = None

    return posts


def _get_file_content(file_link: Optional[str]) -> Optional[str]:
    """
    Fetches the content of a file link and returns it as a string.
    Returns None or an empty string if the fetch fails.
    """
    if not file_link:
        return None

    try:
        response = requests.get(file_link, timeout=10)
        response.raise_for_status()  # Raises error for 4xx/5xx status codes
        response.encoding = 'utf-8-sig'
        return response.text

    except requests.RequestException as e:
        logger.error(f"Error fetching {file_link}: {e}")
        return None
