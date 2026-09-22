# Audit and remediation roadmap

The pipeline in `src/kgbuilder` was generated in one pass; the 8-step plan in [PLAN.md](PLAN.md) was not
followed. This file is the critique of that code and the plan to bring it up to standard **one step at a
time**. Rules for how to work are in [CLAUDE.md](CLAUDE.md); each step is executed with the
`implement-step` skill (`/implement-step N`).

Baseline on 2026-09-21: 16 modules, about 1,650 lines, 10 tests passing (Neo4j up), no linter, no git repository.
After R9 (same day): 11 commits, 59 tests passing, `ruff check` clean, every finding below closed except the items listed under "Still open" at the end.

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
| R1 | Tooling, shared core, file headers | done 2026-09-21: ruff + markers, `core/`, `graph/`, headers, lessons moved |
| R2 | Ports and adapters: LLM, graph, settings injection | done 2026-09-21: LLM port, Gemini adapter with retry, cache decorator, injected context, fakes |
| R3 | Tracking port and full MLflow logging | done 2026-09-21: tracking port, MLflow adapter, LLM traces + usage metrics, prompt versions |
| R4 | Structured path (PLAN 1-2) | done 2026-09-21: `structured/`, shared refine loop, staging report + wipe guard, gold expectations |
| R5 | Text path: documents, chunks, lexical graph, text schema (PLAN 3-4) | done 2026-09-21: `text/`, chunks read back from the graph, stale-chunk cleanup, overlap, schema critic |
| R6 | Extraction and linking (PLAN 5) | done 2026-09-21: extraction / subject graph split, rejection reason codes, linking as read-match-write |
| R7 | Entity resolution (PLAN 6) | done 2026-09-21: five-step resolver, Strategy matchers, parallel adjudication, snapshot undo |
| R8 | Validation checks and evaluation harness (PLAN 7) | done 2026-09-21: `validation/` check families, evaluation harness, `kg eval` |
| R9 | Stage abstraction, runner, thin CLI, docs (PLAN 8) | done 2026-09-21: `pipeline/` Stage + runner, approval pauses, thin CLI, README |
| R10 | Trustworthy tracking after the first real run; current model defaults | done 2026-09-22: thinking tokens, failed attempts, embedding calls, traces linked from worker threads, guarded run open/close, `git_sha`/`code_version` tags, Gemini 3 defaults |
| R11 | Scoped linking of text entities to the domain graph | done 2026-09-22: plan `name_column` (code rejects code-like columns), matching inside the document's product neighbourhood, recomputed links; defect → part → supplier now answerable (entities linked 9 → 17) |
| R12 | Local LLM provider (Ollama) for free smoke runs | done 2026-09-22: `llm/ollama.py`, shared retry loop `llm/retry.py`, `LLM_PROVIDER` setting; full local run on `data/` for $0 |
| R13 | Model presets (`presets.yaml`): smoke, dev, quality | done 2026-09-22: preset settings source between environment and `.env`, `kg --preset`, `preset` tag on every run; dev run measured at $0.06 |
| R14 | A goal-neutral text schema prompt | done 2026-09-22: example list removed (it invited reviewers and locations), goal-question rule for proposer and critic, `facts_touching_domain_rate`; MLflow comparison mixed, accuracy left to the gold set |
| R15 | Small data subsets for smoke and dev; smoke on a free Gemini key | done 2026-09-22: `samples/smoke`, `samples/dev`, `data_dir` and `gemini_key` settings, optional `DATA_DIR` argument; a run on the smoke subset costs $0.012 on the paid key |
| R16 | Thinking level per role; quality preset on Gemini 3.8 Flash | done 2026-09-22: `schema_thinking` / `extract_thinking`, `ThinkingLLM` decorator; a quality run costs $0.17 instead of $1.25, 19/19 checks |
| R17 | Run policy: when and how often any pipeline run may happen | done 2026-09-22: `run-policy` skill (default no run, one smoke/dev run per step, quality only with agreement, cost in every report); CLAUDE.md and skills point to it |
| R18 | Permission gate: a comprehensive run needs the user's approval, enforced by a hook | done 2026-09-22: `ask_permission` in presets.yaml, `.claude/hooks/run_guard.py` registered in `.claude/settings.json`, rule in CLAUDE.md and `run-policy`; 18 tests |
| R19 | Subsets built from config: a `sample` block per preset and `kg sample` | done 2026-09-22: `sampling.py` follows the profiler's foreign keys; the regenerated `samples/` equal the committed ones; drift test |
| R20 | Cost in one place: a price table and a `cost_usd` metric per run | done 2026-09-22: `prices.yaml`, per-model tokens in `UsageMeter`, `cost_usd` on every run; docs point to presets.yaml, prices.yaml and MLflow |
| R21 | Evaluation rules: gold set written by Claude, Claude as the LLM judge | done 2026-09-22: `evaluation` skill, CLAUDE.md section 5, run-policy and tracking pointers, R22/R23 planned |
| R22 | Gold set for all 10 review files, written before seeing output | done 2026-09-22: `tests/gold/text_gold.json` (74 triples, 12 ER pairs, 5 questions, verbatim evidence), `GoldTriple.evidence`, integrity test |
| R23 | LLM-as-a-judge scoring: judge sheet, verdict file, `kg eval --verdicts`, validated metrics in MLflow | done 2026-09-22: `validation/judge.py` + `gold.py`, `EvaluationError`, stage params/metrics/artifacts, 7 tests |
| R24 | First judge pass on a quality run; `PART_OF` gold triples | done 2026-09-22: 96 gold triples, pinned plan + schema, validated P/R/F1 1.00/0.71/0.83 vs exact 0.14/0.16/0.15, schema drift found |
| R25 | `evaluation/` folder: criteria and metric definitions, dated result snapshots | done 2026-09-22: `evaluation/README.md`, `evaluation/results_2026-09-22.md` (assessment of R24's numbers), README pointer. Moved to the git-ignored `docs/evaluation/` the same day (local only); section 6 of the snapshot holds the stage-by-stage root-cause analysis of the recall gap and the recommended steps |

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

### R10. Trustworthy tracking after the first real run; current model defaults
Found by reviewing the MLflow adapter and by the first `kg run data/` against Gemini (2026-09-22).
- Token usage left out thinking tokens (`thoughts_token_count`), which are billed as output: on the plan
  stage they were 2,843 of 3,511 output tokens, so MLflow showed about a fifth of the billed output.
- Traces of calls made in extraction's worker threads were not attached to any run (MLflow links a trace
  only to the calling thread's active run); now the innermost open run is set on each trace.
- Failed attempts and embedding batches were not reported at all; now `llm_failures` and `embed_calls`.
- Opening or closing a run was not guarded, so a locked store could stop the pipeline.
- No tag tied a run to its code; now `git_sha` (with `-dirty`) and `code_version` on every run.
- `gemini-2.5-pro` / `gemini-2.5-flash` return 404 "no longer available to new users"; defaults are now
  `gemini-3.1-pro-preview` and `gemini-3.8-flash`.
- **Accept:** unit tests for each item; a live `kg plan` and `kg ingest-text` show `thinking_tokens`,
  `embed_calls`, the tags, and the plan's two traces under the plan run.

### R11. Scoped linking of text entities to the domain graph
Found in the first real run: no review component linked to the domain, so a defect could not be traced
to a part or supplier.
- Cause 1 (bug): the linker guessed the name column as the first column containing "name", which is
  `sub_assembly_name` for parts and `assembly_name` for assemblies (codes like "uppsala_sofa_assembly").
  Now `NodeRule.name_column`, proposed by the LLM and checked in code: it must be imported, and a column
  whose profiled samples all look like codes is rejected with a hint (the LLM chose `assembly_name` once
  even with the prompt rule).
- Cause 2: generic names repeat across products ("Legs" in every chair and table). Entities are matched
  inside the 2-hop domain neighbourhood of the node their documents are ABOUT, with one REFERS_TO per
  product; outside every scope only names unique in the domain link, ties are counted as ambiguous.
  Chosen over splitting entities per product, which would have meant graph surgery after resolution
  and would have broken `kg resolve --undo`.
- Links are recomputed on each `kg link`; new metrics `entities_linked_in_scope`, `entities_ambiguous`,
  `entity_links`.
- **Accept (met):** a Neo4j test traces a defect in a chair review to the chair's supplier, not the
  table's. On `data/` the MLflow runs compare as below; the defect → part → supplier query answers for 7
  product/part pairs (e.g. Helsingborg Dresser drawer rails "rough sliding mechanisms" → 2 suppliers).

  | Run | entities linked | in scope | ambiguous | REFERS_TO |
  |---|---|---|---|---|
  | first real run (before R11) | 9 | – | – | 9 |
  | R11, LLM chose `assembly_name` | 14 | 12 | 1 | 14 |
  | R11, with the code check | 17 | 17 | 1 | 19 |

### R12. Local LLM provider (Ollama) for free smoke runs
Asked for to test that the code works without paying for Gemini; not for quality results.
- `llm/retry.py`: the retry, parse and report loop moved out of the Gemini adapter (behaviour kept, its
  tests unchanged and green), so both providers share it.
- `llm/ollama.py`: `/api/chat` with the pydantic JSON schema as `format`, `think: false`, an explicit
  `num_ctx` (Ollama silently cuts prompts at its default window), token usage from `prompt_eval_count` /
  `eval_count`; `/api/embed` in batches. httpx only, no SDK.
- `LLM_PROVIDER`, `OLLAMA_URL`, `OLLAMA_NUM_CTX` settings; `build_provider` in the composition root.
- **Accept (met):** unit tests with an httpx MockTransport; a real run with `qwen2.5:7b-instruct` +
  `nomic-embed-text` in the `kgbuilder-smoke` experiment. The plan stage stopped after 3 invalid
  proposals (code gate), so the reviewed plan was used and every other stage ran: 19/19 checks pass,
  70 extract traces linked to their run, 69 facts stored and 285 rejected (off-schema 158, evidence not
  verbatim 107, argument not in chunk 20), extract 22 minutes, cost $0. `qwen3.5:4b` broke the JSON.

### R13. Model presets (`presets.yaml`): smoke, dev, quality
Asked for to keep test runs cheap: two cheap presets for checking the pipeline and one for reported results.
- `presets.yaml`: `smoke` (Ollama, $0), `dev` (`gemini-3.5-flash-lite`), `quality` (`gemini-3.1-pro-preview`
  for schema work + `gemini-3.8-flash`), each with its own MLflow experiment.
- `PresetSettingsSource` in `config.py`: priority environment > preset > `.env` > defaults. An unknown preset,
  a misspelled key or the API key in the file is a `ConfigurationError`, shown by the CLI without a traceback.
- `kg --preset <name>` and `KG_PRESET`; every run gets a `preset` tag.
- **Accept (met):** unit tests for loading, typos, the forbidden key, the priority order and the committed
  file; CLI tests for an unknown preset and the tag. A full `kg --preset dev run data/` cost $0.057 in about
  30 seconds, 19/19 checks, 167 facts extracted and 18 rejected, plan accepted in round 2.

### R14. A goal-neutral text schema prompt
The strong models extracted every reviewer and their city (70 of ~150 facts). The proposer prompt caused
it: its only example list was "products, parts, problems, materials, locations, people/roles".
- Decision (with the user): the system must represent any kind of data, so no domain-specific exclusion
  rule ("leave out authors / locations") was added. Instead the example list was removed; types come from
  the goal, the domain graph and the text, and each fact type must answer a question of the goal. The
  critic asks the same. Nothing is lost: the text stays in the lexical graph for another schema.
- New descriptive metric `facts_touching_domain_rate` (validate): share of facts with an end linked to the
  domain graph. Not a target: 0 for text-only data, and a goal may rightly want unlinked facts.
- **Comparison** (same plan, `dev` preset with `SCHEMA_MODEL=gemini-3.1-pro-preview`, one run each; the
  before schema was rejected by the critic after 3 rounds and accepted by hand to extract):

  | | before (prompt 33bc13d3f026) | after (prompt c8f4a6d96e5a) |
  |---|---|---|
  | schema | rejected after 3 rounds | accepted in round 2 |
  | entity types | +Customer, Location, Process, Material | +Assembly (domain reuse), UseCondition, Reviewer |
  | facts / `LOCATED_IN` | 296 / 69 | 184 / 0 |
  | reviewer facts (REVIEWED, REPORTED) | 111 (38 %) | 110 (60 %) |
  | defect facts on a part (Component, Assembly) | 27 | 15 |
  | `facts_touching_domain_rate` | 0.301 | 0.321 |
  | text schema stage cost | $0.26 (6 calls) | $0.11 (4 calls) |

  Result: mixed. Locations are gone and the schema reuses the domain's Assembly, but reviewer facts
  remain and fewer defects are tied to a part. One run per side cannot separate the prompt from LLM
  variance, and without a gold set neither side can be called more accurate. The neutral prompt is kept
  on principle (no domain bias); its effect on accuracy is measured once the gold set exists (next step).

### Model choice for the three presets (decided with the user, 2026-09-22)
Running and developing the system was too expensive, and local models do not work well enough. Prices
(USD per 1M tokens, input / output) were checked on the providers' pages on 2026-09-22. The quality score is
the Artificial Analysis Intelligence Index (AA): a general score, since no public benchmark measures
fact extraction into strict JSON, so our own gold set is the final judge.

| Model | Price | AA | Role |
|---|---|---|---|
| gemini-3.5-flash-lite | 0.30 / 2.50 | 22 | smoke (free-tier key) and dev (paid key) |
| gemini-3.8-flash | 0.75 / 3.75 (7.50 output from 2027) | 41 | quality, every role, with a set thinking level |
| gemini-3.1-pro-preview | 2.00 / 12.00 | 30 | dropped: 2.7x Flash's price, lower score |
| gpt-5.6-luna / qwen3.8-flash (OpenRouter) | 0.20 / 1.20 and 0.15 / 0.47 | 37 / 40 | not now: need an OpenAI-compatible adapter |

Measured on `data/`, a quality run cost $1.25, of which $1.00 was Flash thinking during extraction (about
250k tokens for 70 short chunks), because no thinking level is set. So the savings come from small data
subsets for smoke and dev (R15) and a thinking level per role (R16). The run limits are rules for the
assistant, not code (R17, user decision).

### R15. Small data subsets for smoke and dev; smoke on a free Gemini key
Smoke and dev runs only show that the system works, so they need little data and no quality.
- `samples/smoke/` (one product) and `samples/dev/` (three products): committed subsets of `data/` that
  keep their keys consistent (every assembly, part, mapping and supplier row of the chosen products), with
  the review files cut short. They live outside `data/`, because every stage reads `data/` recursively.
- New setting `data_dir` (default `data`), so a preset names its dataset; the `DATA_DIR` argument of the
  CLI becomes optional and, when given, wins like any other explicit value.
- Smoke moves from Ollama to `gemini-3.5-flash-lite` on a second, free-tier key: new setting
  `gemini_free_api_key` (forbidden in presets) and `gemini_key: paid | free`, chosen in the composition root.
  The Ollama provider stays available through `LLM_PROVIDER`, but no preset uses it.
- **Accept:** config tests for `data_dir` and key choice (missing free key is a clear `ConfigurationError`);
  CLI test that `run` without an argument uses the preset's data; one smoke run on `samples/smoke/`.
- **Result (met, with one substitution):** 8 new tests (96 in total): API keys refused in presets, each
  preset's dataset exists, cheap presets read `samples/`, every key of a subset resolves inside it, a command
  without a directory reads `DATA_DIR`, the free key is used only when chosen and never replaced by the paid
  one. No free key exists in `.env` yet, so the run used the `dev` preset on `samples/smoke`: 10 to 12 LLM
  calls, about 18.7k input and 2.6k output tokens, $0.012 and 18 seconds; after a `kg reset` all 12 checks
  passed (counts 1 / 5 / 10 / 10 as expected). Two thirds of the cost is the plan prompt, whose size depends
  on the columns, not the rows, so a smaller subset would not make it cheaper.

### R16. Thinking level per role; quality preset on Gemini 3.8 Flash
- Settings `schema_thinking` and `extract_thinking` (`minimal | low | medium | high`, empty = model
  default), passed to Gemini as `thinking_level` and logged as params.
- `quality`: `gemini-3.8-flash` for every role, `extract_thinking: low`, `schema_thinking: medium`.
- **Accept:** adapter test that the level reaches the request; one `quality` run compared in MLflow with
  the last quality run (cost, facts, rejections, checks). Target: well under $1.25.
- **Design:** the level is part of the `LLMClient` port (`thinking=""` = model default; Ollama ignores it,
  it always turns thinking off). A stage wraps its client with `with_thinking(llm, level)` (Decorator), so
  proposer, extraction and resolver are unchanged. The cache key includes the level only when one is set,
  so earlier entries stay valid.
- **Result (met):** 5 new tests (100 in total). Comparison on `data/`, goal "supply chain root cause
  analysis", one run per side:

  | | before (8657dca, Pro + Flash, no level) | after (3.8 Flash, medium / low) |
  |---|---|---|
  | plan | Pro, 3.8k thinking, $0.07 | Flash medium, 9.9k thinking, $0.05 |
  | text schema | Pro, 2 rounds, $0.13 | Flash medium, 1 round, $0.03 |
  | extraction (70 chunks) | 258k thinking tokens, $1.05 | 3.3k thinking tokens, $0.10 |
  | **LLM cost / wall time** | **$1.25 / about 5 min** | **$0.17 / 1 min 45 s** |
  | facts / rejected | 145 / 0 | 126 / 2 (evidence stitched with "...") |
  | entities linked to the domain | 17 | 26 |
  | checks | 19/19 | 19/19 |

  The cost result is clean: extraction used the same model and prompt, only the level changed. The
  quality result is not: the "before" run predates R11 (linking) and R14 (text schema prompt), and the
  schema model changed too. The new facts fit the goal (26 component defects, failure modes, no reviewer or
  city facts); 51 are "Component PART_OF Product", which restate the tables. Accuracy is for the gold set.

### R17. Run policy: when and how often any pipeline run may happen
Rules only, no code limits (user decision).
- A `run-policy` skill: which change justifies which run, preset order (tests, then smoke, then dev,
  quality only when the user asks), at most one smoke or dev run per step, cost stated in every report.
- CLAUDE.md section 1 and the `implement-step` skill point to it.
- **Result (met):** `.claude/skills/run-policy/SKILL.md` (local, like every skill): preset table with
  measured costs, a change-to-run table, the quality agreement rule, a per-step budget, how to run cheaply
  (`kg reset` after the tests, no data directory with smoke/dev) and the prices for cost reports.
  CLAUDE.md (section 1, the MLflow rule and the skill table), `implement-step` and `mlflow-tracking`
  point to it. No code change, so no run (tests and ruff unchanged).

### R18. Permission gate: a comprehensive run needs the user's approval, enforced by a hook
Asked for by the user: "run a comprehensive test only with my permission". A rule in text can be
overlooked, so Claude Code itself asks.
- `presets.yaml`: `ask_permission: true` on `quality` (a key about the preset, not a setting).
- `.claude/hooks/run_guard.py`, a PreToolUse hook on Bash and PowerShell: for a `kg` command that calls
  an LLM (`run`, `plan`, `text-schema`, `extract`, `resolve`) it resolves the preset (`--preset`, a
  `KG_PRESET` assignment in the command, else `.env`) and the data directory; if the preset has
  `ask_permission` or the directory is outside `samples/`, it answers "ask" and Claude Code shows a prompt.
- `.claude/settings.json` registers the hook (committed). CLAUDE.md and `run-policy` state the rule.
- **Accept:** tests for the guard's decisions (preset flag, flag, environment variable, `.env`, explicit
  `data/`, cheap presets, non-LLM commands, non-`kg` commands); a committed-preset test that quality asks.
