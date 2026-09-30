"""
Pipeline Base Contract & Result Models.

Defines the standard execution protocol and return types for all Quant System pipelines.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Dict, Any, List


@dataclass
class PipelineResult:
    """
    Standardized result contract returned by all pipeline runners.
    """
    pipeline_name: str
    session_date: date
    success: bool
    rows_affected: int = 0
    duration_sec: float = 0.0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_name": self.pipeline_name,
            "session_date": str(self.session_date),
            "status": "SUCCESS" if self.success else "FAILED",
            "rows_affected": self.rows_affected,
            "duration_sec": self.duration_sec,
            "error": self.error_message,
            "metadata": self.metadata,
        }


class BasePipeline(ABC):
    """
    Abstract base class providing standard interface for all pipeline runners.
    """
    name: str = ""
    display_name: str = ""
    description: str = ""
    upstream: List[str] = []
    target_tables: List[str] = []
    timeout_sec: int = 600

    @abstractmethod
    def run(self, session_date: date, config: Optional[Dict[str, Any]] = None, dry_run: bool = False) -> PipelineResult:
        """
        Executes the pipeline for the given market session date.
        Must return a structured PipelineResult.
        """
        pass
