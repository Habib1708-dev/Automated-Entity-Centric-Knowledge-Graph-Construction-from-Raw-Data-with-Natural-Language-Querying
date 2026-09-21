"""The tracking port: what the pipeline needs from an experiment tracker, independent of MLflow.

Role in the pipeline: every stage runs inside `tracker.start_run(...)` and logs params, metrics and
artifacts on the `Run` it gets back. LLM adapters report their calls to `tracker.record_llm_call`.
Design: Dependency Inversion + Null Object. `NullTracker` satisfies the same protocols and does nothing,
so tracking can be switched off (tests, a broken MLflow store) without a single `if` in stage code.
Not here: anything that imports mlflow (tracking/mlflow_tracker.py).
"""

import threading
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Protocol

from ..llm.base import LLMCallRecord


class Run(Protocol):
    """One tracked execution of a stage."""

    def params(self, **values: object) -> None:
        """Log inputs known before the work starts (models, thresholds, prompt versions, paths)."""
        ...

    def metrics(self, **values: float | int | None) -> None:
        """Log measured outputs. `None` values are skipped, booleans should be passed as 0/1."""
        ...

    def artifact(self, path: Path) -> None:
        """Attach a file that the stage has finished writing."""
        ...

    def text(self, content: str, artifact_path: str) -> None:
        """Attach text without a file on disk, for example a prompt template under `prompts/plan.txt`."""
        ...


class Tracker(Protocol):
    """Opens runs and receives LLM call reports."""

    def start_run(self, name: str, **params: object) -> AbstractContextManager[Run]:
        """Open a run named after the stage; nested automatically when another run is active."""
        ...

    def record_llm_call(self, record: LLMCallRecord) -> None:
        """Trace one LLM call and count it towards every active run. Called from worker threads."""
        ...


class NullRun:
    """`Run` that ignores everything."""

    def params(self, **values: object) -> None: ...

    def metrics(self, **values: float | int | None) -> None: ...

    def artifact(self, path: Path) -> None: ...

    def text(self, content: str, artifact_path: str) -> None: ...


class NullTracker:
    """`Tracker` that ignores everything (Null Object)."""

    def start_run(self, name: str, **params: object) -> AbstractContextManager[Run]:
        return nullcontext(NullRun())

    def record_llm_call(self, record: LLMCallRecord) -> None: ...


class UsageMeter:
    """Thread-safe totals of the LLM calls made while one run was active."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # extraction reports from a thread pool
        self._calls = self._cache_hits = self._prompt_tokens = self._completion_tokens = 0
        self._latency_s = 0.0

    def add(self, record: LLMCallRecord) -> None:
        with self._lock:
            self._calls += 1
            self._cache_hits += int(record.cache_hit)
            self._prompt_tokens += record.prompt_tokens or 0
            self._completion_tokens += record.completion_tokens or 0
            self._latency_s += record.latency_s

    def as_metrics(self) -> dict[str, float]:
        """Metric names are part of the MLflow contract (see the mlflow-tracking skill); keep them stable."""
        with self._lock:
            return {
                "llm_calls": self._calls,
                "cache_hits": self._cache_hits,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "llm_latency_s": round(self._latency_s, 3),
            }
