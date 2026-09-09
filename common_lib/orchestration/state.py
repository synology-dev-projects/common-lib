"""
PostgreSQL State & Metadata Persistence for Pipeline Runs.

Provides run tracking, idempotency enforcement, and dependency checks
via the centralized `pipeline_runs` fact/audit table in PostgreSQL (with SQLite
compatibility for unit testing).
"""

import json
import uuid
import logging
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple
import sqlalchemy as sa
from sqlalchemy.engine import Engine

logger = logging.getLogger("quant.orchestration.state")


def ensure_pipeline_runs_table(engine: Engine) -> None:
    """Idempotently creates the pipeline_runs table and indexes."""
    is_sqlite = engine.dialect.name == "sqlite"

    statements = []
    if is_sqlite:
        statements.append(sa.text("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id VARCHAR(64) PRIMARY KEY,
                pipeline_name VARCHAR(64) NOT NULL,
                session_date DATE NOT NULL,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status VARCHAR(32) NOT NULL,
                rows_affected INTEGER DEFAULT 0,
                error_message TEXT,
                metadata TEXT DEFAULT '{}'
            )
        """))
        statements.append(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_lookup
            ON pipeline_runs (pipeline_name, session_date, started_at DESC)
        """))
    else:
        statements.append(sa.text("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id VARCHAR(64) PRIMARY KEY,
                pipeline_name VARCHAR(64) NOT NULL,
                session_date DATE NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                status VARCHAR(32) NOT NULL,
                rows_affected INTEGER DEFAULT 0,
                error_message TEXT,
                metadata JSONB DEFAULT '{}'::jsonb
            )
        """))
        statements.append(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_lookup
            ON pipeline_runs (pipeline_name, session_date, started_at DESC)
        """))
        statements.append(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
            ON pipeline_runs (status, session_date)
        """))

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(stmt)
    logger.debug("Ensured pipeline_runs table and indexes exist.")


def record_run_start(
    engine: Engine,
    pipeline_name: str,
    session_date: date,
    allow_concurrent: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Records the start of a pipeline run.
    Raises RuntimeError if a run is already 'RUNNING' within the last 15 minutes
    unless allow_concurrent=True.
    """
    ensure_pipeline_runs_table(engine)
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata or {})
    is_sqlite = engine.dialect.name == "sqlite"

    with engine.begin() as conn:
        if not allow_concurrent:
            stale_threshold = now - timedelta(minutes=15)
            active_check_sql = sa.text("""
                SELECT run_id, started_at FROM pipeline_runs
                WHERE pipeline_name = :p_name
                  AND session_date = :s_date
                  AND status = 'RUNNING'
                  AND started_at > :threshold
                LIMIT 1
            """)
            row = conn.execute(active_check_sql, {
                "p_name": pipeline_name,
                "s_date": str(session_date),
                "threshold": stale_threshold if not is_sqlite else str(stale_threshold),
            }).fetchone()

            if row:
                raise RuntimeError(
                    f"Pipeline '{pipeline_name}' for session date {session_date} "
                    f"is already executing (active run_id: {row[0]}, started: {row[1]})."
                )

        insert_sql = sa.text("""
            INSERT INTO pipeline_runs (
                run_id, pipeline_name, session_date, started_at, status, metadata
            ) VALUES (
                :run_id, :pipeline_name, :session_date, :started_at, 'RUNNING', :metadata
            )
        """)
        conn.execute(insert_sql, {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "session_date": str(session_date),
            "started_at": now if not is_sqlite else str(now),
            "metadata": meta_json,
        })

    logger.info(f"Recorded start for pipeline '{pipeline_name}' (run_id: {run_id}, session_date: {session_date})")
    return run_id


