"""
Prefect Flow Definition for Quant System Market Pipelines.

Orchestrates daily incremental market data pipelines with native dependency gating,
exponential backoff retries, and unified checkpointing.
"""

import os
import sys
import logging
import subprocess
from datetime import datetime, date
from typing import Dict, Any, Optional, List

try:
    from prefect import flow, task
except ImportError:
    # Fallback decorators if prefect package is importing in minimal environment
    def flow(*args, **kwargs):
        def decorator(f):
            f.name = kwargs.get("name", f.__name__)
            return f
        if len(args) == 1 and callable(args[0]):
            args[0].name = args[0].__name__
            return args[0]
        return decorator

    def task(*args, **kwargs):
        def decorator(f):
            return f
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator

logger = logging.getLogger("quant.orchestration.prefect")

ACTIVE_PIPELINES = [
    "quant_levels",
    "unusual_options_flow",
    "unusual_option_flow",
    "gexdex_snapshot",
    "market_confluence"
]
INACTIVE_PIPELINES = [
    "economic_events",
]


@task(name="execute-quant-levels", retries=2, retry_delay_seconds=10)
def execute_quant_levels(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Executes the Quant Levels daily incremental pipeline."""
    logger.info(f"🚀 [Prefect] Starting Quant Levels Pipeline for session: {session_date or 'latest'}")
    root_path = os.getenv("SYNOLOGY_ROOT_PATH", "/volume2/homes/rachardv/git-repos/master")
    proj_dir = os.path.join(root_path, "quant-level-pipeline")
    src_dir = os.path.join(proj_dir, "src")
    common_lib_dir = os.path.join(root_path, "common-lib")
    script = os.path.join(src_dir, "scripts", "daily_incremental.py")
    
    python_bin = sys.executable
    cmd = [python_bin, script]
    if session_date:
        cmd.extend(["--date", str(session_date)])

    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{proj_dir}:{common_lib_dir}:{current_pp}".rstrip(":")

    res = subprocess.run(cmd, cwd=proj_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"❌ Quant Levels failed (exit {res.returncode}): {res.stderr}")
        raise RuntimeError(f"Quant Levels failed: {res.stderr}")

    logger.info("✅ Quant Levels Pipeline completed successfully.")
    return {"status": "SUCCESS", "pipeline": "quant_levels", "session_date": session_date}


@task(name="execute-unusual-flow", retries=2, retry_delay_seconds=10)
def execute_unusual_flow(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Executes the Unusual Options Flow daily incremental pipeline."""
    logger.info(f"🚀 [Prefect] Starting Unusual Options Flow Pipeline for session: {session_date or 'latest'}")
    root_path = os.getenv("SYNOLOGY_ROOT_PATH", "/volume2/homes/rachardv/git-repos/master")
    proj_dir = os.path.join(root_path, "unusual-option-flow-pipeline")
    src_dir = os.path.join(proj_dir, "src")
    common_lib_dir = os.path.join(root_path, "common-lib")
    script = os.path.join(src_dir, "scripts", "daily_incremental.py")

    python_bin = sys.executable
    cmd = [python_bin, script]
    if session_date:
        cmd.extend(["--date", str(session_date)])

    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{proj_dir}:{src_dir}:{common_lib_dir}:{current_pp}".rstrip(":")

    res = subprocess.run(cmd, cwd=proj_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"❌ Unusual Flow failed (exit {res.returncode}): {res.stderr}")
        raise RuntimeError(f"Unusual Flow failed: {res.stderr}")

    logger.info("✅ Unusual Options Flow Pipeline completed successfully.")
    return {"status": "SUCCESS", "pipeline": "unusual_options_flow", "session_date": session_date}


@task(name="execute-snapshot", retries=2, retry_delay_seconds=10)
def execute_snapshot(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Executes the GEX/DEX Snapshot daily pipeline (rate limited to <50 requests)."""
    logger.info(f"🚀 [Prefect] Starting GEX/DEX Snapshot Pipeline for session: {session_date or 'latest'}")
    root_path = os.getenv("SYNOLOGY_ROOT_PATH", "/volume2/homes/rachardv/git-repos/master")
    proj_dir = os.path.join(root_path, "gexdex-snapshot-pipeline")
    src_dir = os.path.join(proj_dir, "src")
    common_lib_dir = os.path.join(root_path, "common-lib")
    script = os.path.join(src_dir, "scripts", "daily_snapshot.py")

    python_bin = sys.executable
    cmd = [python_bin, script]
    if session_date:
        cmd.extend(["--date", str(session_date)])

    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{proj_dir}:{src_dir}:{common_lib_dir}:{current_pp}".rstrip(":")

    res = subprocess.run(cmd, cwd=proj_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"❌ GEX/DEX Snapshot failed (exit {res.returncode}): {res.stderr}")
        raise RuntimeError(f"GEX/DEX Snapshot failed: {res.stderr}")

    logger.info("✅ GEX/DEX Snapshot Pipeline completed successfully.")
    return {"status": "SUCCESS", "pipeline": "gexdex_snapshot", "session_date": session_date}


@task(name="execute-confluence", retries=2, retry_delay_seconds=10)
def execute_confluence(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Executes the Market Confluence daily radar pipeline (rate limited to <50 requests)."""
    logger.info(f"🚀 [Prefect] Starting Market Confluence Radar Pipeline for session: {session_date or 'latest'}")
    root_path = os.getenv("SYNOLOGY_ROOT_PATH", "/volume2/homes/rachardv/git-repos/master")
    proj_dir = os.path.join(root_path, "market-confluence-pipeline")
    src_dir = os.path.join(proj_dir, "src")
    common_lib_dir = os.path.join(root_path, "common-lib")
    script = os.path.join(src_dir, "scripts", "daily_incremental.py")

    python_bin = sys.executable
    cmd = [python_bin, script]
    if session_date:
        cmd.extend(["--date", str(session_date)])

    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{proj_dir}:{src_dir}:{common_lib_dir}:{current_pp}".rstrip(":")

    res = subprocess.run(cmd, cwd=proj_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"❌ Market Confluence failed (exit {res.returncode}): {res.stderr}")
        raise RuntimeError(f"Market Confluence failed: {res.stderr}")

    logger.info("✅ Market Confluence Pipeline completed successfully.")
    return {"status": "SUCCESS", "pipeline": "market_confluence", "session_date": session_date}


@flow(name="daily-market-cycle", description="Daily Morning Quant Data Pipeline Orchestration Flow")
def daily_market_cycle_flow(
    session_date: Optional[str] = None,
    only_pipeline: Optional[str] = None,
    from_pipeline: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main DAG Flow coordinating the active market pipelines in topological sequence.
    Sequence: quant_levels & unusual_options_flow -> gexdex_snapshot -> market_confluence
    """
    results: Dict[str, Any] = {}

    # Handle selective execution (single pipeline rerun from settings)
    if only_pipeline:
        if only_pipeline == "quant_levels":
            results["quant_levels"] = execute_quant_levels(session_date)
            return results
        elif only_pipeline in ("unusual_options_flow", "unusual_option_flow"):
            results["unusual_options_flow"] = execute_unusual_flow(session_date)
            return results
        elif only_pipeline == "gexdex_snapshot":
            results["gexdex_snapshot"] = execute_snapshot(session_date)
            return results
        elif only_pipeline == "market_confluence":
            results["market_confluence"] = execute_confluence(session_date)
            return results
        elif only_pipeline in INACTIVE_PIPELINES:
            logger.warning(f"Pipeline '{only_pipeline}' is currently INACTIVE.")
            return {only_pipeline: {"status": "INACTIVE", "message": "Pipeline is disabled."}}

    # Default Full Cycle: Run active pipelines in strict topological sequence
    if "quant_levels" in ACTIVE_PIPELINES:
        results["quant_levels"] = execute_quant_levels(session_date)

    if "unusual_options_flow" in ACTIVE_PIPELINES:
        results["unusual_options_flow"] = execute_unusual_flow(session_date)

    # Downstream Tier: Snapshot depends on Flow
    if "gexdex_snapshot" in ACTIVE_PIPELINES:
        results["gexdex_snapshot"] = execute_snapshot(session_date)

    # Downstream Tier: Confluence depends on Flow & Snapshot
    if "market_confluence" in ACTIVE_PIPELINES:
        results["market_confluence"] = execute_confluence(session_date)

    return results



if __name__ == "__main__":
    daily_market_cycle_flow()
