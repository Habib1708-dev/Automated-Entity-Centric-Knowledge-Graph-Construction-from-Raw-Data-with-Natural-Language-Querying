"""Orchestration: the stage abstraction (stage.py), the concrete stages (stages.py), the runner (runner.py).

Public surface for the CLI and for tests.
"""

from .runner import FULL_PIPELINE, run_all, run_stages
from .stage import PipelineContext, PipelineState, Stage

__all__ = ["FULL_PIPELINE", "PipelineContext", "PipelineState", "Stage", "run_all", "run_stages"]