- **Result (met):** 18 tests in `tests/test_run_guard.py`, including the hook run as a subprocess (the JSON
  Claude Code reads, and silence when nothing needs asking). Pipe-tested by hand on eight commands; one call
  takes about 0.13 s. The guard errs on the side of asking: a `kg` command quoted inside another command (an
  `echo`, a commit message) also asks. No pipeline run: nothing in the pipeline changed.

### R19. Subsets built from config: a `sample` block per preset and `kg sample`
The subsets of R15 were made by a throwaway script, so nothing records how. Move that into config and code.
- A `sample` block in the smoke and dev presets: source directory, root table, key column and values, the
  documents to keep and how many sections each keeps.
- `kg sample [--preset]` writes the preset's `data_dir` from its `sample` block, following the foreign keys
  the profiler finds (down from the root rows, then up to every row they reference).
- **Accept:** unit tests for the key following and the section cut; a test that the committed `samples/`
  equal what the config produces, so config and files cannot drift.
- **Result (met):** `sample` is a preset meta key (like `description`), read with `config.read_presets`.
  `kg sample` rebuilt both subsets byte for byte the same as the R15 files (git saw no change), so the
  config now fully describes them. `SECTION_BREAK` in `text/chunking.py` became public so documents are cut
  where the chunker splits. 9 tests in `tests/test_sampling.py`. No pipeline run: the datasets did not change.

