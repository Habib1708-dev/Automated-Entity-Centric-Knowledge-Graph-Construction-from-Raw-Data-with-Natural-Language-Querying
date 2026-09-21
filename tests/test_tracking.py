"""The tracking port: usage metering, the Null Object, and the MLflow adapter against a temporary store.
Never touches the project's real mlflow.db."""

import mlflow
import pytest

from kgbuilder.llm.base import LLMCallRecord
from kgbuilder.tracking.base import NullTracker, UsageMeter
from kgbuilder.tracking.mlflow_tracker import MlflowTracker, create_tracker


def call(cache_hit: bool, prompt_tokens: int | None = None, completion_tokens: int | None = None):
    return LLMCallRecord(
        model="m",
        temperature=0.0,
        prompt="p",
        response="{}",
        latency_s=0.5,
        cache_hit=cache_hit,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def test_usage_meter_totals():
    meter = UsageMeter()
    meter.add(call(False, 10, 4))
    meter.add(call(True))
    assert meter.as_metrics() == {
        "llm_calls": 2,
        "cache_hits": 1,
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "llm_latency_s": 1.0,
    }


def test_null_tracker_accepts_everything(tmp_path):
    tracker = NullTracker()
    with tracker.start_run("stage", a=1) as run:
        run.params(b=2)
        run.metrics(c=3, d=None)
        run.artifact(tmp_path / "missing.json")
        run.text("x", "prompts/x.txt")
    tracker.record_llm_call(call(False))


@pytest.fixture
def store(tmp_path):
    """A throwaway MLflow store; restores the process-wide tracking URI afterwards."""
    previous = mlflow.get_tracking_uri()
    tracker = create_tracker(f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}", "test")
    yield tracker
    mlflow.set_tracking_uri(previous)


def test_mlflow_runs_are_nested_and_llm_usage_counts_towards_every_open_run(store):
    assert isinstance(store, MlflowTracker)
    with store.start_run("pipeline", goal="g"):
        with store.start_run("extract", model="m") as run:
            run.metrics(facts=3)
            store.record_llm_call(call(False, 10, 4))
        store.record_llm_call(call(True))

    runs = mlflow.search_runs(experiment_names=["test"]).set_index("tags.mlflow.runName")
    stage, parent = runs.loc["extract"], runs.loc["pipeline"]
    assert stage["tags.mlflow.parentRunId"] == parent["run_id"]
    assert stage["params.model"] == "m" and stage["metrics.facts"] == 3
    assert stage["metrics.llm_calls"] == 1 and stage["metrics.prompt_tokens"] == 10
    assert parent["metrics.llm_calls"] == 2 and parent["metrics.cache_hits"] == 1
    assert stage["metrics.duration_s"] >= 0


def test_stage_metrics_are_logged_even_when_the_stage_fails(store):
    with pytest.raises(ValueError), store.start_run("plan"):
        store.record_llm_call(call(False))
        raise ValueError("boom")
    run = mlflow.search_runs(experiment_names=["test"]).iloc[0]
    assert run["metrics.llm_calls"] == 1 and run["status"] == "FAILED"
