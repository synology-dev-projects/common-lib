"""
Database loader for Economic Events.
Idempotently upserts EconomicEventRecord into PostgreSQL 16 / TimescaleDB.
"""

import logging
from typing import List, Optional
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common_lib.database.schemas import ensure_pgvector_extension
from common_lib.economic_events.transform import EconomicEventRecord

try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    Vector = None
    HAS_PGVECTOR = False

logger = logging.getLogger("quant.common_lib.economic_events.load")


def get_economic_events_table(metadata: Optional[sa.MetaData] = None, table_name: str = "economic_events") -> sa.Table:
    """Returns canonical SQLAlchemy Table definition for economic_events."""
    meta = metadata or sa.MetaData()
    if table_name in meta.tables:
        return meta.tables[table_name]

    embedding_col = sa.Column("embedding", Vector(768)) if HAS_PGVECTOR else sa.Column("embedding", sa.NullType)

    return sa.Table(
        table_name, meta,
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("impact_tier", sa.String(16), nullable=False),
        sa.Column("forecast", sa.String(32)),
        sa.Column("previous", sa.String(32)),
        sa.Column("actual", sa.String(32)),
        sa.Column("synthetic_summary", sa.Text),
        sa.Column("raw_payload", sa.JSON),
        embedding_col,
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def ensure_economic_events_table(engine: sa.Engine, table_name: str = "economic_events") -> None:
    """Ensures table and indices exist in database idempotently."""
    ensure_pgvector_extension(engine)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        event_id VARCHAR(64) NOT NULL,
        event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
        country VARCHAR(8) NOT NULL,
        title VARCHAR(256) NOT NULL,
        impact_tier VARCHAR(16) NOT NULL,
        forecast VARCHAR(32),
        previous VARCHAR(32),
        actual VARCHAR(32),
        synthetic_summary TEXT,
        raw_payload JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_id, event_timestamp)
    );
    CREATE INDEX IF NOT EXISTS idx_econ_ts ON {table_name}(event_timestamp);
    CREATE INDEX IF NOT EXISTS idx_econ_country_impact ON {table_name}(country, impact_tier);
    ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding VECTOR(768);
    CREATE INDEX IF NOT EXISTS idx_econ_embedding ON {table_name} USING hnsw (embedding vector_cosine_ops);
    """
    with engine.begin() as conn:
        for stmt in ddl.strip().split(";"):
            clean_stmt = stmt.strip()
            if clean_stmt:
                try:
                    conn.execute(sa.text(clean_stmt))
                except Exception as e:
                    logger.warning(f"Error executing statement '{clean_stmt[:50]}...': {e}")


def load_events_to_postgres(
    records: List[EconomicEventRecord],
    engine: sa.Engine,
    table_name: str = "economic_events"
) -> int:
    """
    Idempotently upserts EconomicEventRecord items into PostgreSQL economic_events table.
    Updates actual, forecast, previous, synthetic_summary, raw_payload, and updated_at on conflict.
    Returns count of upserted rows.
    """
    if not records:
        logger.info("No records to load into database.")
        return 0

    ensure_economic_events_table(engine, table_name)
    tbl = get_economic_events_table(table_name=table_name)

    payload_rows = []
    for r in records:
        payload_rows.append({
            "event_id": r.event_id,
            "event_timestamp": r.event_timestamp,
            "country": r.country,
            "title": r.title,
            "impact_tier": r.impact_tier,
            "forecast": r.forecast,
            "previous": r.previous,
            "actual": r.actual,
            "synthetic_summary": r.synthetic_summary,
            "raw_payload": r.raw_payload,
        })

    stmt = pg_insert(tbl).values(payload_rows)
    update_dict = {
        "title": stmt.excluded.title,
        "impact_tier": stmt.excluded.impact_tier,
        "forecast": stmt.excluded.forecast,
        "previous": stmt.excluded.previous,
        "actual": stmt.excluded.actual,
        "synthetic_summary": stmt.excluded.synthetic_summary,
        "raw_payload": stmt.excluded.raw_payload,
        "updated_at": sa.text("CURRENT_TIMESTAMP"),
    }

    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["event_id", "event_timestamp"],
        set_=update_dict
    )

    with engine.begin() as conn:
        result = conn.execute(upsert_stmt)
        rowcount = result.rowcount

    logger.info(f"Successfully upserted {len(payload_rows)} economic event records into {table_name}")
    return len(payload_rows)