### R20. Cost in one place: a price table and a `cost_usd` metric per run
Prices live in a skill and costs were worked out by hand. Move them into config and tracking.
- `prices.yaml`: USD per 1M input and output tokens per model (thinking billed as output), with the date.
- Every run logs `cost_usd` from its token metrics; a model without a price logs no cost and a warning.
- README, CLAUDE.md and `run-policy` stop repeating numbers: they point to `presets.yaml`, `prices.yaml`
  and the MLflow metric.
- **Accept:** unit tests for the cost formula and the missing-price case; tracking test that the metric
  is logged.
- **Result (met):** `ModelPrice` in the tracking port; `UsageMeter` sums tokens per model and logs
  `cost_usd` (list price, thinking as output); an unpriced model gives no number and a warning, never a
  partial sum. `config.read_prices` (missing file: no cost; malformed: `ConfigurationError`), wired in the
  composition root. 5 new tests, including one that every model a committed preset uses has a price. The
  README and `run-policy` no longer repeat models, datasets or prices. No pipeline run: the tests cover the
  metric end to end with a temporary MLflow store, and nothing that shapes the graph changed.

### R21. Evaluation rules: gold set written by Claude, Claude as the LLM judge
Nobody has time to hand-label, and the builder model must not grade itself. Write the rules down before
any gold or verdict exists, so the order "label first, look at output second" is enforceable.
- `evaluation` skill: the two scores (exact match and judge), who labels and who judges, gold rules
  (whole documents, schema predicates, verbatim evidence, gold corrections listed), the judging
  procedure and verdict kinds, the verdict file format, the metrics, and how to write numbers up.
