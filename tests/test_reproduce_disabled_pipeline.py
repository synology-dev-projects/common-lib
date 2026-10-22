import pytest
from common_lib.orchestration.manifest import parse_pipeline_manifest
from common_lib.orchestration.registry import get_registered_dag

def test_reproduce_pipeline_manifest_enabled_support():
    """
    REPRODUCTION TEST: Pipeline manifests must support 'enabled: bool'.
    Expected: Manifest parses enabled=False, and get_registered_dag() ignores disabled pipelines.
    Actual: PipelineManifest lacks 'enabled', and economic_events is actively registered in DAG.
    """
    # 1. Arrange & Act: parse manifest with enabled: false
    yaml_text = """
    name: disabled_test_pipeline
    display_name: Disabled Test
    description: Should not appear in DAG
    runner: dummy.module:runner
    enabled: false
    """
    manifest = parse_pipeline_manifest(yaml_text)

    # 2. Assert: manifest has enabled attribute and is False
    assert hasattr(manifest, "enabled"), "PipelineManifest must have 'enabled' attribute"
    assert manifest.enabled is False, f"Expected manifest.enabled=False, got {manifest.enabled}"

    # 3. Assert: economic_events must be disabled / not in active registered DAG
    dag = get_registered_dag(force_refresh=True)
    assert "economic_events" not in dag, (
        f"Pipeline 'economic_events' is disabled for FEAT-002, but is present in registered DAG: {list(dag.keys())}"
    )
