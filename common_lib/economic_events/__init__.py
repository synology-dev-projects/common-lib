"""Economic Events package in common_lib."""

from common_lib.economic_events.transform import (
    EconomicEventRecord,
    generate_rag_summary,
    transform_raw_event,
    transform_raw_events,
)
from common_lib.economic_events.extract import (
    fetch_calendar_feed,
    extract_all_upcoming_events,
)
from common_lib.economic_events.load import (
    get_economic_events_table,
    ensure_economic_events_table,
    load_events_to_postgres,
)
from common_lib.economic_events.runner import (
    run_economic_events_sync,
)
from common_lib.economic_events.embeddings import (
    generate_embeddings,
    embed_missing_records,
)
from common_lib.economic_events.retrieval import (
    TICKER_CURRENCY_MAP,
    TICKER_THEMATIC_PROFILES,
    get_ticker_currency,
    get_thematic_expansion,
    retrieve_relevant_events,
    format_rag_context_block,
)

__all__ = [
    "EconomicEventRecord",
    "generate_rag_summary",
    "transform_raw_event",
    "transform_raw_events",
    "fetch_calendar_feed",
    "extract_all_upcoming_events",
    "get_economic_events_table",
    "ensure_economic_events_table",
    "load_events_to_postgres",
    "run_economic_events_sync",
    "generate_embeddings",
    "embed_missing_records",
    "TICKER_CURRENCY_MAP",
    "TICKER_THEMATIC_PROFILES",
    "get_ticker_currency",
    "get_thematic_expansion",
    "retrieve_relevant_events",
    "format_rag_context_block",
]
