"""
Contract tests for PipelineResult, PipelineManifest, and Dynamic Registry Auto-Discovery.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import date
import sqlalchemy as sa

# These imports will fail initially (RED GATE) until implemented
from common_lib.orchestration.base import PipelineResult, BasePipeline
from common_lib.orchestration.manifest import PipelineManifest, parse_pipeline_manifest
from common_lib.orchestration.registry import (
    discover_pipeline_manifests,
    build_dag_from_manifests,
    get_topological_order,
)
from common_lib.orchestration.executor import execute_single_pipeline
from common_lib.orchestration.state import ensure_pipeline_runs_table


@pytest.fixture
def memory_db():
    engine = sa.create_engine("sqlite:///:memory:")
    ensure_pipeline_runs_table(engine)
    return engine


def test_pipeline_result_dataclass():
    res = PipelineResult(
        pipeline_name="test_pipeline",
        session_date=date(2026, 9, 29),
        success=True,
        rows_affected=150,
        duration_sec=3.14,
        metadata={"source": "api_test"}
    )
    assert res.pipeline_name == "test_pipeline"
    assert res.session_date == date(2026, 9, 29)
    assert res.success is True
    assert res.rows_affected == 150
    assert res.duration_sec == 3.14
    assert res.metadata["source"] == "api_test"
    assert res.error_message is None


def test_pipeline_manifest_parsing():
    manifest_yaml = """
name: sample_pipeline
display_name: Sample Ingestion Pipeline
description: Ingests structured test data
version: 1.0.0
category: test
upstream:
  - upstream_a
target_tables:
  - sample_table
timeout_sec: 450
runner: sample_module:run_pipeline
"""
    manifest = parse_pipeline_manifest(manifest_yaml)
    assert manifest.name == "sample_pipeline"
    assert manifest.display_name == "Sample Ingestion Pipeline"
    assert manifest.description == "Ingests structured test data"
    assert manifest.upstream == ["upstream_a"]
    assert manifest.target_tables == ["sample_table"]
    assert manifest.timeout_sec == 450
    assert manifest.runner == "sample_module:run_pipeline"


def test_dynamic_manifest_discovery(tmp_path):
    # Create two temporary pipeline directories with pipeline.yaml manifests
    p1_dir = tmp_path / "pipe-one"
    p1_dir.mkdir()
    (p1_dir / "pipeline.yaml").write_text("""
name: pipe_one
display_name: Pipe One
description: Root pipeline
upstream: []
runner: dummy:run_one
target_tables: ["table_one"]
""", encoding="utf-8")

    p2_dir = tmp_path / "pipe-two"
    p2_dir.mkdir()
    (p2_dir / "pipeline.yaml").write_text("""
name: pipe_two
display_name: Pipe Two
description: Child pipeline
upstream:
  - pipe_one
runner: dummy:run_two
target_tables: ["table_two"]
""", encoding="utf-8")

    discovered = discover_pipeline_manifests(search_dirs=[tmp_path])
    assert "pipe_one" in discovered
    assert "pipe_two" in discovered

    dag = build_dag_from_manifests(discovered)
    assert dag["pipe_one"]["upstream"] == []
    assert dag["pipe_two"]["upstream"] == ["pipe_one"]

    order = get_topological_order(dag)
    assert order.index("pipe_one") < order.index("pipe_two")


def test_executor_handles_pipeline_result(memory_db):
    test_dag = {
        "result_pipeline": {
            "upstream": [],
            "runner": "dummy:dummy_runner",
            "description": "Tests PipelineResult return handling",
            "target_tables": ["test_tbl"],
            "timeout_sec": 60,
        }
    }

    # Runner that returns standard PipelineResult object
    def mock_runner(session_date: date):
        return PipelineResult(
            pipeline_name="result_pipeline",
            session_date=session_date,
            success=True,
            rows_affected=342,
            duration_sec=1.25,
            metadata={"custom_metric": 99}
        )

    res = execute_single_pipeline(
        engine=memory_db,
        pipeline_name="result_pipeline",
        session_date=date(2026, 9, 29),
        dag=test_dag,
        runner_override=mock_runner,
    )

    assert res["status"] == "SUCCESS"
    assert res["rows_affected"] == 342
    assert "run_id" in res
