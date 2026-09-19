"""
common_lib/connectors/alerts.py - Unified Pipeline & System Failure Alerting Dispatcher.

Dispatches Priority 5 (Max / Urgent) NTFY alerts to topic 'quant_alerts' on any fatal pipeline
exception, rate limit crash, or unhandled system failure. Includes zero-config resilient fallbacks.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

logger = logging.getLogger("quant.common_lib.alerts")


def get_current_et_timestamp() -> str:
    """Returns formatted Eastern Time (ET) timestamp string."""
    try:
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        return datetime.now(eastern).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        # Fallback to UTC-4 (EDT)
        edt = timezone(timedelta(hours=-4))
        return datetime.now(edt).strftime("%Y-%m-%d %H:%M:%S EDT")


def resolve_ntfy_endpoint(config: Optional[Any] = None) -> str:
    """
    Resolves the canonical NTFY endpoint using resilient fallback hierarchy:
    1. config.ntfy_endpoint (if provided and non-empty)
    2. os.environ['NTFY_ENDPOINT']
    3. Default public gateway: 'https://richntfynotifier.synology.me'
    """
    endpoint = None
    if config and getattr(config, "ntfy_endpoint", None):
        endpoint = str(config.ntfy_endpoint).strip()
    
    if not endpoint:
        endpoint = os.getenv("NTFY_ENDPOINT", "").strip()

    if not endpoint:
        endpoint = "https://richntfynotifier.synology.me"

    # Normalize endpoint URL: strip trailing slash and remove duplicate /alerts suffix if present
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/alerts"):
        endpoint = endpoint[:-7]

    return endpoint


def dispatch_pipeline_failure_alert(
    pipeline_name: str,
    error: Any,
    session_date: Optional[Any] = None,
    details: Optional[str] = None,
    config: Optional[Any] = None,
    priority: int = 5,
    tags: str = "rotating_light,skull,error",
) -> bool:
    """
    Dispatches a high-priority push alert to NTFY 'quant_alerts' on pipeline failures.
    Guaranteed never to raise unhandled exceptions.
    """
    try:
        from common_lib.connectors import nfty

        endpoint = resolve_ntfy_endpoint(config)
        ts_str = get_current_et_timestamp()
        
        session_str = f"Session: {session_date}\n" if session_date else ""
        error_msg = str(error) if error else "Unknown failure"
        if len(error_msg) > 400:
            error_msg = error_msg[:397] + "..."

        extra_details = f"\nDetails:\n{details[:400]}" if details else ""

        title = f"🚨 PIPELINE FAILURE: {pipeline_name}"
        message = (
            f"Pipeline '{pipeline_name}' failed at {ts_str}.\n"
            f"{session_str}"
            f"Error: {error_msg}"
            f"{extra_details}"
        )

        nfty.send_ntfy_notification(
            endpoint=endpoint,
            topic="quant_alerts",
            title=title,
            message=message,
            priority=priority,
            tags=tags,
            timeout=10,
        )
        logger.info(f"Dispatched failure notification to '{endpoint}/quant_alerts' for {pipeline_name}.")
        return True

    except Exception as ex:
        logger.error(f"Failed to dispatch pipeline failure alert for '{pipeline_name}': {ex}", exc_info=True)
        return False
