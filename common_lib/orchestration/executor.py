"""
Pipeline Execution Engine & DAG Cycle Runner.

Handles in-flight exponential backoff retries, checkpoint resumption,
resilience gates, and NTFY failure alerts.
"""

import time
import logging
from datetime import datetime, date, timezone
from typing import Dict, List, Any, Optional, Callable
from sqlalchemy.engine import Engine

from common_lib.orchestration.registry import (
    PIPELINE_DAG,
    get_topological_order,
    get_downstream_dependencies,
    resolve_runner,
)
from common_lib.orchestration.state import (
    record_run_start,
    record_run_success,
    record_run_failure,
    record_run_skip,
    get_pipeline_status,
    check_upstream_dependencies,
    get_all_pipeline_statuses,
)

logger = logging.getLogger("quant.orchestration.executor")


def _dispatch_ntfy_alert(topic: str, title: str, message: str, priority: int = 4) -> None:
    """Safely dispatches NTFY alerts without raising exceptions on network hiccups."""
    try:
        from common_lib.config.main_config import load_config
        from common_lib.connectors import nfty
        config = load_config()
        endpoint = getattr(config, "ntfy_endpoint", "https://richntfynotifier.synology.me/alerts")
        nfty.send_ntfy_notification(endpoint, topic, title, message, priority)
    except Exception as e:
        logger.warning(f"Failed to dispatch NTFY alert: {e}")


