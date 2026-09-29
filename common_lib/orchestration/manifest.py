"""
Pipeline Manifest Declarations & Parsers.

Parses and validates pipeline.yaml manifests for zero-boilerplate DAG registration.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
import yaml


@dataclass
class PipelineManifest:
    name: str
    display_name: str
    description: str
    runner: str
    upstream: List[str] = field(default_factory=list)
    target_tables: List[str] = field(default_factory=list)
    timeout_sec: int = 600
    version: str = "1.0.0"
    category: str = "general"
    schedule: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "runner": self.runner,
            "upstream": self.upstream,
            "target_tables": self.target_tables,
            "timeout_sec": self.timeout_sec,
            "version": self.version,
            "category": self.category,
            "schedule": self.schedule,
        }


def parse_pipeline_manifest(source: Union[str, Path, Dict[str, Any]]) -> PipelineManifest:
    """
    Parses a pipeline.yaml file path, YAML text content, or dictionary into a PipelineManifest.
    """
    if isinstance(source, Path):
        with open(source, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    elif isinstance(source, str):
        if "\n" not in source and Path(source).is_file():
            with open(source, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            data = yaml.safe_load(source)
    elif isinstance(source, dict):
        data = source
    else:
        raise TypeError(f"Expected Path, str, or dict for pipeline manifest, got {type(source)}")

    if not isinstance(data, dict):
        raise ValueError("Invalid pipeline manifest format: top-level structure must be a key-value mapping.")

    # Validation of required fields
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Pipeline manifest must specify a non-empty 'name'.")

    runner = str(data.get("runner", "")).strip()
    if not runner:
        raise ValueError(f"Pipeline manifest '{name}' must specify a 'runner' entrypoint.")

    display_name = str(data.get("display_name", "")).strip() or name.replace("_", " ").title()
    description = str(data.get("description", "")).strip()
    upstream = data.get("upstream") or []
    if not isinstance(upstream, list):
        upstream = [str(upstream)]
    upstream = [str(u).strip() for u in upstream if str(u).strip()]

    target_tables = data.get("target_tables") or []
    if not isinstance(target_tables, list):
        target_tables = [str(target_tables)]
    target_tables = [str(t).strip() for t in target_tables if str(t).strip()]

    timeout_sec = int(data.get("timeout_sec", 600))
    version = str(data.get("version", "1.0.0"))
    category = str(data.get("category", "general"))
    schedule = str(data.get("schedule", "")) if data.get("schedule") else None

    return PipelineManifest(
        name=name,
        display_name=display_name,
        description=description,
        runner=runner,
        upstream=upstream,
        target_tables=target_tables,
        timeout_sec=timeout_sec,
        version=version,
        category=category,
        schedule=schedule,
    )
