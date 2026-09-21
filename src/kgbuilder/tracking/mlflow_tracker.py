"""MLflow implementation of the tracking port: runs, params, metrics, artifacts and LLM traces.

Role in the pipeline: built once by the composition root and handed to the pipeline (as `Tracker`) and
to the LLM adapters (as the call listener).
Design: Adapter. The only module that imports mlflow. `create_tracker` falls back to `NullTracker` when
MLflow cannot start, and every logging call is guarded, because tracking must never break a pipeline run.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import mlflow

from ..llm.base import LLMCallRecord
from .base import NullTracker, Run, Tracker, UsageMeter

log = logging.getLogger(__name__)

# MLflow rejects param values longer than this (6000 in recent versions; 500 is safe everywhere).
_MAX_PARAM_CHARS = 500


def create_tracker(tracking_uri: str, experiment: str) -> Tracker:
    """Return an `MlflowTracker`, or a `NullTracker` (with one warning) when the store is unusable."""
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment)
    except Exception as e:  # any store/config problem: degrade to no tracking instead of failing the run
        log.warning("MLflow disabled: %s", e)
        return NullTracker()
    return MlflowTracker()


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


class MlflowTracker:
    """`Tracker` backed by MLflow. Expects `create_tracker` to have selected the store and experiment."""

    def __init__(self) -> None:
        # Meters of all runs that are open right now (pipeline + current stage). A plain list under a
        # lock, not thread-local state: LLM calls arrive from extraction worker threads, and they must
        # count towards the stage run that the main thread opened.
        self._meters: list[UsageMeter] = []
        self._lock = threading.Lock()

    @contextmanager
    def start_run(self, name: str, **params: object) -> Iterator[Run]:
        nested = mlflow.active_run() is not None
        meter = UsageMeter()
        started = time.perf_counter()
        with mlflow.start_run(run_name=name, nested=nested, tags={"stage": name}):
            run = MlflowRun()
            run.params(**params)
            with self._lock:
                self._meters.append(meter)
            try:
                yield run
            finally:
                # logged even when the stage fails, so failed runs still show their cost and duration
                with self._lock:
                    self._meters.remove(meter)
                run.metrics(duration_s=round(time.perf_counter() - started, 3), **meter.as_metrics())

    def record_llm_call(self, record: LLMCallRecord) -> None:
        with self._lock:
            meters = list(self._meters)
        for meter in meters:
            meter.add(record)
        _guarded(_trace, record)


def _trace(record: LLMCallRecord) -> None:
    """Write one MLflow Tracing span for a finished call.

    The span is created after the fact, so its own duration is meaningless; the real latency is an
    attribute. The API key is never part of a record, so nothing secret can end up in a trace.
    """
    with mlflow.start_span(name="llm.generate", span_type="LLM") as span:
        span.set_inputs({"prompt": record.prompt})
        span.set_outputs({"response": record.response})
        span.set_attributes(
            {
                "model": record.model,
                "temperature": record.temperature,
                "latency_s": record.latency_s,
                "cache_hit": record.cache_hit,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
            }
        )


def _guarded(fn, *args) -> None:
    """Call an MLflow function; log and swallow any failure (tracking must never break the pipeline)."""
    try:
        fn(*args)
    except Exception as e:  # broad on purpose: MLflow raises many unrelated exception types
        log.warning("MLflow logging failed (%s): %s", getattr(fn, "__name__", fn), e)