def execute_single_pipeline(
    engine: Engine,
    pipeline_name: str,
    session_date: date,
    force_refresh: bool = False,
    max_retries: int = 3,
    retry_delay_sec: int = 5,
    dag: Optional[Dict[str, Any]] = None,
    runner_override: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Executes a single pipeline within the dependency framework:
    1. Validates upstream dependencies (fails fast / skips if upstream is red).
    2. Enforces checkpoint resumption (skips if already SUCCESS unless force_refresh).
    3. Executes with exponential backoff on transient errors.
    4. Atomically logs lifecycle states into PostgreSQL pipeline_runs.
    """
    if dag is None:
        dag = PIPELINE_DAG

    if pipeline_name not in dag:
        raise KeyError(f"Pipeline '{pipeline_name}' not found in registered DAG.")

    meta = dag[pipeline_name]

    # 1. Dependency Gate Sensor Check
    is_ready, blocked_by, upstream_statuses = check_upstream_dependencies(
        engine, pipeline_name, session_date, dag=dag
    )

    if not is_ready:
        reason = f"Upstream dependencies not satisfied: {blocked_by} ({upstream_statuses})"
        logger.warning(f"🚫 [DEPENDENCY BLOCKED] Skipping '{pipeline_name}' on {session_date}: {reason}")
        record_run_skip(engine, pipeline_name, session_date, reason)
        return {
            "pipeline_name": pipeline_name,
            "session_date": str(session_date),
            "status": "SKIPPED",
            "reason": reason,
            "blocked_by": blocked_by,
        }

    # 2. Checkpoint Resumption (Skip already successful jobs)
    current_status = get_pipeline_status(engine, pipeline_name, session_date)
    if current_status and current_status["status"] == "SUCCESS" and not force_refresh:
        logger.info(f"⏭️ [CHECKPOINT HIT] Pipeline '{pipeline_name}' already SUCCESS on {session_date}. Skipping.")
        return {
            "pipeline_name": pipeline_name,
            "session_date": str(session_date),
            "status": "SKIPPED_ALREADY_SUCCESS",
            "run_id": current_status["run_id"],
            "rows_affected": current_status["rows_affected"],
        }

    # 3. Resolve Runner
    if runner_override:
        runner_fn = runner_override
    else:
        runner_spec = meta["runner"]
        try:
            runner_fn = resolve_runner(runner_spec)
        except Exception as resolve_err:
            fallback_spec = meta.get("fallback_runner")
            if fallback_spec:
                logger.warning(f"Primary runner '{runner_spec}' failed: {resolve_err}. Trying fallback '{fallback_spec}'...")
                runner_fn = resolve_runner(fallback_spec)
            else:
                raise resolve_err

    # 4. Record Execution Start
    run_id = record_run_start(engine, pipeline_name, session_date, allow_concurrent=force_refresh)
    start_time = time.time()

    # 5. Execute with Exponential Backoff Retry Loop
    last_error = None
    rows_affected = 0

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"▶️ Executing '{pipeline_name}' (Attempt {attempt}/{max_retries}) on {session_date}...")
            
            # Smart invocation matching target signatures
            # Try passing session_date, else date string, else kwargs, else 0 args
            try:
                res = runner_fn(session_date=session_date)
            except TypeError:
                try:
                    res = runner_fn(target_date=session_date)
                except TypeError:
                    try:
                        res = runner_fn(target_date_str=str(session_date))
                    except TypeError:
                        try:
                            res = runner_fn(date_str=str(session_date))
                        except TypeError:
                            res = runner_fn()

            if isinstance(res, int):
                rows_affected = res
            elif isinstance(res, (tuple, list)) and len(res) > 0 and isinstance(res[0], int):
                rows_affected = res[0]
            elif isinstance(res, dict) and "rows" in res:
                rows_affected = res.get("rows", 0)
            elif isinstance(res, dict) and "rows_affected" in res:
                rows_affected = res.get("rows_affected", 0)

            duration = round(time.time() - start_time, 2)
            record_run_success(engine, run_id, rows_affected=rows_affected, metadata={"duration_sec": duration})
            logger.info(f"✅ [SUCCESS] '{pipeline_name}' finished in {duration}s. Rows affected: {rows_affected}")
            return {
                "pipeline_name": pipeline_name,
                "session_date": str(session_date),
                "run_id": run_id,
                "status": "SUCCESS",
                "rows_affected": rows_affected,
                "duration_sec": duration,
            }

        except Exception as err:
            last_error = err
            logger.warning(f"⚠️ [RETRYABLE ERROR] Attempt {attempt} for '{pipeline_name}' failed: {err}")
            if attempt < max_retries:
                sleep_sec = retry_delay_sec * (2 ** (attempt - 1))
                time.sleep(sleep_sec)

    # 6. Hard Failure after exhausted retries
    duration = round(time.time() - start_time, 2)
    err_msg = str(last_error)
    record_run_failure(engine, run_id, err_msg, metadata={"duration_sec": duration, "attempts": max_retries})
    
    # Send NTFY Priority 5 alert on fatal pipeline crash
    _dispatch_ntfy_alert(
        topic="quant_alerts",
        title=f"Pipeline Failed: {pipeline_name}",
        message=f"Pipeline '{pipeline_name}' failed after {max_retries} attempts on {session_date}.\nError: {err_msg[:200]}",
        priority=5
    )

    return {
        "pipeline_name": pipeline_name,
        "session_date": str(session_date),
        "run_id": run_id,
        "status": "FAILED",
        "error": err_msg,
        "duration_sec": duration,
    }


def run_dag_cycle(
    engine: Engine,
    session_date: date,
    from_pipeline: Optional[str] = None,
    only_pipeline: Optional[str] = None,
    force_all: bool = False,
    dry_run: bool = False,
    dag: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Orchestrates execution of the pipeline DAG:
    - Calculates topological order.
    - Resolves targets if --from or --only is specified.
    - Executes tasks sequentially, skipping succeeded jobs and blocking on failures.
    """
    if dag is None:
        dag = PIPELINE_DAG

    full_order = get_topological_order(dag)

    # Determine subset of pipelines to execute
    if only_pipeline:
        if only_pipeline not in dag:
            raise KeyError(f"Pipeline '{only_pipeline}' not found in registered DAG.")
        target_pipelines = [only_pipeline]
    elif from_pipeline:
        if from_pipeline not in dag:
            raise KeyError(f"Pipeline '{from_pipeline}' not found in registered DAG.")
        downstream = get_downstream_dependencies(from_pipeline, dag=dag)
        target_pipelines = [from_pipeline] + downstream
    else:
        target_pipelines = full_order

    current_statuses = get_all_pipeline_statuses(engine, session_date)

    if dry_run:
        plan = []
        for name in target_pipelines:
            upstreams = dag[name].get("upstream", [])
            existing = current_statuses.get(name)
            is_ready = all(current_statuses.get(u, {}).get("status") == "SUCCESS" for u in upstreams)

            if existing and existing["status"] == "SUCCESS" and not force_all:
                action = "SKIP (Already SUCCESS)"
            elif not is_ready:
                action = f"BLOCKED (Missing/failed upstreams: {[u for u in upstreams if current_statuses.get(u, {}).get('status') != 'SUCCESS']})"
            else:
                action = "EXECUTE"

            plan.append({
                "pipeline_name": name,
                "description": dag[name].get("description"),
                "upstreams": upstreams,
                "current_status": existing["status"] if existing else "NONE",
                "planned_action": action,
            })

        return {
            "session_date": str(session_date),
            "dry_run": True,
            "topological_sequence": target_pipelines,
            "plan": plan,
        }

    # Real Execution Cycle
    results = {}
    cycle_start = time.time()

    for name in target_pipelines:
        res = execute_single_pipeline(
            engine=engine,
            pipeline_name=name,
            session_date=session_date,
            force_refresh=force_all,
            dag=dag,
        )
        results[name] = res

    cycle_duration = round(time.time() - cycle_start, 2)
    return {
        "session_date": str(session_date),
        "cycle_duration_sec": cycle_duration,
        "total_tasks": len(target_pipelines),
        "results": results,
    }
