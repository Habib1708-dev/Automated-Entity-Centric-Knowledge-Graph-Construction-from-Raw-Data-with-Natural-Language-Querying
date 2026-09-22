"""The tracking port: usage metering and cost, the Null Object, and the MLflow adapter against a temporary
store. Never touches the project's real mlflow.db."""

from concurrent.futures import ThreadPoolExecutor

import mlflow
import pytest

from kgbuilder.llm.base import LLMCallRecord
from kgbuilder.tracking.base import ModelPrice, NullTracker, UsageMeter
from kgbuilder.tracking.mlflow_tracker import MlflowTracker, create_tracker


def call(cache_hit: bool, prompt_tokens: int | None = None, completion_tokens: int | None = None, **extra):
    extra = {"model": "m"} | extra  # one default model; cost tests name a second one
    return LLMCallRecord(
        temperature=0.0,
        prompt="p",
        response="{}",
        latency_s=0.5,
        cache_hit=cache_hit,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        **extra,
    )


def test_usage_meter_totals():
    meter = UsageMeter()
    meter.add(call(False, 10, 4, thinking_tokens=30))
    meter.add(call(True))
    meter.add(call(False, ok=False))
    meter.add(call(False, kind="embed"))
    assert meter.as_metrics() == {
        "llm_calls": 3,  # the failed attempt is a request that was sent; the embedding batch is not
        "llm_failures": 1,
        "cache_hits": 1,
        "embed_calls": 1,
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "thinking_tokens": 30,
        "llm_latency_s": 2.0,
    }


PRICES = {"m": ModelPrice(input=1.0, output=10.0), "big": ModelPrice(input=2.0, output=20.0)}


def test_cost_is_priced_per_model_and_thinking_is_billed_as_output():
    meter = UsageMeter(PRICES)
    meter.add(call(False, 1_000, 100, thinking_tokens=900))  # m: 1000 in, 1000 billed out
    meter.add(call(False, 500, 50, model="big"))  # big: 500 in, 50 out
    meter.add(call(True))  # a cache hit costs nothing
    assert meter.as_metrics()["cost_usd"] == pytest.approx((1_000 * 1 + 1_000 * 10 + 500 * 2 + 50 * 20) / 1e6)


def test_no_cost_is_logged_rather_than_a_partial_one():
    meter = UsageMeter(PRICES)
    meter.add(call(False, 10, 4))
    meter.add(call(False, 10, 4, model="unpriced"))
    assert "cost_usd" not in meter.as_metrics()  # a partial sum would understate the cost
    assert "cost_usd" not in UsageMeter().as_metrics()  # no price table: no cost at all


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
    tracker = create_tracker(
        f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}", "test", {"git_sha": "abc1234"}
    )
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


def test_every_run_logs_its_cost_when_prices_are_given(tmp_path):
    previous = mlflow.get_tracking_uri()
    tracker = create_tracker(f"sqlite:///{(tmp_path / 'm.db').as_posix()}", "priced", prices=PRICES)
    with tracker.start_run("pipeline"), tracker.start_run("extract"):
        tracker.record_llm_call(call(False, 1_000_000, 0))
    runs = mlflow.search_runs(experiment_names=["priced"]).set_index("tags.mlflow.runName")
    mlflow.set_tracking_uri(previous)
    assert runs.loc["extract", "metrics.cost_usd"] == runs.loc["pipeline", "metrics.cost_usd"] == 1.0


def test_stage_metrics_are_logged_even_when_the_stage_fails(store):
    with pytest.raises(ValueError), store.start_run("plan"):
        store.record_llm_call(call(False))
        raise ValueError("boom")
    run = mlflow.search_runs(experiment_names=["test"]).iloc[0]
    assert run["metrics.llm_calls"] == 1 and run["status"] == "FAILED"


def test_every_run_carries_the_tracker_tags_and_its_stage(store):
    with store.start_run("pipeline"), store.start_run("extract"):
        pass
    runs = mlflow.search_runs(experiment_names=["test"]).set_index("tags.mlflow.runName")
    assert list(runs["tags.git_sha"]) == ["abc1234", "abc1234"]
    assert runs.loc["extract", "tags.stage"] == "extract"


def test_traces_from_worker_threads_are_attached_to_the_open_stage_run(store):
    # extraction reports its calls from a thread pool, where MLflow sees no active run
    with store.start_run("pipeline"), store.start_run("extract"):
        stage_id = mlflow.active_run().info.run_id
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda _: store.record_llm_call(call(False, 1, 1)), range(3)))
    mlflow.flush_trace_async_logging()  # MLflow exports traces from a background queue
    experiment = mlflow.get_experiment_by_name("test").experiment_id
    traces = mlflow.search_traces(locations=[experiment], run_id=stage_id, return_type="list")
    assert len(traces) == 3


def test_a_run_mlflow_cannot_open_does_not_stop_the_stage(store):
    mlflow.set_tracking_uri("unsupported-scheme://nowhere")  # every store call now fails
    finished = False
    with store.start_run("plan", model="m") as run:
        run.metrics(rounds=1)
        store.record_llm_call(call(False))
        finished = True
    assert finished
