# Audit and remediation roadmap

The pipeline in `src/kgbuilder` was generated in one pass; the 8-step plan in [PLAN.md](PLAN.md) was not
followed. This file is the critique of that code and the plan to bring it up to standard **one step at a
time**. Rules for how to work are in [CLAUDE.md](CLAUDE.md); each step is executed with the
`implement-step` skill (`/implement-step N`).

Baseline on 2026-09-21: 16 modules, about 1,650 lines, 10 tests passing (Neo4j up), no linter, no git repository.

## Verdict

The design idea is sound and worth keeping: exact profiling in code, LLM output always parsed into
pydantic models and validated, evidence quotes verified against the chunk, idempotent parameterised
Cypher, a disk cache for reproducibility. The code is compact and readable line by line.

What is wrong is structural. Everything reaches for globals, the three external systems (LLM, Neo4j,
MLflow) are hard-wired into the logic, several functions do four jobs, the tests can only work by
monkeypatching, MLflow logs a fraction of what PLAN.md promised, and four plan deliverables are missing.

## Findings

Severity: **H** blocks quality or correctness claims, **M** maintainability, **L** polish.

### A. Architecture and SOLID

| # | Sev | Finding | Where |
|---|---|---|---|
| A1 | H | Dependency inversion is absent. Logic imports the `llm` module, the global `settings` object and calls `get_driver()` itself. Nothing can be tested without `monkeypatch` or a live Neo4j. | `llm.py:13-24`, `config.py:30`, `pipeline.py:69,91,105,117,130`, `resolve.py:54`, `extract.py:95,156` |
| A2 | H | Shared helpers live inside unrelated feature modules, so features import each other sideways: `norm` from `extract` (used by `link`, `resolve`, `validate`), `cypher_ident` and `get_driver` from `importer` (used by `extract`, `link`, `validate`, `pipeline`, `cli`). | `extract.py:60`, `importer.py:37-43` |
| A3 | M | `validate_graph` is one 90-line function holding four check families; adding a check means editing it (open/closed). | `validate.py:36-124` |
| A4 | M | `resolve_entities` scores pairs, calls the LLM, builds union-find groups, merges in Neo4j and cleans self-loops in one function. `link_graphs` similarly mixes reading, matching and writing. | `resolve.py:53-119`, `link.py:33-77` |
| A5 | M | The propose → validate → feedback → retry loop is implemented twice with different return shapes (`SchemaResult` vs a bare tuple). | `schema.py:74-98`, `textschema.py:107-123` |
| A6 | M | Every stage repeats `with track(...)` plus `driver = get_driver(); try/finally: close()`. There is no `Stage` abstraction; `run_all` hard-codes order and skipping. | `pipeline.py:62-137,148-161` |
| A7 | M | Bare `dict` and tuple results cross module boundaries. | `pipeline.py:103,115`, `textschema.py:109`, `extract.py:116` |
| A8 | M | Bare `RuntimeError` for domain failures; the CLI catches it in one command only, so `kg build` with an invalid plan prints the issues and then crashes with a traceback. | `pipeline.py:42,49,84`, `cli.py:49-51` |
| A9 | L | Flat package; CLI contains logic (re-chunking, plan loading, validation). | `cli.py:44-51,65,75` |

### B. Correctness and robustness

| # | Sev | Finding | Where |
|---|---|---|---|
| B1 | H | `kg extract` and `kg text-schema` re-chunk the documents instead of reading the chunks that `kg ingest-text` stored. If chunk settings change in between, `chunk_id`s no longer match the graph and `MENTIONS` / provenance silently break. | `cli.py:65,75` |
| B2 | M | LLM call has no retry, no handling of an unparsable or empty response, and a hard-coded temperature. One transient API error aborts a whole extraction run. | `llm.py:50-59` |
| B3 | M | Non-tabular JSON is skipped silently (`except ValueError: pass`), so a user never learns a file was ignored. | `ingest.py:78-79` |
| B4 | M | `stage_structured` does `shutil.rmtree` on a caller-supplied path with no guard that it is a staging dir. | `ingest.py:64-65` |
| B5 | M | ER compares all pairs per type (quadratic) and adjudicates borderline pairs serially; merges record `merged_from` ids only, so they are **not reversible** as PLAN step 6 requires. | `resolve.py:67-88,99-105` |
| B6 | L | Linking runs one query per document and per entity (N+1). Document linking relies on the file name only. | `link.py:47-76` |
| B7 | L | Gold accuracy threshold `0.5` is a magic number inside the validator. | `validate.py:122` |
| B8 | L | Unused import. | `pipeline.py:3` |

