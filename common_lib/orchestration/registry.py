"""
Pipeline Registry and Declarative DAG Definitions.

Provides dependency graph declaration, topological ordering via Python 3.9+
built-in graphlib.TopologicalSorter, transitive downstream resolution, and
dynamic discovery of pipeline.yaml manifests.
"""

import os
import sys
import logging
import importlib
from pathlib import Path
from graphlib import TopologicalSorter, CycleError
from typing import Dict, List, Any, Callable, Optional, Union

from common_lib.orchestration.manifest import PipelineManifest, parse_pipeline_manifest

logger = logging.getLogger("quant.orchestration.registry")


PIPELINE_DAG: Dict[str, Dict[str, Any]] = {
    "unusual_option_flow": {
        "upstream": [],
        "runner": "common_lib.flow.runner:run_daily_incremental",
        "description": "Scrapes and cleans institutional sweeps and options flow from TradingEdge",
        "target_tables": ["unusual_option_flow_te"],
        "timeout_sec": 600,
        "display_name": "Options Flow Ingestion",
    },
    "quant_levels": {
        "upstream": [],
        "runner": "common_lib.quant_levels.runner:run_daily_incremental",
        "description": "Ingests daily key structural support & resistance levels from Mighty Networks",
        "target_tables": ["quant_lvl_data_te"],
        "timeout_sec": 300,
        "display_name": "Quant Levels Ingestion",
    },
    "gexdex_snapshot": {
        "upstream": ["unusual_option_flow"],
        "runner": "gexdex-snapshot-pipeline.src.scripts.daily_snapshot:run_snapshot_pipeline",
        "description": "Computes daily GEX/DEX scorecard watchlist snapshot from session flow",
        "target_tables": ["gexdex_snapshot"],
        "timeout_sec": 600,
        "display_name": "GEX/DEX Snapshot",
    },
    "market_confluence": {
        "upstream": ["unusual_option_flow", "gexdex_snapshot"],
        "runner": "market-confluence-pipeline.src.scripts.daily_incremental:run_pipeline",
        "description": "Scores multi-factor asymmetric confluence radar against flow and GEX/DEX",
        "target_tables": ["daily_confluence_scans", "daily_confluence_summary"],
        "timeout_sec": 600,
        "display_name": "Market Confluence Radar",
    },
}

_DYNAMIC_DAG_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def discover_pipeline_manifests(search_dirs: Optional[List[Union[str, Path]]] = None) -> Dict[str, PipelineManifest]:
    """
    Discovers all pipeline.yaml (or pipeline.yml) manifests in the workspace or candidate search directories.
    """
    manifests: Dict[str, PipelineManifest] = {}

    if search_dirs is None:
        candidate_roots = [
            Path.cwd(),
            Path.cwd().parent,
            Path(__file__).resolve().parent.parent.parent.parent,
            Path("/volume2/homes/rachardv/git-repos/master"),
            Path("/app"),
        ]
        valid_roots = [p for p in candidate_roots if p.is_dir()]
    else:
        valid_roots = [Path(p) for p in search_dirs if Path(p).is_dir()]

    discovered_paths = set()
    for root in valid_roots:
        # Search direct children and subdirectories for pipeline.yaml
        for pattern in ["pipeline.yaml", "pipeline.yml", "*/pipeline.yaml", "*/pipeline.yml"]:
            for match in root.glob(pattern):
                match_resolved = match.resolve()
                if match_resolved in discovered_paths:
                    continue
                discovered_paths.add(match_resolved)
                try:
                    manifest = parse_pipeline_manifest(match_resolved)
                    manifests[manifest.name] = manifest
                    logger.debug(f"Discovered pipeline manifest '{manifest.name}' at {match_resolved}")
                except Exception as ex:
                    logger.warning(f"Failed to parse manifest at {match_resolved}: {ex}")

    return manifests


def build_dag_from_manifests(manifests: Dict[str, PipelineManifest]) -> Dict[str, Dict[str, Any]]:
    """Converts a dictionary of PipelineManifest objects into the standard DAG dictionary."""
    dag: Dict[str, Dict[str, Any]] = {}
    for name, m in manifests.items():
        dag[name] = {
            "upstream": m.upstream,
            "runner": m.runner,
            "description": m.description,
            "target_tables": m.target_tables,
            "timeout_sec": m.timeout_sec,
            "display_name": m.display_name,
            "version": m.version,
            "category": m.category,
        }
    return dag


