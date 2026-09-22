"""The tracking port: what the pipeline needs from an experiment tracker, independent of MLflow.

Role in the pipeline: every stage runs inside `tracker.start_run(...)` and logs params, metrics and
artifacts on the `Run` it gets back. LLM adapters report their calls to `tracker.record_llm_call`.
Design: Dependency Inversion + Null Object. `NullTracker` satisfies the same protocols and does nothing,
so tracking can be switched off (tests, a broken MLflow store) without a single `if` in stage code.
Not here: anything that imports mlflow (tracking/mlflow_tracker.py).
"""

import logging
import threading
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from ..llm.base import LLMCallRecord

log = logging.getLogger(__name__)


class ModelPrice(BaseModel):
    """List price of one model in USD per 1M tokens (prices.yaml). Thinking tokens are billed as output."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)


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
    """Thread-safe totals of the LLM calls made while one run was active, and their cost.

    `prices` is the table from prices.yaml; without it (None) no cost is computed.
    """

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._lock = threading.Lock()  # extraction reports from a thread pool
        self._calls = self._failures = self._cache_hits = self._embed_calls = 0
        self._prompt_tokens = self._completion_tokens = self._thinking_tokens = 0
        self._latency_s = 0.0
        self._prices = prices
        # per model, because a price belongs to a model and one run may use several (plan and extraction)
        self._input_by_model: dict[str, int] = {}
        self._output_by_model: dict[str, int] = {}  # billed output: visible answer + thinking

    def add(self, record: LLMCallRecord) -> None:
        with self._lock:
            # embedding batches are counted apart, so `llm_calls` keeps meaning "generate requests"
            if record.kind == "embed":
                self._embed_calls += 1
            else:
                self._calls += 1  # failed attempts included: each one is a request that was sent
                self._failures += int(not record.ok)
                self._cache_hits += int(record.cache_hit)
            self._prompt_tokens += record.prompt_tokens or 0
            self._completion_tokens += record.completion_tokens or 0
            self._thinking_tokens += record.thinking_tokens or 0
            self._latency_s += record.latency_s
            input_tokens = record.prompt_tokens or 0
            output_tokens = (record.completion_tokens or 0) + (record.thinking_tokens or 0)
            if input_tokens or output_tokens:  # a cache hit or an embedding without usage costs nothing
                model = record.model
                self._input_by_model[model] = self._input_by_model.get(model, 0) + input_tokens
                self._output_by_model[model] = self._output_by_model.get(model, 0) + output_tokens

    def as_metrics(self) -> dict[str, float]:
        """Metric names are part of the MLflow contract (see the mlflow-tracking skill); keep them stable.

        Billed output tokens are `completion_tokens + thinking_tokens`. `cost_usd` (list price, see
        prices.yaml) is left out when no price table was given or a model that used tokens has no price.
        """
        with self._lock:
            cost = self._cost()
            return {
                "llm_calls": self._calls,
                "llm_failures": self._failures,
                "cache_hits": self._cache_hits,
                "embed_calls": self._embed_calls,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "thinking_tokens": self._thinking_tokens,
                "llm_latency_s": round(self._latency_s, 3),
                **({"cost_usd": round(cost, 6)} if cost is not None else {}),
            }

    def _cost(self) -> float | None:
        """USD for the tokens so far, or None when it cannot be known. Call with the lock held."""
        if self._prices is None:
            return None
        unpriced = sorted(set(self._input_by_model) - set(self._prices))
        if unpriced:
            # a partial sum would look like a real cost and understate it: log no number instead
            log.warning("no price in prices.yaml for %s: cost_usd not logged", ", ".join(unpriced))
            return None
        return sum(
            (
                self._input_by_model[m] * self._prices[m].input
                + self._output_by_model[m] * self._prices[m].output
            )
            / 1_000_000
            for m in self._input_by_model
        )