### C. MLflow (against "Where MLflow fits" in PLAN.md)

| # | Sev | Finding |
|---|---|---|
| C1 | H | No LLM tracing at all: prompts, responses, latency, token usage and cost are never recorded. |
| C2 | H | Params are nearly empty: no temperature, prompt version/hash, chunk size, ER thresholds, link threshold. Runs cannot be compared meaningfully. |
| C3 | M | Missing metrics: validation errors per round, cache hits, rejected-by-reason, duration, precision (only recall exists). Critic feedback per round is not kept as an artifact. |
| C4 | M | `Run` guards every method with `if self._mlflow`; should be a `Tracker` protocol with an MLflow adapter and a Null Object. `set_experiment` is called on every stage. |
| C5 | L | `stage_profile` stages files and `stage_build` validates the plan outside the tracked run, so their failures and time are invisible. |

### D. Plan deliverables that are missing or diverge

| # | PLAN step | Gap |
|---|---|---|
| D1 | 2 | No gold expectation file for the domain graph (labels, relationship types, counts). Reconciliation covers node counts only, not relationships. |
| D2 | 3 | Chunker has no overlap; relationship is `PART_OF`, plan says `FROM_DOCUMENT` (decide and document one). |
| D3 | 4 | Text-schema proposal has code validation but no critic pass. |
| D4 | 6 | No embedding-similarity candidates; merges not reversible (see B5). |
| D5 | 7 | No `kg eval`, no precision/F1, no entity-level scores, no ER accuracy, no end-to-end question checks, no hand-labelled gold set in the repo. |
| D6 | 8 | No approval pauses in `kg run`; no demo script. |

### E. Hygiene, comments, tests

| # | Sev | Finding |
|---|---|---|
| E1 | M | No purpose header in `cli.py`, `config.py`, `__init__.py`, `tests/conftest.py`, `tests/__init__.py`. Other headers are one line and do not state role or boundaries. |
| E2 | M | Public functions mostly lack docstrings; prompts, thresholds and several Cypher queries have no why-comments. |
| E3 | M | No linter, formatter or type checker configured; no pytest markers, so "needs Neo4j" is implicit. |
| E4 | M | Tests: one large end-to-end test carries most of the coverage; it monkeypatches `llm.generate`; `test_pipeline.py` imports a fixture constant from another test module. No unit tests for profiler edge cases, chunker, ER scoring, linking. |
| E5 | M | The project is not a git repository, so steps cannot be reviewed or reverted. |
| E6 | L | `lesson*.md` clutter the root; `lesson3.md`/`lesson4.md` and `lesson9.md`/`lesson10.md` have identical sizes (probable duplicates). |

## Roadmap

Each step is behaviour-preserving unless it says otherwise, ends with green tests, and is one session.
Steps R1 to R3 build the foundations; R4 to R8 then revisit the original PLAN steps in order, fixing
design and filling the gaps.

| Step | Title | Status |
|---|---|---|
| R0 | Rules, skills, audit | done 2026-09-21 |
| R1 | Tooling, shared core, file headers | todo |
| R2 | Ports and adapters: LLM, graph, settings injection | todo |
| R3 | Tracking port and full MLflow logging | todo |
| R4 | Structured path (PLAN 1-2) | todo |
| R5 | Text path: documents, chunks, lexical graph, text schema (PLAN 3-4) | todo |
| R6 | Extraction and linking (PLAN 5) | todo |
| R7 | Entity resolution (PLAN 6) | todo |
| R8 | Validation checks and evaluation harness (PLAN 7) | todo |
| R9 | Stage abstraction, runner, thin CLI, docs (PLAN 8) | todo |

