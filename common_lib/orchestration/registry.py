"""
Pipeline Registry and Declarative DAG Definitions.

Provides dependency graph declaration, topological ordering via Python 3.9+
built-in graphlib.TopologicalSorter, and transitive downstream resolution.
"""

import importlib
from graphlib import TopologicalSorter, CycleError
from typing import Dict, List, Any, Callable, Optional


PIPELINE_DAG: Dict[str, Dict[str, Any]] = {
    "unusual_option_flow": {
        "upstream": [],
        "runner": "common_lib.flow.runner:run_daily_incremental",
        "description": "Scrapes and cleans institutional sweeps and options flow from TradingEdge",
        "target_tables": ["unusual_option_flow_te"],
        "timeout_sec": 600,
    },
    "quant_levels": {
        "upstream": [],
        "runner": "common_lib.quant_levels.runner:run_daily_incremental",
        "description": "Ingests daily key structural support & resistance levels from Mighty Networks",
        "target_tables": ["quant_lvl_data_te"],
        "timeout_sec": 300,
    },
    "gexdex_snapshot": {
        "upstream": ["unusual_option_flow"],
        "runner": "quant-pwa.gateway.app.engine.snapshot_pipeline:run_snapshot_pipeline",
        "fallback_runner": "gexdex-snapshot-pipeline.src.scripts.daily_snapshot:run_snapshot_pipeline",
        "description": "Computes daily GEX/DEX scorecard watchlist snapshot from session flow",
        "target_tables": ["gexdex_snapshot"],
        "timeout_sec": 600,
    },
    "market_confluence": {
        "upstream": ["unusual_option_flow", "gexdex_snapshot"],
        "runner": "market-confluence-pipeline.src.scripts.daily_incremental:run_pipeline",
        "description": "Scores multi-factor asymmetric confluence radar against flow and GEX/DEX",
        "target_tables": ["daily_confluence_scans", "daily_confluence_summary"],
        "timeout_sec": 600,
    },
}


def get_topological_order(dag: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """
    Returns a linear execution order of pipelines respecting all dependencies.
    Raises ValueError if a cycle is detected.
    """
    if dag is None:
        dag = PIPELINE_DAG

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
        dag = PIPELINE_DAG

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
        dag = PIPELINE_DAG

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
        dag = PIPELINE_DAG
    if pipeline_name not in dag:
        raise KeyError(f"Unknown pipeline: '{pipeline_name}'")
    return list(dag[pipeline_name].get("upstream", []))


def resolve_runner(runner_spec: str | Callable) -> Callable:
    """
    Resolves a callable runner from a string spec 'module_path:function_name'
    or returns the callable directly.
    """
    if callable(runner_spec):
        return runner_spec

    if not isinstance(runner_spec, str) or ":" not in runner_spec:
        raise ValueError(f"Invalid runner specification '{runner_spec}'. Expected 'module.path:func_name'")

    module_name, func_name = runner_spec.split(":", 1)
    try:
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name)
        if not callable(func):
            raise TypeError(f"Attribute '{func_name}' in module '{module_name}' is not callable.")
        return func
    except Exception as e:
        raise ImportError(f"Failed to resolve runner '{runner_spec}': {e}")