def record_run_success(
    engine: Engine,
    run_id: str,
    rows_affected: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Marks a pipeline run as successfully completed."""
    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata or {})
    is_sqlite = engine.dialect.name == "sqlite"

    update_sql = sa.text("""
        UPDATE pipeline_runs
        SET status = 'SUCCESS',
            completed_at = :completed_at,
            rows_affected = :rows_affected,
            metadata = :metadata
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(update_sql, {
            "run_id": run_id,
            "completed_at": now if not is_sqlite else str(now),
            "rows_affected": int(rows_affected),
            "metadata": meta_json,
        })

    logger.info(f"Recorded SUCCESS for run_id '{run_id}' (rows_affected: {rows_affected})")


def record_run_failure(
    engine: Engine,
    run_id: str,
    error_message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Marks a pipeline run as failed with error details."""
    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata or {})
    is_sqlite = engine.dialect.name == "sqlite"

    update_sql = sa.text("""
        UPDATE pipeline_runs
        SET status = 'FAILED',
            completed_at = :completed_at,
            error_message = :error_message,
            metadata = :metadata
        WHERE run_id = :run_id
    """)

    with engine.begin() as conn:
        conn.execute(update_sql, {
            "run_id": run_id,
            "completed_at": now if not is_sqlite else str(now),
            "error_message": str(error_message),
            "metadata": meta_json,
        })

    logger.error(f"Recorded FAILED for run_id '{run_id}': {error_message}")


def record_run_skip(
    engine: Engine,
    pipeline_name: str,
    session_date: date,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Records that a pipeline execution was skipped (e.g. upstream failure)."""
    ensure_pipeline_runs_table(engine)
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata or {})
    is_sqlite = engine.dialect.name == "sqlite"

    insert_sql = sa.text("""
        INSERT INTO pipeline_runs (
            run_id, pipeline_name, session_date, started_at, completed_at,
            status, error_message, metadata
        ) VALUES (
            :run_id, :pipeline_name, :session_date, :now, :now,
            'SKIPPED', :reason, :metadata
        )
    """)

    with engine.begin() as conn:
        conn.execute(insert_sql, {
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "session_date": str(session_date),
            "now": now if not is_sqlite else str(now),
            "reason": reason,
            "metadata": meta_json,
        })

    logger.warning(f"Recorded SKIPPED for pipeline '{pipeline_name}' on {session_date}: {reason}")
    return run_id


def _parse_row(row: Any) -> Dict[str, Any]:
    """Parses a database row into a dictionary."""
    started_at = row[3]
    if hasattr(started_at, "isoformat"):
        started_at = started_at.isoformat()
    elif started_at:
        started_at = str(started_at)

    completed_at = row[4]
    if hasattr(completed_at, "isoformat"):
        completed_at = completed_at.isoformat()
    elif completed_at:
        completed_at = str(completed_at)

    meta = row[8]
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    elif not isinstance(meta, dict):
        meta = {}

    return {
        "run_id": str(row[0]),
        "pipeline_name": str(row[1]),
        "session_date": str(row[2]),
        "started_at": started_at,
        "completed_at": completed_at,
        "status": str(row[5]),
        "rows_affected": int(row[6] or 0),
        "error_message": row[7],
        "metadata": meta,
    }


def get_pipeline_status(
    engine: Engine,
    pipeline_name: str,
    session_date: date,
) -> Optional[Dict[str, Any]]:
    """
    Returns the latest run record for a specific pipeline and session date,
    or None if never executed.
    """
    ensure_pipeline_runs_table(engine)
    query_sql = sa.text("""
        SELECT run_id, pipeline_name, session_date, started_at, completed_at,
               status, rows_affected, error_message, metadata
        FROM pipeline_runs
        WHERE pipeline_name = :p_name
          AND session_date = :s_date
        ORDER BY started_at DESC
        LIMIT 1
    """)

    with engine.connect() as conn:
        row = conn.execute(query_sql, {
            "p_name": pipeline_name,
            "s_date": str(session_date),
        }).fetchone()

    if not row:
        return None

    return _parse_row(row)


def get_all_pipeline_statuses(
    engine: Engine,
    session_date: date,
) -> Dict[str, Dict[str, Any]]:
    """
    Returns a dictionary mapping pipeline_name -> latest run dict for the session date.
    """
    ensure_pipeline_runs_table(engine)
    is_sqlite = engine.dialect.name == "sqlite"

    if is_sqlite:
        query_sql = sa.text("""
            SELECT run_id, pipeline_name, session_date, started_at, completed_at,
                   status, rows_affected, error_message, metadata
            FROM pipeline_runs
            WHERE session_date = :s_date
            ORDER BY started_at ASC
        """)
        statuses = {}
        with engine.connect() as conn:
            for row in conn.execute(query_sql, {"s_date": str(session_date)}):
                # in ascending order, later overwrites earlier, so we get latest
                statuses[row[1]] = _parse_row(row)
        return statuses
    else:
        query_sql = sa.text("""
            SELECT DISTINCT ON (pipeline_name)
                   run_id, pipeline_name, session_date, started_at, completed_at,
                   status, rows_affected, error_message, metadata
            FROM pipeline_runs
            WHERE session_date = :s_date
            ORDER BY pipeline_name, started_at DESC
        """)
        statuses = {}
        with engine.connect() as conn:
            for row in conn.execute(query_sql, {"s_date": str(session_date)}):
                statuses[row[1]] = _parse_row(row)
        return statuses


def check_upstream_dependencies(
    engine: Engine,
    pipeline_name: str,
    session_date: date,
    dag: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, str]]:
    """
    Checks if all declared upstream dependencies for pipeline_name have completed
    with status='SUCCESS' for the given session date.

    Returns:
        (is_ready: bool, blocked_by: List[str], upstream_statuses: Dict[str, str])
    """
    from common_lib.orchestration.registry import PIPELINE_DAG
    if dag is None:
        dag = PIPELINE_DAG

    if pipeline_name not in dag:
        raise KeyError(f"Unknown pipeline: '{pipeline_name}'")

    upstreams = dag[pipeline_name].get("upstream", [])
    if not upstreams:
        return True, [], {}

    all_statuses = get_all_pipeline_statuses(engine, session_date)
    blocked_by = []
    upstream_statuses = {}

    for upstream in upstreams:
        up_run = all_statuses.get(upstream)
        status_val = up_run["status"] if up_run else "NOT_STARTED"
        upstream_statuses[upstream] = status_val
        if status_val != "SUCCESS":
            blocked_by.append(upstream)

    is_ready = len(blocked_by) == 0
    return is_ready, blocked_by, upstream_statuses