### R1. Tooling, shared core, file headers
Closes A2, B8, E1, E2 (headers only), E3, E5, E6.
- `git init` (with the user's consent) and extend `.gitignore` (`mlflow.db`, `mlruns/`).
- Add ruff (lint + format, line length 110) and pytest markers (`neo4j`) to `pyproject.toml`; fix what ruff finds.
- Create `core/` with `text.py` (`norm`), `cypher.py` (`cypher_ident`), `errors.py`; create
  `graph/connection.py` (`get_driver`). Update imports; no re-exports left behind.
- Add the purpose header to every source and test file. Move `lesson*.md` to `docs/lessons/`.
- **Accept:** tests green and same count; `ruff check` clean; no feature module imports another for a helper;
  every `.py` file starts with a header.

### R2. Ports and adapters: LLM, graph, settings injection
Closes A1, B2, part of A8. Depends on R1.
- `llm/base.py` (`LLMClient`, `Embedder` protocols), `llm/gemini.py` (adapter, with retry and a typed
  error for unparsable output, temperature as a parameter), `llm/cache.py` (decorator).
- Functions take the client, driver and the specific setting values as arguments. Remove the `_client`
  singleton and deep reads of global `settings`.
- `tests/fakes.py` with `ScriptedLLM`; replace `monkeypatch.setattr(llm, ...)`.
- **Accept:** `google.genai` imported only in `llm/gemini.py`; no `monkeypatch` of project globals in tests;
  new unit tests for cache hit/miss and retry run without network.

### R3. Tracking port and full MLflow logging
Closes C1 to C5. Depends on R2.
- `tracking/base.py` (`Tracker`, `Run`, `NullTracker`), `tracking/mlflow_tracker.py`.
- `Traced` LLM decorator: spans per call, latency, cache hit, token usage; aggregate metrics per stage.
- Prompt version hashing; log every param listed in the `mlflow-tracking` skill; prompts as artifacts.
- `RecordingTracker` fake and tests asserting the logged names per stage.
- **Accept:** a `kg profile data/` run and (with an API key) a `kg plan` run show the full param/metric/
  artifact set and LLM traces in the MLflow UI; `mlflow` imported only in the adapter.

### R4. Structured path (PLAN steps 1-2)
Closes A5 (introduces `llm/refine.py`), A8, B3, B4, D1. Depends on R3.
- Move to `structured/` (`staging`, `profiler`, `plan`, `proposer`, `importer`).
- Shared refinement loop with a typed result; critic feedback per round kept as an artifact.
- Typed errors (`PlanRejectedError`, `InvalidPlanError`) handled uniformly by the CLI.
- Warn on skipped files; guard the staging `rmtree`.
- Relationship reconciliation; `tests/gold/domain_expectations.json` for `data/` and a test against it.
- **Accept:** `kg build data/` reconciles nodes and relationships against the gold expectations.

### R5. Text path: documents, chunks, lexical graph, text schema (PLAN steps 3-4)
Closes B1, D2, D3. Depends on R4.
- Move to `text/` (`documents`, `chunking`, `lexical`, `schema`).
- Chunks are read back from the graph (or a chunk artifact) by later stages, never recomputed.
- Chunk overlap as a setting (behaviour change, measured in MLflow); settle `PART_OF` vs `FROM_DOCUMENT`.
- Text-schema proposer uses `llm/refine.py` and gains a critic pass.
- **Accept:** chunker unit tests (overlap, tiny and oversized sections); changing chunk settings between
  `ingest-text` and `extract` can no longer desynchronise ids.

### R6. Extraction and linking (PLAN step 5)
Closes A7 (extract), B6, part of A4. Depends on R5.
- Split `text/extraction.py` (prompt, verify, dedupe) from `text/subject_graph.py` (writes).
- `resolution/linking.py`: separate read, match (pure, unit-tested) and batched write.
- Metrics: rejected by reason, triples per chunk.
- **Accept:** matching and verification fully covered by unit tests without Neo4j.

### R7. Entity resolution (PLAN step 6)
Closes A4, B5, D4. Depends on R6.
- `resolution/matchers.py` (Strategy: fuzzy, embedding), blocking to avoid all-pairs, parallel adjudication.
- Merge audit log sufficient to undo a merge, plus `kg resolve --undo`.
- **Accept:** duplicate counts before/after in MLflow; an integration test merges then undoes a merge.

### R8. Validation checks and evaluation harness (PLAN step 7)
Closes A3, B7, D5. Depends on R7.
- `validation/checks/` with one Strategy class per family; `validation/evaluate.py` with precision, recall,
  F1 at entity and triple level, ER accuracy, and question checks.
- Hand-labelled gold set for 2 to 3 review files under `tests/gold/`; `kg eval` command.
- **Accept:** `kg eval` logs a report artifact; thresholds are settings, logged as params.

### R9. Stage abstraction, runner, thin CLI, docs (PLAN step 8)
Closes A6, A9, D6, remaining E2/E4. Depends on R8.
- `pipeline/stage.py`, `stages.py`, `runner.py`; the runner owns tracking, resources, ordering, approval pauses.
- `cli.py` becomes composition root + argument parsing only.
- Split tests into `tests/unit` and `tests/integration`; README module map and demo script.
- **Accept:** clean clone → `uv sync` → `kg run data/` → graph + evaluation report, as PLAN step 8 demands.

## Found along the way

(Add items here during a step instead of widening its scope.)
