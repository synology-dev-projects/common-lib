"""
Quant System Pipeline Orchestration & Dependency Management Framework.

Zero-bloat, in-process DAG resolution, PostgreSQL execution checkpointing,
dynamic manifest discovery, and resilience gates.
"""

from common_lib.orchestration.base import PipelineResult, BasePipeline
from common_lib.orchestration.manifest import PipelineManifest, parse_pipeline_manifest
from common_lib.orchestration.registry import (
    PIPELINE_DAG,
    get_registered_dag,
    discover_pipeline_manifests,
    build_dag_from_manifests,
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
    check_upstream_dependencies,
    get_all_pipeline_statuses,
)
from common_lib.orchestration.executor import execute_single_pipeline, run_dag_cycle

__all__ = [
    "PipelineResult",
    "BasePipeline",
    "PipelineManifest",
    "parse_pipeline_manifest",
    "PIPELINE_DAG",
    "get_registered_dag",
    "discover_pipeline_manifests",
    "build_dag_from_manifests",
    "get_topological_order",
    "get_topological_batches",
    "get_downstream_dependencies",
    "get_upstream_dependencies",
    "resolve_runner",
    "ensure_pipeline_runs_table",
    "record_run_start",
    "record_run_success",
    "record_run_failure",
    "record_run_skip",
    "get_pipeline_status",
    "check_upstream_dependencies",
    "get_all_pipeline_statuses",
    "execute_single_pipeline",
    "run_dag_cycle",
]