def get_registered_dag(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Returns the comprehensive pipeline DAG, merging built-in PIPELINE_DAG
    with dynamically discovered pipeline.yaml manifests.
    """
    global _DYNAMIC_DAG_CACHE
    if _DYNAMIC_DAG_CACHE is not None and not force_refresh:
        return _DYNAMIC_DAG_CACHE

    combined = dict(PIPELINE_DAG)
    try:
        discovered = discover_pipeline_manifests()
        if discovered:
            discovered_dag = build_dag_from_manifests(discovered)
            combined.update(discovered_dag)
    except Exception as e:
        logger.warning(f"Error during dynamic manifest discovery: {e}")

    _DYNAMIC_DAG_CACHE = combined
    return combined


def get_topological_order(dag: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """
    Returns a linear execution order of pipelines respecting all dependencies.
    Raises ValueError if a cycle is detected.
    """
    if dag is None:
        dag = get_registered_dag()

    # graphlib expects node -> set of predecessors
    graph = {name: set(meta.get("upstream", [])) for name, meta in dag.items()}
    ts = TopologicalSorter(graph)
    try:
        return list(ts.static_order())
    except CycleError as e:
        raise ValueError(f"Cyclic dependency detected in pipeline DAG: {e}")


def get_topological_batches(dag: Optional[Dict[str, Dict[str, Any]]] = None) -> List[List[str]]:
    """
    Returns grouped batches of pipelines where all items in a batch can execute
    concurrently (or sequentially) without dependency conflicts.
    """
    if dag is None:
        dag = get_registered_dag()

    graph = {name: set(meta.get("upstream", [])) for name, meta in dag.items()}
    ts = TopologicalSorter(graph)
    ts.prepare()

    batches = []
    while ts.is_active():
        ready = list(ts.get_ready())
        if not ready:
            break
        batches.append(ready)
        for node in ready:
            ts.done(node)
    return batches


def get_downstream_dependencies(pipeline_name: str, dag: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """
    Returns all direct and transitive downstream dependents of pipeline_name.
    Useful for '--from <pipeline>' cascading restarts.
    """
    if dag is None:
        dag = get_registered_dag()

    if pipeline_name not in dag:
        raise KeyError(f"Unknown pipeline: '{pipeline_name}'")

    downstream = set()
    to_visit = [pipeline_name]

    while to_visit:
        current = to_visit.pop(0)
        for name, meta in dag.items():
            if current in meta.get("upstream", []):
                if name not in downstream:
                    downstream.add(name)
                    to_visit.append(name)

    # Return in topological order
    full_order = get_topological_order(dag)
    return [name for name in full_order if name in downstream]


def get_upstream_dependencies(pipeline_name: str, dag: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """Returns direct upstream dependencies of pipeline_name."""
    if dag is None:
        dag = get_registered_dag()
    if pipeline_name not in dag:
        raise KeyError(f"Unknown pipeline: '{pipeline_name}'")
    return list(dag[pipeline_name].get("upstream", []))


def resolve_runner(runner_spec: Union[str, Callable]) -> Callable:
    """
    Resolves a callable runner from a string spec 'module_path:function_name'
    or returns the callable directly. Supports BasePipeline class instances.
    """
    if callable(runner_spec):
        return runner_spec

    if not isinstance(runner_spec, str) or ":" not in runner_spec:
        raise ValueError(f"Invalid runner specification '{runner_spec}'. Expected 'module.path:func_name'")

    module_name, func_name = runner_spec.split(":", 1)
    try:
        try:
            mod = importlib.import_module(module_name)
        except (ModuleNotFoundError, ImportError):
            parts = module_name.split(".")
            repo_folder = parts[0]
            candidate_paths = [
                Path("/app") / repo_folder,
                Path(__file__).resolve().parent.parent.parent.parent / repo_folder,
                Path.cwd() / repo_folder,
                Path.cwd().parent / repo_folder,
                Path("/volume2/homes/rachardv/git-repos/master") / repo_folder,
            ]
            for cp in candidate_paths:
                if cp.is_dir() and str(cp) not in sys.path:
                    sys.path.insert(0, str(cp))
            mod = importlib.import_module(".".join(parts[1:]))

        target = getattr(mod, func_name)
        if isinstance(target, type):
            # Instantiate class if class is a BasePipeline runner
            instance = target()
            if hasattr(instance, "run") and callable(instance.run):
                return instance.run
        if not callable(target):
            raise TypeError(f"Attribute '{func_name}' in module '{module_name}' is not callable.")
        return target
    except Exception as e:
        raise ImportError(f"Failed to resolve runner '{runner_spec}': {e}")