- CLAUDE.md section 5 with the invariants; `run-policy` (judging needs no run) and `mlflow-tracking`
  (judge metrics row, comparison step 5) point to the skill.
- **Accept:** rules and skill only, no code; `uv run pytest` and `uv run ruff check` unchanged.
- **Result (met):** as listed. The gold and the verdicts will come from the same model family; the skill
  makes the thesis state that limitation and lists the two mitigations (label before looking; every gold
  correction reported).

### R22. Gold set for all 10 review files
Closes D5 (as a Claude-labelled reference set, not a human one). Depends on R21 and an approved
`out/text_schema.json` from a quality run. Widened from "2 to 3 files" to all 10 on 2026-09-22: the
corpus is four pages, 3 files would give about 25 facts (one fact = 4 points of precision, pure noise),
and Claude labels, so the time argument for a subset no longer holds.
- `tests/gold/text_gold.json`: every fact in the chosen files, `doc_id`, schema predicate, `evidence`.
- `GoldTriple.evidence` (optional) in `validation/evaluate.py`; a test that every committed gold triple's
  evidence is a substring of its document.
- **Accept:** `kg eval tests/gold/text_gold.json` runs against the current graph and logs exact-match
  scores; the step report confirms no extraction output was opened before labelling.
- **Result (met, `kg eval` deferred):** 74 triples (42 `HAS_DEFECT`, 21 `EXHIBITS_FAILURE`,
  8 `IMPEDES_ASSEMBLY_OF`, 3 `CAUSES_FAILURE`), 12 ER pairs, 5 goal questions (drawer rails → supplier
  through the domain graph and through the text graph; wobbling, misaligned holes, squeaking products),
  from the schema of quality run `f55a8747` (its `text_schema.json` artifact hash equals `out/`). Labelled
  from the review text alone; `out/triples.jsonl` and `rejected.jsonl` were not opened. Rules in the
  file's `_comment`: only defects, failures and assembly-impeding defects; praise, taste, instructions and
  assembly time are not facts; `PART_OF` not labelled (implied, never stated). Helsingborg Dresser holds
  21 of the 74 facts, Stockholm Chair 1: the corpus is skewed and every rate needs its `n`. The integrity
  test found all 74 quotes verbatim. `kg eval` against the graph was not run: the Neo4j tests wipe the
  database, so the quality graph must be rebuilt first (a `quality` run, mostly cache hits; needs the
  user's yes), which R23 does together with the first judge pass.

### R23. LLM-as-a-judge scoring: judge sheet, verdict file, `kg eval --verdicts`, validated metrics
Depends on R22. Split on 2026-09-22: the code here, the first judge pass (which needs a quality graph) in R24.
Scheme agreed with the user: gold for recall, the review text for precision, each with an exact-match
shortcut; three verdicts (`SUPPORTED` with quote, `UNSUPPORTED` with a reason code, `AMBIGUOUS` outside
the denominator), a `vague` flag, and `gold_corrections` for supported facts the gold lacks.
- `validation/gold.py`: gold models, loader and matching moved out of `evaluate.py` (structural), so that
  `judge.py` and `evaluate.py` share them without a cycle.
- `validation/judge.py`: `build_sheet` (code decides what still needs a verdict: in-scope facts with a
  stable id and their exact-match result, gold triples with found/unfound), the verdict models with their
  own consistency rules, `score_verdicts`, and a coverage check that raises `EvaluationError` when a
  verdict file does not fit the graph's sheet (missing, duplicate, unknown or stale ids).
- `kg eval gold.json` always writes `out/judge_sheet.json`; `--verdicts` adds the validated metrics.
- Tracking: params `gold_hash`, `verdicts`, `judge_model`, `judge_verdicts_hash`; metrics
  `precision_validated`, `recall_validated`, `f1_validated`, `ambiguous_rate`, `vague_rate`, `judged_facts`,
  `gold_corrections`, `unsupported_<reason>` (all four, 0 when unused); artifacts gold, sheet, verdicts,
  `eval_report.json`.
- **Accept:** unit tests for the sheet, the verdict rules, the metrics and the refusal cases; a Neo4j test
  of the eval stage's tracking contract in two passes (sheet, then verdicts).
- **Result (met):** 7 tests (140 total), `ruff` clean. No pipeline run: nothing that shapes the graph
  changed, and the stage test covers the MLflow contract with `RecordingTracker`. The eval report file no
  longer embeds the sheet (own artifact). Exact-match metric names unchanged (`triple_*`), so earlier runs
  stay comparable; the skill maps `precision_exact` to `triple_precision`.

### R24. First judge pass on a quality run; `PART_OF` gold triples
Depends on R23 and on the user's yes for one `quality` run (about $0.17, mostly cache hits).
- Gold: add one `PART_OF` triple per component a review names, to its product (about 22; the title plus the
  sentence state it). Done before the sheet is opened, in its own commit.
- Rebuild the graph (`kg --preset quality run`), `kg eval tests/gold/text_gold.json`, fill
  `out/judge_verdicts.json` as the judge (Claude Fable 5.1), `kg eval ... --verdicts`.
- **Accept:** the eval run shows exact-match and validated scores side by side; the step report gives
  both with `n`, the gold corrections, the run id and its `cost_usd`.
- **Result (met, with a detour):** gold grown to 96 triples (22 `PART_OF`, own commit, before any sheet
  was opened). A first `quality` run (`84e5eccf`, **$0.32**, 81 calls, 2 cache hits; the ≤$0.17 estimate
  assumed cache hits that a changed prompt made impossible) proposed a *different* schema and plan than
  the gold was built on (see "Found along the way"), so the graph was rebuilt with the pinned
  `tests/gold/domain_plan.json` and the new `tests/gold/text_schema.json` (the reviewed schema of
  `f55a8747`): stages `build → link → validate` as runs `55de714a … 5eece5b1`, extract `6d69b626`,
  **$0.00** (70/70 extraction and 4/4 resolve calls from the cache), 126 facts, 120 entities, 19/19 checks.
  Judge pass by Claude Fable 5.1 on the 108 facts and 81 gold triples exact matching could not settle
  (`tests/gold/judge_verdicts_2026-09-22.json`, also an artifact of eval run `9d3e5113`). Numbers,
  n = 126 facts, 96 gold:
  exact-match precision 0.143 / recall 0.156 / F1 0.149; **validated precision 1.000 / recall 0.708 /
  F1 0.829**; ambiguous 1 of 108 (0.9 %), vague 2 (1.6 %), unsupported 0 of every kind; `er_accuracy`
  0.667 (8 of 12 pairs); `question_accuracy` 0.8 (4 of 5; the drawer-rails → supplier chain answers
  through the text graph; the misaligned-holes question fails because the extractor makes the holes,
  not the product, the subject). Reading: the extractor invents nothing (every quote is verbatim and
  states its fact) but misses 28 of 96 gold facts, 13 of them in the Helsingborg Dresser file, the
  densest one; 14 misses are `HAS_DEFECT`, 4 `IMPEDES_ASSEMBLY_OF`. 40 gold corrections, all `PART_OF`
  for parts named only in praise sentences (35 distinct pairs): the gold labelled `PART_OF` only for parts
  with a defect, the extractor labels every named part, and the document supports both. 70 of the 126
  facts are `PART_OF`. Exact match understates precision by 0.86 because entity names differ ("table",
  "the holes") and objects are phrased freely; it is kept as the reproducible floor. Limitation: gold and
  verdicts come from the same model family, labelled before the output was opened.

## Found along the way

(Add items here during a step instead of widening its scope.)

- **A smoke run on `samples/smoke` extracts no facts (found in R15).** Flash-Lite's text schema copied the
  domain types (Product, Assembly, Part, Supplier, and their relations), which three reviews never state,
  so extraction returned empty lists and resolve / link ran on nothing. The code worked; the weak model
  on tiny data did not exercise the later stages. Watch it in the next smoke runs; if it persists, the
  text-schema prompt may need to discourage restating the domain graph (a behaviour change, own step).
- **A smoke run checks the whole pipeline but fails on a graph left by the tests (found in R15).** The
  first run after `uv run pytest` failed five checks because of the test nodes (11 parts instead of 10, an
  orphan chunk). The fix is the open item "the test suite wipes the working graph": a separate Neo4j.

- **Plan and text schema share one model setting (found in R14).** `SCHEMA_MODEL` drives both, so testing a
  stronger text-schema model also changes (and pays for) the plan. Split into `PLAN_MODEL` if it matters.
- **Single runs are too noisy to judge a prompt (found in R14).** Fact counts moved by a third between
  prompts on one run each. Prompt comparisons need the gold set, and ideally two or three runs per side.

- **LLM requests had no time limit (found in R14, fixed in its own commit).** A `gemini-3.1-pro-preview`
  plan request stalled and the run waited 62 minutes: the SDK's default timeout is none, so no error was
  raised and the retry loop never ran. Now `LLM_TIMEOUT_S` (300 s) for both providers; a stall is a failed,
  retried attempt.

- **The vector index keeps its first dimension (found in R12).** `chunk_embeddings` was created with 3,072
  dimensions by a Gemini run; `kg reset` deletes nodes but not indexes, so the 768-dim Ollama vectors are
  silently not indexed. Harmless today (nothing queries the index), but `kg reset` should drop the index,
  or the index name should include the embedding model.
- **Profile samples are not deterministic (found in R11).** `profiler.py` takes `SELECT DISTINCT ... LIMIT 5`
  without `ORDER BY`, so the samples, and therefore the plan prompt, differ between runs: the plan is never
  served from the cache and runs are not exactly reproducible. Fix with an `ORDER BY` and a test.
- **The test suite wipes the working graph (found in R11).** `neo4j` tests share the one database with
  the pipeline, so `uv run pytest` deletes the graph of the last `kg run`. Point tests at a separate
  Neo4j (a second container or port).
- **Extraction thinking dominated cost (found in R11, fixed in R16).** A full run costs about $1.25;
  extraction is $1.05 of it, and 258k of its 270k output tokens are Gemini Flash thinking for 70 short
  chunks. Try a thinking budget for extraction and compare the facts in MLflow.

- **Temperature 0 with Gemini 3 (found in R10).** Google recommends temperature 1.0 for Gemini 3 models and
  warns that lower values can cause looping or degraded answers. The first run at 0 looked fine; changing it
  is a behaviour change, to be decided by comparing MLflow runs.
- **First real run, 2026-09-22 (`kg run data/`, Gemini 3.1 Pro + 3.8 Flash).** All 19 checks pass, domain
  counts match the gold expectations, 171 facts with 100 % verified evidence. Weak for the goal: 70 of 171
  facts are `Customer LOCATED_IN Location`, `Customer REPORTED Defect` got none, and no text component is
  linked to a domain `Component`, so a defect cannot be traced to a supplier yet.

Found and fixed during R1 to R9:
- **Entity merging never worked (fixed in R7).** The APOC call used `collect(o)` as a procedure argument,
  which is a Cypher syntax error. It was never noticed because no test reached a merge: see next item.
- **The end-to-end test did not test what it claimed (fixed in R7).** Its three short reviews were packed
  into one chunk, so "Tables" was never extracted and "Table/Tables were merged" was vacuously true.
  "Table" vs "Tables" also scores 90.9, below the 92 auto-merge default. The test now uses small chunks
  and an explicit threshold, and asserts the chunk count and the merge count.
- NDJSON with a malformed line crashed staging with an uncaught `JSONDecodeError` (fixed in R4).
- With `--out` inside the data dir, staged CSVs and generated text were re-ingested on the next run
  (fixed in R4 and R5).
- Re-ingesting with other chunk settings left the old chunks in the graph as ghost evidence (fixed in R5).
- "Every document is linked" failed for text-only data sets, which have nothing to link to (fixed in R8).
- The LLM cache wrote files non-atomically under a thread pool, and its key ignored temperature (fixed in R2).

Still open (need a human or an API key, so they were not done):
- **Gold set for `data/` (D5).** Done in R22 as a Claude-labelled reference set (`tests/gold/text_gold.json`);
  the judge pass against it is R23. No human labels exist, and the thesis must say so.
- **The proposed schema and plan drift between runs (found in R24, 2026-09-22).** A fresh quality run
  (`84e5eccf`, $0.32) proposed a text schema with `OCCURS_IN` (Defect→Product, the reverse of
  `HAS_DEFECT`), `EXHIBITS`, `CAUSES`, no `IMPEDES_ASSEMBLY_OF` and no `Assembly` type, and a plan with
  label `Component` and relationship `PART_OF` instead of `Part` / `CONTAINS`. Same prompts, same model,
  same data. Exact-match scores against the gold fell to 0.055 / 0.073 and all five questions failed on
  names alone. Consequence: evaluation runs pin the reviewed plan and schema (`tests/gold/domain_plan.json`,
  `tests/gold/text_schema.json`, copied into `out/` before `kg build`); the proposal stages are evaluated
  separately. Open: a schema-stability metric (overlap of a proposed schema with the reviewed one).
- **`kg eval` without `--preset` logs to the `.env` preset's experiment (found in R24).** `.env` has
  `KG_PRESET=dev`, so the first eval runs of the quality graph landed in `kgbuilder-dev`. Always give the
  preset of the graph being scored: `kg --preset quality eval ...`. Open: `kg eval` could refuse when the
  preset's experiment differs from the one the graph's stage runs are in.
- **`PART_OF` facts and exact-match precision (found in R22).** The gold labels no `PART_OF` (a review
  implies that its parts belong to its product, it never states it), but the extractor produces them (51 of
  171 facts in R14). Exact-match precision therefore counts every `PART_OF` fact as wrong. Decide in R23:
  either the judge scores them as `supported` from context, or `score_triples` ignores predicates absent
  from the gold; report which.
- **A full `kg run data/` against Gemini** to check the new MLflow params, traces and token metrics in
  the UI. Everything LLM-free was run on `data/`; the LLM stages were verified with `ScriptedLLM` only.
- Embedding candidates for ER are implemented but off (`er_embedding_candidates = 0`); choose the
  threshold by comparing `resolve` runs in MLflow once a gold `er_pairs` list exists.
- Tests are separated by the `neo4j` marker, not by `tests/unit` and `tests/integration` directories:
  several files mix pure and database tests of one feature, and the marker gives the same fast subset.
- PLAN step 8's optional ADK conversational front end and a recorded demo script.
