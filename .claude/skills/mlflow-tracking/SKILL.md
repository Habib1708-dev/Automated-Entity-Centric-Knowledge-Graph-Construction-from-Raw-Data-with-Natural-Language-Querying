---
name: mlflow-tracking
description: MLflow experiment-tracking conventions for kgbuilder - run structure, required params, metrics and artifacts per stage, LLM call tracing, prompt versioning, and how to compare variants. Use when adding or changing a pipeline stage, an LLM call, a prompt, a model, a threshold or a metric, or when evaluating whether a change improved the graph.
---

# MLflow tracking

MLflow is how this thesis proves its claims: every number in the write-up must be reproducible from a
logged run. Tracking is part of a stage's definition of done.

## Setup

- Store: `sqlite:///mlflow.db` (setting `mlflow_tracking_uri`), experiment `kgbuilder`
  (setting `mlflow_experiment`). UI: `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db`.
- `mlflow.db` and `mlruns/` are never committed.
- Tests use `NullTracker` or `RecordingTracker`; they never write to the real store.

## Access rule

`mlflow` is imported only in `tracking/mlflow_tracker.py`. Everything else uses the protocol:

```python
class Run(Protocol):
    def params(self, **values: object) -> None: ...
    def metrics(self, **values: float | int | None) -> None: ...
    def artifact(self, path: Path) -> None: ...
    def tags(self, **values: str) -> None: ...


class Tracker(Protocol):
    def start_run(self, name: str, **params: object) -> ContextManager[Run]: ...
    def trace_llm_call(
        self,
        *,
        model: str,
        prompt: str,
        response: str,
        latency_s: float,
        cache_hit: bool,
        usage: TokenUsage | None,
    ) -> None: ...
```

`NullTracker` (Null Object) implements the same protocol and does nothing. If MLflow cannot start, the
factory logs one warning and returns `NullTracker`; tracking must never break a pipeline run.

## Run structure

- `kg run` → one parent run `pipeline`; each stage is a **nested** run named after the stage:
  `profile`, `plan`, `build_domain`, `ingest_text`, `text_schema`, `extract`, `resolve`, `link`, `validate`
  (and `eval` once it exists).
- A stage run on its own from the CLI is a top-level run with the same name.
- The runner (not each stage) opens the run, so a stage cannot forget it.
- Tags on every run: `stage`, `dataset` (data dir name), `git_sha` when available, `code_version`.

## What every stage logs

| Stage | Params | Metrics | Artifacts |
|---|---|---|---|
| all | `data_dir`, `goal` (if any), relevant settings | `duration_s` | the files it wrote to `out/` |
| all with LLM | `model`, `temperature`, `prompt_version` (sha256[:12] of the prompt template), `max_rounds` | `llm_calls`, `cache_hits`, `prompt_tokens`, `completion_tokens`, `llm_latency_s` | |
| profile | `min_inclusion` | `files`, `rows`, `foreign_key_candidates` | `profile.json` |
| plan | `use_critic` | `rounds`, `accepted`, `open_issues`, `validation_errors_round_1` | `plan.json`, critic feedback per round |
| build_domain | `batch_size` | `rules`, `rows_written`, `rows_dropped`, `clean`, nodes/relationships vs expected | `build_report.json` |
| ingest_text | `chunk_max_chars`, `chunk_min_chars`, `embed_model` | `documents`, `chunks`, `embedded`, `avg_chunk_chars` | |
| text_schema | | `rounds`, `open_issues`, `entity_types`, `fact_types` | `text_schema.json` |
| extract | `workers` | `entities`, `facts`, `mentions`, `rejected`, `accept_rate`, `triples_per_chunk`, rejected count per reason | `triples.jsonl`, `rejected.jsonl` |
| resolve | `er_auto_merge`, `er_borderline` | `before`, `after`, `merges`, `llm_adjudications`, `self_loops_removed` | `resolve.json` (full decision log) |
| link | `domain_link_threshold` | `documents_linked`, `documents_total`, `entities_linked` | |
| validate / eval | `gold` path | checks passed/total, `evidence_verified_rate`, precision, recall, F1 per level (entity, triple) | `validation.json`, eval report |

Rules for values:
- Params are inputs known before the run; metrics are measured outputs. Never log an output as a param.
- A setting that influences a stage's result is a param of that stage. If you add a threshold, log it.
- Metric names are `snake_case`, stable across runs (renaming breaks comparisons; if you must, note it in
  the step report). Rates are 0..1 floats. Booleans are 0/1.
- Log artifacts after the file is fully written. Large intermediate data goes to artifacts, not params.

## Prompts are versioned

- Each prompt template is a module constant. Its `prompt_version` is the first 12 hex chars of the sha256
  of the template text (before formatting), computed by one helper in `llm/`.
- Changing a prompt therefore changes `prompt_version` automatically, and also invalidates the disk cache
  for it (the cache key includes the full prompt).
- Log a prompt template itself as an artifact (`prompts/<stage>.txt`) so a run is self-describing.

## LLM tracing

- Tracing lives in the LLM decorator chain (`Traced(Cached(Gemini))`), not in feature code.
- Each call records: model, temperature, prompt, raw response, parsed-OK flag, latency, cache hit/miss,
  token usage when the provider returns it. Prefer MLflow Tracing spans (`mlflow.start_span`) inside the
  adapter; aggregate counters are logged as run metrics at stage end.
- Never log secrets. The API key never reaches a param, tag, span or artifact.

## Comparing variants (how to evaluate a change)

1. Run the baseline (`kg run ...` or the single stage) and note the run id.
2. Change exactly one thing: a prompt, a model, a threshold, the chunk size.
3. Run again with `use_cache` semantics in mind: a changed prompt misses the cache by design; an unchanged
   prompt with a changed model also misses. A changed threshold with unchanged prompts is served from
   cache, which is what you want (free and deterministic).
4. Compare the two runs in the MLflow UI (or `mlflow.search_runs`) on the stage's metrics and on the
   `validate` / `eval` metrics. Report the deltas in the step report. Keep the better variant.

## Checklist for a new or changed stage

- [ ] Run opened by the runner with the stage name; nested under `pipeline` when applicable.
- [ ] All influencing settings logged as params; prompt version logged for LLM stages.
- [ ] Count, rate and duration metrics logged; LLM usage metrics logged.
- [ ] Output files logged as artifacts.
- [ ] A unit test asserts, with `RecordingTracker`, that the expected metric and param names are logged.
