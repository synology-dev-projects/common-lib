"""
Comprehensive Unit Tests for Pipeline Orchestrator & Dependency Manager.
"""

import pytest
from datetime import date, datetime
import sqlalchemy as sa

from common_lib.orchestration.registry import (
    PIPELINE_DAG,
    get_topological_order,
    get_topological_batches,
    get_downstream_dependencies,
    get_upstream_dependencies,
    resolve_runner,
)
from common_lib.orchestration.state import (
    ensure_pipeline_runs_table,
    record_run_start,
    record_run_success,
    record_run_failure,
    record_run_skip,
    get_pipeline_status,
    get_all_pipeline_statuses,
    check_upstream_dependencies,
)
from common_lib.orchestration.executor import (
    execute_single_pipeline,
    run_dag_cycle,
)


@pytest.fixture
def memory_db():
    """In-memory SQLite database engine for fast isolated state tests."""
    engine = sa.create_engine("sqlite:///:memory:")
    ensure_pipeline_runs_table(engine)
    return engine


# -----------------------------------------------------------------------------
# 1. Registry & DAG Tests
# -----------------------------------------------------------------------------

def test_registry_topological_order():
    order = get_topological_order()
    assert "unusual_option_flow" in order
    assert "quant_levels" in order
    assert "gexdex_snapshot" in order
    assert "market_confluence" in order

    # Precedence assertions
    flow_idx = order.index("unusual_option_flow")
    snap_idx = order.index("gexdex_snapshot")
    conf_idx = order.index("market_confluence")

    assert flow_idx < snap_idx, "unusual_option_flow must precede gexdex_snapshot"
    assert snap_idx < conf_idx, "gexdex_snapshot must precede market_confluence"


def test_registry_topological_batches():
    batches = get_topological_batches()
    assert len(batches) >= 3

    # Batch 1 must contain roots with 0 upstreams
    batch1 = batches[0]
    assert "unusual_option_flow" in batch1
    assert "quant_levels" in batch1

    # Batch 2 must contain gexdex_snapshot
    batch2 = batches[1]
    assert "gexdex_snapshot" in batch2

    # Final batch contains market_confluence
    flattened = [item for b in batches for item in b]
    assert flattened.index("gexdex_snapshot") < flattened.index("market_confluence")


def test_registry_cycle_detection():
    cyclic_dag = {
        "A": {"upstream": ["C"]},
        "B": {"upstream": ["A"]},
        "C": {"upstream": ["B"]},
    }
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        get_topological_order(cyclic_dag)


def test_registry_downstream_dependencies():
    # flow -> snapshot -> confluence
    downstream_flow = get_downstream_dependencies("unusual_option_flow")
    assert "gexdex_snapshot" in downstream_flow
    assert "market_confluence" in downstream_flow

    # snapshot -> confluence
    downstream_snap = get_downstream_dependencies("gexdex_snapshot")
    assert downstream_snap == ["market_confluence"]

    # confluence is leaf
    downstream_conf = get_downstream_dependencies("market_confluence")
    assert downstream_conf == []


# -----------------------------------------------------------------------------
# 2. State & Metadata Persistence Tests
# -----------------------------------------------------------------------------

def test_state_lifecycle(memory_db):
    session_date = date(2026, 9, 8)

    # 1. Start
    run_id = record_run_start(memory_db, "unusual_option_flow", session_date)
    status_entry = get_pipeline_status(memory_db, "unusual_option_flow", session_date)
    assert status_entry is not None
    assert status_entry["run_id"] == run_id
    assert status_entry["status"] == "RUNNING"
    assert status_entry["rows_affected"] == 0

    # 2. Success
    record_run_success(memory_db, run_id, rows_affected=150, metadata={"source": "test"})
    status_entry = get_pipeline_status(memory_db, "unusual_option_flow", session_date)
    assert status_entry["status"] == "SUCCESS"
    assert status_entry["rows_affected"] == 150
    assert status_entry["metadata"]["source"] == "test"

    # 3. Failure on another pipeline
    run_id_fail = record_run_start(memory_db, "quant_levels", session_date)
    record_run_failure(memory_db, run_id_fail, error_message="API connection reset")
    fail_entry = get_pipeline_status(memory_db, "quant_levels", session_date)
    assert fail_entry["status"] == "FAILED"
    assert "API connection reset" in fail_entry["error_message"]

    # 4. Skip
    skip_id = record_run_skip(memory_db, "gexdex_snapshot", session_date, reason="Upstream blocked")
    skip_entry = get_pipeline_status(memory_db, "gexdex_snapshot", session_date)
    assert skip_entry["status"] == "SKIPPED"
    assert "Upstream blocked" in skip_entry["error_message"]


def test_state_concurrent_rejection(memory_db):
    session_date = date(2026, 9, 8)
    record_run_start(memory_db, "unusual_option_flow", session_date)

    # Re-starting without allow_concurrent must raise RuntimeError
    with pytest.raises(RuntimeError, match="already executing"):
        record_run_start(memory_db, "unusual_option_flow", session_date, allow_concurrent=False)

    # With allow_concurrent=True, it succeeds
    run_id_2 = record_run_start(memory_db, "unusual_option_flow", session_date, allow_concurrent=True)
    assert run_id_2 is not None


