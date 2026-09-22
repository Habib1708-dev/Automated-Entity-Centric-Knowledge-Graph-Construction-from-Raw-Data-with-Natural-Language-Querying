"""MLflow implementation of the tracking port: runs, params, metrics, artifacts and LLM traces.

Role in the pipeline: built once by the composition root and handed to the pipeline (as `Tracker`) and
to the LLM adapters (as the call listener).
Design: Adapter. The only module that imports mlflow. `create_tracker` falls back to `NullTracker` when
MLflow cannot start, and every MLflow call (including opening and closing a run) is guarded, because
tracking must never break a pipeline run.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import mlflow

from ..llm.base import LLMCallRecord
from .base import ModelPrice, NullRun, NullTracker, Run, Tracker, UsageMeter

log = logging.getLogger(__name__)

# MLflow rejects param values longer than this (6000 in recent versions; 500 is safe everywhere).
_MAX_PARAM_CHARS = 500

# The trace metadata key MLflow itself uses to attach a trace to a run. MLflow fills it only from the
# calling thread's active run, and worker threads have none, so we set it ourselves.
_SOURCE_RUN_KEY = "mlflow.sourceRun"


def create_tracker(
    tracking_uri: str,
    experiment: str,
    tags: dict[str, str] | None = None,
    prices: dict[str, ModelPrice] | None = None,
) -> Tracker:
    """Return an `MlflowTracker`, or a `NullTracker` (with one warning) when the store is unusable.

    `tags` are put on every run (for example `git_sha`, `code_version`), next to the `stage` tag;
    `prices` (prices.yaml) lets every run log `cost_usd`.
    """
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment)
    except Exception as e:  # any store/config problem: degrade to no tracking instead of failing the run
        log.warning("MLflow disabled: %s", e)
        return NullTracker()
    return MlflowTracker(tags, prices)


class MlflowRun:
    """`Run` backed by the currently active MLflow run."""

    def params(self, **values: object) -> None:
        _guarded(mlflow.log_params, {k: str(v)[:_MAX_PARAM_CHARS] for k, v in values.items()})

    def metrics(self, **values: float | int | None) -> None:
        _guarded(mlflow.log_metrics, {k: float(v) for k, v in values.items() if v is not None})

    def artifact(self, path: Path) -> None:
        if Path(path).exists():
            _guarded(mlflow.log_artifact, str(path))

    def text(self, content: str, artifact_path: str) -> None:
        _guarded(mlflow.log_text, content, artifact_path)


@dataclass
class _OpenRun:
    """A run that is open right now: its id (None when MLflow failed to open it) and its usage meter."""

    run_id: str | None
    meter: UsageMeter


class MlflowTracker:
    """`Tracker` backed by MLflow. Expects `create_tracker` to have selected the store and experiment."""

    def __init__(
        self, tags: dict[str, str] | None = None, prices: dict[str, ModelPrice] | None = None
    ) -> None:
        self._tags = dict(tags or {})
        self._prices = prices
        # All runs open right now, outermost first (pipeline, then the current stage). A plain list under a
        # lock, not thread-local state: LLM calls arrive from extraction worker threads, and they must
        # count towards (and be traced under) the runs that the main thread opened.
        self._open: list[_OpenRun] = []
        self._lock = threading.Lock()

    @contextmanager
    def start_run(self, name: str, **params: object) -> Iterator[Run]:
        run_id = _open_mlflow_run(name, {**self._tags, "stage": name})
        run: Run = MlflowRun() if run_id is not None else NullRun()
        entry = _OpenRun(run_id, UsageMeter(self._prices))
        started = time.perf_counter()
        run.params(**params)
        with self._lock:
            self._open.append(entry)
        failed = False
        try:
            yield run
        except BaseException:
            failed = True
            raise
        finally:
            # logged even when the stage fails, so failed runs still show their cost and duration
            with self._lock:
                self._open.remove(entry)
            run.metrics(duration_s=round(time.perf_counter() - started, 3), **entry.meter.as_metrics())
            if run_id is not None:
                _guarded(mlflow.end_run, "FAILED" if failed else "FINISHED")

    def record_llm_call(self, record: LLMCallRecord) -> None:
        with self._lock:
            open_runs = list(self._open)
        for entry in open_runs:
            entry.meter.add(record)
        # the innermost open run is the stage that made the call
        run_id = open_runs[-1].run_id if open_runs else None
        _guarded(_trace, record, run_id)


def _open_mlflow_run(name: str, tags: dict[str, str]) -> str | None:
    """Start a run (nested when another one is active) and return its id, or None if MLflow fails."""
    try:
        nested = mlflow.active_run() is not None
        return mlflow.start_run(run_name=name, nested=nested, tags=tags).info.run_id
    except Exception as e:  # broad on purpose: a locked or broken store must not stop the pipeline
        log.warning("MLflow could not open run '%s'; continuing without tracking it: %s", name, e)
        return None


def _trace(record: LLMCallRecord, run_id: str | None) -> None:
    """Write one MLflow Tracing span for a finished request and attach it to `run_id`.

    The span is created after the fact, so its own duration is meaningless; the real latency is an
    attribute. The API key is never part of a record, so nothing secret can end up in a trace.
    """
    with mlflow.start_span(
        name=f"llm.{record.kind}", span_type="EMBEDDING" if record.kind == "embed" else "LLM"
    ) as span:
        span.set_inputs({"prompt": record.prompt})
        span.set_outputs({"response": record.response})
        span.set_attributes(
            {
                "model": record.model,
                "temperature": record.temperature,
                "ok": record.ok,
                "latency_s": record.latency_s,
                "cache_hit": record.cache_hit,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "thinking_tokens": record.thinking_tokens,
            }
        )
        if run_id is not None:
            mlflow.update_current_trace(metadata={_SOURCE_RUN_KEY: run_id})


def _guarded(fn, *args) -> None:
    """Call an MLflow function; log and swallow any failure (tracking must never break the pipeline)."""
    try:
        fn(*args)
    except Exception as e:  # broad on purpose: MLflow raises many unrelated exception types
        log.warning("MLflow logging failed (%s): %s", getattr(fn, "__name__", fn), e)
