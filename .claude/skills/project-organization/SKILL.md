---
name: project-organization
description: Package layout, module boundaries and dependency direction for kgbuilder. Use when creating, moving, renaming or splitting a file, when deciding where a function or class belongs, when adding a new pipeline stage, LLM provider or data source, or when an import looks like it crosses a boundary.
---

# Project organization

## Target layout

The codebase started as a flat package of 16 modules and was restructured in steps R1 to R9 of
`REFACTOR_PLAN.md`. This is the layout as built; keep it.

```
src/kgbuilder/
  cli.py                  Typer commands + composition root. Parses args, builds objects, prints. No logic.
  config.py               Settings (pydantic-settings). Read only by the composition root.
  core/                   Shared kernel. Imports nothing from the rest of kgbuilder.
    text.py               norm() and other text normalisation
    cypher.py             cypher_ident() and Cypher helpers
    errors.py             KgBuilderError and its subclasses
  llm/                    LLM port and adapters
    base.py               LLMClient / Embedder Protocols, LLMCallRecord + CallListener (Observer), prompt_version
    gemini.py             Adapter: google-genai
    cache.py              Decorator: disk cache around any LLMClient
    refine.py             Template Method: propose -> validate in code -> critique -> retry loop; Critique
  graph/                  Neo4j port
    connection.py         driver factory, session/context helper
  tracking/               Experiment tracking port
    base.py               Tracker / Run Protocols, NullTracker (Null Object), UsageMeter
    mlflow_tracker.py     Adapter: MLflow runs, params, metrics, artifacts, tracing
  structured/             Domain graph from tables
    staging.py            JSON/CSV staging
    profiler.py           DuckDB profiling, FK candidates
    plan.py               ConstructionPlan models + validate_plan
    proposer.py           LLM plan proposal and critique (uses llm/refine.py)
    importer.py           rule-based import + reconciliation report
  text/                   Lexical and subject graphs from documents
    documents.py          loaders (md, txt, pdf)
    chunking.py           chunk_document
    lexical.py            write Document/Chunk graph
    schema.py             TextSchema models, validation, proposer
    extraction.py         triple extraction + evidence verification
    subject_graph.py      write Entity/fact graph
  resolution/
    matchers.py           Strategy: candidate scoring (fuzzy, embedding)
    resolver.py           read -> find_candidates -> decide -> group_merges -> apply_merges; undo_merges
    linking.py            Document-ABOUT and Entity-REFERS_TO links
  validation/
    checks/               Strategy: one class per check family (structure, provenance, consistency, accuracy)
    report.py             Check, CheckOutput, ValidationReport
    validator.py          runs the check families (+ accuracy check with gold data)
    evaluate.py           gold-set scoring: triples, entities, ER pairs, read-only questions
  pipeline/
    stage.py              Stage protocol, PipelineContext (deps), PipelineState (data between stages)
    stages.py             the concrete stages (wiring + MLflow logging only)
    runner.py             run_stage / run_stages / run_all: tracking, ordering, skipping, approval pauses
tests/
  test_<feature>.py       one file per package; database tests are marked @pytest.mark.neo4j
  fakes.py                ScriptedLLM, RecordingTracker
  sample_plans.py         plans matching the conftest CSV fixtures
  gold/                   reviewed plan + expected counts for data/
```

Do not create empty packages or placeholder modules; a file appears when real code needs it.

## Dependency rules

```
cli  ->  pipeline  ->  structured | text | resolution | validation  ->  llm | graph | tracking (protocols)  ->  core
```

- `core` has no project imports. `llm`, `graph`, `tracking` import only `core`.
- Feature packages import the **protocols** (`llm.base`, `tracking.base`), never the adapters
  (`llm.gemini`, `tracking.mlflow_tracker`). Only `cli.py` and `pipeline/` wiring import adapters.
- Feature packages may import each other's **models** (for example `text` reads `structured.plan.ConstructionPlan`)
  but not each other's private functions. If two features need the same helper, it moves to `core`.
- Third-party SDKs are confined: `google.genai` only in `llm/gemini.py`; `mlflow` only in
  `tracking/mlflow_tracker.py`; `GraphDatabase.driver` only in `graph/connection.py`; `typer` only in `cli.py`.
- No circular imports, no function-level imports to dodge one (lazy import of a heavy optional SDK inside
  its adapter is the only exception, with a comment).

## Where does it go?

| You are adding | It goes in |
|---|---|
| a new LLM provider | new adapter in `llm/` implementing `LLMClient`; wired in the composition root |
| a new input format | loader in `structured/staging.py` or `text/documents.py` |
| a new validation check | new class in `validation/checks/`, registered in the check list |
| a new ER signal | new matcher in `resolution/matchers.py` |
| a new pipeline stage | a `BaseStage` subclass in `pipeline/stages.py`, its place in `FULL_PIPELINE`, a CLI command, MLflow logging, a test |
| a prompt | constant next to the code that uses it, with a version comment; its hash is logged to MLflow |
| a threshold or tunable | `config.py` field with a comment, passed down explicitly, logged as an MLflow param |
| a helper used by two packages | `core/` |

## File conventions

- One concept per file; aim for under about 250 lines. A file that needs "and" in its header gets split.
- File names are nouns for models/services (`importer.py`) and never generic (`utils.py`, `helpers.py`,
  `misc.py` are banned).
- Every package has an `__init__.py` with a purpose header and an explicit `__all__` public surface.
- Private helpers are prefixed `_` and are not imported across modules.
- Generated output goes to `out/`, caches to `.cache/`, MLflow data to `mlflow.db` / `mlruns/`. None of
  these are committed. Test data lives in `tests/` fixtures, never in `data/`.
- Course notes live in `docs/lessons/` and are reference material only.