def test_state_upstream_dependency_checks(memory_db):
    session_date = date(2026, 9, 8)

    # Initially: flow not started -> gexdex_snapshot blocked
    ready, blocked, statuses = check_upstream_dependencies(memory_db, "gexdex_snapshot", session_date)
    assert not ready
    assert blocked == ["unusual_option_flow"]
    assert statuses["unusual_option_flow"] == "NOT_STARTED"

    # When flow fails -> still blocked
    run_id = record_run_start(memory_db, "unusual_option_flow", session_date)
    record_run_failure(memory_db, run_id, "Scrape failed")
    ready, blocked, statuses = check_upstream_dependencies(memory_db, "gexdex_snapshot", session_date)
    assert not ready
    assert blocked == ["unusual_option_flow"]
    assert statuses["unusual_option_flow"] == "FAILED"

    # When flow succeeds -> unblocked!
    run_id_success = record_run_start(memory_db, "unusual_option_flow", session_date, allow_concurrent=True)
    record_run_success(memory_db, run_id_success, rows_affected=25)
    ready, blocked, statuses = check_upstream_dependencies(memory_db, "gexdex_snapshot", session_date)
    assert ready
    assert blocked == []
    assert statuses["unusual_option_flow"] == "SUCCESS"


# -----------------------------------------------------------------------------
# 3. Executor & Checkpoint Resumption Tests
# -----------------------------------------------------------------------------

def test_executor_checkpoint_resumption(memory_db):
    session_date = date(2026, 9, 8)
    call_count = 0

    def mock_flow_runner(**kwargs):
        nonlocal call_count
        call_count += 1
        return 42

    custom_dag = {
        "unusual_option_flow": {
            "upstream": [],
            "runner": mock_flow_runner,
        }
    }

    # First run: executes runner
    res1 = execute_single_pipeline(
        memory_db, "unusual_option_flow", session_date, dag=custom_dag, runner_override=mock_flow_runner
    )
    assert res1["status"] == "SUCCESS"
    assert res1["rows_affected"] == 42
    assert call_count == 1

    # Second run without force_refresh: skips runner!
    res2 = execute_single_pipeline(
        memory_db, "unusual_option_flow", session_date, dag=custom_dag, runner_override=mock_flow_runner
    )
    assert res2["status"] == "SKIPPED_ALREADY_SUCCESS"
    assert call_count == 1  # Runner was NOT called again!

    # Third run with force_refresh: executes runner again
    res3 = execute_single_pipeline(
        memory_db, "unusual_option_flow", session_date, force_refresh=True, dag=custom_dag, runner_override=mock_flow_runner
    )
    assert res3["status"] == "SUCCESS"
    assert call_count == 2


def test_executor_retry_backoff(memory_db):
    session_date = date(2026, 9, 8)
    attempts = 0

    def flaky_runner(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("Temporary network blip")
        return 100

    custom_dag = {
        "flaky_task": {
            "upstream": [],
            "runner": flaky_runner,
        }
    }

    res = execute_single_pipeline(
        memory_db,
        "flaky_task",
        session_date,
        max_retries=3,
        retry_delay_sec=0.01,  # ultra-fast for testing
        dag=custom_dag,
        runner_override=flaky_runner,
    )
    assert res["status"] == "SUCCESS"
    assert res["rows_affected"] == 100
    assert attempts == 3


def test_executor_blocks_downstream_on_upstream_failure(memory_db):
    session_date = date(2026, 9, 8)

    def failing_flow(**kwargs):
        raise ValueError("Critical portal auth failure")

    snapshot_called = False
    def mock_snapshot(**kwargs):
        nonlocal snapshot_called
        snapshot_called = True
        return 16

    custom_dag = {
        "flow": {"upstream": [], "runner": failing_flow},
        "snapshot": {"upstream": ["flow"], "runner": mock_snapshot},
    }

    cycle_res = run_dag_cycle(
        memory_db,
        session_date,
        dag=custom_dag,
    )

    assert cycle_res["results"]["flow"]["status"] == "FAILED"
    assert cycle_res["results"]["snapshot"]["status"] == "SKIPPED"
    assert not snapshot_called, "Snapshot must NEVER be invoked when upstream fails!"


def test_executor_dry_run(memory_db):
    session_date = date(2026, 9, 8)

    dry_plan = run_dag_cycle(
        memory_db,
        session_date,
        dry_run=True,
    )
    assert dry_plan["dry_run"] is True
    assert len(dry_plan["plan"]) == len(PIPELINE_DAG)
    assert dry_plan["plan"][0]["planned_action"] == "EXECUTE"
    assert "BLOCKED" in dry_plan["plan"][2]["planned_action"]  # snapshot blocked until flow runs
