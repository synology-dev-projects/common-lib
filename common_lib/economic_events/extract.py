"""
Extraction module for Economic Events.
Provides rate-limited, cached, and resilient extraction from economic calendar feeds.
"""

import os
import json
import time
import random
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("quant.common_lib.economic_events.extract")

FEED_THIS_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_NEXT_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"


def _get_cache_dir() -> Path:
    base = os.getenv("CACHE_DIR")
    if base:
        p = Path(base)
    else:
        p = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_cache_path(url: str, cache_dir: Optional[str] = None) -> Path:
    filename = "thisweek.json" if "thisweek" in url else ("nextweek.json" if "nextweek" in url else "feed.json")
    p = Path(cache_dir) if cache_dir else _get_cache_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p / filename


def _read_cache(cache_file: Path, max_age_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not cache_file.exists():
        return None
    try:
        mtime = cache_file.stat().st_mtime
        age = time.time() - mtime
        if age <= max_age_seconds:
            logger.info(f"Loaded {cache_file.name} from cache (age: {int(age)}s <= {max_age_seconds}s)")
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        logger.warning(f"Error reading cache file {cache_file}: {e}")
    return None


def _write_cache(cache_file: Path, data: List[Dict[str, Any]]) -> None:
    try:
        tmp_file = cache_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_file.replace(cache_file)
        logger.debug(f"Saved {len(data)} items to disk cache: {cache_file}")
    except Exception as e:
        logger.warning(f"Failed writing cache to {cache_file}: {e}")


def fetch_calendar_feed(
    url: str = FEED_THIS_WEEK_URL,
    cache_dir: Optional[str] = None,
    cache_ttl_seconds: int = 900,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    request_timeout_seconds: int = 10,
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """
    Fetches raw economic calendar events with disk TTL caching,
    HTTP 429 backoff retries, and offline fallback.
    """
    cache_path = _get_cache_path(url, cache_dir)

    # 1. Check disk cache
    if not force_refresh:
        cached = _read_cache(cache_path, cache_ttl_seconds)
        if cached is not None:
            return cached

    # 2. Network request with exponential backoff & 429 resilience
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            logger.info(f"Fetching calendar feed from {url} (attempt {attempt}/{max_retries})...")
            with urllib.request.urlopen(req, timeout=request_timeout_seconds) as response:
                if response.status == 200:
                    raw_bytes = response.read()
                    data = json.loads(raw_bytes.decode("utf-8"))
                    if isinstance(data, list):
                        _write_cache(cache_path, data)
                        logger.info(f"Successfully fetched {len(data)} events from {url}")
                        return data
                    else:
                        raise ValueError(f"Expected JSON list, got {type(data)}")
        except urllib.error.HTTPError as he:
            last_error = he
            if he.code == 429 or 500 <= he.code < 600:
                sleep_secs = (backoff_factor ** attempt) + random.uniform(0.5, 1.5)
                logger.warning(f"HTTP {he.code} on attempt {attempt}. Retrying in {sleep_secs:.2f}s...")
                time.sleep(sleep_secs)
            else:
                logger.error(f"HTTP error {he.code} fetching {url}: {he.reason}")
                break
        except Exception as ex:
            last_error = ex
            sleep_secs = (backoff_factor ** attempt) + 0.5
            logger.warning(f"Error fetching {url} on attempt {attempt} ({ex}). Retrying in {sleep_secs:.2f}s...")
            time.sleep(sleep_secs)

    # 3. Graceful degradation: read stale cache if available
    logger.warning(f"All network attempts failed for {url}. Attempting fallback to stale disk cache...")
    stale = _read_cache(cache_path, max_age_seconds=86400 * 7)
    if stale is not None:
        logger.warning(f"Using stale cache for {url} ({len(stale)} events)")
        return stale

    logger.error(f"Extraction failed for {url}: {last_error}")
    raise RuntimeError(f"Could not extract calendar feed from {url}: {last_error}") from last_error


def extract_all_upcoming_events(
    cache_dir: Optional[str] = None,
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """Extracts events from thisweek calendar feed."""
    all_raw: List[Dict[str, Any]] = []
    try:
        events_thisweek = fetch_calendar_feed(FEED_THIS_WEEK_URL, cache_dir=cache_dir, force_refresh=force_refresh)
        all_raw.extend(events_thisweek)
    except Exception as e:
        logger.error(f"Failed to fetch thisweek feed: {e}")

    try:
        events_nextweek = fetch_calendar_feed(FEED_NEXT_WEEK_URL, cache_dir=cache_dir, force_refresh=force_refresh)
        all_raw.extend(events_nextweek)
    except Exception as e:
        logger.debug(f"Nextweek feed not available (expected if not published): {e}")

    return all_raw
