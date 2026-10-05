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
| R26 | Document context on every chunk: the extractor may name the product the document is about | done 2026-09-22: `Chunk.context` (first heading, else title) stored in the graph, shown as `<document>` in the extraction prompt, accepted by `verify` for names only; 4 tests (144 total); effect measured in R29 |
| R27 | `PART_OF` derived in code from mention → document → product; removed from the extraction schema | done 2026-09-22: `FactType.derived`, `resolution/derivation.py` in the link stage (`facts_derived` metric, `extractor: derived`), `core/identity.py`, reference schema updated; 3 tests (147 total); effect measured in R29 |
| R28 | Entity merging must not fold repeated evidence (`mergeRels`) | done 2026-09-22: `mergeRels: false`, exact repeats (same type, ends, chunk, quote) and doubled mentions removed explicitly, `duplicate_facts_removed` metric; test failed before the fix (2 facts folded to 1), 148 tests |
| R29 | One quality extraction run and judge pass after R26–R28; new results snapshot | done 2026-09-22: run **$0.07** (extract `fc26bfe4`, 70 paid calls), judge pass by Claude Fable 5.1, eval `0002b0c0`: validated P/R/F1 1.00/0.77/0.87 (from 1.00/0.71/0.83), exact 0.35/0.28/0.31 (from 0.14/0.16/0.15), gold corrections 40 → 11; `docs/evaluation/results_2026-09-22_r29.md` |
| R30 | Exhaustive extraction prompt; one run, one judge pass | done 2026-09-22: prompt `f19abd49dec8`, run **$0.085** (extract `b1fa5eec`), judge pass, eval `d9ce6ed8`: validated P/R/F1 **1.00/1.00/1.00** (strict 0.99 / 0.96; from 1.00/0.77/0.87), `vague_rate` 0.064, gold corrections 23; `docs/evaluation/results_2026-09-22_r30.md` |
| R31 | Extraction thinking `low` against `medium` on the pinned schema; one run, one judge pass | done 2026-09-22: run **$0.354** (extract `9e24bde4`, 74 452 thinking tokens), judge pass, eval `ac83d9a2`: validated 1.00/1.00/1.00, exact F1 0.273 (low: 0.265), vague 0.044 (0.064), 5 fewer facts; **preset keeps `low`**; `docs/evaluation/results_2026-09-22_r31.md` |
| R32 | Derivation reuses the product entity that resolution merged under another spelling | done 2026-09-22 (before R30): `existing_entities` lookup by name or alias, `entity_id` only as fallback; 1 test (149 total, 5 blocked by a Windows policy, see Found along the way) |

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

### R26. Document context on every chunk
Closes finding 3 of the root-cause analysis (`docs/evaluation/results_2026-09-22.md`, section 6): the
file title is only in chunk 0, so in 39 of 70 chunks the extractor cannot name the product ("dresser",
"table", "lamp": 23 product entities for 10 products, `er_accuracy` 0.667, the holes question fails).
- `text/chunking.py`: `Chunk.context`, the document's first markdown heading (else its title), set on
  every chunk. Chunk ids and chunk text unchanged.
- `text/lexical.py`: `context` stored on the `Chunk` node and read back by `read_chunks`.
- `text/extraction.py`: the prompt shows `<document>` above the chunk with a rule to use its proper
  name for the thing the text refers to generically; `verify` accepts an entity name that occurs in the
  chunk *or* in the context. Evidence must still be verbatim in the chunk.
- **Accept:** unit tests for the context (heading, fallback), the verifier and the prompt; the lexical
  round trip keeps the context. No run: the prompt version changes, so the effect is measured in R29.
- **Result (met):** 4 new tests (144 total), `ruff check` and `ruff format --check` clean. No run: the
  prompt version changes (new `prompt_version` on the next extract run), so the effect on product naming,
  `er_accuracy` and the holes question is measured in R29, not here.

### R27. `PART_OF` derived in code
Closes finding 2: 76 of 132 raw triples are `PART_OF` although "this part was named in a review about
this product" is the path Entity ←MENTIONS− Chunk −PART_OF→ Document −ABOUT→ Product, built by code.
- `resolution/linking.py` (link stage): for every Component or Assembly entity, one `PART_OF` fact per
  product its documents are about, to the text-graph Product entity of that name (created if missing),
  with the mention's `chunk_id`, the chunk sentence containing the part name as verbatim `evidence`,
  and `extractor = "derived"`. `MERGE` on the usual key; metric `part_of_derived`.
- The three `PART_OF` fact types leave `tests/gold/text_schema.json` (schema version change, noted
  here); the extraction prompt therefore no longer lists them. Part-to-assembly structure stays in the
  domain graph (CONTAINS via REFERS_TO).
- **Accept:** unit tests for sentence picking and the rule (one part in one review → one fact with the
  right product, chunk and evidence; no ABOUT link → no fact); the 22 gold `PART_OF` triples are still
  matched by `kg eval` against derived facts (Neo4j test).
- **Result (met):** `FactType.derived` (the extractor's prompt and `verify` see only extractable fact
  types; `TextSchema.allows` still accepts derived ones for stored facts), `resolution/derivation.py`
  (`pick_sentence`, `derive_facts`, `DerivationReport`) called by the link stage, `entity_id` moved to
  `core/identity.py` because two packages now write entities. Reference schema: the two product `PART_OF`
  types are `derived`, the part-to-assembly one is gone. 3 tests (147 total), `ruff` clean. No run.
  Note: the LLM's schema proposal could set `derived` itself (the field is in the response schema, with a
  description); the reviewer of `out/text_schema.json` checks it, as for every other field.

### R28. Entity merging must not fold repeated evidence
Closes finding 4: `apoc.refactor.mergeNodes(..., mergeRels: true)` folded 6 of 132 facts (three reviews
stating "drawer PART_OF nightstand" became one relationship), against the invariant of
`text/subject_graph.py` that each statement is separate evidence.
- `resolution/resolver.py`: merge with `mergeRels: false` and re-create the snapshotted facts on the
  canonical entity with the chunk-and-evidence `MERGE` key (the code `undo_merges` already has).
- **Accept:** a Neo4j test that fails before the fix: two facts of one predicate between merged entities
  from different chunks both survive; `facts` before and after resolve differ only by self-loops.
- **Result (met):** `apply_merges` merges with `mergeRels: false` (APOC then moves relationships instead
  of folding them) and afterwards deletes only exact repeats: facts identical in type, ends, `chunk_id` and
  `evidence` (one statement extracted under two spellings), and doubled `MENTIONS` of one chunk.
  `ResolveReport.duplicate_facts_removed` (default 0, so older `resolve.json` files still load for
  `--undo`) is logged by the stage. The new test failed before the fix with the two facts folded into one
  (`[('d.md#1', 'Tables wobble')]`), passes after; the undo round trip is unchanged. 148 tests, `ruff` clean.

### R29. Quality run and judge pass after the structural fixes
Depends on R26–R28 and on the user's yes (about $0.13 for extraction, no cache hits possible).
- Pinned plan and schema, `build → ingest-text → extract → resolve → link → validate → eval`, judge
  pass by Claude with the same gold (hash `3c847ee7dec4`), `docs/evaluation/results_<date>.md`.
- **Accept:** validated and exact-match scores next to the 2026-09-22 snapshot, `er_accuracy`, the five
  questions, `cost_usd`; the report says which numbers moved and attributes them to R26–R28 only.
- **Result (met):** user's yes given in the session; `kg reset`, pinned plan and schema (R27 version),
  build → ingest-text → extract → resolve → link → validate → eval with `--preset quality`. Extract
  `fc26bfe4`: 60 facts (no `PART_OF` asked), 0 rejected, 49 674 / 5 810 / 2 662 tokens, **$0.069**;
  resolve `df856359` $0.002, 6 merges, 0 facts folded; link `91472b1a` 36 `PART_OF` derived. Judge pass
  (`tests/gold/judge_verdicts_2026-09-22_r29.json`, hash `f5c738def4d9`, same gold `3c847ee7dec4`):
  96 facts in scope, 34 exact, 62 judged, all `SUPPORTED`; gold found 27 + 47 = 74 of 96. Validated
  P/R/F1 **1.000 / 0.771 / 0.871** (baseline 1.000 / 0.708 / 0.829); exact-match 0.354 / 0.281 / 0.314
  (baseline 0.143 / 0.156 / 0.149); gold corrections 11 (from 40); `er_accuracy` 0.667 and
  `question_accuracy` 0.8 unchanged (both for reasons in the snapshot's findings); 19/19 checks. Recall:
  11 gold facts newly found, 5 newly lost (all in the bookshelf file: prompt change reshuffles borderline
  claims), net +6. Three judge decisions disclosed in the snapshot (two predicate-tolerant matches, one
  subject generalisation); without them recall is 0.740. 12 of the 22 remaining misses are hedged
  wording, the target of R30.

### R30. Exhaustive extraction prompt
Depends on R29 and on the user's yes for one run.
- `text/extraction.py` prompt: one triple per distinct claim; a sentence listing several defects yields
  one triple per item; mild or hedged complaints are still defects; a complaint about the product as a
  whole goes on the product. Nothing else changes.
- **Accept:** one run, one judge pass, the two eval runs side by side; the report names the prompt
  versions and says the judge was not used to tune wording between passes.
- **Part 1 (done):** the rules block gains one "be exhaustive" rule (one triple per distinct claim; one
  triple per item of a list; hedged complaints count; whole-product complaints go on the product; a
  defect that complicates assembly is also stated as a defect). Nothing else changed; `prompt_version`
  `63b4a11c5b95` → `f19abd49dec8`. One wording test. The wording was written from the R29 miss analysis,
  not adjusted against the judge. Part 2 (the run, the judge pass, the snapshot) follows the user's yes.
  The run will also carry R32 (deterministic, one product entity), which the report will say.
- **Part 2 (done):** user's yes given in the session; same procedure as R29 (`kg reset`, pinned plan and
  schema, build → ingest-text → extract → resolve → link → validate → eval, `--preset quality`). Extract
  `b1fa5eec`: 91 facts (R29: 60), 0 rejected, 58 634 / 8 278 / 2 617 tokens, **$0.085**; resolve 6 cache
  hits; link `b479311e` 49 `PART_OF` derived, 0 entities created (R32 works). Judge pass
  (`tests/gold/judge_verdicts_2026-09-22_r30.json`, hash `8c9ed01361ac`, gold `3c847ee7dec4`): 140 facts
  in scope, 35 exact, 105 judged, all `SUPPORTED`, 9 vague; gold found 27 + 69 = **96 of 96**. Validated
  P/R/F1 **1.000 / 1.000 / 1.000** (R29: 1.000 / 0.771 / 0.871); strict readings 0.990 (without the one
  predicate-tolerant match) and 0.958 (also without three matches on the generic subject "parts").
  Exact-match F1 0.265 (R29 0.314: longer objects, larger denominator). `vague_rate` 0.064 (from 0):
  six subjective complaints entered the graph, listed among the 23 gold corrections. The eval runs
  `0002b0c0` (R29) and `d9ce6ed8` (R30) stand side by side in MLflow with their prompt versions.

### R31. Extraction thinking level: `low` against `medium`
Depends on R30 and on the user's yes for one run (medium costs more; quote the no-cache ceiling).
- Same prompt and schema as R30, `extract_thinking: medium`; one run, one judge pass.
- **Accept:** validated recall and `cost_usd` of both levels side by side; the preset keeps the level
  that the numbers justify.
- **Result (met):** user's yes given in the session; `EXTRACT_THINKING=medium` in the environment for the
  extract command only (the preset and the resolve stage unchanged), same prompt `f19abd49dec8`, same
  pinned plan and schema. Extract `9e24bde4`: 86 facts (low: 91), 0 rejected, 58 634 / 8 132 / **74 452**
  tokens (1 064 thinking tokens per call against 37), **$0.354** (low: $0.085), 385 s of model latency.
  Judge pass (`tests/gold/judge_verdicts_2026-09-22_r31.json`, hash `365ec394febd`): 136 facts, 35 exact,
  101 judged, all `SUPPORTED`, 6 vague; gold 28 + 68 = 96 of 96. Validated P/R/F1 1.000 / 1.000 / 1.000,
  identical to `low`, strict readings identical (0.990 / 0.958); exact-match F1 0.273 (+0.008); 25 gold
  corrections of the same kinds. Eval runs `d9ce6ed8` (low) and `ac83d9a2` (medium) side by side.
  Decision: the `quality` preset keeps `extract_thinking: low` (same accuracy, 4.2× cheaper, shorter
  entity names); `presets.yaml` records the comparison. 13 of 70 traces missing again (pandas block).

### R32. Derivation reuses the entity that resolution merged
Closes the first R29 finding. Done before R30 (out of number order) so that the next paid run does not
carry a known duplicate; R30's report says so.
- `resolution/derivation.py`: before creating the object entity, look for an existing entity of the
  object type whose name or aliases contain the product name (`existing_entities`); `entity_id` is the
  fallback for a product no fact has named yet.
- **Accept:** a Neo4j test with a product merged under another spelling: one derived fact, it points at
  the canonical entity, no entity created; the earlier derivation tests unchanged.
- **Result (met):** `existing_entities` reads every entity of the object type once per derived fact type
  and maps each name and alias to its id. New test passes; `ruff` clean; 144 of 149 tests pass, the other
  5 (four in `test_tracking.py`, one in `test_cli.py`) fail on an environmental import error that started
  during this step and is unrelated to the change (see Found along the way). No run.

### R33. `er_accuracy` over extracted names only; judge-validated `er_accuracy_valid`
Closes the R29 finding "`er_accuracy` scores absence as not merged". Asked for by the user on 2026-09-23,
together with a judge-validated variant (the judge being Claude in the session, as for facts).
- `validation/er.py` (new): the ER sheet (every entity with id, type, name, aliases; every gold pair with
  the entities exact lookup finds for each name), `score_er` (exact: a pair is scored only when both
  names exist; the rest is `not_extracted`), `PairVerdict` (the judge maps an unplaced name to an entity
  id or `null`; code decides merged and right) and `score_er_verdicts` with a coverage guard.
- `judge.py`: `JudgeSheet.er`, `Verdicts.er` (optional, so the R29-R31 verdict files still score);
  `evaluate.py`: `EvalReport.er` / `er_valid`, metrics `er_accuracy`, `er_pairs_scored`,
  `er_not_extracted` and the same three with `_valid`; `kg eval` prints the ER pairs needing a judge.
- Metric definition change: `er_accuracy` before R33 is not comparable with R33 on (0.667 in R24-R31
  counted 3 absent names as errors). Behaviour of the pipeline is unchanged; only scoring changes.
- **Accept:** unit tests for exact scoring, the absent-name rule, judge placements and the refusal cases;
  a Neo4j test of both metrics on the eval run.
- **Result (met, 2026-09-23):** 5 tests (the old `score_er` test replaced by the stricter ones in
  `tests/test_er.py`); 149 passed, 5 failed only on the known pandas block (Smart App Control), `ruff`
  clean. No run: scoring only; the first judged `er_accuracy_valid` comes with R34's run.

### R34. Domain-neutral "be exhaustive" rule
Asked for by the user on 2026-09-23 before testing other datasets: the R30 rule was written in the
furniture reviews' words (defects, failures, assembly, quotes from the corpus) and would steer extraction
on any other dataset.
- `text/extraction.py`: same four instructions (one triple per claim; one per list item; hedged claims
  count; a whole-document claim goes on the document's thing; one statement, two fact types: both
  facts), in general words, examples limited to hedge words. Nothing else changes.
- **Accept:** a wording test that the rule names no domain word; one `quality` run and one judge pass
  (with the R33 ER verdicts) against R30 (`d9ce6ed8`); the report names both prompt versions.
- **Part 1 (done, 2026-09-23):** rule reworded, `prompt_version` `f19abd49dec8` → `984fbd29166b`; the
  R30 wording test now also asserts the rule contains none of "defect", "failure", "complaint",
  "assembly", "product". 149 passed (5 pandas-blocked), `ruff` clean. Part 2 is the run.
- **Part 2 (done, 2026-09-23):** user's yes given in the session; R30 procedure (`kg reset`, pinned plan
  and schema, build → ingest-text → extract → resolve → link → validate → eval, `--preset quality`).
  Extract `b83972d7`: 84 facts (R30: 91), 0 rejected, 55 974 / 7 769 / 2 580 tokens, **$0.081**;
  resolve 5/5 cache hits, 4 merges; link 43 `PART_OF` derived, 0 entities created; 19/19 checks.
  Judge pass by Claude Opus 5.5 (R30: Fable 5.1), `tests/gold/judge_verdicts_2026-09-23_r34.json`, same
  gold `3c847ee7dec4`, eval run `cbcf2764`: 127 facts, 33 exact, 94 judged: 91 `SUPPORTED`, 3
  `UNSUPPORTED` (all `wrong_relation`), 3 vague; gold 26 + 61 = 87 of 96. Validated P/R/F1
  **0.976 / 0.906 / 0.940** (R30 1.000 / 1.000 / 1.000); exact-match F1 0.265 (same as R30);
  `er_accuracy` 0.800 (4 of 5 scored, 7 not found by name), **`er_accuracy_valid` 0.800** (8 of 10,
  2 not extracted; pairs 1 and 4 are right by construction, their variant spelling never occurs in the
  corpus: 6 of 8 without them); `question_accuracy` 0.8. Reading: all 9 recall misses (back panel
  "basically cardboard", drawer bottoms "feel flimsy", the lamp's light base ×3, Gothenburg "scratches
  a bit more easily", Linköping parts as a defect, two derived `PART_OF`) are claims that R30's rule
  quoted almost word for word ("feels flimsy", "lighter than I expected", "it scratches easily"): part
  of R30's 1.000 came from examples copied from the evaluation corpus. 0.906 is the more honest
  estimate for unseen text. Confounds: the judge model changed, and this judge rejected three relation
  choices R30's judge had no counterpart for (a coordinated "and" read as `CAUSES_FAILURE`, "assembly
  was simple enough" read as `IMPEDES_ASSEMBLY_OF`, "flimsy" as a failure mode); recall misses are
  absent facts, not judge calls. The two failing ER pairs are synonyms the resolver keeps apart
  ("drawer rails" / "metal rails", "dimmer switch" / "dimmer function").

### R35. ER gold pairs that test the resolver
Asked for by the user on 2026-09-23 (the "keep apart" safety net before meaning-based candidates).
Gold-only step, written after the R34 output had been seen, so every change is a **gold correction**.
- `tests/gold/text_gold.json` `er_pairs`: 12 → 26 (9 same, 17 keep apart). Pairs 1 ("drawer rail") and 4
  ("predrilled holes") named spellings the corpus never uses and scored as right by construction; they
  now test real variants ("Västerås Bookshelf" / "Västerås Bookshelves", "dimmer switch" / "dimmer
  function"). New same pairs: "Norrköping Nightstand(s)", "drawer handle(s)", "leg(s)". New keep-apart
  pairs are related things a meaning-based matcher could confuse ("veneer" / "finish", "back panel" /
  "panels", "slats" / "frame", "scratches" / "dent", "wobbles" / "tips over easily", "drawer rails" /
  "drawers", ...). `same` means the same kind of thing, because entities are one node per type and name.
- Test: every ER pair name occurs as a whole word in the corpus (after `norm`).
- **Accept:** the test fails on the old pairs 1 and 4 and passes on the new set; gold hash changes, so
  ER scores before and after R35 are not comparable.
- **Result (met, 2026-09-23):** as listed; triples unchanged (96). No run.

### R36. Meaning-based ER candidates, with the threshold chosen from real scores
Closes the R34 finding "the resolver keeps synonyms apart": spelling scores of 61 ("drawer rails" /
"metal rails") and 57 ("dimmer switch" / "dimmer function") never reach the LLM (cut-off 80), and the
embedding matcher that finds synonyms is built but off (`er_embedding_candidates: 0`). Depends on R35.
- `resolution/resolver.py`: `nominate` (step 2 with the optional embedding matcher, shared by a run and
  its preview), `is_auto_merge` (the one rule "only a spelling score merges alone", shared too),
  `preview` / `preview_candidates` with `ResolvePreview` (every candidate pair by name, signal, score and
  route, highest score first). `pipeline/stages.py`: `PreviewResolveStage` (`resolve_preview` run: ER
  params incl. `embed_model`, metrics `candidates`, `candidates_fuzzy`, `candidates_embedding`,
  `would_auto_merge`, artifact `resolve_preview.json`). `kg resolve --preview`.
- Part 2: preview with a low threshold on the R34 graph, choose `er_embedding_candidates` from the
  scores, set it in the `quality` preset, one resolve run and an ER judge pass against the R35 pairs.
- **Accept:** unit tests for the switch, routes and order; a Neo4j test that the preview changes
  nothing; part 2 reports `er_accuracy_valid` before and after with n, and the new LLM calls' cost.
- **Part 1 (done, 2026-09-23):** as listed; 4 tests (154 passed, 5 pandas-blocked), `ruff` clean. No run.
- **Part 2 (done, 2026-09-23):** user's yes given in the session. R34 graph rebuilt from the cache (extract
  `aac6a258`, 70/70 hits, $0). Preview `eeed9e07` at 70: 147 meaning-based pairs; same-meaning rewordings
  above ~78, related-but-different things below ("wobbles" / "tips over", "drawer handles" / "drawer
  rails", "back panel" / "panels"). `quality` preset: `er_embedding_candidates: 78` (61 pairs to the
  LLM). Disclosed: the gold pair "drawer rails" / "metal rails" scores 78.7 and was visible when choosing.
  Resolve `32bdf0fb`: 65 adjudications (60 new), 10 merges (R34: 4), **$0.016**; restore after the
  baseline `8eb07f3e` $0.0004 (2 cache misses, see Found along the way). Same 26 R35 pairs, judge Claude
  Opus 5.5, both verdict files committed: `er_accuracy` 0.857 → **0.929** (n = 14), `er_accuracy_valid`
  0.870 → **0.913** (n = 23, 3 not extracted; eval runs `4cce6055` off, `30337d52` on). The one gained pair
  is "drawer rails" / "metal rails"; the two still wrong are the dimmer pairs, which the LLM saw and kept
  apart. No keep-apart pair was merged. Other merges: "rough" / "rough edges", "uneven metal edges" /
  "rough edges" (debatable), "constantly stick" / "stick", "misalign" / "wouldn't align properly",
  "no longer opens smoothly" / "doesn't open as smoothly as I'd like". Fact scores unchanged
  (0.976 / 0.906; 6 renamed facts re-judged, 85 verdicts carried over by id from R34).

### R37. A blocking rule that fits any dataset: mutual nearest neighbours
Asked for by the user on 2026-09-23: the R36 threshold (78) was read off this dataset's scores, and the
scale of embedding similarity changes with the embedding model and the domain, so it would have to be
re-tuned for every dataset. The system must work for any dataset without hand-tuning.
- `resolution/blocking.py` (new, Strategy): `ScoreThreshold(t)` (the R36 behaviour) and
  `MutualNearest(k)` (a pair when each name is among the other's k most similar names of its type;
  ranks only, so no scale; at most k*n/2 pairs per type). `blocking_from` maps the settings.
- `resolver.find_candidates` / `nominate` / `resolve_entities` / `preview_candidates` take a `Blocking`
  instead of a threshold number. Settings: `er_embedding_blocking` (`off` / `threshold` /
  `mutual_nearest`, default `off`), `er_neighbours` (default 2, >= 1). The `quality` preset names
  `threshold` explicitly, so part 1 changes no behaviour.
- `k = 2` fixed before any result was seen: a name rarely has more than one or two other wordings in
  one corpus, and it is the cheapest k that allows more than one.
- **Accept:** unit tests (threshold, mutual rule with a hub, scale invariance, ties, settings); part 2:
  a preview of `mutual_nearest` on the current graph (no LLM), one resolve, and the ER judge pass on the
  R35 pairs next to R36's threshold result, at the lowest cost the cache allows.
- **Part 1 (done, 2026-09-23):** as listed; 5 new tests, gate green. No run.
- **Part 2 (done, 2026-09-23):** user's yes given in the session. Graph rebuilt from the cache ($0).
  Preview `c1b4ad6c` (`mutual_nearest`, k = 2): 51 meaning-based pairs (threshold 78: 61), including both
  target pairs ("drawer rails" / "metal rails" 78.7, "dimmer switch" / "dimmer function" 84.3) and
  near-synonyms below 78 ("sharper than I'd prefer" / "sharp"). Resolve `9351d5e2`: 56 adjudications
  (27 cache hits), 9 merges, **$0.0074**; the merges are R36's minus the debatable "uneven metal edges"
  / "rough edges", which is not a mutual pair. Judge Claude Opus 5.5, same 26 R35 pairs, eval `24fa0c89`,
  `tests/gold/judge_verdicts_2026-09-23_r37.json`: `er_accuracy` 0.929 (n = 14), `er_accuracy_valid`
  **0.913** (n = 23), identical to the threshold; no keep-apart pair merged; the dimmer pairs are still
  kept apart by the LLM. Facts unchanged (0.976 / 0.906; 2 renamed facts judged, 91 carried over by id).
  The `quality` preset now uses `mutual_nearest`, `er_neighbours: 2`.

### R38. Gold correction: "dimmer switch" and "dimmer function" are different things
Decided by the user on 2026-09-23, after the R36/R37 output had been seen: a dimmer switch is the
physical device, a dimmer function the capability of adjusting light. Gold-only step, a **gold
correction that favours the system** (the LLM had kept the pair apart), disclosed as such.
- `tests/gold/text_gold.json` `er_pairs`: "dimmer switch" / "dimmer function" `same` true → false;
  "dimmer switch" / "dimmer" removed ("dimmer" alone can mean either). 26 → 25 pairs (7 same, 18 apart).
- Rescored in code, no run, from the judge sheets logged by the eval runs and the committed ER
  placements (the placements are per name pair, so none had to change):

  | Candidate rule | `er_accuracy` (exact) | `er_accuracy_valid` |
  |---|---|---|
  | off (eval `4cce6055`) | 0.929 (13 of 14) | 0.955 (21 of 22) |
  | threshold 78 (R36, `30337d52`) | 1.000 (14 of 14) | 1.000 (22 of 22) |
  | mutual nearest k = 2 (R37, `24fa0c89`) | 1.000 (14 of 14) | 1.000 (22 of 22) |

  The MLflow eval runs keep their logged values (old gold hash); the next eval run logs these.
- **Result (done, 2026-09-23):** as listed; 3 of 25 pairs are still "not extracted" (drawer bottoms,
  the lamp's base, the cheap-feeling switch). n = 22: every pair moves the score by 0.045.

### R39. Adjudication context: the sentences that name the entity, with their document
Asked for by the user on 2026-09-23. The adjudicator showed the first 300 characters of one arbitrary
mentioning chunk; measured on the 70 chunks (median 468, max 813 characters), 20 of the 78 names the LLM
judged never appear within the first 300 characters of any of their chunks, so the LLM often saw only a
star rating and praise. The pick was also unordered (the R36 cache-miss finding), and the document name
was missing.
- `core/text.py`: `pick_sentence` moved from `resolution/derivation.py` (now used by two modules).
- `resolution/resolver.py`: `read_mentions` (every mentioning chunk, ordered by entity, document and
  position) and `mention_lines` (up to 3 distinct "[document] sentence" lines per entity, only sentences
  that name the entity or an alias, capped at 400 characters); the prompt lists them per name.
  `prompt_version` 5a6f0ee58a4d → 8dc5bcb0cb7d.
- **Accept:** unit test of the lines (order, alias, repeated sentence, limit, a name past character
  300); a Neo4j test that the prompt carries each name's sentence and document and not the praise before
  it; one resolve run (no cache hits possible: the prompt changed) and the ER judge pass on the 25 pairs.
- **Part 1 (done, 2026-09-23):** as listed; 2 tests (166 passed), `ruff` clean. No run.
- **Part 2 (done, 2026-09-23):** user's yes given in the session. Graph rebuilt from the cache ($0);
  resolve `a0c06737` (`mutual_nearest`, k = 2): 56 adjudications, 0 cache hits (new prompt), 11 merges,
  **$0.0099** (8 007 prompt tokens, $0.00018 per question against $0.00027 with the old context). Judge
  Claude Opus 5.5, eval `a9f36ae3`, `tests/gold/judge_verdicts_2026-09-23_r39.json`: `er_accuracy` 1.000
  (n = 14), `er_accuracy_valid` 1.000 (n = 22), facts unchanged (0.976 / 0.906). The gold pairs cannot
  separate R37 and R39; the merges that changed are outside the gold, judged directly: new and right
  "wobbles slightly on its base" / "wobbles slightly", "sharper than I'd prefer" / "sharp", "horrible
  metal-on-metal sound" / "annoying scraping sound"; new and debatable "defective" / "poorly
  manufactured"; lost "no longer opens smoothly" / "doesn't open as smoothly as I'd like" (a miss) and
  "rough" / "rough edges" (debatable). Net: 3 right merges gained, 1 lost, cheaper per question.

### R40. A larger ER test set: products are items, everything else is a kind
Asked for by the user on 2026-09-23: the 25 ER pairs no longer separate resolver versions (R36, R37 and
R39 all score 1.000). Gold-only step, written before the R41 run.
- `tests/gold/text_gold.json` `er_pairs`: 25 → 69 (30 same, 39 keep apart), from the review text alone,
  no pipeline output opened while writing; not blind (the day's earlier outputs of this corpus were
  seen, and the pairs were written knowing the "item or kind" question R41 addresses). Rule written into
  the file: for products `same` means the same item; for every other type the same kind of thing, since
  entities are one node per type and name across documents. New pairs: wordings of one kind across
  products ("didn't line up properly" / "didn't align properly", "drawer handles" / "drawer pulls"),
  different products, and related-but-different parts, defects and failures.
- Test: no pair listed twice (either order); every name occurs in the corpus (R35 test).
- **Baseline (R39 graph, rebuilt from the cache at $0: resolve 56/56 cache hits):** `er_accuracy` 0.780
  (n = 41), **`er_accuracy_valid` 0.793** (46 of 58, 11 not extracted), verdicts
  `tests/gold/judge_verdicts_2026-09-23_r40_on_r39.json`. All 12 errors are same-kind pairs the LLM kept
  apart (misalignment wordings across products, "drawer handles" / "drawer pulls", thin, chipping,
  dent, stick, opening smoothly); no keep-apart pair is merged.
- **Result (done, 2026-09-23).** Found along the way: a Neo4j test without its marker wiped the graph
  under `-m "not neo4j"`; fixed in its own commit (conftest marks every `driver` test).

### R41. Adjudication asks "same item" for items and "same kind" for kinds
Asked for by the user on 2026-09-23. Entities are one node per type and name across documents, so for
most types an entity is a kind; the adjudicator was asked about "the same real-world thing" and, shown
the document names since R39, kept same-kind wordings apart across products (all 12 R40 baseline errors).
- `resolution/resolver.py` `ADJUDICATE_PROMPT`: if the type names individual items (product, person,
  organisation, place) the question is "the same item", otherwise "the same kind, even across
  documents". The LLM reads the type name, so no schema needs a flag. `prompt_version` 8dc5bcb0cb7d →
  f8ad72ce4935.
- **Accept:** the prompt test checks the wording; one resolve run (no cache: the prompt changed) and an ER
  judge pass on the 69 R40 pairs next to the R39 baseline (0.793, n = 58).
- **Part 1 (done, 2026-09-23):** as listed; 166 passed, `ruff` clean. No run.
- **Part 2 (done, 2026-09-23):** user's yes given in the session. Graph rebuilt from the cache ($0);
  resolve `4ba527f2`: 56 adjudications, 28 merges (R39: 11), **$0.0143**. Judge Claude Opus 5.5, eval
  `682b849d`, `tests/gold/judge_verdicts_2026-09-23_r41.json` (17 renamed facts judged, 71 carried over):
  `er_accuracy` 0.780 → **0.976** (n = 41), `er_accuracy_valid` 0.793 → **0.966** (56 of 58, n = 58);
  facts unchanged (0.976 / 0.906). No keep-apart pair merged; merges checked one by one, two debatable
  as before ("defective" / "poorly manufactured", "rough" / "rough edges"). Remaining errors: the six
  misalignment wordings form two groups of three (each name nominates only its 2 nearest), and
  "stick" / "sticks when i open it too fast" stay apart. Three findings below.

### Verification of the three R41 findings (2026-09-23, no run, $0)
Asked for by the user: confirm or refute each finding from the code and the saved artifacts, measure it,
fix nothing. All three confirmed; details and numbers under "Found along the way" (entries marked
*Verified 2026-09-23*). Recomputed offline from the logged `judge_sheet.json` of eval runs `cbcf2764`
(R34), `9a79cfee` (R39), `682b849d` (R41), the R41 resolve report `4ba527f2` and the previews
`eeed9e07` / `c1b4ad6c`. Order agreed with the user: R42 → R43 → R44 → R45.

### R42. Exact matching only within the gold triple's document
Closes the F1 finding. Evaluation code only; the graph does not change.
- `validation/gold.py` `matches`: a fact matches a gold triple only when the triple has no `doc_id` or
  the fact comes from that document. Used by `score_triples` and `build_sheet`, so both agree.
- **Test first:** `test_a_fact_matches_only_gold_triples_of_its_own_document` (fails before: `[0, 0]`).
- **Accept:** gate green; the R41 sheet re-scored in code from the logged artifact with the one verdict
  the doc-aware sheet adds (fact `b72dc353c94b`, the Västerås veneer): exact 0.299 / 0.292, validated
  unchanged 0.976 / 0.906; R34 / R39 exact recall 0.260 recorded as corrections. No pipeline run.
- **Result (done, 2026-09-23).** As listed; 167 passed (166 + the new test), `ruff` clean. No run, $0.
  The R41 sheet re-scored in code (`score_verdicts` on the logged sheet with doc-aware matching): exact
  precision / recall 0.307 / 0.292 → **0.299 / 0.292** (n = 127 facts, 96 gold); validated 0.976 / 0.906 /
  0.940 unchanged with `tests/gold/judge_verdicts_2026-09-23_r42.json` (R41's verdicts plus fact
  `b72dc353c94b` SUPPORTED as triple 69; judge Claude Opus 5.5); the R41 file is refused as stale, as it
  should be. Corrected exact numbers for earlier reports: R34 and R39 recall 0.271 → 0.260, their
  `gold_corrections` one too high (the Västerås back-panel fact is triple 73). The `evaluation` skill's
  definition of exact match now says "of the fact's own document".

### R43. Gold questions read aliases as well as names
Gold-only step. Questions 3-5 filter on `f.name`, so the canonical name a merge picks (R44) could move
`question_accuracy` without any fact changing.
- `tests/gold/text_gold.json`: the name filters test `[f.name] + coalesce(f.aliases, [])`.
- **Test first:** a Neo4j test where a merged entity carries the searched word only as an alias.
- **Accept:** gate green; expected answers unchanged. No run (measured with R44's run).
- **Result (done, 2026-09-23).** Questions 3-5 filter `any(n IN [f.name] + coalesce(f.aliases, []) WHERE
  ...)`; expected answers, triples and ER pairs unchanged (`gold_hash` changes). New Neo4j test runs the
  committed wobbling question on a graph where "wobbles slightly" is only an alias; it failed before
  (`[]`). 168 passed, `ruff` clean. No run; `question_accuracy` is measured with R44's run.

### R44. A merged kind is named by its most general name; every fact keeps its own wording
Closes the F2 finding. Behaviour change in resolution and in the fact writer.
- `resolution/resolver.py` `group_merges`: canonical = most mentioned, then the **shortest** name, then
  id; the other names stay aliases (derivation, R32, looks up name + aliases, so it is unaffected).
- `text/subject_graph.py`: each fact edge stores the subject and object names the extractor gave it,
  set on create only (not part of the `MERGE` key, so writes stay idempotent); `StoredFact` and the
  judge sheet show them.
- **Test first:** a merge of "crack developing along the bottom" and "crack" names the node "crack";
  each fact still reads its own wording.
- **Accept:** gate green; one cache rebuild of the R41 graph (`quality`, $0 LLM, needs the user's yes)
  and a judge pass on the renamed facts; ER and fact scores next to R41.
- **Part 1 (done, 2026-09-23).** As listed, plus one determinism fix the new property needs: after a
  merge, `apply_merges` deletes exact repeats (one quote extracted under two spellings); they now differ
  in wording, so they are ordered by it and the same one survives every rebuild (the fact id depends on
  it). `fact_id` uses the fact's own wording, so a canonical name no longer changes the ids the verdicts
  refer to. Tests: shortest canonical (unit), own wording after a merge (Neo4j), the surviving repeat
  (extended). 170 passed, `ruff` clean. Local `docs/system-overview.html` updated. No run yet.
- **Part 2 (done, 2026-09-23).** User's yes given in the session. Cache-only rebuild of the R41 graph
  (`quality`; `ingest-text`'s chunk embedding hit 429 three times, worked after a 30-minute wait):
  extract `63696a58` 70/70 cache, resolve `d7d881f6` 56/56 cache, 28 merges, **$0** (embedding calls
  unpriced). Canonical names now general: "crack", "small dent", "damaged", "scratches", "Västerås
  Bookshelf" (was "Bookshelves"); a few same-review groups got a terser name ("rough" for "rough edges",
  "misalign"), with each fact still showing its own wording. Judge Claude Opus 5.5, eval `dfe61e24`,
  `tests/gold/judge_verdicts_2026-09-23_r44.json`: the 33 facts whose id changed once (ids now come
  from the fact's own wording) mapped by document, relation and quote and re-read; every verdict holds,
  26 reasons that named a merged kind rewritten. Scores equal R41: validated 0.976 / 0.906 / 0.940,
  exact 0.299 / 0.292, `er_accuracy` 0.976 (n = 41), `er_accuracy_valid` 0.966 (n = 58),
  `question_accuracy` 0.8 (question 4, see Found along the way). Naming changed, merges did not.

### R45. A second resolution pass over the merged groups
Closes the F3 finding. Behaviour change in resolution.
- `resolution/resolver.py`: after merging, block and adjudicate again on the merged entities, until a
  pass merges nothing (at most 3 passes). A group of more than k near-identical wordings fills its
  members' k slots; once merged it is one entity and can meet its neighbour group. Keeps mutual
  nearest, k = 2. Unchanged pairs are answered from the LLM cache.
- **Test first:** a fake embedder with two trios: one pass gives two groups, the repeat gives one; a
  keep-apart pair answered "no" stays apart.
- **Accept:** gate green; one `quality` resolve on the cached graph (estimate $0.003-0.008, needs the
  user's yes) and an ER judge pass; pair 35 checked, keep-apart pairs 49, 61, 64, 65 checked.
- **Part 1 (done, 2026-09-23).** `decide_in_passes` repeats nominate, decide and group on a merged view
  (`merged_view`: a group is one record with its canonical's id and name, so pass 1's embeddings still
  score it, and every member's aliases) until a pass merges nothing, at most 3 passes; the graph is
  written once from one union-find over all decisions, so the snapshot and `--undo` are unchanged. A
  question is not asked again unless one side gained members. The adjudicator shows a merged entity the
  sentences of all its members (`mention_lines(owner=...)`). Pass 1 builds the same candidates and
  prompts as before (tests unchanged, cache hits). `ResolveReport.passes` and a `passes` metric. Tests:
  two trios with a fake embedder (one pass: two groups; three passes: one group of six, the keep-apart
  name apart, 9 questions, none repeated), merged-entity context; 172 passed, `ruff` clean. No run yet.
- **Part 2 (done, 2026-09-23).** User's yes given in the session. Rebuild (`quality`; extract `65aabedc`
  70/70 cache), resolve `7a9dc180`: 3 passes, 85 adjudications (56 cache hits, 29 new), 32 merges (R44:
  28), **$0.0080**. Pass 2 asked the 93.2 bridge and merged the two misalignment trios into one entity
  of six. Judge Claude Opus 5.5, eval `35be69bf`, `tests/gold/judge_verdicts_2026-09-23_r45.json`: all
  89 fact and 68 recall verdicts carried over unchanged (fact ids from own wording, R44); ER pairs 23
  and 43 placed anew. `er_accuracy` 0.976 → **1.000** (n = 41), `er_accuracy_valid` 0.966 → **0.983**
  (57 of 58, n = 58): pair 35 right; no keep-apart pair merged (64 and 65 asked and kept apart). Facts
  unchanged (validated 0.976 / 0.906 / 0.940, exact 0.299 / 0.292), `question_accuracy` 0.8. The 4 new
  merges outside the gold: 3 clearly right ("didn't fit where they were supposed to" / "not fitting
  together properly", "don't allow the drawers to slide smoothly" / "no longer opens smoothly", the six
  misalignment wordings), 1 debatable ("sticks when i open it too fast" into "no longer opens
  smoothly"). Still wrong: pair 43 ("stick" / "sticks"), an over-long extracted name.

### R46. Gold question 4 follows the pinned schema (gold correction, decided by the user)
Asked for by the user on 2026-09-23 after R45. Question 4 ("Which products are reported with misaligned
pre-drilled holes?") looked for "hole" in a `Defect` name; the pinned schema stores the holes as a
`Component` with `HAS_DEFECT` to the misalignment, so it answered `[]` in every run. **A gold correction
made after seeing output**, disclosed as such: the question text and the six expected products are
unchanged, only its Cypher now follows the schema.
- `tests/gold/text_gold.json`: a `Component` whose name or alias contains "hole", `HAS_DEFECT` to a
  `Defect` whose name or alias says "align" or "line up"; the product comes from that fact's own chunk,
  because the holes node is one entity shared by every review.
- **Test first:** a Neo4j test with one holes node, a misalignment fact from one review and an unrelated
  defect from another; only the first product is answered (failed before: `[]`).
- **Result (done, 2026-09-23).** 173 passed, `ruff` clean. Cache-only rebuild of the R45 graph (user's
  request): extract `5480aa1b` 70/70 and resolve `9c819dc6` 85/85 from the cache, **$0**; eval `cf8e30d3`
  with `judge_verdicts_2026-09-23_r45.json`: `question_accuracy` 0.8 → **1.000** (5 of 5; question 4
  answers all six products); every other score as R45 (validated 0.976 / 0.906 / 0.940, exact 0.299 /
  0.292, `er_accuracy` 1.000 (n = 41), `er_accuracy_valid` 0.983 (n = 58)).

### R47. Entity names leave out when and under which condition a claim holds
Asked for by the user on 2026-09-23 ("fix pair 43, keep the prompt general"). The extractor named the
Norrköping failure "sticks when i open it too fast"; the clause dominates the name, so no resolver can
match it with "stick" (ER pair 43). Behaviour change in the extraction prompt.
- `text/extraction.py` `PROMPT`: names contain only the words that name the thing; a clause of time or
  condition ("when ...", "if ...", "after ...") and frequency words ("sometimes", "often") stay in the
  evidence. Generic function words only; the hedge rule still keeps degree words in the claim.
- **Tests first:** the rule's wording, with no domain word (it failed before); a guard that no four
  consecutive words of the prompt's rules occur in the review corpus (passes before and after: the
  rules share no 4-gram with the corpus today, and must not start to).
- **Accept:** gate green; one `quality` run (the prompt changes every extraction prompt, so no cache:
  extract, resolve, link, eval; needs the user's yes) and a full judge pass next to R46: pair 43, the
  fact scores (names change, so fact ids change) and the keep-apart ER pairs.
- **Part 1 (done, 2026-09-23).** As listed; 175 passed, `ruff` clean. No run yet.
- **Part 2 (done, 2026-09-23).** User's yes given in the session. Uncached `quality` run: extract
  `defa4806` 70 calls **$0.0826** (76 facts, R46: 84), resolve `59e5c327` 3 passes, 36 new questions
  **$0.0097**, 28 merges; total **$0.092**. Judge Claude Opus 5.5, eval `29b64199`,
  `tests/gold/judge_verdicts_2026-09-23_r47.json` (55 fact verdicts carried unchanged, 20 new facts
  judged, 17 gold triples and 12 ER placements judged anew; names not extracted are placed on nothing).
  **Pair 43 right**: "sticks" is now a name and merged with "stick". Scores next to R46: `er_accuracy_valid`
  0.983 (n = 58) → **1.000** (n = 57), `er_accuracy` 1.000 (n = 40); facts validated P / R / F1 0.976 /
  0.906 / 0.940 → **0.992 / 0.865 / 0.924** (n = 96 gold), exact 0.299 / 0.292 → 0.370 / 0.365;
  `question_accuracy` 1.000. Recall lost 4 gold triples: both "veneer extremely thin" (the sentence
  "extremely thin and chipped" gave only the chipping), the Jönköping sharp edges and the Helsingborg
  rails' mismatched dimensions; "quite thin" / "a bit thin" became "thin" (degree words dropped against
  the rule's intent). One uncached run cannot separate the rule's effect from run-to-run variation: see
  Found along the way.

### R48. Every reported run re-scored on one scorer and one gold version ($0)
Asked for by the user on 2026-09-23 ("correct the past results with you as the judge"). Reported numbers
were computed with a doc-blind exact match (fixed in R42) and on changing gold versions (ER pairs 12 → 26
→ 25 → 69, question 4 in R46), so they could not be compared across steps. Evaluation only; no graph,
no LLM, no run.
- `validation/rescore.py`: `rescore(sheet, gold, verdicts)` rebuilds each logged fact's names from the
  sheet's entity list and passes them through the same `build_sheet`, `score_verdicts` and ER functions
  as `kg eval`, keeping the logged fact ids so the run's verdicts still apply; a sheet written before
  R33 (no entity list) is refused. `RescoreStage` (MLflow run `rescore`: params sheet, gold and verdict
  hashes; artifacts the three inputs, `rescore_sheet.json`, `rescore_report.json`), `kg rescore SHEET
  GOLD --verdicts FILE`. Questions need the graph: reported from R46 on only.
- **Judging rules for the re-score** (Claude Opus 5.5): no existing verdict changed. Added only what the
  corrected sheets ask: gold triple 73 → the Västerås "back panel quite thin" fact in R34-R40 (its
  SUPPORTED verdict gets `gold_index` 73: the doc-blind sheet never showed the triple as unfound, so it
  had been counted as a gold correction), fact `b72dc353c94b` in R41 (R42's verdict). ER placements for
  R34-R39, judged on older pair lists (whose indices no longer match), come from one set of **name
  anchors** judged once on the R39 graph for all 69 pairs (the extracted name each gold name refers to),
  applied identically to every run of the same extraction; they agree with every placement the older runs
  judged themselves (0 conflicts) and with R41's independently judged placements (28 of 28). Verdict
  files: `tests/gold/rescore_r48/` (R34-R41); R44-R47 use their committed files unchanged.
- **Tests first:** re-scoring the R41 case in miniature (cross-document match removed, ids kept, ER on
  today's pairs), refusal of a sheet without entities, the stage's tracking contract. Checks on real
  artifacts: re-scoring R47 reproduces its logged numbers exactly, R41 + R42's verdicts reproduce R42.
- **Result (done, 2026-09-23).** 178 passed, `ruff` clean, 11 `rescore` runs logged, $0. All on
  `tests/gold/text_gold.json` as of R46 (96 triples, 69 ER pairs); judge Claude Opus 5.5:

| Step (eval run) | What changed | Exact P / R | Validated P (n) | Validated R (n = 96) | ER valid (n) | ER exact (n) |
|---|---|---|---|---|---|---|
| R34 (`cbcf2764`) | domain-neutral extraction rule | 0.260 / 0.260 | 0.976 (127) | 0.906 | 0.724 (58) | 0.683 (41) |
| R36 off (`4cce6055`) | spelling candidates only | 0.260 / 0.260 | 0.976 (127) | 0.906 | 0.724 (58) | 0.683 (41) |
| R36 (`30337d52`) | meaning candidates, threshold 78 | 0.283 / 0.260 | 0.976 (127) | 0.906 | 0.759 (58) | 0.707 (41) |
| R37 (`24fa0c89`) | mutual nearest, k = 2 | 0.268 / 0.260 | 0.976 (127) | 0.906 | 0.759 (58) | 0.707 (41) |
| R39 (`a9f36ae3`) | sentence context for the adjudicator | 0.276 / 0.260 | 0.976 (127) | 0.906 | 0.793 (58) | 0.780 (41) |
| R41 (`682b849d`) | "same item" / "same kind" question | 0.299 / 0.292 | 0.976 (127) | 0.906 | 0.966 (58) | 0.976 (41) |
| R44 (`dfe61e24`) | general names, own wording | 0.299 / 0.292 | 0.976 (127) | 0.906 | 0.966 (58) | 0.976 (41) |
| R45 (`35be69bf`) | second resolution pass | 0.299 / 0.292 | 0.976 (127) | 0.906 | 0.983 (58) | 1.000 (41) |
| R46 (`cf8e30d3`) | gold question 4 (questions 1.000) | 0.299 / 0.292 | 0.976 (127) | 0.906 | 0.983 (58) | 1.000 (41) |
| R47 (`29b64199`) | naming rule, uncached extraction | 0.370 / 0.365 | 0.992 (119) | 0.865 | 1.000 (57) | 1.000 (40) |

  95% Wilson intervals: validated precision 124/127 [0.93, 0.99]; recall 87/96 [0.83, 0.95] (R47 83/96
  [0.78, 0.92]); ER valid R34 42/58 [0.60, 0.82], R39 46/58 [0.67, 0.88], R41 56/58 [0.88, 0.99], R45
  57/58 [0.91, 1.00], R47 57/57 [0.94, 1.00]. Corrections to earlier reports: exact recall R34-R40 0.271 →
  0.260; ER numbers of R34-R39 were on 12-25 pairs (R36 / R37 0.913 on n = 23, R39 1.000 on n = 22) and
  are 0.724-0.793 on the 69 pairs; the step that moved ER is R41's question (0.793 → 0.966), then R45.
  Fact scores R34-R46 are identical because extraction was served from the cache; only R47 re-sampled it.

### R49. Rule or chance? The old and the new extraction prompt, each sampled once more uncached
Asked for by the user on 2026-09-23 (both runs, about $0.18). R47's recall fell 0.906 → 0.865 on its
first uncached extraction since R34, so the naming rule and run-to-run variation were confounded. No code
change: a fresh cache folder (`CACHE_DIR`) forces new samples; the old prompt ran from a temporary
worktree of `e65422b` (R46) into this project's MLflow database, then every graph was evaluated here
with today's scorer and gold and judged (Claude Opus 5.5; a fact verdict carries over when the fact id,
i.e. chunk, relation and wording, was judged before; everything else judged anew).
- **Result (done, 2026-09-23).** Run A, old prompt: extract `aa34e105` $0.0762, resolve `0ac20077`
  $0.0210, eval `f4db4823`, `tests/gold/judge_verdicts_2026-09-23_r49a.json`. Run B, new prompt: extract
  `fc7ad5e5` $0.0788, resolve `d036d115` $0.0204, eval `c4ecfdee`, `..._r49b.json`. Total **$0.196**.

  | Prompt, sample | Validated P | Validated R (n = 96) | ER valid (n) | Exact P / R |
  |---|---|---|---|---|
  | old, cached (R46) | 0.976 | 0.906 (87) | 0.983 (58) | 0.299 / 0.292 |
  | old, fresh (A) | 1.000 | 0.906 (87) | 0.982 (55) | 0.355 / 0.365 |
  | new, fresh (R47) | 0.992 | 0.865 (83) | 1.000 (57) | 0.370 / 0.365 |
  | new, fresh (B) | 1.000 | 0.854 (82) | 1.000 (53) | 0.385 / 0.396 |

  Reading: validated recall is stable within a prompt (old 87 and 87, new 83 and 82) and 4-5 triples
  lower with the naming rule in both samples, so the drop is the rule's, not chance (two samples per
  arm: a direction, not a proof). Both new samples lose the veneer's thinness ("extremely thin and
  chipped" gives only the chipping) and drop degree words. Pair 43 is right in both new samples and
  wrong in the old one. Exact scores move by up to 0.07 between two samples of one prompt, so they
  cannot rank prompt variants; validated scores can. Next candidate (not started): keep the rule and
  say explicitly that degree words ("a bit", "extremely") stay in the name and that one sentence with
  "X and Y" gives both claims, then the same two-sample comparison.

### Held-out benchmark: does the system generalise? (R50-R53, planned 2026-09-24)
Asked for by the user on 2026-09-24: a second, heterogeneous dataset, of good quality and small enough to
keep runs cheap, with its own gold set, to show that the system works beyond the furniture reviews.
Decisions made by the user: **NHTSA vehicle safety data** (US government, public domain: real records
and real owner narratives), the **automatic arm** (the pipeline proposes the plan and the text schema
itself; nothing is pinned) and **about 50 gold triples**. Rules for the whole benchmark:
- **The pipeline is frozen.** No prompt, threshold or model is changed because of this dataset. A crash
  or a code bug found on it is fixed in its own step with a test and reported as such; a quality problem
  is a result, recorded, not tuned away.
- **Label before looking.** The gold claims are written from the source files before any pipeline output
  for this dataset exists (R51). Only the predicate names are aligned to the proposed schema afterwards,
  before extraction (R52); that mapping is listed as its own table, not hidden in the gold.
- Five vehicles of five makes, one model year each (2016 Honda Civic, 2017 Nissan Rogue, 2019 Toyota
  RAV4, 2015 Ford Escape, 2019 Subaru Outback). Complaints are chosen by a fixed rule, not by content.

### R50. The held-out dataset `heldout/nhtsa/` and a `heldout` preset
- `heldout/nhtsa/build.py`: fetches the five vehicles' recalls and complaints from api.nhtsa.gov, keeps
  every recall and, per vehicle, the 5 complaints with the lowest ODI number whose narrative has 40-110
  words, and saves only those records under `raw/` (with the fetch date). From `raw/` alone (no network)
  it writes `data/`: `vehicles.csv` (CSV), `recalls.json` (the API's nested JSON shape), `complaints.ndjson`
  (one record per line, the product as a nested object), `complaints/<make>_<model>_complaints.md` (one
  document per vehicle, one section per complaint narrative). `README.md`: source, licence, selection
  rule, transformations. Nothing in `data/` is edited by hand.
- `presets.yaml`: `heldout` = the `quality` models and thinking levels on `heldout/nhtsa/data`,
  `ask_permission: true`, experiment `kgbuilder-heldout`.
- **Tests:** rebuilding `data/` from `raw/` reproduces the committed files; staging and profiling the
  dataset (no LLM) stage three tables and find the recall -> vehicle key; the preset loads.
- **Accept:** gate green. No run.
- **Result (done, 2026-09-24).** As listed; fetched 2026-09-24: 29 recalls (Civic 5, Rogue 5, RAV4 7,
  Escape 9, Outback 3), 25 complaints of 596-1 095 per vehicle, 1 974 words of narrative, 25 chunks at
  the default chunk size (one per complaint). Offline check: staging reads all three tables (nothing
  skipped; the nested product becomes `product.productModel`), the profiler finds `recalls.Model` and
  `complaints.product.productModel` -> `vehicles.model`. The `heldout` preset merges the `quality` block
  (YAML `<<`), so the two cannot drift apart. 161 passed, 23 skipped (Neo4j down; no graph code
  touched), `ruff` clean. No run, $0.

### R51. Gold set for the held-out dataset, written before any run
- `tests/gold/heldout_nhtsa_gold.json`: every complaint section labelled exhaustively, each claim with
  `doc_id` and verbatim `evidence`; the predicates are Claude's own vocabulary, fixed before any proposal
  (`HAS_PROBLEM`, `CAUSES`, `PART_OF`, `COVERED_BY_RECALL`), because the schema does not exist yet. ER pairs whose names occur in the narratives; 3-5
  questions with expected answers from the data (Cypher follows in R52).
- Integrity test as for the furniture gold (quotes verbatim, every section labelled, no repeats).
- **Accept:** gate green. No run; the step report states that no output for this dataset existed.
- **Result (done, 2026-09-24).** Written by Claude Opus 5.5 from the five documents alone; no pipeline
  output for this dataset existed (no run had ever been made on it). **65 triples** (HAS_PROBLEM 40,
  PART_OF 18, CAUSES 5, COVERED_BY_RECALL 2; 10-16 per document), **18 ER pairs** (7 same, 11 keep
  apart), **5 questions** (1 domain only, 4 text or text + domain) with expected vehicle models or campaign
  numbers; the goal string and the labelling rules are in the file's `_comment`. Three integrity tests
  (quotes verbatim, every document labelled once per claim; ER names occur as whole words; expected
  answers exist in the data). The question Cypher is a `MATCH` placeholder until R52. 164 passed, 23
  skipped (Neo4j down), `ruff` clean. No run, $0.
- **Part 2: the user's review (done, 2026-09-24), before any run, so not a gold correction.** (1) The Civic
  ACC/LKAS complaint put its problems on the vehicle although the text names the two systems; they are now
  the subjects (4 problems each) with a `PART_OF` each. (2) "windshield crack" and "cracked" were one claim
  counted twice in one document; one kept. (3) The placeholder node "part failure" is replaced by
  `piston clip ring CAUSES engine failure / stalling`. (4) "auto-shutoff" named a normal feature;
  "premature auto-shutoff" says what is wrong (the one name not copied from the text). Applying (1)'s
  principle, Claude also removed the Rogue's vehicle-level "braked on its own" (the same event as the
  front camera system's "activated the brakes"). ER pairs: ACC / adaptive cruise control, LKAS / lane
  keeping assist system (same), the two systems (apart). Now **68 triples** (HAS_PROBLEM 41, PART_OF 20,
  CAUSES 5, COVERED_BY_RECALL 2), **21 ER pairs** (9 same, 12 apart), 5 questions. The ACC/LKAS complaint
  now carries 10 of the 68 triples, so it weighs more in recall than any other complaint.

### R52. Proposal stages on the held-out dataset; predicate mapping
- One `heldout` run of profile, plan, build, ingest-text and text-schema (user's yes needed; estimate
  about $0.03, corrected to $0.10-0.20 before asking: schema work at medium thinking barely depends on data size). The accepted plan and schema are frozen as this arm's result.
- Each gold `relation` is mapped to one proposed predicate, or marked "not expressible" (a recall loss the
  schema causes, counted as such); the question Cypher is written against the proposed labels.
- **Accept:** gate green; the mapping table in this file.
- **Result (done, 2026-09-24).** User's yes given in the session (for R52 and R53). Goal as in the gold's
  `_comment`, output in `out/heldout/`. Plan `5306a688` $0.0385 (2 calls, accepted in round 1): `Vehicle`
  (key and name `model`), `Recall`, `Complaint`, `AFFECTS_VEHICLE`, `CONCERNS_VEHICLE`; build 25 + 29 + 5
  nodes, 54 relationships, nothing dropped; ingest 5 documents, 25 chunks; text schema `296ec11d` $0.0563
  (4 calls, 2 rounds): entity types Complaint, Vehicle, Recall, Component, Problem; facts `REPORTS_PROBLEM`
  (Complaint→Problem), `INVOLVES_COMPONENT` (Complaint→Component), `OCCURS_IN_COMPONENT`
  (Problem→Component), `MENTIONS_RECALL` (Complaint→Recall), `CONCERNS_VEHICLE` (Complaint→Vehicle),
  `PART_OF_VEHICLE` (Component→Vehicle, derived). **Total $0.095.** Frozen as
  `tests/gold/heldout_nhtsa_plan.json` / `heldout_nhtsa_text_schema.json`. Mapping, a fixed rule, done
  before any extraction; each triple keeps its R51 labels in `labelled_as`:

  | R51 relation | n | Proposed schema |
  |---|---|---|
  | component HAS_PROBLEM problem | 32 | problem OCCURS_IN_COMPONENT component (inverted) |
  | vehicle HAS_PROBLEM problem | 9 | "Complaint <ODI>" REPORTS_PROBLEM problem (no vehicle-problem relation) |
  | PART_OF | 20 | PART_OF_VEHICLE (derived in code) |
  | COVERED_BY_RECALL | 2 | "Complaint <ODI>" MENTIONS_RECALL recall |
  | CAUSES | 5 | not expressible: can only be missed |

  Recall is therefore reported twice: over all 68 triples (end to end) and over the 63 the schema can
  express (extraction). The schema is complaint-centred where the gold is component-centred: facts such as
  `Complaint INVOLVES_COMPONENT brake` have no gold counterpart and will surface as judge-supported gold
  corrections, not as errors. Question Cypher written and dry-run on the built graph: question 1 (domain
  only) already answers correctly; 2-5 need R53's extraction. New test: every gold predicate is in the
  frozen schema or is `CAUSES`. The `heldout` preset's cost line corrected (about $0.15 per run).

### R53. Extraction, resolution and the judge pass on the held-out dataset
- One `heldout` run of extract, resolve, link, eval (user's yes needed; estimate about $0.03-0.05), a
  judge pass (Claude in the session), both scores with `n` and Wilson intervals, next to the furniture
  numbers; a dated results snapshot.
- **Result (done, 2026-09-24).** User's yes given in the session. Extract `fa8abb59` 25 calls $0.0627 (134
  facts, 1 rejected), resolve `374037af` 81 adjudications $0.0192 (106 → 94 entities, 12 merges), link
  `732c9b91` (5 of 5 documents, 25 entities linked); **R53 $0.082, benchmark total $0.177**. Judge Claude
  Opus 5.5, `tests/gold/judge_verdicts_2026-09-24_r53_heldout.json`, eval `d332c60f`:

  | Score | Held-out NHTSA (automatic arm) | Furniture, R49 new prompt (pinned schema) |
  |---|---|---|
  | validated precision | **0.977** (170 / 174) [0.94, 0.99] | 0.992-1.000 |
  | validated recall, all gold | **0.706** (48 / 68) [0.59, 0.80] | 0.854-0.865 (n = 96) |
  | validated recall, expressible gold | 0.762 (48 / 63) [0.64, 0.85] | – |
  | exact precision / recall | 0.080 / 0.162 | 0.370-0.385 / 0.365-0.396 |
  | ER valid (exact) | 0.952 (20 / 21) [0.77, 0.99] (0.941, n = 17) | 1.000 |
  | questions | **1.000** (5 / 5) | 1.000 |

  95% Wilson intervals in brackets. The 20 missed gold triples: 5 `CAUSES` (not in the proposed schema),
  11 where the extractor put the problem on the complaint (`REPORTS_PROBLEM`) and the part on the complaint
  (`INVOLVES_COMPONENT`) instead of the problem on the part (8 of them the Civic ACC / LKAS complaint), 4
  content misses (the camera misreading the bridge, the navigation system staying on at night, the double
  image in the mirrors, the premature shutoff). Unsupported: 4, all `wrong_relation` (wiper blade and RAV4
  engine / gas gauge named as involved or faulty although the text says otherwise); 1 ambiguous.
  `gold_corrections` 115 is a count, not a gap: 111 are facts of the complaint-centred schema the
  component-centred gold has no counterpart for (INVOLVES_COMPONENT 39, REPORTS_PROBLEM 30,
  CONCERNS_VEHICLE 24, derived PART_OF_VEHICLE of parts the gold does not list 18); 4 are content (3
  OCCURS_IN_COMPONENT, the vague "recall" mention). ER: pair 4 ("brake suddenly" / "braked on its own")
  not merged. Reading: on unseen, real, mixed-format data the frozen pipeline designed a working plan and
  schema, stays precise (0.977) and answers every question; recall falls from about 0.86 to 0.71, mostly
  through the proposed schema's shape (16 of 20 misses), not through missed text (4 of 20). Arm and domain
  are confounded: furniture ran the pinned schema, this ran the automatic arm. One sample; n is small.

### R54. Controlled arm on the held-out data: new domain or the proposed schema?
Asked for by the user on 2026-09-24 after R53. Held-out recall (0.706) is below furniture (about 0.86), but
two things changed at once: the domain and the arm (furniture pinned a reviewed schema, R53 used the
proposed one). This step changes one: the text schema. Same data, same frozen plan (R52's proposal),
same prompts, models and gold claims.
- `tests/gold/heldout_nhtsa_text_schema_controlled.json`: the gold's own four relations as a schema
  (Vehicle, Component, Problem, Recall; `HAS_PROBLEM` from a part or the vehicle, `CAUSES` from a problem or
  a part, `COVERED_BY_RECALL`, `PART_OF` derived). The descriptions restate the gold's definitions (its
  `_comment`, fixed in R51 before any run); **disclosed: written after R53's output had been seen**, so the
  schema is not blind, but it adds nothing the gold did not already define.
- `tests/gold/heldout_nhtsa_gold_controlled.json`: the same claims with their R51 labels (`labelled_as`),
  generated by code; question 3's Cypher follows the pinned schema. Tests keep the two gold files in step
  and check the schema.
- **Accept:** gate green; one `heldout` run with both files pinned (reset, build, ingest-text, extract,
  resolve, link, eval; user's yes given; estimate about $0.08), a judge pass, both arms side by side.
- **Part 1 (done, 2026-09-24).** As listed; 11 held-out tests (2 new). No run yet.
- **Part 2 (done, 2026-09-24).** User's yes given in the session. Same frozen plan, pinned controlled schema,
  output `out/heldout_controlled/`: extract `535c1f40` 25 calls $0.0393 (73 facts, 0 rejected), resolve
  `3e4d3242` 67 adjudications $0.0141 (99 → 85 entities), link `0b7617a2`; **$0.053**. Judge Claude Opus
  5.5, `tests/gold/judge_verdicts_2026-09-24_r54_heldout_controlled.json`, eval `e62bf4ba`:

  | Score | Automatic arm (R53) | Controlled arm (R54) | Furniture, pinned (R49) |
  |---|---|---|---|
  | validated precision | 0.977 (170 / 174) [0.94, 0.99] | 0.942 (98 / 104) [0.88, 0.97] | 0.992-1.000 |
  | validated recall (n = 68) | 0.706 [0.59, 0.80] | **0.838** (57) [0.73, 0.91] | 0.854-0.865 (n = 96) |
  | validated F1 | 0.820 | **0.887** | – |
  | exact precision / recall | 0.080 / 0.162 | 0.311 / 0.412 | 0.370-0.385 / 0.365-0.396 |
  | ER valid | 0.952 (n = 21) | 1.000 (n = 21) | 1.000 |
  | questions | 5 / 5 | 5 / 5 | 5 / 5 |
  | gold corrections | 115 | 32 | – |

  **Answer: the recall drop was the proposed schema's, not the domain's.** With the gold's own relations
  pinned, held-out recall (0.838) is close to the furniture range (0.85-0.87; the intervals overlap), up 9 triples from the automatic
  arm; `CAUSES` alone now finds 3 of 5. The 11 misses: 6 Civic ACC / LKAS problems put on the vehicle
  although the schema says a part named in a when-clause is the subject, 2 causes named by the generic word
  "PART", "SYSTEMS" for the navigation system, the camera's braking stated as CAUSES, the shutoff put on the
  tank. Precision is lower (0.942): 6 unsupported, of which 3 are the ice-crash sequence stated as vehicle
  problems (the schema's Problem includes "an event it leads to"), 1 a hypothetical accident as a cause, 2
  correct gauge readings as faults; vague rate 0.092 (generic names, owner assessments). Judgement calls,
  applied to both arms: generic subject names and a different relation or subject never find a gold triple.
  Limits: one sample per arm; the controlled schema was written after R53's output was seen (disclosed in
  Part 1); gold and judge are one model family.

### R55. The schema proposer sees the whole text (automatic arm, furniture then held-out)
Asked for by the user on 2026-09-24 after R54: the proposer sees 12 evenly sampled chunks (17 % of the
furniture text, 48 % of the held-out text), which may be why the automatic arm's schema misses relations.
Both corpora are small (about 8,600 and 2,900 tokens), so the whole text fits. Behaviour change in
`text/schema.py`; the prompt wording is unchanged, so only the context differs.
- **Part 1 (baseline, no code change):** one `quality` automatic-arm run on furniture with today's
  sampling (profile, plan, build, ingest-text, text-schema; gold predicates mapped to the proposed schema
  by a fixed rule before extraction; then extract, resolve, link, eval) and a judge pass. The furniture
  data never had a judged automatic run (R24's `84e5eccf` was set aside unjudged).
- **Part 2 (code):** a `schema_context_chars` setting (default 200,000, about 50,000 tokens): all chunks,
  whole, when the corpus fits, else an even sample of as many as fit; logged as a param with
  `context_chunks` / `chunks_total` metrics. Tests first.
- **Part 3:** the same furniture run with the whole text, reusing Part 1's plan, mapping, judge pass.
- **Part 4:** one held-out run with the whole text, reusing R52's frozen plan, next to R53 (recall 0.706).
- User's yes given in the session for the three runs (estimate about $0.70 in all). One sample per
  variant: proposals drift between runs (R24), so the result is a direction, not a proof.
- **Part 1 (done, 2026-09-24).** `quality`, goal "supply chain root cause analysis", output
  `out/r55_sampled/`: plan `05389321` $0.1147 (Product, Assembly, Component, Supplier; `PART_OF`,
  `SUPPLIED_BY`), text schema `699aa31c` $0.0552 (accepted in round 1: Product, Component, Defect,
  AssemblyIssue; `HAS_DEFECT` from a component or product, `HAS_ASSEMBLY_ISSUE`, `CAUSES`
  Defect→AssemblyIssue, `PART_OF` derived), extract `3873cbf9` $0.0804 (98 facts), resolve `832109b8`
  $0.0172 (123 → 79 entities); **$0.268**. Frozen under `tests/gold/r55/`. Gold mapped before extraction
  (`furniture_gold_sampled.json`): HAS_DEFECT 42 and PART_OF 22 unchanged, EXHIBITS_FAILURE 21 →
  HAS_DEFECT (the schema's Defect includes "functional malfunction"), IMPEDES_ASSEMBLY_OF 8 → product
  HAS_ASSEMBLY_ISSUE defect, CAUSES_FAILURE 3 not expressible; questions follow the plan's `PART_OF` /
  `Component`. Judge Claude Opus 5.5 (48 of 91 facts carried over from earlier passes by fact id, 43 new),
  eval `8988a1fc`: validated P **1.000** (n = 136), R **0.823** (79 / 96) [0.73, 0.89], F1 0.903; exact
  0.359 / 0.396; ER valid 0.983; questions 5 / 5; vague 0.107 (assembly-difficulty statements); 2
  ambiguous ("They constantly stick": rails or drawers). Strict rule as in R54: 7 near-misses would count
  under a lenient reading (4 "impedes assembly" claims stated as CAUSES, 3 "parts not fitting" stated
  only as assembly issues). Against the pinned schema with the same prompts (R47 / R49b recall 0.865 /
  0.854), the furniture automatic arm loses about 3 gold triples: the proposer works on this data.
- **Part 2 (done, 2026-09-24).** `select_context(chunks, budget_chars)` replaces `sample_chunks`: every
  chunk whole while the text fits the budget, else an even sample of whole chunks that fits (the old cut at
  1,200 characters per chunk is gone; it never cut anything here, no chunk exceeds 813). The stage chooses
  the context and logs `schema_context_chars` (param) and `context_chunks`, `chunks_total`,
  `context_chars` (metrics); `propose_text_schema` shows exactly the chunks it is given. Prompt wording
  unchanged. Tests: all chunks under the budget, an even sample that fits above it, every chunk whole in the
  prompt, the stage's tracking contract. 193 passed (Neo4j up), `ruff` clean. No run.
- **Part 3 (done, 2026-09-24).** Part 1's plan reused, whole text: text schema `0dcdf55e` $0.0607 (all
  70 chunks; accepted in round 1: Product, Component, Defect, Symptom; `HAS_DEFECT`, `EXHIBITS_SYMPTOM`
  from a component or product, `CAUSES` Defect→Symptom and Defect→Defect, `PART_OF` derived), extract
  `94a3cef3` $0.0767 (88 facts), resolve `a6eefade` $0.0165; **$0.154**. Gold mapped before extraction
  (`furniture_gold_full.json`): EXHIBITS_FAILURE → EXHIBITS_SYMPTOM, CAUSES_FAILURE → CAUSES (now
  expressible), IMPEDES_ASSEMBLY_OF → product EXHIBITS_SYMPTOM defect, the rest unchanged; question 4
  accepts the misalignment as a defect or a symptom (the schema allows both; decided before extraction).
  Judge Claude Opus 5.5 (47 of 88 facts carried over by id), eval `e0519d5e`.
  **Judging rule made explicit (applies to every arm):** a swap between two problem relations with the
  same subject and object (defect / failure / symptom / assembly issue) is the same claim, as R45-R49
  already judged ("cushions losing their shape"); a change of claim structure (CAUSES instead of a problem
  relation, another subject) is not. Part 1 re-scored under it with `kg rescore` ($0, run `bf462659`,
  verdict file updated): gold 36, 63, 70 found, recall 0.823 → 0.854. R53 / R54 have no such swaps.

  | Furniture, automatic arm | Precision (judge) | Recall (judge, n = 96) | F1 | ER valid | Questions |
  |---|---|---|---|---|---|
  | 12 sampled chunks (Part 1) | 1.000 (n = 136) | 0.854 [0.77, 0.91] | 0.921 | 0.983 | 5 / 5 |
  | whole text (Part 3) | 0.969 (n = 129) | 0.854 [0.77, 0.91] | 0.908 | 0.948 | 5 / 5 |
  | pinned schema, same prompts (R47 / R49b) | 0.992 / 1.000 | 0.865 / 0.854 | – | 1.000 | 5 / 5 |

  On furniture the whole text changes the schema (a Symptom type, CAUSES expressible: gold 17 and 18
  found) but not the recall; precision drops by 4 facts that type the instructions as a Component. The
  automatic arm on furniture is as good as the pinned schema either way.
- **Part 4 (done, 2026-09-24).** Held-out, R52's frozen plan, whole text: text schema `634d8d36` $0.0403
  (all 25 chunks, accepted in round 1: Complaint, Recall, Vehicle, Component, Problem; `REPORTS_PROBLEM`,
  `IDENTIFIES_COMPONENT`, `REFERENCES_RECALL`, `CONCERNS_VEHICLE` from the complaint, `AFFECTS_COMPONENT`
  Problem→Component, `CAUSES_PROBLEM`, `COVERS_COMPONENT` / `ADDRESSES_PROBLEM` / `AFFECTS_VEHICLE` from a
  recall, `SUBCOMPONENT_OF`, `PART_OF_VEHICLE` derived), extract `8776a278` $0.0670 (140 facts), resolve
  `e39b86cf` $0.0054; **$0.113**. Gold's R51 labels mapped before extraction as in R52
  (`r55/heldout_gold_full.json`; 3 problem CAUSES now expressible, 2 component CAUSES not). Judge Claude
  Opus 5.5 (88 of 168 facts carried over from R53 by id), eval `9c7bc139`: precision **0.994** (180 / 181)
  [0.97, 1.00], recall **0.706** (48 / 68) [0.59, 0.80], F1 0.826, ER valid 0.905, questions 5 / 5.
  Judged on name + aliases: the merged "SYSTEM" entity carries NAV / NAVIGATION SYSTEM, so its facts count
  for the navigation system (R54's "SYSTEMS" and "PART" were unmerged and stay misses).

  | Automatic arm, recall (judge) | Sampled context | Whole text | Pinned schema |
  |---|---|---|---|
  | furniture (n = 96) | 0.854 (R55 part 1) | 0.854 (part 3) | 0.854-0.865 (R47 / R49b) |
  | held-out NHTSA (n = 68) | 0.706 (R53) | 0.706 (part 4) | 0.838 (R54) |

  **Answer: the whole text does not fix the held-out gap.** It changes the schemas (furniture gains a
  Symptom type and a Defect→Defect cause; the held-out schema gains `CAUSES_PROBLEM` and the recall
  relations, and its critic no longer strikes them), costs about $0.005 more per proposal, and moves
  precision by a few facts (furniture −0.031, held-out +0.017), but recall is identical in both datasets.
  The held-out schema is still centred on the complaint: the Civic ACC / LKAS problems again went to the
  complaint (8 misses), and none of the 3 now-expressible causes was extracted. The context size was not
  the cause; the plan's `Complaint` node, which the proposer copies, remains the lead candidate (finding
  below). One sample per variant. R55 total **$0.535**.

### R56. Goal-first schema proposal: questions, then paths, then types
Asked for by the user on 2026-09-24 after R55 ("give priority to the goal"). The goal was one weak rule in
the schema prompt, while "reuse the domain concepts" came first; on the held-out data the proposer made
the plan's `Complaint` record the hub of every fact (R53, R55). Behaviour change in `text/schema.py`.
Disclosed: the idea comes from a held-out failure; the prompt names no domain, a test keeps it from
quoting either corpus, and it is measured on furniture before one held-out run. No second development
dataset with a record-like table was built (the user asked to implement and run); furniture can show that
nothing breaks, not that the rule helps where a record node exists.
- **Part 1 (done, 2026-09-24).** `TextSchema.goal_questions` (first field, so the model writes them
  first): each question with its `path` of fact-type signatures. `validate_text_schema` checks every path
  (each step a fact type of the schema, consecutive steps share a type, at most 4 steps); a proposal
  without questions goes back (`_proposal_issues`); pinned schemas without questions stay valid. Prompt:
  goal first ("work in this order": questions, paths, types), plan labels for naming and linking, one rule
  that a path does not route a connection through the source a text comes from (a document, a report, a
  message: provenance is stored outside the schema). Critic: one more check for that. Stage metric
  `goal_questions`. Tests: path checks, the retry for a proposal without questions, goal-first order and
  domain-neutral wording, no 4-gram of either corpus in the prompt. 197 passed (Neo4j up), `ruff` clean.
- **Part 2 (done, 2026-09-24): a clear regression on the development data.** R55's plan, output
  `out/r56_furniture/`: text schema `d2663328` **$0.1691** (2 rounds: the critic struck the copied domain
  relations; 5 goal questions, all paths valid: FailureMode `CAUSED_BY` Defect, Defect `AFFECTS`
  Component / Product, `PART_OF` derived), extract `fc735ba7` $0.0731 (**55 facts**; R55: 88-98), resolve
  `2ae42fe4` $0.0070; **$0.249**. Gold mapped before extraction (`r56/furniture_gold.json`): HAS_DEFECT and
  EXHIBITS_FAILURE → `AFFECTS` (inverted; a failure counts only if stated as a defect, since the schema
  attaches failures to nothing), CAUSES_FAILURE → `CAUSED_BY` (inverted), IMPEDES_ASSEMBLY_OF not
  expressible (8). Judge Claude Opus 5.5, eval `89930703`: precision **1.000** (n = 99), recall **0.552**
  (53 / 96) [0.45, 0.65], F1 0.711, ER valid 0.972, questions **2 / 5** (wobbling, holes, squeaking fail).
  Of 43 misses, 19 are failures (wobbles, sticks, squeaks, noise, doesn't close) the schema gives no place
  on a part or product, 8 are not expressible, the rest not extracted. **Reading:** the questions the model
  wrote all start from a failure and ask for its cause ("which defect caused an observed failure?"), so the
  paths never needed a failure-to-part fact type; code confirmed the paths exist, not that they cover what
  the text says. Goal-first narrowed the schema to the goal's causal chain and lost the direct
  observations the chain starts from.
- **Part 3: not run (the user's decision, 2026-09-24).** A design that failed on the development data does
  not get the held-out set's one look. **Reverted:** `text/schema.py`, `pipeline/stages.py`,
  `tests/test_text.py`, `tests/test_pipeline.py` restored to `8451910` (R55's state) in their own commit;
  the default proposer is R55's again (furniture 0.854). Kept as a documented negative result: this entry,
  `tests/gold/r56/` (the proposal, the mapped gold, the verdicts) and eval `89930703` in MLflow.
  **Finding for the thesis:** a goal stated as a causal question ("root cause analysis") made the proposer
  model the causal chain only; code-checked question paths prove that the schema can answer the questions
  the model chose, not that it covers what the text observes. Any later goal-driven design needs a check in
  the other direction too (every observed problem type reaches the thing it happens to). R56 total $0.249.

### Plan R57-R61: as many true, consistent, linked facts as possible
Decided by the user on 2026-09-24 after R56. The system's main job is a faithful graph: as many true facts
as possible, stored consistently and linked to each other. The goal decides which subjects matter (scope),
not how the graph is shaped. A question-answering layer can walk any well-built graph, and it can fall back
to a fact's chunk for a question about one thing ("lookup"). It cannot count, list or chain facts that were
never extracted ("aggregate"), so recall on text stays the main gap. The steps in order: R57 measure,
R58 schema proposer, R59 near misses in `verify`, R60 generic names and the duplicate vehicle, R61 a second
extraction pass (`extract_passes`; the user asked for it to be planned now). **Methodology limit (the
user's decision):** R58-R61 are designed from the furniture and NHTSA misses, so NHTSA is a development set
from here on. No new held-out set is built, and the thesis states that the NHTSA numbers after R55 are not
from a blind test.

### R57. Measure first: consistency and coverage metrics
No behaviour change. Split in two parts (one concern each).
- **Part 1 (done, 2026-09-24).**
  - What was added:
    - The extract stage logs `rejected_off_schema_rate`: triples the schema had no fact type for, as a
      share of all returned triples.
    - It also writes `off_schema.json`, which lists each missing fact type as `Subject -[P]-> Object`
      with its count (`ExtractionResult.off_schema_rate` / `off_schema_signatures`).
    - The consistency check logs `predicates_distinct`, the number of relation names among the facts.
  - Dropped from the plan, with reasons:
    - `entities_without_facts`: entities are only created by facts, so the number is always 0.
    - The "facts on source-record types" share (see "Found along the way": the rule behind it does not
      fit the data).
  - Tests: the rate and the report on a hand-made result; both metrics in the end-to-end run and on a
    hand-made graph. 194 passed (193 before), `ruff` clean.
  - Baseline from the saved outputs, recomputed with the new code. Counts are before resolution; no run,
    $0.

    | Output | Facts | Rejected | Off-schema rate | Relation names |
    |---|---|---|---|---|
    | R55 furniture, whole text (`out/r55_full`) | 88 | 0 | 0.000 | 3 |
    | R55 held-out, whole text (`out/r55_heldout_full`) | 140 | 0 | 0.000 | 6 |
    | R53 held-out, automatic arm (`out/heldout`) | 134 | 1 | 0.000 | 5 |
    | R54 held-out, controlled arm (`out/heldout_controlled`) | 73 | 0 | 0.000 | 3 |
    | R56 furniture (`out/r56_furniture`) | 55 | 0 | 0.000 | 2 |

    **Reading:** code never rejected a fact for its type in any of these runs. The extractor follows
    "Skip anything that does not fit" and never returns what the schema cannot hold, so missed facts are
    lost silently inside the model, before `verify` sees them. The recall gap is therefore the schema's
    and the single pass's, not the code's. R59 (repair near misses in `verify`) has nothing to repair
    (its own condition: under ~2% of facts), so it is skipped unless a later run shows off-schema
    rejections.
- **Part 2 (open): question kinds.** `GoldQuestion.kind` (`lookup` | `aggregate`), with
  `question_accuracy_lookup` and `question_accuracy_aggregate`. Every committed text question lists
  things across documents, so it is aggregate already. New questions need Cypher per schema variant, so
  this part is decided with the user before it starts.

### R58. Schema proposer: cover what the text observes, attach facts to the thing they are about (done, 2026-09-24)
The planned code check ("a fact type touching the type documents are ABOUT must be derived") does not fit
the data (see "Found along the way"), so the source/subject distinction is made in the prompt and the
critic. Behaviour change in `text/schema.py`. Measured on furniture and on the held-out data. The user
said yes in the session to the two runs (about $0.35).
- **Part 1 (done, 2026-09-24).** Three prompt changes, each answering a measured failure:
  - **Domain wording.** "Reuse its concepts as entity types" became "name the entity type like that node,
    so that code can link the two". The old wording invited copying the plan's `Complaint` record (R53,
    R55).
  - **Source rule.** No entity type for the source a statement comes from (a document, a report, a
    message, a post), and no fact routed through one: provenance records it.
  - **Coverage.** A fact type for every kind of claim the text makes about the things the goal cares
    about. This replaces "name a question of the goal each fact type answers", the narrowing R56 measured.
  - Critic: a source check and a "claims with no place" check, instead of "answers none of the goal's
    questions".
  - Code: `validate_text_schema` sends back an entity type that no fact type uses. All 14 committed and
    output schemas pass.
  - Tests: the unused-type check; the rules name no domain word and share no 4-gram with either corpus.
    198 passed (196 before), `ruff` clean.
  - No run, $0.
- **Part 2 (done, 2026-09-24): furniture.**
  - Setup: `quality`, R55's frozen plan, output `out/r58_furniture/`.
  - Text schema `e7bc53c3` **$0.1469**, 2 rounds (the critic struck a Supplier type the text gives no
    named instance of). The result:
    - Entity types: Product, Component, Defect. Defect now includes operational failures, so R55's
      separate Symptom type is gone.
    - Fact types: `HAS_DEFECT` from a component or a product, `Defect CAUSES Defect`,
      `Component PART_OF Component`, and the derived `PART_OF`.
  - Extract `ee76fea7` $0.0836 (90 facts, 0 rejected), resolve `d219ab0b` $0.0097 (114 → 81 entities).
    Total **$0.240**.
  - Gold mapped before extraction (`r58/furniture_gold.json`): HAS_DEFECT and EXHIBITS_FAILURE →
    `HAS_DEFECT`, IMPEDES_ASSEMBLY_OF → product `HAS_DEFECT`, CAUSES_FAILURE → `CAUSES`. Nothing is
    inexpressible.
  - Judge: Claude Opus 5.5, eval `adfe5cad`. 73 of 98 verdicts were carried over by fact id (all
    SUPPORTED, and their gold links point into the same documents); 25 are new.
- **Part 3 (done, 2026-09-24): held-out.**
  - Setup: R52's frozen plan, output `out/r58_heldout/`.
  - Text schema `83894b4b` **$0.0895**, accepted in round 1. **No Complaint type.** The result:
    - Entity types: Vehicle, Component, Problem, Recall.
    - Fact types: `Problem AFFECTS_COMPONENT Component`, `AFFECTS_VEHICLE`, `CAUSES_PROBLEM`,
      `Component PART_OF Component`, `Recall COVERS_COMPONENT` / `ADDRESSES_PROBLEM` /
      `AFFECTS_VEHICLE`, and the derived `INSTALLED_IN`.
  - Extract `60c5114b` $0.0483 (88 facts), resolve `1182f162` $0.0054. Total **$0.143**.
  - Gold mapped before extraction (`r58/heldout_gold.json`): 2 component causes are inexpressible.
  - Judge: eval `dfad8a45`, 9 of 77 verdicts carried over.
- R58 total **$0.383** (estimate $0.35).

  | Automatic arm, judge (Claude Opus 5.5) | Furniture R55 | Furniture **R58** | Held-out R55 | Held-out **R58** | Held-out controlled (R54) |
  |---|---|---|---|---|---|
  | precision | 0.969 | **1.000** (155 / 155) [0.98, 1.00] | 0.994 | **0.950** (115 / 121) [0.90, 0.98] | 0.942 |
  | recall | 0.854 | **0.885** (85 / 96) [0.81, 0.93] | 0.706 | **0.750** (51 / 68) [0.64, 0.84] | 0.838 |
  | ER accuracy (valid) | 0.948 | 0.984 | 0.905 | 1.000 | 1.000 |
  | questions | 5 / 5 | 4 / 5 | 5 / 5 | 5 / 5 | |
  | relation names extracted | 3 | 3 | 6 | 5 | |

  - **Judging rules, kept from R55:**
    - An assembly gold triple (IMPEDES_ASSEMBLY_OF) counts only if a fact states the effect on
      assembly. Under R58's mapping, 7 of the 8 are textually identical to a defect triple, and one fact
      is not counted twice.
    - A problem stated on the vehicle while the gold places it on the part is a recall miss, but a
      SUPPORTED fact.
    - An entity is judged on its name and aliases, so the unmerged "SYSTEM" is not the navigation
      system.
  - **Reading:**
    - The source rule did what it was meant to do: the held-out schema no longer has a Complaint hub, and
      problems sit on parts.
    - Held-out recall rose by 3 triples. The misses that are left are mostly the 6 ACC / LKAS problems
      (put on the vehicle, not on the systems), 2 inexpressible component causes, and 3 unmerged or
      generic names.
    - Held-out precision fell (6 unsupported, all readings of the text, not invented facts). The new
      `AFFECTS_VEHICLE` doubles many part-level problems on the vehicle: 58 gold corrections, most of them
      those copies.
    - Furniture gained 3 triples (the dimmer flicker, the light base's cause, the rails not aligning).
    - The failed furniture question is a flaw in the question's query, not in the graph (see "Found
      along the way").
    - One sample per dataset, so this is a direction, not a proof. NHTSA is a development set from R57
      on.
- **Accept:** held-out recall above 0.706; furniture recall not below 0.854; precision at least 0.94;
  `predicates_distinct` not above R55's (3 furniture, 6 held-out).

### R59. Extraction keeps deterministic near misses (skipped by R57's measurement)
Planned: swap a reversed direction and normalise predicate spelling in `verify`. R57 found 0 off-schema
rejections in five runs, so there is nothing to repair.

### R60. Linking and naming: no generic entities, one node per thing
- **Part 1 (done, 2026-09-24): one node per vehicle.** Behaviour change in `resolution/derivation.py`.
  - What changed: derivation looks for an existing entity before it creates the object entity of a
    derived fact.
    - It first tries the node's name or an alias (as before, R29).
    - Then it tries the entity whose name contains every word of the node's name (`containing_entity`:
      whole words, the most-mentioned first).
    - Only entities of the object type that the documents `ABOUT` that node mention are candidates.
  - Why: the plan names a vehicle by its `model` ("OUTBACK"), while the text says "2019 Subaru Outback".
    So every NHTSA vehicle existed twice, and the gold's derived triples ("... PART_OF 2016 Honda
    Civic") could not match exactly. Furniture is unchanged, because its product names match exactly.
  - New metric `targets_by_containment`.
  - Tests: the selection rule as a pure function (whole words, ties, no match), and with Neo4j the
    vehicle case: reused, not created, an entity from another vehicle's document ignored, and
    idempotent. 196 passed (194 before), `ruff` clean.
  - No run, $0.
- **Dropped: rejecting generic names.** The saved outputs hold only a few such names (`PART`,
  `SYSTEMS`, `ISSUES`, `FAILURE`: about 5 facts in R54, 2 in R53). A rule of the form "the name is a word
  of its type's description" would also reject true facts ("damaged" against the description's "damage",
  a "noise" symptom). And removing a generic fact does not raise recall. The fix, if any, is a naming
  instruction in the extraction prompt, which needs a run to measure.
- **Part 2 (done, 2026-09-24): measured on the held-out data, $0.** The user said yes in the session.
  - Setup: R55 part 4 rebuilt from the LLM cache with R52's frozen plan and R55's frozen whole-text
    schema (both byte-identical to `out/r55_heldout_full`). Output `out/r60_heldout/`.
  - Runs: extract `76030c05` (140 facts, as in R55), resolve `8860b465` (112 → 100 entities), link
    `fb62d5de` (43 derived facts, `entities_created` 0, `targets_by_containment` 5), eval `0d5df53c`.
  - Cost: 111 LLM calls, all cache hits, and 3 embedding calls; `cost_usd` 0.0 in every stage.
  - Result: the graph has 5 vehicle entities, one per vehicle, each with its facts. Resolution also
    folded "2019 OUTBACK" into "2019 Subaru Outback".

  | Held-out, R55 gold (`r55/heldout_gold_full.json`), exact match | R55 (eval `9c7bc139`) | R60 (eval `0d5df53c`) |
  |---|---|---|
  | triple recall (n = 68) | 0.176 (12) | **0.456** (31) |
  | triple precision | 0.077 | **0.198** |
  | ER accuracy, exact (n = 16) | 0.875 | 0.875 |
  | questions | 5 / 5 | 5 / 5 |

  **Reading:** the derived `PART_OF` triples now match the gold by name, because the gold names the
  vehicle as the text does. The validated (judge) scores were not recomputed. The R55 judge already
  matched these facts by meaning ("OUTBACK" is the 2019 Subaru Outback), so validated recall (0.706) is
  expected to stay the same. A judge pass on the 57 facts without a verdict (their fact ids changed with
  the object's name) would confirm it. The gain is in exact recall and in the graph's shape: one node per
  thing, so a query from a vehicle entity now reaches all of its parts.

### R61. Second extraction pass ("gleaning") (done, 2026-09-24; not made the default)
The user said yes in the session (2026-09-24) to the two runs. Behaviour change behind a setting; the
default is unchanged.
- **Part 1 (done, 2026-09-24).**
  - New setting `extract_passes` (default 1, from 1 to 3).
  - Pass 2 sends the first-pass prompt plus the facts accepted so far (`GLEAN_SUFFIX`, logged with its
    own `glean_prompt_version`) and asks only for the claims they miss. The same `verify` and the same
    repeat key apply across passes, so a later pass can only add verified facts.
  - Metrics `facts_pass1`, `facts_pass2`; param `passes`; artifact `prompts/extract_glean.txt`.
  - Tests: pass 2 sees the found facts, a repeat in other casing is dropped, an ungrounded fact is still
    rejected, and one pass makes one call. The rule is domain-neutral. 200 passed (198 before), `ruff`
    clean. No run, $0.
- **Part 2 (done, 2026-09-24): measured. Recall up on both datasets; held-out precision below the bar.**
  - Setup: `EXTRACT_PASSES=2` with R58's frozen plans and text schemas, and the same R58 gold. Outputs
    `out/r61_furniture/` and `out/r61_heldout/`.
  - Pass 1 came from the cache (the same 90 and 88 facts as R58), so the difference is pass 2 alone.
  - Furniture: extract `ac640b42` $0.0791 (pass 2 added 11 facts), resolve `420ee9fc` $0.0016, eval
    `550595e3`.
  - Held-out: extract `cba73ffb` $0.0296 (pass 2 added 27), resolve `29525eb6` $0.0020, eval `ed0e1ff5`.
  - R61 total **$0.112** (estimate $0.45; pass 1 was free).
  - Judge: Claude Opus 5.5. Earlier verdicts were reused by fact id (101 of 110 and 76 of 102); 9 and 26
    are new.

  | Judge (Claude Opus 5.5) | Furniture R58 | Furniture **R61** | Held-out R58 | Held-out **R61** |
  |---|---|---|---|---|
  | precision | 1.000 (155) | **0.976** (163 / 167) [0.94, 0.99] | 0.950 (121) | **0.919** (137 / 149) [0.86, 0.95] |
  | recall | 0.885 (85 / 96) | **0.906** (87 / 96) [0.83, 0.95] | 0.750 (51 / 68) | **0.868** (59 / 68) [0.77, 0.93] |
  | ER accuracy exact / valid | 1.000 / 0.984 | 1.000 / 0.984 | 1.000 / 1.000 | 0.947 / 0.952 |
  | questions | 4 / 5 | 4 / 5 | 5 / 5 | 5 / 5 |

  - **What pass 2 found:**
    - The veneer thinness in both furniture reviews (lost since R47's naming rule).
    - On the held-out data: the double image in the mirrors, "stays on at night" with its cause, the
      battery that "wouldn't take a jump", the ACC as a part, and the link from "this part failure" to the
      piston clip ring.
    - Resolution then merged SYSTEM with the navigation names.
  - **Judging decisions made in this pass, stated here so they can be checked:**
    - G26 and G27 (the piston clip ring causes engine failure or stalling) were labelled not expressible.
      They count as found because two facts together state them ("THIS PART FAILURE affects PISTON CLIP
      RING" and "THIS PART FAILURE causes …"). The rules allow a split. Without it, held-out recall is
      0.838.
    - G46 counts because the SYSTEM entity now carries the navigation names. R58 judged the same fact a
      miss when it did not.
  - **What pass 2 got wrong:**
    - Furniture: 4 "Assembly instructions" facts typed as a physical Component (as in R55).
    - Held-out: the pronoun "IT" as an entity (2); "SHOWING FULL" (2, R54's rule); the wiper blade
      PART_OF the windshield; the snap ring PART_OF the engine (general knowledge, not the text); and 3
      AMBIGUOUS dealer explanations.
    - Exact entity resolution fell to 0.947, because "ACC" became its own entity next to
      "adaptive cruise control".
  - **Decision against the acceptance rule:** furniture passes (recall up, precision 0.976). The held-out
    data does not: recall rose by 8 triples, but precision is 0.919, below 0.94. `quality` keeps
    `extract_passes: 1`, and a second pass stays available with `EXTRACT_PASSES=2`. Whether the recall
    is worth the precision is the user's call (one sample per dataset).
- **Accept:** recall up on both datasets, precision at least 0.94. If that holds, `quality` gets
  `extract_passes: 2`.

### R62. Precision of the extraction: pronoun names and second-pass over-reach (done, 2026-09-24; bar not met)
Asked for by the user on 2026-09-24 after R61 ("what do you recommend to fix precision; implement it").
R61's 16 unsupported facts fall into three groups:
- pronoun names;
- second-pass over-reach: general knowledge, a location read as "part of", a non-physical thing typed as
  a physical one;
- first-pass misreadings: a comparison, a normal gauge reading, a possible outcome.

The first two groups are addressed here. The third would need a fact-check pass and is not started.
Behaviour change in `text/extraction.py`.
- **Part 1 (done, 2026-09-24).**
  - `verify` rejects a subject or object made only of pronouns or determiners (`PRONOUNS`, a closed word
    class; new reason `pronoun_argument`).
  - `GLEAN_SUFFIX` gets three over-reach rules: nothing generally known or only suggested; no place or
    comparison read as a relation; an entity only of a type whose description covers it.
  - Tests: the pronoun check, including a real name next to a pronoun. The pass-2 rules name no domain
    word and share no 4-gram with either corpus.
  - **Measured at $0 for the pronoun check:** it was applied to the judged facts of R58 and R61 and
    re-scored with their verdicts, with no model call. It removes exactly the 2 "IT" facts of R61
    held-out, both UNSUPPORTED, and nothing else. Held-out R61 precision goes from 0.919 (137 / 149) to
    **0.932** (137 / 147); the other runs have no pronoun names.
- **Part 2 (done, 2026-09-24): measured. Furniture better on both scores; held-out mixed and still below
  the bar.**
  - Setup: the user said yes in the session. Same setup as R61 (frozen R58 schemas, `EXTRACT_PASSES=2`,
    pass 1 cached, R58 gold). Outputs `out/r62_furniture/` and `out/r62_heldout/`.
  - Furniture: extract `38709bd5` $0.0775 (pass 2 added 13 facts), resolve `362c0c86` $0.0036, eval
    `1d6f6552`.
  - Held-out: extract `8bd3d063` $0.0300 (pass 2 added 24 facts), resolve `1b472320` $0.0012, eval
    `ea6d4f60`.
  - R62 total **$0.112**.
  - Judge: Claude Opus 5.5. Verdicts reused by fact id; 7 and 17 new.

  | Judge (Claude Opus 5.5) | Furniture R61 | Furniture **R62** | Held-out R61 | Held-out **R62** |
  |---|---|---|---|---|
  | precision | 0.976 (163 / 167) | **1.000** (170 / 170) [0.98, 1.00] | 0.919 (0.932 with the pronoun check) | **0.925** (135 / 146) [0.87, 0.96] |
  | recall | 0.906 (87 / 96) | **0.927** (89 / 96) [0.86, 0.96] | 0.868 (59 / 68) | **0.824** (56 / 68) [0.72, 0.90] |

  - **Furniture:** the rules removed the instructions-as-part facts and the uncertain cause, and pass 2
    found two more gold claims: "scratches a bit more easily" (G4) and "dimmer stiff to turn" (G58).
  - **Held-out:** 11 unsupported facts.
    - 6 come from pass 1 and were already there in R58: rotors / pads as the squeal's place, "driving on
      ice", "could cause an accident", and the gauge reading.
    - 5 come from pass 2. The rules did not stop general knowledge (the clip ring PART_OF the engine), the
      place of a repair read as the place of a problem (transmission ×2), or a possible outcome read as an
      event.
    - Without the pass-2 errors, precision would be 0.957; the pass-1 errors alone cost about 4 points.
  - **Variation:** pass 2 did not return R61's "stays on at night" and "wouldn't take a jump" facts this
    time, and the split-fact link for G26 / G27 is missing too. Recall 0.824 against R61's 0.868 (0.838
    strict) is within one sample's variation (the intervals overlap widely).
  - **Decision:**
    - The pass-2 rules stay: they are better on furniture and not worse in precision on the held-out
      data.
    - The acceptance bar (held-out precision at least 0.94) is still not met.
    - The remaining errors are misreadings that no wording rule stopped, in both passes. The next lever
      is a fact-check call per chunk (the model re-reads its accepted facts against the chunk), proposed
      as its own step.
- **Accept:** held-out precision at least 0.94 with recall kept (R61: 0.868), and furniture not worse.

### Plan R63-R68: the observation graph (branch `observation-graph`)
Decided with the user on 2026-09-24 after an independent audit of the R62 outputs. Each fact is almost
always true on its own, but the graph states false things when its links are followed: entities are kinds
shared across documents (R41), so a derived `PART_OF` attaches one `drawer rails` node to every product
that mentions it, and a query product → part → claim reaches other products' claims (the Linköping Bed
got the Helsingborg Dresser's defective drawer rails). The graph also holds defects only, and on NHTSA the
text never reaches the structured data (`entities_linked` 0). Direction: every claim becomes an
`Observation` node tied to the thing it is about, its subject and object kinds, polarity, value, time and
source. The work runs on the branch `observation-graph`; the step list and the checks that close each step
are in the local task file `docs/tasks/observation-graph.md` (git-ignored, like all of `docs/`):
R63 path-truth metric, R64 observations in the graph, R65 questions walk observations, R66 polarity,
values and time, R67 recall texts and NHTSA linking, R68 coverage estimate. `main` keeps R62's system
until the branch is merged after the user's review.
Split (2026-09-25, the user's choice): record dates (NHTSA `dateOfIncident`) moved from R66 to R67, because
they need each complaint section tied to its Complaint record, which is R67's text-to-record linking.

### R63. Path truth: measure the false paths before fixing them (done, 2026-09-24)
No behaviour change: a new metric in `kg eval` and `kg rescore`, no change to what is built.
- A pure function over the stored facts (`validation/paths.py`): a *thing* is the object of a derived fact,
  and the documents about it are the documents its derived facts come from. For every thing, every part a
  derived fact attaches to it and every extracted fact with that part at one end, the path is true when a
  fact with the same subject, relation and object (the same entities after resolution) comes from a
  document about that thing.
- Kind structure is left out: a fact between two part types ("drawer pulls PART_OF drawer") says how a
  kind is built, not what one document claims about one thing, and the observation model keeps it as a
  shared edge. Counted, it would have made 14 furniture paths and 1 held-out path "false" for good.
- Metrics `path_truth`, `paths_true`, `paths_total`; the false paths are listed in `eval_report.json` /
  `rescore_report.json`. The text schema (which fact types are derived) is read from `out/`, and its hash
  is logged as the param `text_schema_hash`.
- Tests: the leak case (false for the second product), the same claim in the second product's own
  document (true), a merged entity under another review's wording (the same claim), a product's own
  claim and kind structure (not paths), no derived types (no paths), the eval metrics, `rescore` with and
  without the schema, and the stage's MLflow params and metrics. 210 passed (201 before), `ruff` clean.
- **Baseline ($0, no graph, no LLM):** `kg rescore` on the judge sheets R62 logged, with R62's text
  schemas copied to `out/r63_heldout/` and `out/r63_furniture/`. The judge's scores reproduce R62 exactly
  (held-out 0.925 / 0.824, furniture 1.000 / 0.927), so the sheets were read back faithfully.

  | R62 graph | paths | true | path_truth | false |
  |---|---|---|---|---|
  | Held-out NHTSA (`out/r63_heldout/rescore_report.json`) | 56 | 42 | **0.750** | 14 |
  | Furniture (`out/r63_furniture/rescore_report.json`) | 100 | 64 | **0.640** | 36 |

  Examples of false paths:
  - `2019 TOYOTA RAV4` via `TRANSMISSION`: "TRANSMISSION ERRONEOUSLY SWITCHED TO REVERSE", stated only for
    the Nissan Rogue.
  - `Linköping Bed` via `drawer rails`: 8 Helsingborg defects ("defective", "rough", "misalign", ...). The
    rails reached the bed through the merge with its praised "drawer slides".
- **Against the audit's hand count** (held-out 36 of 47, 11 false): the audit used each fact's own
  wording and only `AFFECTS_COMPONENT`. The metric compares resolved entities. That adds 3 false paths
  created by merges:
  - "BRAKE SUDDENLY" (the Civic's, merged with the Rogue's "activated the brakes") reaches the Escape via
    BRAKE;
  - the RAV4's gear shifter claim reaches the Escape and the Rogue, via the merge TRANSMISSION =
    TRANSMISSION BOX.

  On furniture the audit found about 10 distinct false product claims by meaning (59 paths by wording).
  The metric's 36 count every claim entity separately: the bed's 8 rail defects are one false product
  claim.
- **Limit, kept on purpose:** "the same claim" means the same entities after resolution. A thing's
  documents may state a close but unmerged claim, so some false paths are true by meaning. Example:
  `Norrköping Nightstand` via `back panel` "flimsy", while its review says "a bit thin". Path truth is
  therefore a strict lower bound. The observation model makes the question moot: a claim can only
  reach the thing it was stated for.

### R64. Observations in the graph (done, 2026-09-25)
A change of how the graph is stored. Extraction, prompts, the schema and the gold questions are unchanged.
Every claim becomes its own node, tied to the thing its document is about, instead of an edge between two
kinds that every product with that kind shares.
- **Part 1 (done, 2026-09-25): code and tests.**
  - Shape:
    `(thing)-[:HAS_OBSERVATION {name}]->(:Observation {id, predicate, chunk_id, evidence, subject_name,
    object_name, extractor})`, with `-[:SUBJECT]->(:Entity)`, `-[:OBJECT]->(:Entity)` and `-[:FROM]->(:Chunk)`.
    The id (`core/identity.observation_id`) is built from the chunk, the predicate and the claim's own
    wording, which is the key the judge sheet has used for fact ids since R44. So the R62 verdicts still
    apply to R62's claims stored as observations. No R62 triple shares an id with another (103 furniture
    and 112 held-out triples, 0 collisions).
  - `text/subject_graph.py` writes observations (`write_observations`, shared with derivation). A triple
    repeated in its chunk adds no node.
  - `resolution/derivation.py`: a derived claim is an observation with `extractor = "derived"`. Its wording
    is the two entities' display names after resolution, which is what the sheet showed before, so its id
    is unchanged too.
  - `resolution/linking.py`: `attach_observations` recomputes `HAS_OBSERVATION` from the ABOUT link of the
    observation's document, after derivation, on every `kg link`. The ABOUT link now carries the thing's
    display name. The link stage logs `observations_attached`.
  - `resolution/resolver.py`:
    - A merge moves the SUBJECT / OBJECT edges.
    - Repeat rule: same subject, predicate, object, chunk and quote is one observation. It is the old rule;
      the thing follows from the chunk.
    - A claim whose subject and object become one node is deleted (`self_loops_removed`).
    - The snapshot keeps every touched observation (`ResolveReport.observations`, was `facts`), and
      `undo_merges` restores them with their edges.
  - Readers and checks:
    - `CheckContext.facts` reads observations as triples, plus `things` (where it hangs) and `about` (what
      its document is about). Exact match, the judge sheet and `kg eval` are unchanged in shape.
    - Consistency counts self-references and `facts_touching_domain_rate` on observations.
    - A new provenance check: every observation has a subject, an object and a source chunk.
  - Path truth (`validation/paths.py`) reads the graph's shape from the facts. For an observation graph, a
    path is thing -> observation, true when the observation's document is about the thing. Pre-R64 graphs
    and sheets keep R63's part paths, so the R63 baselines rescore unchanged. The judge sheet carries
    `things` / `about`, so `kg rescore` gives the same number as `kg eval`.
  - **Deviation from the task plan (for the user's review):** kind structure ("drawer pulls PART_OF
    drawer") is stored as an observation too, not as a shared edge. It stays true of the thing whose review
    states it, and one storage shape keeps every reader, the resolver and undo to one code path. Path truth
    still leaves it out of its counts.
  - Size: about 710 changed lines (536 added, 176 removed), a third of them tests and docstrings. Not
    split: a writer without the matching resolver and reader leaves `undo_merges` and the checks
    half-migrated.
  - Tests:
    - The leak case end to end with Neo4j: the Linköping Bed has the drawer rails, not the dresser's
      "stick"; `path_truth` 1.0.
    - Three reviews of one claim stay three observations after a merge, and rewriting the same extraction
      adds nothing.
    - The repeat rule. Undo restores observations and their edges (the dump now compares them).
    - The incomplete-observation check. Path truth in observation mode, and through `kg rescore`.
    - 215 passed (210 before), `ruff` clean.
  - Not done here: the gold questions still walk fact edges, so `question_accuracy` is expected to fall on
    a rebuilt graph until R65 rewrites them (gold change, listed there).
- **Part 2 (done, 2026-09-25): both R62 datasets rebuilt from the LLM cache, $0.**
  - Setup: the user said yes in the session. The same as R62: frozen R58 plan and text schema copied from
    `out/r62_*`, `EXTRACT_PASSES=2`, stage by stage (reset, build, ingest-text, extract, resolve, link,
    eval with R62's verdict file). Outputs `out/r64_furniture/` and `out/r64_heldout/`.
  - Runs:
    - Furniture (`quality`): extract `d888dba5` (140/140 cache hits), resolve `95463b55` (90 calls, 2
      embedding calls), link `20672b67`, eval `a479d424`.
    - Held-out (`heldout`): extract `1b2c725b` (50/50 cache hits), resolve `2263093e` (86 calls, 2
      embedding calls), link `39b77996`, eval `bd1d3b2e`.
    - `cost_usd` 0 in every stage (MLflow). The only live calls are the embeddings in ingest and resolve.
  - Same claims as R62, stage by stage:

    | | Furniture R62 | Furniture **R64** | Held-out R62 | Held-out **R64** |
    |---|---|---|---|---|
    | extracted (pass 1 + pass 2) | 103 (90 + 13) | 103 (90 + 13) | 112 (88 + 24) | 112 (88 + 24) |
    | entities after resolution (merges) | 86 (38) | 86 (38) | 79 (24) | 79 (24) |
    | repeats removed | 1 | 1 | 2 | 2 |
    | derived | 70 | 70 | 40 | 40 |
    | claims in the graph | 172 facts | 172 observations, 172 attached | 150 facts | 150 observations, 150 attached |

  - Scores identical to R62: `triple_precision` / `recall` / `f1`, and the judge's validated precision and
    recall (furniture 1.000 / 0.927, held-out 0.925 / 0.824). R62's verdict files covered every fact id
    of the new sheets, so the judge scores were not recomputed: the claims and their ids are unchanged.
  - **`path_truth` 1.000 on both:** furniture 89 / 89, held-out 107 / 107 (R63 baseline on the edge
    graph: 0.640 and 0.750). The totals are not comparable with R63's: there a path was thing -> shared
    part -> claim, here it is thing -> observation (derived claims and kind structure excluded in both).
  - The audit's examples are gone:
    - "TRANSMISSION ERRONEOUSLY SWITCHED TO REVERSE" hangs on the Rogue only (its three observations,
      chunk `nissan_rogue_complaints.md#3`); the RAV4 has 29 observations, none of them this one.
    - The Linköping Bed has no claim from another document. Its derived part claim reads "drawer rails
      PART_OF Linköping Bed": resolution merged its "drawer slides" into the "drawer rails" kind, and a
      derived claim uses the merged name (see "Found along the way").
  - `question_accuracy`: furniture 0.8 -> 0.2, because four gold questions walk fact edges that no longer
    exist; held-out 1.0, unchanged (its questions search chunk mentions). R65 rewrites the questions.
- The user confirmed the deviation (kind structure stored as observations) on 2026-09-25, before R65.

### R65. Questions walk observations (done, 2026-09-25)
A gold change only: code, prompts and the graph are unchanged. New gold files `tests/gold/r65/
furniture_gold.json` and `heldout_gold.json` are R58's with `triples` and `er_pairs` unchanged (a test
checks it) and the questions rewritten to walk `thing -[:HAS_OBSERVATION]-> Observation -[:SUBJECT|
OBJECT]-> kind`. The R58 files stay as the record of R58-R64.
- **Gold corrections** (made after seeing R64 output; expected answers of the old questions unchanged):

  | Dataset | Question | Before (R58) | After (R65) |
  |---|---|---|---|
  | furniture | 2 suppliers behind the dresser's defective drawer rails | review document → chunk → mentioned entity `-[:HAS_DEFECT]->` | dresser → HAS_DEFECT observation → SUBJECT kind `-[:REFERS_TO]->` Drawer Rails → supplier |
  | furniture | 3 wobbling | any chunk mentioning an entity with a HAS_DEFECT edge to "wobbl…", then its Document ABOUT | product → HAS_DEFECT observation → OBJECT "wobbl…" |
  | furniture | 4 misaligned holes | shared holes node `-[:HAS_DEFECT {chunk_id}]->` Defect, chunk → Document ABOUT | product → HAS_DEFECT observation, SUBJECT "hole", OBJECT "align" / "line up" (the `Defect` type filter dropped: the predicate says it) |
  | furniture | 5 squeak / creak | as question 3 | as question 3, OBJECT "squeak" / "creak" |
  | held-out | 2-4 braking, cracked windshield, air bags | chunk MENTIONS Problem, Document ABOUT vehicle | vehicle → observation → SUBJECT or OBJECT Problem (the windshield may be the Problem's name or the observation's other end) |
  | held-out | 5 piston ring recall → engine recall | chunk MENTIONS, Document ABOUT vehicle ← AFFECTS_VEHICLE recall | vehicle → observation → piston Recall / Component; vehicle ← AFFECTS_VEHICLE recall |
  | furniture | **6 new**: products with defective drawer rails | - | expected `Helsingborg Dresser` only (the only reviews calling the rails defective; Linköping, Malmö and Norrköping praise their slides) |
  | held-out | **6 new**: transmission switched to reverse on its own | - | expected `ROGUE` only (the RAV4 complaint says reverse only when shifting) |

  Questions 1 (domain graph only) are unchanged.
- Tests (`tests/test_observation_questions.py`): no R65 question uses ABOUT, and every question that reads
  the text graph uses HAS_OBSERVATION; R65 changes only R58's questions; every question is valid Cypher;
  the two new questions on hand-made graphs where the shared kind is part of both things. 223 passed (215
  before), `ruff` clean.
- **Check on the R64 graphs, rebuilt from the cache** (the user said yes; the same recipe as R64 part 2,
  outputs `out/r65_furniture/`, `out/r65_heldout/`): the same claims as R64 (103 and 112 extracted, 38 and
  24 merges, 172 and 150 observations), the same triple and judge scores, `path_truth` 1.000 (89 / 89,
  107 / 107). `cost_usd` 0 in every stage.
  - Furniture (`quality`): extract `e6c1d939` (140 cache hits), resolve `4fa694a2`, link `f06546ef`, eval
    `fed59651`. **`question_accuracy` 1.000 (6 / 6)**; R64 0.2, R62 0.8.
  - Held-out (`heldout`): extract `723d1453` (50 cache hits), resolve `d0c4aeb9`, link `da330e64`, eval
    `1640e139`. **`question_accuracy` 1.000 (6 / 6)**; R64 and R62 1.0.
  - The leak the new walk removes, on the rebuilt furniture graph: the old walk shape (chunk MENTIONS the
    "drawer rails" kind, then Document ABOUT) answers the defective-rails question with Linköping Bed and
    Helsingborg Dresser, because the bed's chunk mentions the kind under its alias "drawer slides"; the new
    walk answers Helsingborg Dresser. R62's squeak / creak question failed the same way (it also answered
    Linköping Bed through a mentioned "frame"); on the observation walk it passes.
- Closes the R58 item "Questions 3 and 5 join through an entity's mentions".

### R66. Polarity, values and time (done 2026-09-26: bounds met on Gemini; DeepSeek's held-out schema cost recall)
Every claim carries its qualifiers: whether it is positive, negative or neutral, the time its sentence gives,
and a number with a unit as its object. The schema proposer describes aspects of things, not only defects.
- **Part 1 (done, 2026-09-25): code and tests, $0, no run.**
  - Decisions taken with the user before the code (all three recommendations accepted):
    - A number is a `Value` entity at the end of an OBJECT edge, named in a canonical spelling ("25 kg"),
      and the observation carries `value` and `unit`. The plan said "no OBJECT edge"; one shape keeps the
      readers, the resolver, snapshot and undo, and path truth to one code path (as R64's kind structure).
    - No empty subject: a claim about the thing itself keeps the thing as its subject ("Gothenburg Table
      HAS_DEFECT wobbles"), as the extractor already does.
    - Record dates (NHTSA `dateOfIncident`) moved to R67 (split recorded in the plan above).
  - `core/values.py` (new): the built-in type `Value` and `parse_quantity`. "25kg" and "25 kilograms" are
    `25 kg`; "30,000 Martindale rubs" keeps its unit as written (only units of measurement code knows for
    certain are normalised); "3,5 kg" and "two months" are no number.
  - `text/extraction.py`: a triple has `polarity` (default `neutral`) and `time` (default empty). Three
    prompt rules, in generic words: polarity is the claim's own tone toward its subject; time is copied
    from the quote; a `Value` object is only the number and unit. `verify` rejects a `Value` object that is
    no number (`value_not_a_number`) or not in the quote (`value_not_in_evidence`), and a time not in the
    quote (`time_not_in_evidence`). A repeat within a chunk now also needs the same time.
  - `text/schema.py`: `Value` may end a fact type without a definition; defining it, or a `Value`
    subject, is an issue. Proposer rules: describe aspects (what things are made of, how they are
    measured or rated, what they do well and badly); one fact type holds all three tones; numbers use
    `Value`. The critic knows both.
  - `core/identity.observation_id`: the time joins the key only when set, so every claim without a time
    keeps the id it had (the R62 verdicts still apply to such claims).
  - `text/subject_graph.py`: stores `polarity`, `time`, `value`, `unit`; the extract run logs
    `observations_positive`, `_neutral`, `_negative`, `observations_with_value`, `observations_with_time`,
    and the three new `rejected_<reason>` counts.
  - `resolution/resolver.py`: two `Value` entities are never candidates ("25 kg" and "35 kg" are 91 alike
    by spelling); two kinds whose claims use them with opposite polarity are never candidates (`opposed`;
    neutral use says nothing); the repeat rule includes the time.
  - `validation/`: `StoredFact` and the judge sheet carry polarity and time (rescore keeps them, since the
    time is part of the id). A verdict file may hold `polarity`: one `{id, correct, reason}` per sheet fact,
    exact matches included (the gold has no polarity); code checks the coverage and logs
    `polarity_accuracy`, `polarity_judged`, `polarity_correct`. Files without it score as before.
  - Tests (`tests/test_qualifiers.py`, 19): number parsing, the three rejections, a positive and a number
    claim accepted, the schema's `Value` rules, the id before and after R66, both resolution guards (pure
    and with Neo4j: resistant to scratches / scratch, 97 alike, not merged), the judge's polarity coverage
    and score, and what the subject graph stores (two spellings of 25 kg are one node, each claim keeps its
    wording). The pipeline test checks the new extract metrics. The corpus-quote test now reads every
    extraction rule (see "Found along the way") and both corpora; the new rules quote neither.
    242 passed (223 before), `ruff` clean.
- **Part 2: one run per dataset**, a fresh schema proposal with the frozen R62 plan and `EXTRACT_PASSES=2`
  (as R62), judged with the R65 gold (precision, recall, polarity). The user said yes on 2026-09-25 and
  chose DeepSeek-V4.1-Flash as the builder model, so the runs use the `quality_deepseek` and
  `heldout_deepseek` presets of R69. The comparison with R62 (Gemini) then changes model and code at once;
  the user accepted that, and if a bound is missed, a Gemini run is proposed to tell the two apart.
  - Runs (fresh text schema each, frozen R62 plan, `EXTRACT_PASSES=2`), $1.35 at the peak price (upper
    bound; off-peak is half): furniture text_schema `545b0c04` $0.034, extract `76730376` $0.801, resolve
    `6079366f` $0.147; held-out text_schema `7a1b5611` $0.045, extract `1e579b3b` $0.292, resolve `04f3b7b7`
    $0.034. DeepSeek at effort `low` still thought about 3,900 tokens per extraction call (Gemini at `low`:
    37), three times the estimate given to the user. Then a cached rebuild for scoring ($0, every call a
    cache hit): eval `9bff1263` (furniture) and `bbcd4ded` (held-out), same claims and ids.
  - The proposed schemas describe aspects: furniture has Product, Component, Material, Supplier,
    AssemblyProcess, Condition and 15 fact types (HAS_CONDITION for good and bad alike, MADE_OF,
    HAS_MEASURE / TAKES_TIME / HAS_RATING to `Value`, CAUSES, FITS_WITH); held-out has Vehicle, Recall,
    Problem, Component and 7 (INVOLVES_COMPONENT, CAUSES, OCCURRED_AT_MILEAGE to `Value`, derived
    CONCERNS_VEHICLE, three recall types) and **no relation between a part and a vehicle**.
  - What the graph now holds (MLflow, extract runs): furniture 498 observations, 257 positive / 99 neutral /
    142 negative, 116 with a number, 29 with a time; held-out 78, 0 / 15 / 63, 9, 6. The criteria's
    examples are all there: "drawer slides HAS_MEASURE 25kg" (Linköping; neutral, where the plan expected
    positive), "weighted base HAS_MEASURE 3.2kg" (Örebro), "Gothenburg Table HAS_CONDITION resistant to
    scratches" (positive) next to "... scratches a bit more easily than I'd like" (negative, another
    review), "slats HAS_CONDITION started to crack", time "after just two months of use" (Linköping).
  - Judge: Claude Fable 5.1 in the session, every claim, split by document over 7 agents with one rule set
    (`tests/gold/r66/`); entity pairs by the lead judge. Gold: `tests/gold/r65/` (R58 triples, unchanged).

    | | Furniture R62 | Furniture **R66** | Held-out R62 | Held-out **R66** |
    |---|---|---|---|---|
    | claims in the graph | 172 | 604 | 150 | 118 |
    | judge precision | 1.000 (170/170) | **0.990** (598/604, CI 0.978-0.995) | 0.925 (135/146) | **0.958** (113/118, CI 0.905-0.982) |
    | judge recall | 0.927 (89/96) | **0.896** (86/96, CI 0.819-0.942) | 0.824 (56/68) | **0.618** (42/68, CI 0.499-0.724) |
    | polarity accuracy | - | **0.980** (592/604) | - | **0.949** (112/118) |
    | `path_truth` | 0.640 | **1.000** (496/496) | 0.750 | **1.000** (78/78) |
    | `question_accuracy` | 0.8 | 0.167 | 1.0 | 1.0 |

  - Bounds: furniture precision (>= 0.98) and held-out precision (>= 0.87) met; furniture recall within one
    sample (R62's 0.927 is inside R66's interval); **held-out recall missed** (0.618, interval up to 0.724).
    Model and code changed together, so the cause is not separated (as agreed with the user).
  - Held-out recall misses by cause (26): 15 `INSTALLED_IN` gold triples (a part in the complaint's
    vehicle): the proposed schema has no part-vehicle relation, so no single claim can state it (without
    them 42/53 = 0.79); 11 others, e.g. a whole complaint with no claim ("you can hear wind ... smell freon",
    RAV4) and consequences not extracted ("pumped on the brake pedal twice before hitting the concrete wall").
  - Furniture recall misses (10): the extractor splits a stated cause into two plain conditions ("rough,
    uneven surfaces that cause the drawers to stick" -> no CAUSES, gold 17/18), assembly-effect duplicates
    (gold 6, 37, 64, 71), veneer typed as Material so no part claim (gold 81, 94).
  - Unsupported claims (11 of 722): the negation dropped into polarity ("drawers HAS_CONDITION slide right",
    from "couldn't get the drawers to slide right": contradicted); a claim lifted onto the product ("Malmö
    Desk HAS_CONDITION adequate for my needs" from "the storage is adequate"); a figure of speech as a
    rating ("10/10 would recommend"); a part blamed for the reviewer's own mistake (back rest "upside down");
    circular causes ("AIR BAGS CAUSES AIR BAGS FAILED TO DEPLOY"); a distance read as a mileage ("150 miles
    later").
  - Polarity errors: furniture 12, of which 11 are star ratings tagged neutral while the same rating in
    another review is tagged right; held-out 6 (INVOLVES_COMPONENT tagged neutral). Lead-judge ruling:
    derived claims (`PART_OF` a product, `CONCERNS_VEHICLE`) are neutral by rule 2; one held-out agent had
    marked its 20 derived `CONCERNS_VEHICLE` claims wrong, so held-out polarity is 0.949 under the rule and
    0.780 (92/118) under that agent's reading. Both readings are in the verdict reasons.
  - `question_accuracy` 0.167 on furniture: the gold questions filter on `HAS_DEFECT`; the new schema calls
    faults `HAS_CONDITION` with negative polarity. The questions are tied to one schema's names (R55 saw the
    same with proposed plans); held-out questions search names and pass.
  - Gold corrections: 469 (furniture) and 72 (held-out) supported claims are not in the defect-only gold, as
    expected when the schema widens; none changed the gold.
  - One bug found and fixed in its own commit: `kg rescore` failed on number claims (see "Found along the
    way").
- **Part 3 (done 2026-09-26): one Gemini held-out run with the R66 code** (the user's choice, to separate the
  model from the change). `heldout` preset, fresh schema, frozen R62 plan, `EXTRACT_PASSES=2`, output
  `out/r66g_heldout/`. Runs: text_schema `713cb4ec` $0.114, extract `60beae24` $0.108, resolve `80a6234f`
  $0.006 (**$0.23**); eval `d6314299`, judge scores logged by rescore `765bb07c`. Judge: Fable 5.1, every claim,
  two agents with the same rules (`tests/gold/r66/judge_verdicts_heldout_gemini.json`).
  - Gemini's schema, proposed with the same R66 prompt, keeps the derived `Component INSTALLED_IN Vehicle`
    and adds numbers (OCCURS_AT_MILEAGE, OCCURS_AT_SPEED, HAS_DURATION, HAS_MILEAGE to `Value`). 152 claims:
    0 positive / 16 neutral / 101 negative (extract metric; derived ones follow), 18 numbers, 51 times.

    | Held-out | R62 (Gemini, frozen R58 schema) | R66 DeepSeek | **R66 Gemini** |
    |---|---|---|---|
    | judge precision | 0.925 (135/146) | 0.958 (113/118) | **0.960** (143/149) |
    | judge recall | 0.824 (56/68) | 0.618 (42/68) | **0.824** (56/68) |
    | polarity accuracy | - | 0.949 | **0.993** (151/152) |
    | `path_truth` | 0.750 | 1.000 | **1.000** (113/113) |

  - **All R66 bounds are met on Gemini**; the held-out recall loss of the DeepSeek run was DeepSeek's schema
    choice (no part-vehicle relation), not the R66 change. Times: 0 of 51 wrong.
  - Remaining misses (12): the ACC / LKAS symptoms put only on the vehicle with the when-clause as `time`
    (Civic gold 18-23, the same miss as R62), "THIS PART FAILURE" causes not named on the piston clip ring
    (gold 26-27, a two-hop path the one-fact rule does not count), and consequences not extracted (the
    concrete wall, "wouldn't take a jump"). Unsupported (6): `SUBCOMPONENT_OF` claims from general knowledge
    ("PADS SUBCOMPONENT_OF LEFT REAR BRAKE", "PISTON SNAP RING SUBCOMPONENT_OF ENGINE"), the gas gauge that is
    the owner's evidence, not a fault (as R62), and "could cause an accident" stored as "causes".

### R67. Recall texts and NHTSA linking (started 2026-09-27)
One concern: the held-out text reaches its structured records (`entities_linked` 0 since R58, and the 29
recall texts are node properties no claim is made from). Every rule is domain-neutral and computed in code;
the furniture graph must come out unchanged (its `description` column is one short tagline per product).
- Decisions taken with the user before the code (2026-09-27, both recommendations accepted):
  - **Record dates stay on the record.** A complaint section is tied to its Complaint record, and its dates
    (`dateOfIncident`, `dateComplaintFiled`) are read there in one hop. No date is copied onto claims, so code
    never picks which date column of a dataset is "the" date (a choice about meaning). Deviation from the
    task file ("dateOfIncident becomes the record time of their observations").
  - **Claims hang on both things.** `HAS_OBSERVATION` from the thing the document is about (as today, so the
    R65 questions and `path_truth` keep their meaning) and from the record its section is about.
- Parts, one commit each; code parts are $0, the run comes last:
  - **Part 1: record text becomes documents.** A string column whose values are prose (a rule on length and
    sentences, from the profile) is not only a property: each record's prose columns become one document
    `ABOUT` that record by its key, so its claims become observations with the record as thing. On NHTSA:
    the 29 recalls' Summary, Consequence and Remedy (and Notes); on furniture: none.
    **Done 2026-09-27:** profiler computes `is_prose` (avg ≥ 120 chars and ≥ half the values
    multi-sentence); `text/record_documents.py` builds one document per record with a `RecordRef`;
    linking ties it `ABOUT` its record by key (`toString` compare), never by title; `name_property`
    moved to `structured/plan.py`. Gate green (252 tests, ruff clean); no run, tests are the proof.
  - **Part 2: text reaches records.** (a) A text entity links to a domain node whose name its name contains
    as whole words, within its documents' scope (R60's containment rule, now in linking too): "2016 Honda
    Civic" to `Vehicle {model: 'CIVIC'}`. (b) A section whose heading contains a record's unique key as a
    whole token is `ABOUT` that record (`(:Chunk)-[:ABOUT]->(:Complaint)`), and its observations are
    attached to it too.
    **Done 2026-09-27:** `contain_entity` (whole-word subset, scope-only, after fuzzy, longest name wins,
    min 4 chars) and `match_chunk_records` (squashed heading tokens, keys >= 4 chars) in linking;
    `attach_observations` attaches via document AND chunk ABOUT; new metrics
    `entities_linked_by_containment`, `chunks_linked`. Gate green (257 tests, ruff clean); no run, the
    linking changes are measured by part 3's held-out run.
  - **Part 3: one held-out run** (asked first), judged: `entities_linked` > 0 with every text vehicle linked,
    at least one observation per recall with the recall as thing, judge precision of the recall observations
    with its `n`, and a gold question from the Civic's piston ring complaint to recall `16V074000` without a
    `Document -ABOUT->` jump.
    **Done 2026-09-27** (user's yes; $0.271: extract $0.250 + resolve $0.022; MLflow extract run
    `c8593f5a410c4a1e8bce6a63f3c58943`, git 18af45a, frozen R66 plan and text schema, EXTRACT_PASSES=2):
    `entities_linked` 0 -> 30 with all 5 text vehicles linked ("2016 Honda Civic" -> CIVIC by containment,
    5 containment links, 0 ambiguous); 34 of 34 documents linked (29 records by key); 54 complaint sections
    ABOUT their Complaint; all 7 gold questions pass, including the new complaint-10823637 -> 16V074000
    question (added before the run); all validation checks pass. Judge (Fable 5 in the session,
    `tests/gold/r67/recall_observation_verdicts.json`): recall-observation precision **0.963 extracted**
    (52/54, Wilson 0.875-0.99), **0.0 derived** (0/38, one shared cause, see "Found along the way"), 0.565
    all (n=92). **Deviation:** 27 of 29 recalls have an observation; for `16V074000` and `17V472000` the
    extractor returned zero facts from well-formed chunks (nothing rejected) - model variance, not retried
    (run budget).

### R68. Coverage: is every fact a question needs reachable? (done 2026-09-30)
The last step of the observation-graph arm, and Step 0 of the layered-model direction
(`docs/direction/2026-09-27_layered-knowledge-model/`, section 7; task file
`docs/tasks/layered-knowledge-model.md`). Precision is judged against the text and recall against a
defect-only gold, so nothing yet says how much of what the text states a question can reach. No new gold
set: a fixed random sample of sentences per dataset, judged claim by claim, scored by code.
- **Reading (the layered-model goal, 2026-09-27).** The judge lists the claims of each sampled sentence
  that a question could need. A claim is **covered** when an observation from the sentence's chunk states
  it (subject, relation, object, negation and speaker intact) and hangs on the thing it is about. It is
  **reachable** when it is covered, or when its chunk hangs on the thing it is about, so that a retrieval
  route could still read it from the text. Covered is what counting and filtering need; reachable is what
  locating the evidence needs.
- **Ten causes, in order; the judge takes the first that fits**, so every miss counts once, and the model's
  misses are never blamed on the shape (cause 1 comes first):

  | # | Cause | Real example |
  |---|---|---|
  | 1 | Extraction miss: the shape and the schema could hold it, the model did not extract it | the RAV4 complaint "YOU CAN HEAR WIND ... ALSO YOU CAN SMELL FREON" gave no claim (R66) |
  | 2 | No schema type | DeepSeek's held-out schema had no part-vehicle relation: 15 `INSTALLED_IN` gold triples (R66) |
  | 3 | Entity identity | the bed's "drawer slides" merged into the dresser's "drawer rails" (R64) |
  | 4 | Wrong attachment | "had multiple Outbacks in the past" can only hang on the complaint's vehicle |
  | 5 | Assertion | "couldn't get the drawers to slide right" stored as stated |
  | 6 | Attribution | "ADVISED BY FORD THIS IS NORMAL" |
  | 7 | Role | the Civic's when-clauses stored as `time` (gold 18-23) |
  | 8 | Event structure | "3RD TIME, PADS CHANGED AND ROTORS MACHINED ... SQUEAL RETURNED IN TWO DAYS" |
  | 9 | Concept | veneer typed as a Material, so no part claim (gold 81, 94, R66) |
  | 10 | Other | |
- **Order of judging (the gold rule of the `evaluation` skill):** the claims of every sentence are listed
  before any observation is opened; matching them to observations is a second pass.
- Parts, one commit each:
  - **Part 1: code, $0, no run.** A seeded sentence sample over the graph's chunks, a coverage sheet (each
    sentence with its chunk, the things the chunk hangs on and the observations from it), the verdict file
    with the ten causes, and pure scoring: `coverage` and `reachable` with Wilson intervals, coverage by
    polarity and by schema place, the count per cause. Commands `kg coverage-sample`, `kg coverage-sheet`
    and `kg coverage` (the last one needs no graph).
    **Done 2026-09-28:** `validation/sentences.py` (line-wise split at sentence stops, never inside a
    number; sample by SHA-256 rank of seed and sentence id, so it survives re-chunking),
    `validation/coverage_sheet.py` (a sentence is found by document and wording; its chunk's
    observations under their own wording and resolved name; the things the chunk hangs on with their
    record fields, long text cut at 80 characters), `validation/coverage.py` (the ten causes as `Cause`;
    a claim is covered or missed, never both; a miss names its schema type unless `no_schema_type`;
    the file must judge every sentence once and cite only its own chunk's items), `validation/interval.py`
    (`Proportion` with the Wilson interval; reproduces R66's and R67's hand-computed intervals). Stages
    `coverage_sample`, `coverage_sheet`, `coverage`. Gate green (273 tests, 257 before; ruff clean); no
    run, tests are the proof.
  - **Part 2: the estimate (done 2026-09-30).** About 40 sentences per dataset, two judge passes, then
    `kg coverage`. Paused on 2026-09-28 at the held-out `resolve` (the Gemini key answered 402, credits
    depleted; resolution needs live embeddings), resumed on 2026-09-29 after the user topped the key up.
    - **Which graphs (asked first).** R67's held-out graph was built with the R52 text schema (see "Found
      along the way"), so the user chose a new held-out graph on the arm's own schema: `out/r68g_heldout`
      with the frozen plan and R66 part 3's Gemini text schema, current code (git `409d617`),
      `EXTRACT_PASSES=2`. Furniture stays on the R66 DeepSeek graph (the user's choice), rebuilt from the
      cache into `out/r68_furniture`. The builder model differs between the two datasets and from the
      Gemini runs planned for the layered arm, so the two datasets' numbers are no model comparison.
    - **Runs** (the user's yes for both; costs from MLflow): held-out extract `9945e539` **$0.354** (112
      new calls for the 56 recall-text chunks, the 50 complaint calls cached; 532 claims, 415 of them from
      the recall texts) and resolve `b329c518` **$0.117** (365 candidates, 278 new adjudications):
      **$0.471**. That is above both estimates given (first $0.20-0.27, then $0.39-0.41 after the extract;
      the user said yes to continue). I estimated from R67's cost per call, but this schema draws about 7
      claims per recall chunk where R67's drew about 1, and output tokens drive the cost. Furniture:
      extract `0ab9b548` 140/140 and resolve `92f33de8` 277/277 cache hits, run with the DeepSeek key set
      to an invalid value so that a miss would fail instead of paying: **$0**. Build, ingest, link, eval
      and the coverage stages make no LLM call.
    - **The rebuilt graphs reproduce their judged scores.** Furniture: `triples.jsonl` byte-identical to
      R66's, the judge sheet identical (604 facts, same ids), and eval `9f2de201` equals R66's eval
      `9bff1263` on every metric: precision 0.990 (598/604), recall 0.896 (86/96), polarity 0.980
      (592/604). Held-out: the 124 complaint claims keep their ids and gold matches (they now also hang on
      their Complaint, R67), but the 28 derived `INSTALLED_IN` facts changed ids. A derived fact has no
      wording of its own and takes the resolved entity's name; the recall texts brought shorter vehicle
      names ("Escape" for "2015 FORD ESCAPE"; R44 picks the shortest), so exact match also lost 14 gold
      triples. R66 part 3's verdicts were carried over by one written rule (same document, subject,
      predicate and evidence) into `tests/gold/r68/heldout_judge_verdicts.json`; one derived fact is new
      (`EXTERIOR REARVIEW MIRRORS INSTALLED_IN RAV4`, from gold 63's own sentence; SUPPORTED by the lead
      judge). Eval `cef0b8f6`: precision **0.960** (144/150; R66: 143/149), recall **0.824** (56/68),
      polarity 0.993 (152/153), ER valid 0.950, 7/7 questions; `entities_linked` 56 (link `0c93e100`;
      R67's graph 30). `path_truth` reads 0.818 (509/622), but all 113 "false" paths are complaint claims
      on their own Complaint record through R67's section link, which the metric predates (509/509 over
      document links; "Found along the way").
    - **Judging** (`tests/gold/r68/judge_rules.md`, invented examples only). Pass 1: two blind Fable 5.1
      subagents listed the claims of each sentence from the sheet without observations and record fields
      (held-out 67, furniture 72), committed in `1135440` before any matching. One rule clarification was
      added before pass 2 and committed with the claims: "the writer" is the document's own source,
      including the submitter a report relays ("THE CONTACT STATED THAT ..."). Pass 2: two new Fable 5.1
      subagents matched every claim against the full sheet; a script checked that no claim changed. Lead
      judge (Opus 5.5), one change: recall `15V436000` "covers certain model year 2015 Ford Escape vehicles
      manufactured April 1, 2014, to June 12, 2015" (and its Focus and C-Max claims) from covered to
      `role`, because the stored `AFFECTS_VEHICLE` link drops the build window; the furniture judge
      treated "sticks when i open it too fast" the same way. No gold corrections: no claim changed after
      pass 1. Coverage runs `4908cd47` (held-out) and `b9e48f40` (furniture).

    | | Held-out (R66 Gemini schema) | Furniture (R66 DeepSeek graph) |
    |---|---|---|
    | sentences / with claims / claims | 40 / 40 / 67 | 40 / 35 / 72 |
    | **coverage** (Wilson 95 %) | **0.313** (21/67, 0.215-0.432) | **0.486** (35/72, 0.374-0.599) |
    | **reachable** | **1.000** (67/67, 0.946-1.000) | **1.000** (72/72, 0.949-1.000) |
    | coverage_in_schema | 0.457 (21/46, 0.322-0.598) | 0.745 (35/47, 0.605-0.847) |
    | covered by a record field only | 8 (filing dates, makers, a model year) | 0 |
    | coverage negative / neutral / positive | 10/31, 11/36, n = 0 | 8/17, 4/8, 23/47 |

    Misses per cause, each with one real example (`tests/gold/r68/<ds>_verdicts.json`):

    | Cause | Held-out | Furniture |
    |---|---|---|
    | 1 extraction | 4: "A REAR END COLLISION WAS NARROWLY AVOIDED IN ONE CASE." - nothing stored | 9: "resistant to water rings and scratches" - only the water-ring half stored |
    | 2 no_schema_type | 21: "Ford will notify owners, and dealers will update the instrument panel software, free of charge." - no type for remedies, notifications, contact numbers, dealer visits | 25: "I absolutely love my Stockholm Chair!" - the schema's Condition excludes "general opinion" |
    | 3 identity | 0 | 0 |
    | 4 attachment | 0 | 0 |
    | 5 assertion | 12: "The engine block heater may crack ..." stored as `crack AFFECTS_COMPONENT engine block heater` | 1: "absolutely no squeaking or movement" - "no movement" not stored |
    | 6 attribution | 1: "THEY CONFIRMED MY CAR DOES HAVE A RECALL" stored as the writer's `RECALL AFFECTS_VEHICLE 2016 HONDA CIVIC` | 0 |
    | 7 role | 6: the 15V436000 build windows; "DURING DAYLIGHT AND NIGHTTIME HOURS WITH CLEAN CAMERAS" | 2: "the drawer sometimes sticks when i open it too fast" stored as `drawer HAS_CONDITION sticks` |
    | 8 event_structure | 2: "THE CAR DID THIS ON TWO SEPARATE OCCASIONS" - `BRAKED ON ITS OWN` stored once | 0 |
    | 9 concept | 0 | 0 |
    | 10 other | 0 | 0 |

    - **Reading.** Every sampled claim is reachable: each chunk hangs on the thing its claims are about
      (R67), so a retrieval route can locate the evidence for all of them. What a query can count or
      filter on is a third of the held-out claims and half of the furniture claims. The largest cause on
      both datasets is the schema, not the model (21 of 46 and 25 of 37 misses): the held-out schema has no
      place for remedies, notifications or owner actions, and the furniture schema excludes overall
      opinions. Next comes `assertion` on the recall texts: "may" survives only where the extractor kept
      it inside a name ("Water may enter through the steering gear box cover", covered). The model's own
      misses are 4 and 9. Identity and attachment misses are 0 in this sample, so the layered arm's
      identity step is motivated by the path and ER findings, not by coverage.
    - **Recall-text precision** (the user asked to judge all 415 claims from the held-out recall texts,
      which the judge sheet leaves out because the gold covers only the complaints). Rules
      `tests/gold/r68/recall_precision_rules.md`; four Fable 5.1 subagents by whole documents. The groups
      split two against two on two conventions, which the lead judge settled by one principle each: a bare
      "certain" is no limit, as in the coverage decision above (one more Fable 5.1 judge re-decided
      `overstated` on all 203 `AFFECTS_VEHICLE` claims), and `ADDRESSES_PROBLEM` may be negative or
      neutral while the other recall links are neutral. Every change is written into its verdict
      (`tests/gold/r68/heldout_recall_precision.json`; scores computed by code). Precision **0.990**
      (411/415, 0.975-0.996); **strict** precision, counting a claim that drops a "may" or a stated limit
      as wrong, **0.588** (244/415, 0.540-0.634); polarity 0.988 (410/415); 12 vague. Unsupported: 3
      `wrong_relation` (remedy parts as covered components, e.g. "21V839000 COVERS_COMPONENT harness
      protector cover", a new part the dealers install) and 1 `not_in_text`. Overstated: `AFFECTS_VEHICLE`
      109 of 203 (build windows, model years, equipment dropped), `AFFECTS_COMPONENT` 39 of 50 ("may"
      dropped). R67's graph (R52 schema) had 54 extracted recall claims at 0.963; this schema draws 415,
      and all 29 recall documents have claims. Derived facts on recall documents (R67: 0/38 true) were not
      judged; that bug is unchanged.

    **Baseline of the observation-graph arm** (layered-model Step 0; judge scores are Claude's, the exact
    scores sit next to them in each eval run):

    | | Held-out `out/r68g_heldout` | Furniture `out/r68_furniture` |
    |---|---|---|
    | text schema of the graph | R66 part 3, Gemini (text_schema `713cb4ec`) | R66 part 2, DeepSeek (text_schema `545b0c04`) |
    | builder model | gemini-3.8-flash | deepseek-flash |
    | judge precision / recall (gold documents) | 0.960 (144/150) / 0.824 (56/68) | 0.990 (598/604) / 0.896 (86/96) |
    | recall-text precision / strict | 0.990 (411/415) / 0.588 | none (no record texts) |
    | polarity accuracy | 0.993 (152/153) | 0.980 (592/604) |
    | `path_truth` | 0.818 (509/622); 509/509 over document links | 1.000 (496/496) |
    | coverage / reachable | 0.313 (21/67) / 1.000 | 0.486 (35/72) / 1.000 |
    | `entities_linked` | 56 | 33 |
    | `question_accuracy` | 1.000 (7/7) | 0.167 (1/6): the gold questions filter on predicate names this schema renamed (R66) |
    | MLflow | extract `9945e539`, resolve `b329c518`, link `0c93e100`, eval `cef0b8f6`, coverage `4908cd47` | extract `0ab9b548`, resolve `92f33de8`, link `a37a9285`, eval `9f2de201`, coverage `b9e48f40` |

    Runs of this part: $0.471 (held-out extract and resolve); furniture $0; the judging is Claude in the
    session. Gate green (273 tests, ruff clean). **R68 done.**

### R69. DeepSeek as a builder model (done 2026-09-25, before R66 part 2)
The user asked for R66's runs on DeepSeek-V4.1-Flash. A new provider is its own concern, so it is its own
step, done before R66 part 2 (numbered after the reserved R67 and R68).
- `llm/deepseek.py` (new): `DeepSeekClient`, an Adapter over DeepSeek's OpenAI-compatible
  `POST /chat/completions` with httpx, using the shared retry loop (like `llm/ollama.py`). DeepSeek has no
  schema-constrained decoding, only JSON mode: the pydantic schema goes as JSON Schema in a system message
  and code validates the reply; an empty reply (which the docs say JSON mode may return) is a retried
  failure. Usage: DeepSeek's `completion_tokens` includes the reasoning, so the visible answer is
  `completion - reasoning_tokens` and the reasoning is reported as thinking tokens.
- Thinking levels map to DeepSeek's efforts (the user chose to mirror the Gemini presets): `low` → low,
  `medium` and `high` → high (there is no medium), `minimal` → thinking off, "" → the model's default.
- No embedding model at DeepSeek: `cli.build_embedder` keeps Gemini embeddings when the provider is
  DeepSeek (None without a Gemini key). Settings `deepseek_api_key` (in `.env` only), `deepseek_url`.
- Presets `quality_deepseek` and `heldout_deepseek`: the quality settings with `llm_provider: deepseek`
  and `deepseek-flash` for schema and extraction; they inherit `ask_permission`, and the same MLflow
  experiments as `quality` / `heldout`. `prices.yaml`: `deepseek-flash` at the peak price ($0.30 / $1.20 per
  1M), so `cost_usd` is an upper bound (off-peak costs half, cached input a fiftieth).
- Tests: the request (schema in the system prompt, JSON mode, effort), the usage split, the effort mapping,
  an empty reply retried, no key, and the wiring (DeepSeek generates; the embedder is not DeepSeek). The
  run-guard test lists the two new presets as asking. 247 passed (242 before), `ruff` clean.
- Verified live with one tiny call (the user's key; well under $0.001): JSON mode with effort `low` returned
  a valid object, 157 prompt, 13 visible and 100 reasoning tokens, 2.7 s.

### R70. Question-answer benchmark (layered-model Step 1; done 2026-09-30)
The first step of the layered-model arm (branch `layered-model`, task file
`docs/tasks/layered-knowledge-model.md`, Step 1): what "answers correctly" means, fixed before any query
code exists, as R63 measured false paths before R64 fixed them. $0, no run.
- **Split (the user's choice, 2026-09-30): two parts, one commit each.** Part 1: the gold format and the
  scoring code. Part 2: the furniture and held-out QA gold sets and the generality corpus with its
  questions, committed before any query code (Step 2).
- **Part 1: format and scoring (done 2026-09-30).**
  - `validation/qa_gold.py`, the gold file. A question has one of the six types of the task file
    (`QuestionType`: `multi_hop`, `aggregation`, `structured_filter`, `disambiguation`,
    `negation_sensitive`, `lookup`), an expected answer in exactly one form (a set of names with aliases,
    a number, or a short text), the route that should answer it (`exact` / `retrieval`), and its
    evidence: chunks with a verbatim quote, or rows of a staged file picked out by their cells, for the
    questions only records answer (R65's "which suppliers provide the drawer rails of the Helsingborg
    Dresser?"). A retrieval question needs chunk evidence; `origin` names a carried-over question,
    `hard_case` one of the eight hard cases of the generality corpus. `CorpusSpec` pins where the chunks
    come from (data dir, the frozen plan for record documents, the chunk settings), because chunk ids
    (`record/Recall/15V436000#0`) depend on them. `check_qa_gold` raises `InvalidGoldError` (new,
    `core/errors.py`) for a duplicate id, an unknown chunk, a quote that is not a plain substring of its
    chunk, or a record no row matches.
  - `validation/qa.py`, the scoring. The answers file `kg qa` will write (one JSON line per question: the
    chunk ids given to the reader, best first; the answer; citations), the judge's verdict file for the
    free-text answers (a reason each), and `score_qa`: correctness (a set is right when it names every
    expected entity by name or alias under `norm` and nothing else; numbers equal; free text by the
    verdict), recall@k (gold evidence chunks in the top k, pooled over the retrieval-route questions) and
    citation faithfulness (the cited chunk was given to the reader and holds the quote), overall and for
    every type, each a `Proportion` with its Wilson interval (`validation/interval.py`). The answers must
    cover every question once and the verdicts exactly the free-text ones, else `EvaluationError`.
  - Two decisions. Gold quotes are checked as plain substrings, because they are copied by hand and a
    case or markup difference is a copying mistake; model citations are checked under `norm`, as
    extraction's `verify` checks evidence. Recall@k leaves exact-route questions out, so the graph arm and
    the vector-only baseline are scored on one denominator.
  - Tests `tests/test_qa.py` (15, pure): one answer form; evidence required; a quote with dropped
    markdown or another case, an unknown chunk, a cell prefix and a missing file rejected; a right set by
    alias and accent, a set missing one and a set with one too many; numbers; free text by a verdict file
    and without one; a citation to a chunk not retrieved, and to the wrong retrieved chunk; recall@k at
    k = 1 and 2 with the exact-route question left out; per-type n and interval; the answers file read
    and a bad line named. Gate: 288 passed (273 before), `ruff check` clean. No run: tests are the proof.
- **Part 2: gold sets and generality corpus (done 2026-09-30).** Written by Claude (Opus 5.5) from whole
  files, before any query code or answer exists: all 70 furniture reviews and the five tables; all 25
  held-out complaints, the 29 recall texts (as the record documents the pipeline builds from the frozen
  plan) and the three tables. The only pipeline output opened was R68's coverage sheets, to confirm that
  the rebuilt chunks equal the graph's (34 of 34 furniture and 29 of 29 held-out chunk texts identical).

  | Gold file (`tests/gold/qa/`) | multi_hop | aggregation | structured_filter | disambiguation | negation_sensitive | lookup | total | exact / retrieval | sets / numbers / texts |
  |---|---|---|---|---|---|---|---|---|---|
  | `furniture_qa.json` | 7 | 6 | 6 | 6 | 7 | 6 | 38 | 27 / 11 | 24 / 12 / 2 |
  | `heldout_qa.json` | 7 | 6 | 7 | 6 | 6 | 6 | 38 | 26 / 12 | 26 / 6 / 6 |
  | `generality_qa.json` | 3 | 2 | 3 | 4 | 2 | 7 | 21 | 9 / 12 | 10 / 3 / 8 |

  - **Carried over with their answers unchanged:** the six R65 furniture questions and the seven held-out
    ones (R65's six and R67's complaint question), each with `origin`; a test compares their expected
    names with `tests/gold/r65/`. Their types: furniture 2 multi-hop, 1 aggregation, 2 negation-sensitive
    (wobbling, squeak or creak), 1 disambiguation (defective drawer rails); held-out 1 structured filter,
    1 aggregation, 2 disambiguation (windshield, reverse), 1 negation-sensitive (air bags), 2 multi-hop.
  - **Evidence** is what supports the expected answer; the traps are named in each question's `note`, not
    cited, so recall@k never asks a system to fetch a chunk that argues against the answer. An empty answer
    cites the negated mentions it rests on: F29 "Which products do reviews report as sagging?" expects none
    and cites four "no sagging" chunks. Examples of the types: H30 "Which vehicles did owners report being
    in a rear-end collision?" expects none ("A REAR END COLLISION WAS NARROWLY AVOIDED IN ONE CASE."); H23
    "Which NHTSA recall campaign does Ford number 22S25?" expects 22V254000, not the look-alike 22V413000
    (22S43); H06 asks for 2019-model complaints of a car that will not accelerate: the RAV4's 11209676,
    not the 2015 Escape's hesitation.
  - **Generality corpus** `tests/fixtures/generality/` (synthetic, written by Claude): 11 documents in three
    unrelated domains, one folder each: a water utility (pump inspection, incident report, shift notes), a
    research institute (newsletter, committee minutes, field log, seminar notice) and local news (council
    repairs, works budget, a letter, a reopening). Two have no heading and no separator
    (`shift_notes.txt`, `fieldwork_log.txt`). Two keyed tables with short fields, `institute/staff.csv`
    (`staff_id`) and `water/pumps.csv` (`serial_number`), so no record documents arise and the chunk ids do
    not depend on a plan. Every hard case is asked: two individuals with one name (Maria Lopez S-104, Soil
    Ecology, and S-219, Finance Office: G01-G03), one individual under several names (Jonathan, Jon and J.
    Pike: G04, G05), two instances of one model (Hydra P-40 HP40-1183 and HP40-2291: G06), one document
    about two things (G08, G09), a claim by someone other than the author (the mayor, the pump vendor:
    G10, G11), negation (G12, G13), numbers to filter on (G07, G14, G15), and four negative controls whose
    answer is nuance the graph does not model (a counterfactual, two obligations, a nested report:
    G16-G19). The thesis must state that the corpus is synthetic and that gold, corpus and verdicts come
    from one model family.
  - **Tests** `tests/test_qa_gold_files.py` (12): each gold file fits its corpus as the pipeline chunks it
    (`tests/qa_corpus.py` rebuilds the chunks with the loader, the frozen plan's record documents and the
    chunker at 1500 / 200 / 0, and the staged tables); the real sets ask every type at least five times in
    30-50 questions; every earlier question is carried with its answer unchanged; the generality corpus
    asks every hard case and spans three domains in 10-12 documents. The three prompt guards (extraction
    rules, second-pass rules, schema rules) now read their corpora from `tests/evaluation_corpora.py`,
    which adds the generality corpus: no rule quotes it. `QuestionType.AGGREGATION`'s comment now says
    "counts, ranks or collects every match". Gate: 300 passed (288 after part 1), `ruff check` clean.
    $0, no run. **R70 done.**

### R71. Question answering over the graph, and the vector-only baseline (layered-model Step 2; done 2026-09-30; the generality corpus's results are in R72)
Makes the system GraphRAG end to end on the observation-graph arm's graphs as they are, so every later step
has a baseline in answer quality (task file, Step 2). Two parts, one commit each, then the runs, asked
first. The user chose DeepSeek (`deepseek-flash`) for everything the query stage asks a model (2026-09-30);
embeddings stay Gemini's, as DeepSeek has none.
- **Part (a): the retrieval route and the vector baseline (done 2026-09-30).** New package `query/`:
  - `names.py`: a question's names are linked to nodes in code, no model call: a run of its words links a
    thing (domain node) or kind (`:Entity`) whose name or alias it spells alike (`core.similarity`
    `name_similarity`, the resolver's token-sort score, at `qa_link_fuzzy` 90), and the `qa_link_neighbours`
    (3) nodes whose names lie nearest the question in meaning are linked too, by rank, like the mutual-nearest
    ER blocking, so no similarity scale is tuned. Runs made only of English function words link nothing.
  - `traversal.py`: four fixed, parameterised patterns: `thing_observations` (a thing's observations and the
    documents and sections about it), `kind_observations` (claims with the kind at either end, chunks that
    mention it), `related_records` (domain nodes within `qa_hops` 2 relationships, the walk restricted to the
    plan's labels so it never passes through documents or kinds) and `referred_records` (a kind's
    `REFERS_TO` records). Each is reported on its own in the answer's trace.
  - `graph_store.py` (the one Neo4j reader, behind a `GraphStore` protocol), `reader.py` (one domain-neutral
    prompt; the answer is a set, a number or a short text with citations; no chunk means no model call),
    `systems.py`: `GraphRetrieval` ranks the reached chunks by cosine to the question and keeps `qa_top_k` 5;
    `VectorBaseline` takes the 5 nearest from the `chunk_embeddings` index. Both share the reader, k and the
    embedder. No fallback from the graph to vector search: a question that links nothing gets nothing, so the
    failure shows as the graph's own.
  - `kg ask`, `kg qa GOLD` (one MLflow run per system, `qa_graph` / `qa_vector`, so each has its own cost;
    writes `out/answers_<system>.jsonl` with the chunk texts shown and the retrieval trace) and
    `kg qa-score GOLD ANSWERS --verdicts V` (no graph). `kg qa` scores what code can before the judge
    (`score_qa(..., allow_unjudged=True)`: free text counted as unjudged, never as wrong); the final score is
    strict, as in R70.
  - Recall@k: both numbers are reported, as proposed in "Found along the way" (R70): over the retrieval-route
    questions (`recall_at_k`) and over every question with chunk evidence (`recall_all_at_k`).
  - Moves, behaviour unchanged: the two similarity primitives from `resolution/matchers.py` to
    `core/similarity.py`, the input-file helpers from `pipeline/stages.py` to `pipeline/inputs.py`, the index
    name to `text/lexical.CHUNK_VECTOR_INDEX`.
  - Settings `qa_model`, `qa_thinking`, `qa_top_k`, `qa_hops`, `qa_link_fuzzy`, `qa_link_neighbours`,
    `qa_workers`; `quality` sets `qa_thinking: low`, `quality_deepseek` (and so `heldout_deepseek`)
    `qa_model: deepseek-flash`; new preset `generality` (the R70 corpus, DeepSeek, `ask_permission`
    inherited). The run guard now asks for `kg ask` and `kg qa` too.
  - Tests: `tests/test_query.py` (linking by name, alias and rank; function words; ranking; the reader's
    prompt and its no-text answer; the corpus-quote guard on the reader's rules; both systems over a fake
    store; the answers file; `kg qa-score` with a driver to nowhere), `tests/test_query_graph.py` (Neo4j:
    each pattern, the hop limit, no walk through a shared document, names and chunks, the vector index,
    `kg qa` end to end with params, metrics and the answers file), `tests/test_qa.py` (unjudged free text,
    recall over all questions, metric names). Gate: 326 passed (300 before), `ruff check` clean. No run.
- **Part (b): the exact route and the router (done 2026-09-30).**
  - `exact.py` (text2cypher): the model gets the graph's schema, read from the graph itself
    (`graph_schema.py`: labels with property keys and three example values, relationship patterns,
    claim patterns; chunk text, vectors and quotes left out), and proposes one query, its parameters
    and its answer form. Three guards before an answer: the text check (`cypher_check.py`: nothing that
    writes, calls a procedure, loads a file or switches database, checked with quoted names and
    comments taken out; no quoted text value; every `$name` given; one statement; a LIMIT added up to
    `qa_cypher_limit` 100, a larger one refused), the database's plan (`EXPLAIN`: query type `r`, and
    Neo4j 5.26's warnings 01N50/01N51/01N52 on unknown labels, types and property keys), and the run in
    a read transaction that the database cancels at `qa_cypher_timeout_s` 10 and in which it refuses
    any write on its own (verified: "Writing in read access mode not allowed"). A refused or failing
    query gets one retry with its reasons; a second failure gives up. Code reads the rows: the first
    column's distinct values as names, or its one number.
  - `router.py`: the model labels a question `exact` (count, rank, list every match, filter on a
    record field) or `retrieval` (what the texts say, how, why, when), with the schema in view.
    `systems.RoutedGraph` is the graph system `kg qa` scores: exact when the router says so and the
    route answers, retrieval otherwise; a fallback keeps the router's label (for `route_accuracy`) and
    the exact trace (for the failure analysis). Answers carry `route`; `validation/qa.py` scores it
    against the gold's route, leaving out answers without a label (the vector baseline).
  - The `qa_graph` run logs the router and Cypher prompt versions, `qa_cypher_limit`,
    `qa_cypher_timeout_s`, and `routed_exact`, `routed_retrieval`, `exact_answered`,
    `exact_fallbacks`, `cypher_proposals`, `cypher_refused`, `route_accuracy`.
  - Tests: `tests/test_query_exact.py` (the text check: a read passes with a LIMIT, eight writing or
    calling queries refused, a keyword in a quoted name or comment is no clause, quoted values and
    missing parameters refused, one statement, the LIMIT cap; rows as names or a number; the retry
    with its reasons; two failures give up; the router; the routed system's exact answer, fallback
    and retrieval; the router and Cypher prompts quote no corpus); `tests/test_query_graph.py`
    (Neo4j: EXPLAIN refuses a write, unknown names and bad syntax; a read runs and a write is refused
    by the read transaction itself; the schema reader; `kg qa` end to end now routes, retries a
    refused query and counts); `tests/test_qa.py` (route accuracy). Gate: 350 passed (326 after part
    a), `ruff check` clean. No run.
- **Fix after the first held-out run (own commit, 2026-09-30).** The schema text showed every example value
  quoted (`model_year e.g. '2015'`), while the graph stores integers, booleans and dates (`valueType`:
  `model_year` INTEGER, `crash` BOOLEAN, `dateComplaintFiled` DATE). DeepSeek therefore wrote
  `v.model_year = $year` with `$year = '2015'` and `r.ReportReceivedDate STARTS WITH '2016'`: five
  structured filters (H01, H02, H04, H05, H07) ran and returned nothing. Now each property shows its type
  and its examples as Cypher literals (`year (INTEGER) e.g. 2016, 2019`, `since (DATE) e.g.
  date('2015-06-30')`), the Cypher prompt has one rule to compare with the property's own type, and a
  parameter keeps a JSON number or boolean as such (`bool | int | float | str | list[str]`). Failing test
  first (`test_the_schema_lists_labels_with_examples_relationships_and_claim_patterns`), then the fix; two
  pure tests (parameter types, Cypher literals). Gate: 352 passed. The held-out graph answers of that run
  are superseded; the vector answers are unaffected (their prompt did not change).
- **Runs (2026-09-30; the user's yes for all, with DeepSeek, thinking `low`; estimate $1.3-2.9, spent $0.46).**
  The Step 0 graphs were rebuilt from the LLM cache, every call a cache hit and the claims byte-identical to
  R68's: held-out into `out/r71_heldout` (heldout preset: extract `65dd6b4e` 162/162, resolve `b2ce97b3`
  349/349 hits; rebuilt a second time after the tests wiped it), furniture into `out/r71_furniture`
  (quality_deepseek, the DeepSeek key set to an invalid value for the command so a miss would fail).
  | Run (MLflow) | Answers | Cost |
  |---|---|---|
  | held-out `qa_graph` `c8cffdd3` (before the fix, superseded; kept as `answers_graph_before_fix.jsonl`) | 38 | $0.130 |
  | held-out `qa_vector` `ad747b8b` | 38 | $0.056 |
  | held-out `qa_graph` `24172a31` (after the fix, the user's yes for the rerun) | 38 | $0.117 |
  | furniture `qa_graph` `24db0c14` | 38 | $0.115 |
  | furniture `qa_vector` `0cc6c6ac` | 38 | $0.029 |
  | generality `plan` `fa27a97b`: stopped, "schema is not connected, isolated groups: StaffMember; Pump" (R72) | - | $0.011 |
  The graph system averaged about 2.2 calls per question (router, Cypher with its retry, reader), the vector
  baseline one; DeepSeek thought 250-800 tokens per call here (graph about 600), against 3,900 in R66's
  extraction calls, which is why the estimate was high. The judge (Fable 5.1, a subagent; lead judge Opus 5.5) decided the 16 free-
  text answers with `tests/gold/r71/qa_judge_rules.md` (invented examples only) into
  `tests/gold/r71/{heldout,furniture}_{graph,vector}_verdicts.json`; the two cases it flagged (H20 without
  "2.0L", the vector H35 without "free of charge") were kept as correct by the lead judge: neither detail is
  what the question asks. Final scores by `kg qa-score` (no graph, no model): held-out `23cb2d3a` (graph),
  `23b1cb1f` (vector); furniture `bab5d60e` (graph), `7e344fad` (vector).
- **Results: answer accuracy per question type** (sets and numbers by code, free text by the judge; k/n and
  Wilson 95 %):

  | Type | held-out graph | held-out vector | furniture graph | furniture vector |
  |---|---|---|---|---|
  | multi_hop | 0.71 (5/7, 0.36-0.92) | 0.57 (4/7, 0.25-0.84) | 0.43 (3/7, 0.16-0.75) | 0.00 (0/7, 0.00-0.35) |
  | aggregation | 0.83 (5/6, 0.44-0.97) | 0.67 (4/6, 0.30-0.90) | 0.33 (2/6, 0.10-0.70) | 0.17 (1/6, 0.03-0.56) |
  | structured_filter | 1.00 (7/7, 0.65-1.00) | 0.71 (5/7, 0.36-0.92) | 0.50 (3/6, 0.19-0.81) | 0.00 (0/6, 0.00-0.39) |
  | disambiguation | 0.50 (3/6, 0.19-0.81) | 0.50 (3/6, 0.19-0.81) | 0.83 (5/6, 0.44-0.97) | 0.83 (5/6, 0.44-0.97) |
  | negation_sensitive | 0.67 (4/6, 0.30-0.90) | 0.33 (2/6, 0.10-0.70) | 0.57 (4/7, 0.25-0.84) | 0.43 (3/7, 0.16-0.75) |
  | lookup | 0.83 (5/6, 0.44-0.97) | 0.67 (4/6, 0.30-0.90) | 1.00 (6/6, 0.61-1.00) | 0.67 (4/6, 0.30-0.90) |
  | **all** | **0.76 (29/38, 0.61-0.87)** | **0.58 (22/38, 0.42-0.72)** | **0.61 (23/38, 0.45-0.74)** | **0.34 (13/38, 0.21-0.50)** |
  | recall@k, retrieval-route questions | 0.71 (10/14) | 0.86 (12/14) | 0.87 (13/15) | 0.60 (9/15) |
  | recall@k, all questions with chunk evidence | 0.64 (23/36) | 0.83 (30/36) | 0.30 (22/73) | 0.52 (38/73) |
  | citation faithfulness | 1.00 (28/28) | 1.00 (60/60) | 1.00 (30/30) | 1.00 (50/50) |
  | route accuracy | 0.61 (23/38) | - | 0.79 (30/38) | - |

- **Failures of the graph system by cause** (the first cause that fits; one real example each):
  - router sent a record question to text retrieval: held-out 1, furniture 7. F04 "What is the price in
    dollars of the product whose frame a reviewer says creaks whenever someone sits down?" was read from
    the reviews: "The chunks do not give a dollar price".
  - the query answered wrong: held-out 7, furniture 6. Negated claims counted (F26, F27, F28: "no
    squeaking or wobbling" counted as wobbling; the `assertion` gap of Step 4); a part of the question
    dropped (H15 lists all five Civic recalls, not the engine one; F19 ignores "preferred"); the graph's
    naming or shape missed (F08 wants "misalign" and "hole" in one name, the graph has the holes as the
    subject and "didn't line up properly" as the object); the answer's form (H09 "FORD ESCAPE 2015" is not
    the alias "2015 Ford Escape"; H31 returned whole nodes, which code wrote out as node text); a router
    choice of exact for a text question (H25, H26: two look-alike recalls told apart only by their text;
    H38 "When did recall 20V373000 begin?" answered 2020).
  - reader: answer form (H23 and F25 answer a "which" question in text: "The Gothenburg Table") 2; a
    Cypher refused twice, then retrieval (F14) 1.
  - names not linked, traversal missed, ranking cut: 0 of the graph's wrong answers once the route is
    counted first. The vector baseline's wrong answers: held-out 16 (5 without a gold chunk in its top 5,
    11 with one shown or answerable only from records), furniture 25 (5 and 20): its reader cannot count
    over the whole corpus or join records it never sees.
- **Reading.** The graph system answers more questions right than vector-only RAG on both datasets
  (held-out 29 against 22 of 38, furniture 23 against 13), and never fewer on any type; the gap comes from
  the exact route (structured filters 7/7 against 5/7 and 3/6 against 0/6; the furniture multi-hop record
  joins). The intervals overlap per type (6-7 questions each) and for the totals too (held-out 0.61-0.87
  against 0.42-0.72; furniture 0.45-0.74 against 0.21-0.50, nearly apart), so no difference is shown
  beyond one sample's variation yet; the direction is the same on both datasets and every type. The stop
  rule of the task (vector-only matching the graph on every type) does not apply.
  The graph's own retrieval reaches fewer gold chunks than vector search over all questions (0.64 against
  0.83, 0.30 against 0.52), because exact answers read no text; on the retrieval-route questions it is
  lower on held-out (10/14 against 12/14) and higher on furniture (13/15 against 9/15). Citations were
  always found in the cited chunk. The largest single cause is the router (8 misses), then negation in
  counts (3, Step 4's concern).
- Gate after the fix: 352 passed, `ruff check` clean. The generality corpus waits for R72.

### R72. Plans may keep unrelated tables apart, and the generality corpus's results (done 2026-09-30)
The generality corpus (R70) has two keyed tables from unrelated domains (`institute/staff.csv`,
`water/pumps.csv`); no foreign-key candidate joins them. The plan check (`structured/plan.py`
`_connectivity_issues`) and the proposer's prompt ("The schema must be one connected graph. Skip files that
are irrelevant to the goal.") demand one connected domain graph, so the plan was refused three times
("schema is not connected, isolated groups: StaffMember; Pump", run `fa27a97b`, $0.011). Both real datasets
happened to be connected. The user chose to fix it in its own step before building the corpus.
- Scope: a plan may hold several islands when no foreign-key candidate of the profile joins them; tables
  that a candidate joins must still be connected (the check keeps catching a forgotten relationship). The
  proposer's rule is reworded in domain-neutral words. Failing test first. The frozen plans of the real
  datasets are unaffected.
- Then, the user's yes given with the choice: the generality build (`generality` preset, the neutral goal,
  `EXTRACT_PASSES=2`) and `kg qa` on `tests/gold/qa/generality_qa.json` with both systems (estimate
  $0.25-0.6), judged as in R71, results added to R71's table.
- **Code (done 2026-09-30).** `_connectivity_issues` now groups the files that foreign-key candidates chain
  together (a link table included) and requires the labels of each such group to lie in one component of
  the plan; a group's issue names the keys that join it ("schema is not connected, isolated groups:
  Product; Supplier (joined in the data by assemblies.csv.product_id -> products.csv.product_id, ...)").
  Tables in different groups may stand apart. The proposer's rule reads "Connect every pair of tables that
  the foreign keys join, directly or through a link table; tables that no key joins may stay apart", the
  critic's "tables the keys join are connected" (prompt versions change; the frozen plans of the real
  datasets are not re-proposed). Tests first: unrelated tables may stand apart (failed before), and tables
  a link table joins must be joined, with the keys named (failed before on the message); the old test of an
  isolated supplier still passes. Gate: 354 passed, `ruff check` clean.
- **Runs (the user's yes with the choice of R72; estimate $0.25-0.6, spent $0.497 with R71's first plan
  attempt of $0.011).** `generality` preset (DeepSeek, `EXTRACT_PASSES=2`), goal "answer questions about the people,
  equipment, places and events these documents describe", into `out/r72_generality`.
  - The plan now passed the code checks, but the model's critic refused it in all three rounds (plan
    `a83d2cf9`, $0.020): it wanted `Station`, `Team` and event nodes, reading the goal's words "places" and
    "events", and called a plan without relationships "not a knowledge graph". The last proposal (Staff and
    Pump, no relationships) was accepted by the user as the reviewed plan with one edit: Pump's
    `name_column` "model" (both pumps are "Hydra P-40") set to null, so a pump is named by its serial number
    as the texts write it; "HP40-1183" itself counts as a code for `name_column` and is refused there.
    Committed as `tests/gold/generality_plan.json`.
  - The text schema's critic refused too, after three rounds ($0.150: DeepSeek thought 112k tokens in six
    calls). Its last open points: `HAS_COST` on a Project is outside the goal's words; the lab flume pump has
    no type. The user accepted the last proposal unchanged (Person, Role, Organization, Place, Pump,
    Equipment, Project, Event, Condition, Metric; 20 fact types), committed as
    `tests/gold/generality_text_schema.json`.
  - Build: 11 documents, 11 chunks; extract `b3ae982f` 107 claims, 7 rejected ($0.222, 22 calls);
    resolve `b4fb7dbe` 121 -> 112 entities ($0.022); link `313de8e9`: **0 of 11 documents** tied to a record
    (file-name matching; no file name holds a record's name), 4 entities linked.
  - `kg qa`: `qa_graph` `37bf4db8` $0.050, `qa_vector` `7e30dbb6` $0.022. The judge (Fable 5.1) found all 8
    free-text answers right in both systems (`tests/gold/r72/generality_{graph,vector}_verdicts.json`; the
    lead judge kept both flagged points). Scores `qa_score` `8c1f2fe7` (graph), `d27db3e4` (vector).

  | Type | generality graph | generality vector |
  |---|---|---|
  | multi_hop | 0.33 (1/3, 0.06-0.79) | 0.33 (1/3, 0.06-0.79) |
  | aggregation | 0.50 (1/2, 0.09-0.91) | 1.00 (2/2, 0.34-1.00) |
  | structured_filter | 0.00 (0/3, 0.00-0.56) | 0.67 (2/3, 0.21-0.94) |
  | disambiguation | 0.00 (0/4, 0.00-0.49) | 0.50 (2/4, 0.15-0.85) |
  | negation_sensitive | 0.00 (0/2, 0.00-0.66) | 1.00 (2/2, 0.34-1.00) |
  | lookup | 1.00 (7/7, 0.65-1.00) | 1.00 (7/7, 0.65-1.00) |
  | **all** | **0.43 (9/21, 0.24-0.63)** | **0.76 (16/21, 0.55-0.89)** |
  | recall@k, retrieval-route / all questions with chunk evidence | 0.93 (13/14) / 0.59 (16/27) | 1.00 (14/14) / 1.00 (27/27) |
  | citation faithfulness | 1.00 (17/17) | 1.00 (28/28) |
  | route accuracy | 0.86 (18/21) | - |

  - **Failures of the graph system:** the query answered wrong 7 (G14 walks `(core:Entity)-[:SUBJECT]->
    (o1:Observation)`, against the arrow; G12 asks the leak claim to be `polarity 'positive'` and to hold
    "March 2025" in its time; G07 needs text entities that refer to a pump record, and only 4 entities link
    at all; G21 counts ATTENDS claims filtered on the time text "12 May 2025" and finds none), the answer's
    name or form 3 (G01 "Soil Ecology team" for the gold's "Soil Ecology"; G03 and G10 answer in a sentence
    where a name is asked), the router sent a record question to text 2 (G02, G04). The vector baseline's 5
    misses are names and forms too ("Soil Ecology team" in G01 and G03, a sentence in G04) and two reader
    answers (G02 "Finance Office" for the role, G07 "the chunks do not state when any pump was installed",
    a record field it never sees).
  - **Reading.** On the synthetic three-domain corpus the graph system is worse than vector-only RAG
    (9 against 16 of 21), the opposite of the two real datasets. The retrieval route finds the text (13 of
    14 gold chunks), so the loss is the exact route on a graph whose documents hang on no record, whose
    schema a reviewer had to accept over the critic, and whose claims the text2cypher model misreads. This
    is the kind of result the corpus was built to show: the graph's advantage on the real datasets rests on
    their layout (documents named after their record, one connected domain). Step 3's linking rework
    (per-claim attachment, no file-name matching) is where this is addressed; it is recorded here, not
    tuned. The list answer "Soil Ecology team" (graph G01, G04; vector G01, G03) would be right with one
    gold alias: listed as a candidate gold correction, not applied (it was seen after the output).
- **R72 done.** Gate: 354 passed, `ruff check` clean.

### R73. The measuring instrument and the reference numbers (layered-model Step 3; in progress)
Every later step of the arm changes the graph or the query layer and is judged by `kg qa`; first the
instrument must be able to show a change, and the comparison must separate what the records give from
what the extracted claims give (task file, Step 3, revised 2026-10-05). Part a is $0; part b makes the
paid runs, asked first.
- **Split (the user's choice, 2026-10-05): part a in three commits, then part b.** a1: the instrument code
  (outcome rows, the paired test, the scoring decisions, record questions computed by DuckDB). a2: about
  30 record questions per real dataset, so structured filter, aggregation and multi-hop over records reach
  at least 10 questions each. a3: the generality corpus version 2 (about 20 distractor documents, their
  questions, and the changes they cause to the existing gold, written before any output and listed).
  b: records plus vector RAG as a third system, the builds and `kg qa` on all three systems.
- **Decisions taken before any new output (the user's, 2026-10-05).**
  - Set matching ignores word order: "FORD ESCAPE 2015" names the alias "2015 Ford Escape" (H09). The
    words themselves must all be there ("Ford Escape" is still another name).
  - An answer in another form than the gold's stays wrong: a sentence for a "which" question (F25 graph
    "The Gothenburg Table", H23 "NHTSA recall campaign 22V254000") is counted under the cause "answer
    form", not read for a name. Code cannot tell a sentence that names only the answer from one that names
    more; the query plans of Step 4 end in a `list` terminal, which is the fix.
  - The candidate gold alias "Soil Ecology team" (R72) stays listed and unapplied: it was seen after the
    output.
  - **Builder model: Gemini** (`gemini-3.8-flash`) for every build from here on; answering stays on
    DeepSeek (`deepseek-flash`), as in R71-R72. So part b rebuilds furniture (built by DeepSeek in R66) and
    builds the generality corpus version 2 on Gemini; the held-out graph (R66 part 3) is Gemini's already.
- **Part a1: the instrument (done 2026-10-05).**
  - `validation/qa.py`: `name_key` (`norm`, words sorted) is the form names are compared in; `QAOutcome`
    is one question's result for one system (id, type, system, correct or None while unjudged, the gold's
    route, the router's label, the cited chunk ids); `score_qa` returns one per gold question in the
    gold's order (`QAReport.outcomes`) and `load_outcomes` reads a file of them. The report file keeps
    only the totals.
  - `validation/paired.py` (new, pure): `mcnemar_exact` (two-sided exact binomial test on the discordant
    questions, p = 1 with none) and `compare_outcomes`: right in A only, right in B only and p, overall and
    per type, `ALPHA` 0.05 as the line for "beyond one sample's variation". Outcome files of other
    questions, of other types, or with an unjudged answer are refused (`EvaluationError`).
  - `validation/qa_records.py` (new): a gold question may carry `sql`, a DuckDB query over the corpus's
    source files, each `.csv`/`.json`/`.ndjson` a view named by its stem, read as it lies on disk (not
    through staging, so a staging bug cannot hide in the gold); a set is the query's first column, a
    number its one value. `check_record_answers` refuses a stored answer that differs from the data; the
    gold names a record as the data does (an alias does not stand in). A query counts as a question's
    evidence; it needs a set or a number answer.
  - `kg qa-score` writes `qa_outcomes_<system>.jsonl` (an artifact of its run); new `kg qa-compare A B`
    (MLflow run `qa_compare`: both files and hashes as params, the counts and p overall and per type as
    metrics, `qa_compare.json`). Neither reads the graph or calls a model; the run guard does not ask.
  - Cost per question is not in the outcome rows: it needs per-question usage from the LLM calls, which
    the task file puts in Step 4's tracking ("cost per question").
  - Tests: `tests/test_qa.py` (word order, the query field, outcome rows and their file),
    `tests/test_qa_paired.py` (p against hand-computed values, discordant counts overall and per type,
    refusals, `kg qa-compare` with a driver to nowhere), `tests/test_qa_records.py` (views from CSV, a
    wrapped JSON object and NDJSON in subfolders; a stem clash; sets, numbers, a join, an empty set, a
    failing query; a stale set, a stale number, an alias in place of the value), `tests/test_qa_gold_files.py`
    (every committed query's answer equals DuckDB's; none yet), `tests/test_query.py` (`kg qa-score` writes
    the outcome rows). Gate: 369 passed (354 before, Neo4j up), `ruff check` clean.
  - **R71-R72 re-scored under the word-order rule ($0; `kg qa-score` on the saved answers and verdicts,
    into `out/r73_<dataset>`).** Only H09 changes (held-out graph 29 -> 30 of 38); every other total is
    as in R71-R72. Runs `qa_score`: held-out `3d52a62b` (graph) / `2b59fde6` (vector), furniture
    `7fd82370` / `d6a251aa`, generality `31451de6` / `c7769e65`. The paired comparisons (`kg qa-compare`,
    graph = a, vector = b):

    | Dataset (`qa_compare` run) | graph | vector | right in graph only | right in vector only | p |
    |---|---|---|---|---|---|
    | held-out (`5cd466d2`) | 30/38 | 22/38 | 10 | 2 | 0.039 |
    | furniture (`88908265`) | 23/38 | 13/38 | 12 | 2 | 0.013 |
    | generality (`1dbf677f`) | 9/21 | 16/21 | 0 | 7 | 0.016 |

    Overall, each difference is beyond one sample's variation (R71 could only say the intervals overlap).
    No single question type is: the largest per-type splits are 3 to 0 (furniture multi-hop and
    structured filter, p 0.25) and 3 to 1 (held-out negation-sensitive, p 0.63). With 6-7 questions per
    type, a type can only show a difference at 6 or more discordant questions without a loss (p 0.031),
    which is why a2 adds record questions computed by code.

- **Part a2: record questions computed by code (done 2026-10-05).** 30 questions per real dataset, written
  by Claude (Opus 5.5) from the source tables before any output on them exists, each with a DuckDB query
  over the source files (`sql`); the expected answers were computed by `validation/qa_records.py` and every
  one reviewed against the tables (the "most" and "fewest" questions checked for ties: none). All are
  exact-route questions over records only; texts play no part.

  | Gold file | new questions | structured_filter | aggregation | multi_hop | total questions | sets / numbers / texts |
  |---|---|---|---|---|---|---|
  | `furniture_qa.json` | F39-F68 | 10 (6 -> 16) | 10 (6 -> 16) | 10 (7 -> 17) | 68 | 47 / 19 / 2 |
  | `heldout_qa.json` | H39-H68 | 10 (7 -> 17) | 10 (6 -> 16) | 10 (7 -> 17) | 68 | 47 / 15 / 6 |

  - Examples. Filters on text-typed fields: F40 "Which products cost between $200 and $250?" (prices are
    `$246` text), H44 "Which complaints were filed more than 30 days after the incident?" (11180230,
    11209676, 11229137; recall dates are day/month/year, complaint dates month/day/year). Comparisons
    inside a table: F48 "For which parts of the Helsingborg Dresser's drawers does the other supplier quote
    a shorter lead time than the preferred supplier?" (Drawer Front, Drawer Rails). Counts and ranks: F58
    "How many suppliers in the data supply no parts at all?" (4), H53 "In which year did NHTSA receive the
    most of these recall campaigns?" (2020: 6; 2016: 5). Joins: F62 the countries of the Uppsala Sofa's
    seat-cushion suppliers (six), H64 the complaints about a vehicle with a recall that carries an NHTSA
    action number (ten: the Escape's and the Rogue's).
  - Names are the data's own values (the check compares them with the query's rows); a vehicle model also
    carries "<make> <model>" and "<year> <make> <model>" as aliases, as the earlier held-out questions do.
  - The test of the real datasets' shape now asks 30-80 questions (it was 30-50) and at least ten of each
    record type. The answers files of R71-R72 cover only the first 38 questions, so their scores
    (R71-R72, a1's re-score) belong to the gold as of `c2d981f`; `kg qa-score` refuses them against this
    gold (questions without answers), as it should.
  - Gate: 369 passed (the new questions run inside the existing parametrised gold-file tests), `ruff
    check` clean. $0, no run.

- **Part a3: the generality corpus, version 2 (done 2026-10-05).** 21 distractor documents written by
  Claude (Opus 5.5), seven per domain, that name the corpus's people, pumps and places in other contexts;
  32 documents in all (one chunk each), so a reader's top 5 chunks no longer hold half of the corpus. Their
  questions and the one gold change they cause were written before any output on them exists.
  - **Distractors by trap.** Water: a June inspection where the other pump leaks (`inspection_2025-06-13`),
    a second seal replacement by another technician (`work_orders_2025-06`, Marek Hollis on HP40-2291),
    a Harbour Station report, the vendor's quotation with its own claim, June shift notes, a 2024 summary
    ("No seal failures were recorded in 2024."), an operator procedure. Institute: a second committee
    meeting with another chair (9 June, Aiko Tanaka; "J. Pike"), a second field log at another site with
    low-pH cores (Brackwater fen), the grants accountant's travel memo that names the other Maria Lopez,
    a conference report, flume notes, a second "Seminar notice", the award's history (2024 "not awarded",
    a nominee who did not win). News: the 2026 budget, a third Maria Lopez (a baker on Harbour Street), a
    flood meeting where Dr Jonathan Pike speaks, a letter by Judith Pike (not Jonathan), an open day at
    Harbour Station, the library's reopening, council notices.
  - **Layouts the chunker had not met:** entries separated by `---` (`work_orders_2025-06.md`,
    `council_notices.md`: short entries merge into one chunk, the separators dropped), two `.txt` logs
    without a heading, and a document that starts with "## Contents" before its title (see "Found along the
    way").
  - **20 new questions, G22-G41:** 4 disambiguation (G22 the Utrecht poster's Maria Lopez, of three; G24
    who replaced HP40-2291's seal; G31 the vendor's 18-month claim; G36 "Which Pike wrote ...?"), 4
    negation-sensitive (G23 June leaks, G27 cores taken at Brackwater, G32 award winners without the
    nominee, G37 seal failures in 2024: none), 3 multi-hop text -> record (G28 install year of the pump whose
    screen was cleaned, G29 team of the 9 June chair, G34 rated flow of the open-day pump), 3 aggregation
    (G25 seal replacements in 2025: 2, G30 meetings Jonathan Pike attended: 2, plus G40/G41 below), 2
    structured filters over text (G26 Brackwater cores below pH 4.0, G35 the 2026 budget), 2 lookups (G33
    the June finding on HP40-1183; G38 an obligation, a negative control) and 3 record questions with a
    DuckDB query (G39 pumps rated above 40 l/s, G40 total rated flow at North Station: 84, G41 Hydrology
    staff: 2).

    | Gold file | multi_hop | aggregation | structured_filter | disambiguation | negation_sensitive | lookup | total | exact / retrieval | sets / numbers / texts |
    |---|---|---|---|---|---|---|---|---|---|
    | `generality_qa.json` v1 (R70) | 3 | 2 | 3 | 4 | 2 | 7 | 21 | 9 / 12 | 10 / 3 / 8 |
    | `generality_qa.json` v2 (R73) | 6 | 6 | 6 | 8 | 6 | 9 | 41 | 23 / 18 | 21 / 10 / 10 |

  - **Gold change caused by version 2 (written before any output on it):** G05 "In how many documents does
    Jonathan Pike appear, under any form of his name?" 4 -> 8, with four new evidence quotes (the 9 June
    minutes, the award history, the conference report, the flood meeting); Judith Pike does not count. Every
    other earlier answer was re-checked against the new documents by the phrases it depends on ("safe to
    use", "ran dry", "10 December", "1 September", "(chair)", the 2025 budget lines, ...): none changes.
  - The test of the corpus's shape now asks 28-34 documents (it was 10-12). The three prompt guards read
    the new documents too: no rule quotes them. R72's generality answers cover only G01-G21, so they belong
    to the gold as of `cea8baa`.
  - Gate: 369 passed, `ruff check` clean. $0, no run. **Part a done**; part b (records plus vector RAG,
    the Gemini builds, `kg qa` on three systems) needs the user's yes with its cost estimate.

- **Part b, code: records plus vector RAG (done 2026-10-05).** The third system of the task file, until
  Step 4's query plans exist the Step 2 exact route with the claim layer left out. `kg qa` now asks three
  systems by default (`graph`, `vector`, `records_vector`), each in its own MLflow run.
  - `query/systems.py`: `build_records_vector` builds a `RoutedGraph` (now named per system, any retrieval
    system behind it) from the router and the exact route over the record layer, with `VectorBaseline` as
    its retrieval route; every answer carries the system's own name, also when the vector baseline answered.
  - `query/graph_schema.py`: `records_only` keeps the plan's labels, the relationships between two of them
    and no claim patterns (the claim heading is left out when there are none); `names_outside` gives the
    labels and relationship types such a query may not name (a type that also joins two record labels, as
    furniture's `PART_OF`, stays allowed).
  - `query/cypher_check.py`: `excluded_name_issues` refuses a query that names one of them after `:` or `|`,
    backticked or not; the refusal goes into the one retry like any other. A pattern with neither a label
    nor a type could still step into the text layer; the prompt never shows it and every query is kept in
    the trace, so the results check for it.
  - `query/exact.py`: the prompt is built from parts; `RECORDS_PROMPT` has one sentence on records in place
    of the paragraph on documents and claims. The graph system's prompt is byte-identical (prompt version
    `e2bc6e4bfe20` before and after), so its runs stay comparable.
  - Tests: `tests/test_query_exact.py` (five refused forms, record names and map values not mistaken, the
    record layer of a schema, a refused text-layer query retried without reaching the database, the
    system's name on fallback and retrieval answers, the corpus-quote guard on the new prompt),
    `tests/test_query_graph.py` (Neo4j: the record layer of a real graph; `kg qa` with `records_vector`
    end to end, a refused query, no text layer in any prompt). Gate: 381 passed (369 before), `ruff check`
    clean. No run.

- **Part b, runs (2026-10-05; the user's yes with the estimate $1.5-2.8; spent $1.93).** Builds on Gemini
  (`gemini-3.8-flash`), answers on DeepSeek (`deepseek-flash`, thinking low), frozen plans, `EXTRACT_PASSES=2`.

  | Run (MLflow) | Cost |
  |---|---|
  | furniture build into `out/r73b_furniture` (`quality`, fresh schema, goal "supply chain root cause analysis"): text_schema `fa7632f1` (accepted), extract `7b7a2e9c` (508 claims, 42 rejected), resolve `3b34c1b6` (483 -> 325 entities), link `3021a1e8` (10/10 documents, 31 entities linked) | $0.646 |
  | furniture `kg qa`: graph `609901dc`; vector `765bcc99` stopped by a Gemini embedding 429 (68 questions embedded in parallel), rerun `06f9eadc` with `QA_WORKERS=2`; records_vector `5bad69dc` | $0.315 |
  | held-out graph rebuilt from the cache into `out/r73b_heldout` (`heldout`): extract `ca08084c` 162/162 hits, resolve `1eb21451` 349/349 hits: the R68g/R71 graph (532 claims, 56 entities linked) | $0 |
  | held-out `kg qa` (`heldout_deepseek`): graph `aa05eda0` (84 cache hits: R71's answers to H01-H38), vector `309616ed`, records_vector `3b7bfc39` | $0.331 |
  | generality v2 build into `out/r73b_generality` (`generality_gemini`, new preset, goal as R72): text_schema `3b7eef40` refused in all three rounds (below), extract `89ac3fad` (195 claims, 9 rejected), resolve `77f6bf76`, link `12a6bab9` (0 of 32 documents tied to a record, 6 entities linked) | $0.436 |
  | generality `kg qa` (`generality`): graph `e0cd1862`, vector `a469be50`, records_vector `a0cf9631` | $0.201 |

  - **The generality text schema (the user's decision).** The proposer swung between `Staff` + `Person`
    (rounds 1 and 3) and one type named `Staff` (round 2); the critic refused both: two person types an
    extractor cannot tell apart when the text does not say who works where (its examples: Judith Pike,
    Gareth Lowe, Maria Lopez), and a `Staff` type that clashes with the plan's `Staff` records. The user
    chose the critic's own fix on the last proposal: one `Person` type, the fact types that had `Staff`
    merged into it (20 -> 18 fact types; `validate_text_schema` clean), committed as
    `tests/gold/generality_v2_text_schema.json`; Gemini's last proposal is kept as
    `out/r73b_generality/text_schema_proposed.json`. This is the identity question of Step 5.
  - **Judge.** Fable 5.1 subagents with R71's rules (`tests/gold/r71/qa_judge_rules.md`) decided the 54
    free-text answers (18 questions x 3 systems), lead judge Opus 5.5; verdicts in `tests/gold/r73/`. The
    five flagged points were kept as judged, following R71's precedent: H20 without "2.0L", H35 without
    "free of charge", F38 graph (both sides of the comfort question given), G16 without "of HP40-1183",
    G09's extra "the council voted" (true of the source). Final scores by `kg qa-score`: furniture
    `d384c4fa` / `150974c7` / `cd003718` (graph / records_vector / vector), held-out `a41a333c` /
    `320af79e` / `de8fa66b`, generality `2b9f0f64` / `72c69eab` / `4cc01e5f`.
  - **Reference table: answer accuracy per type** (sets and numbers by code, free text by the judge; k/n
    and Wilson 95 %; "rec+vec" is records plus vector RAG):

    | Type | held-out graph | held-out rec+vec | held-out vector | furniture graph | furniture rec+vec | furniture vector | generality graph | generality rec+vec | generality vector |
    |---|---|---|---|---|---|---|---|---|---|
    | multi_hop | 15/17 (0.66-0.97) | 13/17 (0.53-0.90) | 4/17 (0.10-0.47) | 10/17 (0.36-0.78) | 9/17 (0.31-0.74) | 0/17 (0.00-0.18) | 1/6 (0.03-0.56) | 1/6 (0.03-0.56) | 1/6 (0.03-0.56) |
    | aggregation | 16/16 (0.81-1.00) | 12/16 (0.51-0.90) | 4/16 (0.10-0.49) | 4/16 (0.10-0.49) | 6/16 (0.18-0.61) | 1/16 (0.01-0.28) | 3/6 (0.19-0.81) | 4/6 (0.30-0.90) | 3/6 (0.19-0.81) |
    | structured_filter | 17/17 (0.82-1.00) | 15/17 (0.66-0.97) | 6/17 (0.17-0.59) | 5/16 (0.14-0.56) | 7/16 (0.23-0.67) | 0/16 (0.00-0.19) | 0/6 (0.00-0.39) | 2/6 (0.10-0.70) | 4/6 (0.30-0.90) |
    | disambiguation | 3/6 (0.19-0.81) | 3/6 (0.19-0.81) | 3/6 (0.19-0.81) | 6/6 (0.61-1.00) | 5/6 (0.44-0.97) | 5/6 (0.44-0.97) | 3/8 (0.14-0.69) | 5/8 (0.31-0.86) | 5/8 (0.31-0.86) |
    | negation_sensitive | 4/6 (0.30-0.90) | 3/6 (0.19-0.81) | 4/6 (0.30-0.90) | 4/7 (0.25-0.84) | 3/7 (0.16-0.75) | 3/7 (0.16-0.75) | 2/6 (0.10-0.70) | 5/6 (0.44-0.97) | 6/6 (0.61-1.00) |
    | lookup | 5/6 (0.44-0.97) | 4/6 (0.30-0.90) | 4/6 (0.30-0.90) | 5/6 (0.44-0.97) | 4/6 (0.30-0.90) | 4/6 (0.30-0.90) | 8/9 (0.56-0.98) | 8/9 (0.56-0.98) | 8/9 (0.56-0.98) |
    | **all** | **60/68 (0.78-0.94)** | **50/68 (0.62-0.83)** | **25/68 (0.26-0.49)** | **34/68 (0.38-0.62)** | **34/68 (0.38-0.62)** | **13/68 (0.12-0.30)** | **17/41 (0.28-0.57)** | **25/41 (0.46-0.74)** | **27/41 (0.51-0.78)** |
    | route accuracy | 53/68 | 61/68 | - | 47/68 | 47/68 | - | 33/41 | 27/41 | - |

  - **Paired comparisons** (`kg qa-compare`; right only in the first system / only in the second, p):

    | Pair | held-out | furniture | generality |
    |---|---|---|---|
    | graph vs records + vector | 13 / 3, **p 0.021** (`2b67685e`) | 8 / 8, p 1.000 (`eb4ce493`) | 0 / 8, **p 0.008** (`adc1d970`) |
    | graph vs vector | 37 / 2, **p < 0.001** (`4233bccd`) | 23 / 2, **p < 0.001** (`656e6e4a`) | 2 / 12, **p 0.013** (`1b800e8c`) |
    | records + vector vs vector | 31 / 6, **p < 0.001** (`8416ccf8`) | 21 / 0, **p < 0.001** (`3f040d13`) | 3 / 5, p 0.727 (`46603cb1`) |

    Per type, graph against records + vector differs on no type in any dataset (largest splits: held-out
    aggregation 4 / 0, p 0.125; generality negation-sensitive 0 / 3, p 0.25). The record types separate
    both routed systems from the vector baseline (held-out multi-hop, aggregation and structured filter,
    p <= 0.04; furniture multi-hop, p <= 0.004).
  - **Failures by cause** (the first that fits, classified by code from the traces; one real example each):
    - router sent a record or count question to text: furniture graph 15, records + vector 16; generality
      4 / 7; held-out graph 1. F04 "What is the price in dollars of the product whose frame a reviewer says
      creaks ...?" -> "The chunks do not state the price".
    - exact route answered wrong: held-out graph 6, records + vector 16; furniture 13 / 12; generality
      15 / 5. Held-out records + vector H09 returned whole `Vehicle` nodes as names (R71's H31 again);
      generality graph G05 counted 5 documents for Jonathan Pike (gold 8).
    - exact route failed, then text: furniture graph 6, records + vector 3. The Gemini furniture graph stores
      prices as text (`$246`), so F14, F39, F40 ("cost less than / more than / between") and F50, F51 had
      no number to compare.
    - answer form (a sentence for a "which" question, wrong by the R73 decision): held-out graph 1, records
      + vector 2, vector 8; generality 4 / 2 / 4 (G24 "Technician Marek Hollis replaced ...").
    - vector baseline: record questions answered from text, furniture 32, held-out 28 ("The chunks do not
      state any prices"); retrieval missed the evidence chunk 5 / 4 / 1 (furniture / held-out / generality).
    - The records-only check refused no query: the model never named the text layer. Its refused
      proposals (furniture 11, held-out 7, generality 1) failed the other checks. Every records + vector
      query that ran used typed relationships from record-labelled nodes (checked over all answers).
  - **Reading.** Records plus vector RAG matches the claim graph on furniture (34 = 34, 8 / 8 discordant),
    beats it on the generality corpus (25 against 17, 0 / 8, p 0.008), and loses to it on held-out (50
    against 60, 13 / 3, p 0.021); no single type separates them anywhere. Both routed systems beat
    vector-only RAG on the two real datasets through the record questions. On generality, vector-only is
    best (27/41): its documents hang on no record (0 of 32) and the exact route misreads the claim shape
    (15 wrong exact answers for the graph, 5 for records + vector).
  - **Stop rule of Step 3:** "if records plus vector matches the observation-graph arm on every question
    type (no paired difference), stop and decide with the user before Step 4". No question type differs in
    any dataset, so the rule applies as written; the totals point both ways (held-out for the graph,
    generality against it). Step 4 waits for the user's decision. Gate: 381 passed, `ruff check` clean.

- **The user's decision on the stop rule (2026-10-05):** go on with Step 4 as planned. The largest causes of
  wrong answers sit in the query layer both routed systems share (the router, wrong queries), so the claim
  layer's worth cannot be read through it; the comparison of the graph with records plus vector is repeated
  once query plans exist (R74). **R73 done.**

### R74. Query plans of fixed primitives (layered-model Step 4; done 2026-10-05, code only)
The largest measured causes of wrong answers are the router and queries that misread the graph (R71, R73).
This step replaces free text2cypher and the router with query plans that code checks and compiles, before
any change to the graph's shape (task file, Step 4).
- **Split (2026-10-05): three parts, one commit each.** (a) The plan language: a flat `PlanStep` model (one
  `op` and optional fields, checked per operation in code), one class per primitive (Strategy) that checks a
  step against the graph's schema and compiles it to one parameterised, read-only Cypher fragment, and the
  executor that runs the steps in order. Primitives: `find_entity`, `filter_records`, `related`,
  `find_claims`, `read_check`, `retrieve_chunks`; terminals `list`, `count`, `sum`, `rank`,
  `answer_from_chunks`. The schema reader also shows relationship properties (furniture's lead time, cost
  and "preferred" live on `SUPPLIED_BY`, which R73's exact route never saw), and a numeric comparison on a
  text property compares the number in it (R73: furniture prices stored as `'$246'`). (b) The planner: one
  domain-neutral prompt that writes a plan, one retry with the reasons, then the logged fallbacks
  (text2cypher, then retrieval); `graph` and `records_vector` both answer through plans (records plus
  vector without the claim primitives); the router and `RoutedGraph` are removed; tracking of plans,
  refusals, dropped filters, `read_check` calls and the primitives each answer used. (c) Runs, answers
  only, on the R73 graphs, asked first; judge; paired comparison with R73 per question.
- Not in this step: any change to the graph's shape (Steps 5-7).
- **Part a: the plan language (done 2026-10-05).** New modules in `query/`, no model writes Cypher:
  - `plan.py`: `PlanStep` (one `op` and optional fields) and `QueryPlan`; `check_plan` checks each step by
    its own rule (`_RULES`, Strategy): names from the graph's schema (labels, properties, record
    relationships, claim predicates and entity types), the value against the property's type (a number, a
    `YYYY-MM-DD` date, a four-digit year, true/false; a number may be compared with a text that holds one),
    inputs that point back to a step producing items the step takes (record, entity, claim, chunk), and
    exactly the last step a terminal. An optional filter (tone, time) whose words are not in the question
    is removed from the plan and reported (G12). `PlanSchema(claims=False)` refuses `find_claims` and finds
    records only: the records-plus-vector system.
  - `plan_cypher.py`: one pure function per primitive returning `(cypher, parameters)`: comparisons by the
    property's type (`date()`, `.year`, a year among a text date's digits, the number inside a text via
    `apoc.text.regreplace`, case-insensitive text, any member of a list), `related` in a direction code
    reads from the schema, the claim layer written once (`HAS_OBSERVATION`, `SUBJECT`, `OBJECT`, `FROM`,
    `REFERS_TO`), so no plan can walk a claim backwards (G14), and a `LIMIT` on every step (`step_cap` 200).
  - `plan_run.py`: `PlanRunner` interprets a checked plan, ids passed between steps; `find_entity` through
    `NameLinker.find` (every node spelled alike, so both same-named records come back; the nearest in
    meaning only when nothing is spelled alike, or added for claim words); `find_claims` with
    `include_parts` (records whose relationships point at the input within two hops); `read_check` reads
    each candidate's own chunks (the three nearest the statement) and refuses more than `check_limit` 30
    candidates (`QueryPlanError`, new); terminals computed by code (`list` names, property values or a
    claim's record, subject or object; `count` of items, documents or records; `sum`; `rank` by property,
    by related records or by claims per record, ties kept); `answer_from_chunks` gives the top k chunks of
    the input (or of the system's chunk source) to the reader.
  - `read_check.py`: a domain-neutral prompt; `verify` keeps a yes only when its quote, under `norm`, is in
    the chunk it names, which was shown; no text, no call.
  - Also: `GraphSchema` reads relationship properties (types and examples) and shows them; `ReadResult`
    carries the column names; `NodeName` carries a record's label or an entity's type; `rank` moved from
    `systems.py` to `ranking.py`, behaviour unchanged.
  - Tests: `tests/test_query_plan.py` (30, pure: refusals with the known names, value types, inputs and
    terminals, the dropped filter, the records-only system, every comparison as text, values as
    parameters, the claim direction, read_check verification, find), `tests/test_query_plan_graph.py` (8,
    Neo4j: filter and related with the chosen direction, '$950' below 1000 and years in dates and texts, a
    relationship property shown and filtered, claims from a record, its parts and an entity, read_check
    verified and unverified and its bound, rank / sum / property lists, answer_from_chunks). Gate: 419
    passed (381 before), `ruff check` clean. No run.
- **Part b: the planner and the plan systems (done 2026-10-05).**
  - `query/planner.py`: one domain-neutral prompt (the graph's schema; the primitives; six rules, each tied in
    its comment to the R71-R73 failure it answers; one example from an invented graph of hives and
    apiaries) asking for a `QueryPlan`, and a retry text with the refused plan and the reasons.
  - `query/systems.py`: `PlanSystem` (Template for both plan systems): plan, `check_plan`, run; one retry
    with the reasons (a refusal or a `QueryPlanError`); then the logged text2cypher fallback; then reading
    the system's chunk source. `build_graph_system` (whole schema, every primitive, the graph's retrieval
    route as chunk source, the R71 text2cypher as fallback) and `build_records_vector` (record layer only,
    no claim primitives, records only for `find_entity`, vector search as chunk source, R73's records-only
    text2cypher as fallback). `VectorBaseline` and `GraphRetrieval` gained `ranked(question)`, their chunks
    without the reader, as chunk sources. **Removed:** `query/router.py`, `RoutedGraph`,
    `build_routed_graph` (the task file: "the router is removed"); `exact.py` stays as the logged fallback.
  - `query/answers.py`: `SystemAnswer.plan` (`PlanTrace`: every `PlanAttempt` with its plan, issues, dropped
    filters and steps; the fallback; read_check calls and verified candidates). Answers carry no route any
    more, so `route_accuracy` has n = 0 for every system.
  - `kg qa`: params `planner_prompt_version`, `read_check_prompt_version`, `cypher_prompt_version` and the new
    settings `qa_step_cap` 200, `qa_check_limit` 30, `qa_check_chunks` 3; metrics `plans_proposed`,
    `plans_refused`, `answers_retried`, `filters_dropped`, `read_check_calls`, `read_check_verified`,
    `fallback_text2cypher`, `fallback_retrieval`, `cypher_proposals`, `cypher_refused`, and
    `answers_using_<op>` per primitive; prompts logged as artifacts.
  - Tests: `tests/test_query_exact.py` (a checked plan runs and answers; a refused plan is retried with its
    reasons and never runs; a run failure is retried; text2cypher then reading as fallbacks, with the
    system's own name; the corpus-quote and domain-word guard on the planner, read_check and Cypher prompts
    and on the plan's field descriptions), `tests/test_query_graph.py` (Neo4j: `kg qa` end to end with a
    scripted planner: a reading plan, a refused then a counting plan, params and plan metrics; records plus
    vector refusing a claim plan and counting records). The three tests of the router and `RoutedGraph`
    were removed with them. Gate: 422 passed (419 after part a), `ruff check` clean. No run.
- **Part c: no runs (the user's decision, 2026-10-05).** The answering runs of the plan systems (estimate
  $1.4-2.8) were not made: the user judged that running them is not important for the thesis at this point.
  So the acceptance criteria that need answers are open, not met: the comparison with Step 3 per type
  (paired), the re-count of R71's router and wrong-query causes, and the repeat of the graph against
  records plus vector comparison that the stop-rule decision of R73 relied on. What is shown is what the
  tests show: every primitive compiled and run on a hand-made graph, G14's backwards walk and G12's
  invented filter impossible by construction, read_check counting only verified quotes, the retry and
  both fallbacks. **R74 done (code only).** Next: Step 5 (identity: mentions and canonical entities).

### R75. Identity: mentions and canonical entities (layered-model Step 5; done 2026-10-05, no kg qa)
Which real-world entity a name refers to, and when two names in two documents are the same entity (task
file, Step 5). Today an `:Entity` is one node per type and name across all documents, and resolution merges
nodes physically (`apoc.refactor.mergeNodes`, undone only from snapshots): "Maria Lopez" of two documents is
one node whether or not she is one person, and "J. Pike" never reaches "Jonathan Pike".
- **Split (2026-10-05): five parts, one commit each, in order.** The step holds four concerns (the schema's
  identity classes, the graph's new identity shape, joining individuals across documents, the identity
  gold) plus its runs, and the shape change cannot be cut smaller without leaving the tree half-migrated.
  - (a) Identity classes in the text schema ($0): every entity type declares `keyed` (with the plan labels
    its records carry and the key attributes that tell same-named records apart), `individual` or
    `concept`; code checks them against the plan; the proposer and critic know the rule; the prompt
    cleanups of the plan proposer and of the schema's field descriptions (furniture words).
  - (b1) Mentions and canonical entities ($0): `:Mention` per type, name and document replaces `:Entity`;
    each mention `REFERS_TO` one canonical entity (a record, an `:Individual` or a `:Concept`) with
    `{reason, score, evidence, by}`; records found by key, by name in scope, or by an attribute in the same
    sentence; concepts resolved as today but written as edges, so `undo_merges` and the snapshots are
    retired; every writer, reader, primitive compiler, traversal and the R65 gold questions moved to the
    new shape; the flattening reader keeps the triples of today.
  - (b2) One individual across documents and the new blocking rules ($0): name variants, and a join only
    with evidence (the same record, a matching attribute, or an LLM adjudication quoting a sentence of each
    document, verified by code); concept merges also blocked when the two names are different things in
    one sentence, or a part and its whole.
  - (c) Identity gold and its score ($0): mention pairs per dataset marked same or different with their
    evidence sentences, written before any output on the new shape; identity precision and recall in
    `kg eval`.
  - (d) Runs, asked first: builds with identity classes on the three datasets, `kg qa`, judge.
- **Part a: identity classes in the text schema (done 2026-10-05).** `text/schema.py`: `EntityType.identity`
  (`keyed` / `individual` / `concept`, default `concept`, so a schema from before R75 loads and keeps its
  rule), `record_labels` and `key_attributes` (keyed only); `FactType.part_of` (claims stating that the
  subject is one of the pieces the object is made of; read by part b2's part-whole block);
  `identity_issues` checks against the plan: a keyed type names at least one plan label, each key
  attribute is a column of one of them, other types name neither, and a type named like a plan label must
  be keyed (the prompt asks for that name only so that code can link). `validate_text_schema(schema, plan)`
  is the gate of the refine loop and of the stage's reload. The proposer has an identity rule and a
  part-of rule, the critic one question; the field descriptions' examples are invented (beekeeping), and
  "a named part belongs to the product" became "a thing the text names belongs to the thing the whole
  document is about". Prompt cleanup (generality cleanups): the plan proposer's `name_column` example
  ("Drawer Rails", "drawer_unit_subassembly") and the plan's field descriptions ("Product", "part_name",
  "SUPPLIED_BY") are invented too. Prompt versions of the plan and text-schema proposers change; no run,
  since the plans stay frozen and the schemas are rebuilt in part d. Tests: `tests/test_identity_classes.py`
  (14: old schemas read as concepts, each identity issue, no keyed type without a plan, the refine loop
  sends a wrong label back, no corpus word or 4-gram in either proposer, critic or response schema);
  the pipeline test's `Product` type is keyed now. Gate: 436 passed (422 before), `ruff check` clean.
- **Part b1: mentions and canonical entities (done 2026-10-05).** The graph's identity shape, every writer and
  reader moved to it; no run.
  - Shape: `text/subject_graph.py` writes `(:Mention {id, name, type, doc_id})` per type, name and document
    (`core.identity.mention_id`), `(Chunk)-[:MENTIONS]->(Mention)`, and observations whose `SUBJECT` and
    `OBJECT` point at mentions; a value's mention keeps its wording ("25kg"). `kg resolve` writes one
    `(:Mention)-[:REFERS_TO {canonical, name, kind, reason, score, evidence, by}]->(entity)` per mention,
    the entity a record, an `:Individual` or a `:Concept` (`resolution/identity_graph.py`). Readers take
    the canonical id and name from the edge (`graph/canonical.py`, the one place the shape is written in
    Cypher); a mention without an edge stands for itself.
  - Identity (`resolution/identity.py`): by the type's identity class. Keyed: `resolution/records.py` (key in
    the name; name in the scope of the document's thing, R11/R60/R67's rules moved from linking.py; a key
    in a sentence naming the mention; a key attribute in such a sentence when names tie), tied records not
    linked and logged as ambiguous; a keyed mention no record fits, and every individual-class mention,
    refers to an `:Individual` of its own (cross-document joining is b2). Concepts
    (`resolution/concepts.py`): one per type and normalised name (a value: its canonical spelling), the
    resolver's candidates, guards, blocking, passes and canonical choice unchanged, the outcome written as
    edges. The adjudication prompt now asks only "the same kind" (the item-or-kind choice of R41 is the
    schema's class now) and lost its furniture list ("a part, a defect or a symptom", generality cleanups).
  - Retired: `apoc.refactor.mergeNodes`, `snapshot`, `apply_merges`, `undo_merges`, `ResolveReport` and its
    snapshots; `kg resolve --undo` clears the identity layer (`clear_identity`). The graph keeps every
    observation; the fact reader (`validation/checks/base.flatten`) applies the two rules the merge used to
    apply to the graph: a self-reference is left out, and of exact repeats (same canonical ends, predicate,
    chunk, quote and time) the first by its own wording is read, so judge sheets keep their fact ids.
  - Stage order: `extract -> link -> resolve -> validate` (was resolve before link): records are matched in
    the scope of the things the documents are ABOUT, which `kg link` writes; derivation (link stage) now
    targets the document's own mention of the thing. Rebuild recipes change accordingly.
  - Readers: the fact reader, the ER sheet (one entity per canonical id, its mentions' names as aliases),
    the consistency and provenance checks, the query stage's node names (a record is also known by the
    names of its mentions: "2019 Subaru Outback" for OUTBACK), the traversal (a record's text includes the
    claims and chunks of the mentions referring to it, so `referred_records` is gone: three patterns), the
    plan compiler (entities by canonical id; `list what=subject` gives the canonical name), the schema
    reader (claim patterns from mentions; the identity edges' audit fields hidden) and the text2cypher
    prompt's description of the graph.
  - Metrics: `extract`: `mention_nodes` replaces `entities` (one per type, name and document, a new unit).
    `link`: the entity counts left with the matching. `resolve`: `mentions`, `mentions_to_records`,
    `mentions_to_individuals`, `mentions_to_concepts`, `mentions_ambiguous`, `records_referred`,
    `entities_linked` (distinct type and name among record-linked mentions: comparable with the link runs
    before R75), `linked_by_<reason>`, `individuals`, and the concept counts under their old names
    (`before`, `after`, `merges`, `passes`, `candidates`, `llm_adjudications`, `skipped_borderline`);
    params gain `domain_link_threshold`. `validate`: `mentions`, `entities` (canonical), `mentions_linked_to_
    records`, `self_references_hidden`.
  - Gold correction (the shape, made before any output on it): `tests/gold/r75/{furniture,heldout}_gold.json`
    are R65's files with `:Entity` read as `:Mention` in the questions, nothing else (a test checks it).
  - Decisions taken here (not fixed by the task file), for the user to confirm: a keyed type may name
    several plan labels (`record_labels`: furniture texts name assemblies and parts alike, and the linking
    before R75 matched every label in scope); a keyed mention that no record fits, or that several fit, is
    an individual of its own (the task: "no link, logged as ambiguous"); schemas without classes read every
    type as a concept, and a type named like a plan label must be keyed, so a frozen schema needs its
    classes added before a build.
  - Tests: `tests/test_identity.py` (6: the flattening rules; the attribute that finds S-219, the same name
    ambiguous and apart; one individual per document; one concept for two wordings of a number; the
    flattening reader keeps each claim's wording; the stage's params, metrics, audit file and undo; a keyed
    type naming a missing label refused), `tests/test_records.py` (12: the moved name rules plus key, key in
    a sentence, attribute and ambiguity), and every Neo4j test that hand-built `:Entity` graphs rewritten
    to mentions (resolution, derivation, linking, qualifiers, validation, ER, judge, coverage, query, R75
    questions). Gate: 447 passed (436 after part a), `ruff check` clean.
- **Part b2: one individual across documents and the new blocking rules (done 2026-10-05).** No run.
  - `resolution/variants.py`: name variants, pure and language-level: leading titles dropped ("Dr", "Prof",
    ...), the same last word, first words equal, an initial or a short form of at least three letters
    ("Jon" for "Jonathan"); middle words ignored; one word alone pairs with nothing. "J. Pike" fits both
    Jonathan and Judith, so a variant only nominates.
  - `resolution/records.py`: a name is also matched without its title ("Dr Jonathan Pike"), and rule 5,
    `variant_attribute`: when no name matches, the one variant-compatible record whose key attribute stands
    in a sentence naming the mention ("Dr. J. Pike (Soil Ecology)" -> S-131).
  - `resolution/individuals.py`: units (a record's mentions are one unit: the same record is evidence;
    every other keyed or individual mention alone), pairs nominated by variant, spelling (`er_borderline`)
    or meaning (the concepts' blocking rule over name embeddings), decided by one domain-neutral
    adjudication (`IDENTITY_PROMPT`, response `SameIndividual`) that must answer "the same" and quote one
    sentence of each side; code keeps the join only when each quote is in that side's own chunks and names
    that side (`quote_not_verified` otherwise), never puts two records in one group (`different_records`,
    two records are not even asked about), and leaves every pair apart without an LLM (`skipped`).
    `resolution/particulars.py` runs records then individuals and turns each group into assignments (its
    record, else an `:Individual` named after the fullest name of its most mentioned unit; a mention that
    joined says `adjudicated`, with the two quotes as evidence and the model as `by`).
  - `resolution/guards.py` (Strategy): `OpposedPolarity` (R66's rule, moved), `SameSentence` (both names at
    separate places of one sentence of the concepts' chunks; a name inside the other is no second thing),
    `PartAndWhole` (a claim of a fact type the schema marks `part_of`, either direction). The resolver drops
    a nominated pair a guard blocks and logs it once per pair over all passes.
  - Metrics of `resolve`: `individual_candidates`, `individual_joined`, `individual_apart`,
    `individual_quote_not_verified`, `individual_different_records`, `individual_skipped`,
    `blocked_opposed_polarity`, `blocked_same_sentence`, `blocked_part_and_whole`, `linked_by_variant_attribute`;
    param `individual_prompt_version`; both adjudication prompts logged as artifacts. `resolve.json` gains
    `individual_decisions` and `blocked`.
  - Fixed along the way, with a test that failed before: the shared sentence splitter (`core/text.py`) cut
    "Talk by Dr. J. Pike (Soil Ecology)" into three sentences, so no sentence named the person; a full stop
    after a capital initial or a form of address ends no sentence now (a line break still does). It also
    changes the quote a derived claim takes from such a sentence (Found along the way).
  - Tests: `tests/test_individuals.py` (20: variants and non-variants, titles, the titled and the variant
    record rules, nomination, the quote check, joining with refusals and the two-record guard, no LLM no
    join, the display name, the prompt's corpus-language guard), `tests/test_guards.py` (7), and in
    `tests/test_identity.py` (Neo4j, scripted LLM) the Pike case end to end (title, attribute, a verified
    join, a refused one, Judith apart) and a part kept from its whole only by the part-of flag. Gate: 477
    passed (447 after b1), `ruff check` clean.
- **Part c: identity gold and its score (done 2026-10-05).** Written from the corpus text before any build of
  the mention graph exists (none does yet); no output was opened.
  - Format (`validation/gold.py`): `identity_pairs`, each two sides (a document and the names it writes for
    one thing; a side may list spellings with and without a title), `same`, verbatim `evidence` (at least
    one quote per side) and a `note`.
  - Gold (`tests/gold/r75/`): furniture 67 pairs (27 same, 40 different; 26 across documents), held-out 23
    (10 / 13; 6), generality 24 (13 / 11; 23) in a new `generality_gold.json`. Furniture and held-out: the
    R35/R40 ER pairs placed at mention level by text search (a same pair inside one document where both
    names occur, across documents only for states and wordings, which are one kind wherever written; the
    three same pairs that name parts of two products, "drawer handle"/"drawer handles", "leg"/"legs",
    "drawer handles"/"drawer pulls", are left to `er_accuracy`, because whether they are one depends on the
    schema's class for parts), the evidence the first sentence naming each side; plus the recorded wrong
    merges: "drawer slides" (Linköping Bed) / "drawer rails" (Helsingborg Dresser) and TRANSMISSION BOX /
    TRANSMISSION different. Generality: Jonathan Pike under six spellings in seven documents the same,
    Judith Pike apart from every Pike, the Finance Office's and the Soil Ecology's Maria Lopez and the baker
    apart, each the same across her own documents, HP40-1183 and HP40-2291 apart and each one across its
    documents.
  - Labels that differ from the task file's wording, for the user to review: "M. Lopez" of the field log is
    labelled the same as the Soil Ecology Maria Lopez (the log and the newsletter describe her Aldmoor
    campaign, which QA gold G03 already relies on; the task file said "no evidence: stays apart", so a
    system that keeps them apart misses a join, it does not err); "BRAKE SUDDENLY" / "ACTIVATED THE
    BRAKES", listed in the task among the wrong merges of R63, is labelled the same kind, since ER pair 4
    (R51) already has "brake suddenly" = "braked on its own", the Rogue's event of "activated the brakes"
    (R63's harm was the shared kind reaching other vehicles, which R64 removed). Not expressible: the travel
    memo names two different Maria Lopez in one document, which is one mention (type, name, document); it
    is left out of the pairs and stated here.
  - Score (`validation/identity.py`, pure; `evaluate.read_mentions` reads each mention with its canonical
    id): a pair is joined when a mention of each side refers to one canonical entity; `identity_precision`
    (gold-same share of the joined pairs), `identity_recall` (joined share of the gold-same pairs),
    `identity_apart_rate` (kept-apart share of the gold-different pairs; the bar is 1.0), with
    `identity_pairs_scored`, `identity_same_scored`, `identity_different_scored` and
    `identity_not_extracted` (a side with no mention of a listed name). Exact lookup only; a judge mapping
    for unextracted names, as `er_accuracy_valid` has, is not built (Found along the way if part d needs it).
  - Tests: `tests/test_identity_gold.py` (9: names in their documents, quotes verbatim, a quote per side,
    no duplicate, the task's hard cases and recorded merges present, the score on hand-made mentions) and a
    `kg eval` run on the Pike graph in `tests/test_identity.py` (precision 1.0, recall 0.5, apart 1.0).
    Gate: 487 passed (477 after b2), `ruff check` clean.
- **Part d: runs (2026-10-05; the user chose the cached rebuild, estimate $0.2-0.4; spent $0.291).** The three
  R73b graphs rebuilt on the mention graph: the R73b frozen plans and text schemas with identity classes and
  part-of flags added (reviewed by the user before the run; committed as
  `tests/gold/r75/<dataset>_text_schema.json`; a script
  checked that every extraction prompt is byte-identical, and extraction ran with an invalid key, so a cache
  miss would have failed). Extraction: all cache hits, claims byte-identical to R73b (furniture 508, held-out
  532, generality 195). Classes: furniture Product keyed (Product), Component keyed (Component, Assembly),
  Material and QualityAspect concepts; held-out Vehicle keyed (Vehicle; make, model_year), Recall keyed,
  Component and Problem concepts; generality Person keyed (Staff; team, role), Pump keyed (Pump; model,
  station), Equipment concept, Place, Event and Organization individuals.
  - Two bugs found by the runs, each fixed in its own commit with a failing test first (Found along the way):
    a key anywhere in a listing sentence linked "Camry" to the RAV4 record (`77336a2`), and TRANSMISSION /
    TRANSMISSION BOX were joined again (`ce3c652`, the `CompoundName` guard). The numbers below are from
    the rebuild on `ce3c652`; the earlier resolve runs (`dd4e1a0c`, `4fb36808`, `ea90633b`) are not used.

  | | furniture `out/r75_furniture` | held-out `out/r75_heldout` | generality `out/r75_generality` |
  |---|---|---|---|
  | mentions: to records / individuals / concepts | 610: 47 / 79 / 484 | 510: 76 / 192 / 242 | 243: 39 / 139 / 65 |
  | concepts before -> after (merges) | 387 -> 273 (114) | 221 -> 192 (29) | 54 -> 54 (0) |
  | blocked: polarity / same sentence / part-whole / compound | 23 / 2 / 0 / 6 | 0 / 23 / 2 / 9 | 0 / 1 / 0 / 0 |
  | individual pairs: nominated / joined / apart / two records | 109 / 9 / 89 / 11 | 169 / 15 / 134 / 20 | 178 / 87 / 89 / 2 |
  | identity precision (exact) | 1.0 (n 13 joined) | 1.0 (n 4) | 1.0 (n 9) |
  | identity apart rate, the bar 1.0 (exact) | 1.0 (31/31) | 1.0 (10/10) | 1.0 (8/8) |
  | identity recall (exact) | 0.867 (13/15) | 0.667 (4/6) | 0.818 (9/11) |
  | identity pairs not extracted | 21 of 67 | 7 of 23 | 5 of 24 |
  | `er_accuracy` (exact, names) | 0.906 (48/53) | 0.929 (13/14) | no ER gold |
  | `entities_linked` (Step 0 baseline) | 40 (33; R73b 31) | 65 (56) | 12 |
  | `path_truth` | 1.000 (502/502) | 0.818 (507/620; the section-link artefact of R68, as in Step 0) | |
  | `question_accuracy` (R75 gold questions) | 0.167 (1/6, as Step 0: the schema renamed the predicates) | 1.000 (7/7) | |
  | MLflow: link / resolve / eval | `fb69fe18` / `f75cf07d` / `f68a203f` | `5c5e4832` / `caad5459` / `d603068f` | `28359903` / `c746df25` / `783a5fdc` |
  | `cost_usd` of this rebuild (resolve; all else $0) | $0.0002 (the first build's `dd4e1a0c`: $0.094) | $0 (the fixed build's `ea90633b`: $0.082; the discarded `4fb36808`: $0.042) | $0.073 |

  - Acceptance, one by one. Identity precision 1.0 on the different pairs: met on all three datasets.
    Recall stated above; every miss is a missed join, none a wrong one: furniture "drawer rails" / "metal
    rails" and "shelves" / "shelf" (never nominated or kept apart), held-out "brake suddenly" / "braked on
    its own" (one pair under two wordings, the kind joined across documents not made), generality Jon Pike
    and the field log's J. Pike kept apart from Jonathan Pike (the adjudicator found no evidence in their
    sentences). The recorded wrong merges: TRANSMISSION / TRANSMISSION BOX blocked (`compound_name`);
    "drawer slides" (bed) / "drawer rails" (dresser) apart (two products' parts, never joined);
    "BRAKE SUDDENLY" / "ACTIVATED THE BRAKES" apart (labelled the same kind in the gold, so a missed join).
    G02: both minutes' Maria Lopez refer to S-219 by attribute ("Maria Lopez (Finance Office)"), so the role
    is reachable in the record. G05: mentions in 5 of the 8 documents refer to S-131 (newsletter, award,
    seminar by `variant_attribute`, Utrecht, flood meeting); the two Pike mentions above stay apart and the
    9 June minutes' "J. Pike" was not extracted; a count over text goes through `read_check` (Step 4)
    anyway. Disambiguation and multi-hop against Step 4 (paired): not measured, no `kg qa` (the user's
    choice). Judge precision and recall of the claims against Step 0: not measured; the claims are R73b's
    own (cached), which no judge pass covered, and Step 0's graphs are other builds. `entities_linked` and
    `path_truth` are not below Step 0.
  - Review of the joins (lead judge Opus 5.5, a reading of every `joined` decision with its quotes, not a
    scored verdict file): 110 of 111 right (examples: Rosa Delgado in seven documents, "Mayor Achterberg" =
    "Helen Achterberg", KV12-0457 = "the Kestrel V-12 at Harbour Station", "Ford C-MAX" = "C-Max"); one
    questionable: furniture "dimmer" ~ "dimmer function" (the user's R38 decision keeps "dimmer switch" and
    "dimmer function" apart), accepted because the quote check counts "dimmer" inside "dimmer function"
    (Found along the way).
  - **R75 done** (code, gold and the cached rebuild). Open by the user's choice: `kg qa` on the new graphs
    and the paired comparison with Steps 3-4. Next: Step 6 (attachment).
- **Decisions confirmed by the user (2026-10-05, after part d).**
  1. A keyed type may cover several compatible plan labels, provided the mapping is explicit: it is the
     type's `record_labels` in the reviewed text schema, checked against the plan (`identity_issues`), and
     record matching looks only at those labels; nothing is inferred at run time. Furniture's Component
     covering Component and Assembly records stands.
  2. The field log's "M. Lopez" is the Soil Ecology Maria Lopez, by the shared Aldmoor campaign evidence
     (the log is the Aldmoor bog field log of winter 2024-25; the newsletter credits her with the Aldmoor
     campaign over two winters), not by the name, which fits every Maria Lopez. The task file's outdated
     expectation ("no evidence: stays apart") was corrected; gold and QA gold G03 now agree.
  3. "BRAKE SUDDENLY" / "ACTIVATED THE BRAKES" are one kind, unintended sudden braking, and only that: the
     gold keeps "brake" / "left rear brake" and "did not stop" / "braked on its own" apart.
  Gold labels unchanged; the two pairs' notes in `tests/gold/r75/generality_gold.json` and
  `heldout_gold.json` now state the evidence and the scope (a note-only correction, made after the run and
  listed here).

### R76. Attachment: which entity each claim is about (layered-model Step 6; done 2026-10-05, no kg qa)
A claim's grammatical subject ("mechanical seal") and the thing it is about (pump HP40-1183) are two things
(task file, Step 6). Until R76 the second came only from the document's ABOUT link, which needed a file
name to name a thing: the generality corpus's documents hung on nothing (R75 link run `28359903`: 0 of 32
documents linked, 0 observations attached).
- **Split: two parts, one commit each.** (a) The attach stage, the routes, path truth and the readers ($0,
  no run). (b) Runs, asked first: the three R75 graphs rebuilt from the caches with `kg attach` ($0), then
  `kg qa` on the three datasets and the paired comparison with Step 5 (estimate $0.5-1.0).
- **Decision (the user, 2026-10-05):** the most specific route wins **per kind of thing** (a record's label,
  an individual's type), not over all things: the task's literal rule would have taken the vehicle from
  every held-out claim with a section record, against R67's decision that such claims hang on both.
- **Part a: the attach stage (done 2026-10-05).** No run.
  - `resolution/attachment.py` (new): `(record or :Individual)-[:HAS_OBSERVATION {name, how, evidence}]->
    (Observation)`, decided by code from the text, the extractor unchanged. Routes, most specific first:
    `key_in_sentence` (a name of a record or individual that a mention of the claim's own document refers
    to, the mention's wording, the record's key or the canonical name, stands as whole words in the quote;
    names under three characters are not looked for), `part_of` (a claim of the same document, of a fact
    type marked `part_of`, has the claim's subject mention as its part and the thing as its whole),
    `section` (the chunk is ABOUT the record, R67), `document` (the document is ABOUT it). Precedence per
    kind, as decided; a thing found by several routes keeps the most specific one; a quote naming two
    pumps attaches to both. Concepts never hold claims (R62's lesson). The text route for documents: a
    document `kg link` leaves about nothing is made `ABOUT {how: 'text', evidence}` the record its sentences
    name in at least 2 and at least twice as many as the next record ("named in 2 of 6 sentences; the
    next record in 1"); individuals are no candidates.
  - Stage order: `extract -> link -> resolve -> attach -> validate` (two routes read the identity edges);
    `kg attach` is new, `kg link` no longer attaches, so its metric `observations_attached` moves to the
    attach run. The identity stage's scope ignores the text ABOUT links (`mentions.read_mentions`), so a
    rerun of `kg resolve` decides the same whether or not `kg attach` ran before it.
  - Metrics of `attach`: `observations_total`, `observations_attached`, `attachments`,
    `attached_<route>` (edges per route), `documents_about_by_text`, `documents_about_nothing`.
  - Path truth (`validation/paths.py`), a third shape: when the graph's edges carry a route, a path is true
    when its evidence holds in the graph as it is (the name in the quote; a part-of claim of the same
    document with the claim's subject and the thing as whole; the chunk ABOUT the thing; the document ABOUT
    it). The observation graph's rule is logged next to it as `path_truth_about`; it is below 1.0 by
    design wherever a claim hangs on what its quote or section names rather than on the document's thing
    (held-out was already 0.818 in Step 0 for that reason). So the task's "both `path_truth` numbers 1.0"
    cannot hold for the old rule; it is read as `path_truth` 1.0: a `document` path is true by both rules
    alike, so then every miss of the old rule is on another route, by design. The fact reader and the
    judge sheet carry `attachments` and `sections`, so `kg rescore` gives the same number; graphs and
    sheets from before R76 keep their old rule.
  - Readers: a query plan's `find_claims` about an entity and the `entity` item text also follow
    HAS_OBSERVATION from an `:Individual`, and so does the traversal's kind pattern; records already did.
    The schema text hides `how` with the other audit fields, so no prompt input changes.
    `TextSchema.is_part_of` is new.
  - Tests: `tests/test_attachment.py` (7: each route, the precedence per kind, a claim naming two things,
    the text route with its two bounds; with Neo4j an incident report under a neutral file name through
    link, identity and attach: the seal's failure on the pump (G07's shape), the other pump replacing the
    document's, a place named in a quote holding a claim it is no end of, the scope ignoring the text link,
    idempotence, path truth 9 of 9 and 6 of 9 by the old rule, and a query plan reaching a claim through the
    individual it hangs on); `tests/test_paths.py` (+4: each route true, each false when its evidence is
    gone, `path_truth_about` only for an attached graph, rescore carrying the attachments); the linking,
    derivation and pipeline tests moved to the new order (`attach` is a tracked stage of `kg run`). Gate:
    500 passed (489 before), `ruff check` clean.
- **Part b: runs (2026-10-05; the user chose the cached rebuild with `kg attach` and `kg eval`, no `kg qa`;
  spent $0.0137).** The three R75 graphs rebuilt on the R75 recipe (frozen plans and
  `tests/gold/r75/<dataset>_text_schema.json`, `EXTRACT_PASSES=2`, extraction with an invalid key so a cache
  miss would fail), then `kg attach` and `kg eval` with the R75 gold. Three rounds, all reported:
  1. on `e799864`, without `EXTRACT_PASSES=2` (my recipe error): one-pass graphs, not Step 5's (furniture 458
     facts against 508); not used. Its resolve runs adjudicated the new one-pass names: **$0.0137** (2376
     calls, 2328 cache hits; tokens 10117 in / 771 out / 851 thinking).
  2. on `e799864` with two passes: furniture identical to R75; held-out stopped at a 429 on the (uncached)
     embedding call of resolve, a per-minute limit after three quick builds, not the change. Its generality
     run showed the multi-line quote bug (Found along the way), fixed in `e3abb02` with a failing test first.
  3. on `e3abb02`, all three, a minute apart: **$0** (1155 calls, all cache hits). The numbers below.

  | | furniture `out/r76_furniture` | held-out `out/r76_heldout` | generality `out/r76_generality` |
  |---|---|---|---|
  | claims attached (Step 5: by `kg link`) | 668 of 668 (668) | 663 of 663 (663) | **177 of 195 (0)** |
  | edges: key_in_sentence / part_of / section / document | 617 / 378 / 0 / 268 | 1729 / 145 / 575 / 183 | 393 / 2 / 0 / 7 |
  | documents ABOUT a thing: by `kg link` / by text / none | 10 / 0 / 0 | 34 / 0 / 0 | **0 / 4 / 28** |
  | `path_truth` (new rule) | 1.000 | 1.000 | 1.000 |
  | `path_truth_about` (the R64 rule; Step 5 in brackets) | 0.606 (1.000) | 0.224 (0.818) | 0.062 (no paths) |
  | identity precision / recall / apart, `er_accuracy`, `question_accuracy` | as R75: 1.0 / 0.867 / 1.0, 0.906, 0.167 | as R75: 1.0 / 0.667 / 1.0, 0.929, 1.000 | as R75: 1.0 / 0.818 / 1.0 |
  | MLflow attach / eval | `c574c4b0` / `0a0c54dc` | `2a4bc30e` / `1f019acb` | `0e2c9e6e` / `1244229e` |

  - The generality corpus, real examples: the incident report is ABOUT HP40-1183 by its text ("named in 2
    of 6 sentences; the next record in 1"), as are the June shift notes and work orders about HP40-2291 and
    the travel memo about Maria Lopez (a memo naming two people of that name, one mention since R75: the
    link is to the record that mention refers to). `key_in_sentence`: "Maria Lopez PARTICIPATED_IN peat
    conference" on Maria Lopez's record and on the Soil Ecology organisation, both named in "The trip of
    Maria Lopez (Soil Ecology) to the peat conference in Utrecht ...". `part_of`: "intake screen
    LOCATED_AT Harbour Station" on pump KV12-0457, by the same document's claim that the screen is part of
    it. `document`: "flooding of the North Station dry well LOCATED_AT North Station dry well" on
    HP40-1183. The 18 unattached claims name nothing in their sentence: core measurements ("Core AB-17, 3
    December: depth 4.2 m", the bog named only in the log's heading), and "Delgado tagged it for a seal
    replacement" (a surname alone and "it": no mention's name).
  - G07 ("Which pumps installed before 2015 had a seal failure?"): reachable, not answered (no `kg qa`). The
    extractor wrote no failure claim; it wrote "mechanical seal COMPONENT_OF HP40-1183" from "On 29 March
    2025 the mechanical seal of pump HP40-1183 failed during the night shift.", which now hangs on the pump
    (`key_in_sentence`), and the incident document is ABOUT the pump, so `find_claims` and the traversal
    from HP40-1183 reach that chunk; the reader has to read "failed" there (or `read_check`, Step 4).
  - Real datasets: every claim was attached before and is now; what changed is how. Furniture claims hang on
    the product through their part (`part_of` 378) and on the part's own record (Assembly 178, Component 22
    by name); held-out claims on their section's complaint or recall (575) and on the vehicles their
    sentence names. The old rule's drop (furniture 1.000 to 0.606, held-out 0.818 to 0.224) counts exactly
    these: every path it misses is on another route than `document` (`path_truth` 1.0).
  - Acceptance, one by one: unit tests per route, the precedence, two things, a neutral file name: met
    (part a, and the fix's two tests). Generality attached, count per route against 0 before: met (above).
    G07: reachable, reason stated. "No type worse than Step 5": not measured, no `kg qa` (the user's
    choice, as in Steps 4-5). Both `path_truth` numbers 1.0: the new rule 1.0 on all three; the old rule
    cannot be (part a), and its misses are all non-document routes. Gate: 502 passed, `ruff check` clean.
  - **R76 done** (code, the fix and the cached rebuild). Open by the user's choice: `kg qa` and the paired
    comparison with Step 5. Next: Step 7 (assertion).

### R77. Assertion: truth, modality and condition (layered-model Step 7; in progress)
"The pump failed", "the pump may fail" and "if pressure rises, the pump will fail" are different facts, and
a count over claims must tell them apart (task file, Step 7).
- **Decisions (the user, 2026-10-05, before any code):**
  - **Scope: truth (affirmed / negated), modality (actual / possible / conditional) and condition only.**
  - **The speaker (`said_by`, `SAID_BY`) is dropped from Step 7 and becomes a Step 8 candidate.** R68
    attributed 1 of 83 coverage misses to it; only 3 of 177 QA questions depend on it (G10, G11, G31);
    `read_check` covers it at query time; and the extraction prompt change and its weak code check cost
    more than they give. If it is ever needed, start with a cheap `reported` flag.
  - **`valid_time` is deferred with it** (the task allows splitting both off).
- **Asked before any code:** run `kg qa` (graph plan system) on the R76 graphs as the Step 6 baseline
  (estimate $0.7-1.4)? Steps 4-6 have no answer measurement yet. **The user: yes, run it first**
  (2026-10-05). First attempt stopped at `kg ingest-text` of furniture, $0: embeddings are not cached, and
  the invalid Gemini key meant to catch extraction cache misses was set for every stage (my recipe error;
  R76 set it for `extract` only).
- **Step 6 baseline (2026-10-05; the user's yes, estimate $0.7-1.4; spent $1.219).** The three R76 graphs
  rebuilt from the cache on `ccaa258` (R76 recipe, invalid Gemini key on `extract` only; all cache hits,
  $0; every number as R76: furniture 508 claims, 668 attached, routes 617/378/0/268; held-out 532, 663;
  generality 195, 177 of 195 attached; `path_truth` 1.0, identity as R75), then `kg qa --system graph`
  (DeepSeek, as R73b). The tree was dirty only by the user's `.claude/settings.json` permission rule.

  | Run (MLflow) | Cost | LLM calls (cache hits) | tokens in / out / thinking |
  |---|---|---|---|
  | furniture `qa_graph` `2c734fe6` | $0.511 | 140 (5) | 422170 / 9298 / 311235 |
  | held-out `qa_graph` `67de89f3` | $0.375 | 130 (15) | 407345 / 9018 / 201373 |
  | generality `qa_graph` `eed56f5f` | $0.333 | 126 (38) | 274841 / 7121 / 201706 |

  - **Judge.** A Fable 5.1 subagent with R71's rules decided the 18 free-text answers (10 correct); lead
    judge Opus 5.5. Verdicts in `tests/gold/r77/`. Two flagged points kept as judged, as R73 did for the
    same questions: H35 without "free of charge", G16 without the pump id. Scores `kg qa-score`: furniture
    `f36e1e14`, held-out `f671a6a7`, generality `669c480a`; paired with R73b's graph (`kg qa-compare`):
    `e979c9b2`, `ff6ceb51`, `90558ea4`.
  - **Answer accuracy, graph system, Step 3 (R73b) against Step 6 (R76); right only in Step 3 / only in
    Step 6, McNemar p:**

    | Type | furniture | held-out | generality |
    |---|---|---|---|
    | multi_hop | 10 -> 9 /17 (5 / 4, p 1.0) | 15 -> 13 /17 (4 / 2, p 0.69) | 1 -> 1 /6 |
    | aggregation | 4 -> 6 /16 (2 / 4, p 0.69) | 16 -> 12 /16 (4 / 0, p 0.125) | 3 -> 2 /6 (1 / 0) |
    | structured_filter | 5 -> **12** /16 (1 / 8, **p 0.039**) | 17 -> 13 /17 (4 / 0, p 0.125) | 0 -> 1 /6 (0 / 1) |
    | disambiguation | 6 -> 2 /6 (4 / 0, p 0.125) | 3 -> 3 /6 | 3 -> 0 /8 (3 / 0, p 0.25) |
    | negation_sensitive | 4 -> 3 /7 (3 / 2, p 1.0) | 4 -> 4 /6 (1 / 1) | 2 -> 1 /6 (1 / 0) |
    | lookup | 5 -> 3 /6 (2 / 0, p 0.5) | 5 -> 3 /6 (2 / 0, p 0.5) | 8 -> 5 /9 (3 / 0, p 0.25) |
    | **all** | 34 -> 35 /68 (17 / 18, p 1.0) | **60 -> 48 /68 (15 / 3, p 0.008)** | **17 -> 10 /41 (8 / 1, p 0.039)** |

  - **Where the 40 lost answers fail** (classified by code from the plan traces; the first empty step):
    `find_claims` found no claim 13 (F09, F20, F21, F23, F24, F32, H01, H37, G06, G08, G21, G23, G36);
    the plan ran and gave a wrong result 13 (e.g. H08 names ROGUE but not CIVIC; H12 counts 4 for 2);
    a number answered as a list value 6 (F63 "$289" for 289, H33 "2361", H53 four dates for the year 2020);
    `filter_records` or `related` empty 5 (F47, F59, F67, F65, H14); `read_check` verified nothing 3 (F30,
    H43, G11). Real examples: G06 asks for "mechanical seal" as a claim's object, the graph has it as the
    subject ("seal COMPONENT_OF ... on 31 March 2025"), and "seal" and "mechanical seal" are two concepts;
    G08 and G11 end in "No text was retrieved" because an empty `find_claims` leaves the final reader with
    no chunks, where R73b's retrieval route answered both. Most failures are in the query layer (Step 4,
    R74, never measured), not in the graph changes of Steps 5-6; a run of R74's code on the R73b graphs
    would separate the two (not made).
  - **Stop rule (task file section 4) applies:** held-out and generality lost accuracy beyond one sample's
    variation (overall paired p 0.008 and 0.039; no single type below 0.05 on its own). Step 7's code is
    not started; the next step is the user's decision.
- **Paused (2026-10-05, the user's choice):** the query layer is fixed first, as R78; Step 7's code
  resumes after R78 is done. R78 done: 7 answers gained, none lost; held-out still below Step 3 (p 0.021).

### R78. Query plans that come up empty (before R77's code; done 2026-10-05)
The R77 baseline traced 40 answers lost since Step 3 to the query plans of R74. The user chose to fix the
query layer before Step 7. One concern: a plan step whose search finds nothing must not turn into a wrong
answer when the text holds one. Prompts are unchanged, so the planner's calls come from the cache and a
rerun compares the same plans executed differently.
- **Scope** (`query/plan_run.py`, `query/plan_cypher.py`, tests; no prompt, no schema, no graph change):
  1. `answer_from_chunks` (and `retrieve_chunks`) whose input has no text reads the system's chunk source,
     where today the reader gets nothing and answers "No text was retrieved" (G08, G11, H37).
  2. `find_claims` whose claim words match nothing on the end the planner chose matches them on either end
     before giving up (G06: "mechanical seal" asked as an object, stored as the subject). The claims stay
     candidates that `read_check` decides.
  3. A property `list` of one value that is a number also gives that number (F63 "$289" for 289, H33, H65).
- **Not changed:** an empty step is still a valid "none": four of the five gold questions whose answer is
  none or 0 are answered right through one (G37 `find_claims` 0, F29 and H30 `read_check` 0, H54
  `filter_records` 0), so no general "empty step -> fall back" rule.
- **Runs:** `kg qa --system graph` on the three R76 graphs (estimate about $1.2, less with planner cache
  hits), asked first; the judge on the free-text answers; paired with R77's baseline and R73b.
- **Part a: code (done 2026-10-05, no run).** `plan_cypher.find_claims(..., either_end=True)` matches the
  subject and object entities on `SUBJECT|OBJECT`; `PlanRunner._find_claims` runs it only when the strict
  query found nothing and claim words were given (step note "claim words on either end");
  `_chunks_for` returns the chunk source's top k when the input has no text (note "the input had no text:
  the chunk source"), for `answer_from_chunks` and `retrieve_chunks`; a property `list` of one value sets
  `number` to `as_number` of it. Tests (`tests/test_query_plan_graph.py`, Neo4j), each failing before the
  change: a reader after an empty `find_claims` reads the source's chunk; "wobbles" asked as a subject
  finds the claim whose object it is, and asked as the object finds it without widening; one listed
  price gives 1200.0, two years give no number. Gate: 505 passed, `ruff check` clean.
- **Part b: runs (2026-10-05; the user's yes, estimate $0.4-1.2; spent $0.172, of it $0.166 void).** The three
  R76 graphs rebuilt from the cache into `out/r78_<dataset>` (all cache hits, every count as R76), then
  `kg qa --system graph` on `25467d3` (tree dirty only by the user's `.claude/settings.json`).
  - **Void run, my error:** the first generality run (`ab80bbf6`, **$0.166**, 75 calls, 0 cache hits) asked
    the 8-node graph the Neo4j tests leave behind: `uv run pytest` ran after the generality build and I did
    not rebuild (the run-policy rule). Its answers were discarded; the graph was rebuilt and the run repeated.
  - Valid runs: furniture `56ec2d10` $0.0032 (150 calls, 142 hits), held-out `e45eb773` $0.0009 (132, 130),
    generality `3b3d5bd9` $0.0024 (133, 128): the planner's calls and every unchanged read came from the
    cache, so the plans are R77's and only the execution differs. Widening fired 23 times, the chunk source
    14 times.
  - **Judge:** 15 of 18 free-text answers are word for word R77's and keep R77's verdict (marked in the
    reason); the 3 changed ones (G08, G11, H37) judged by a Fable 5.1 subagent with R71's rules: all
    correct, none flagged. Verdicts in `tests/gold/r78/`. Scores `kg qa-score`: furniture `29147acc`,
    held-out `0e4ec62c`, generality `8996bf91`; paired (`kg qa-compare`) with R77: `5c527fad`, `c957ef35`,
    `45261473`; with R73b: `57703931`, `9998bc5a`, `4854280d`.

    | Type | furniture R73b / R77 / **R78** | held-out R73b / R77 / **R78** | generality R73b / R77 / **R78** |
    |---|---|---|---|
    | multi_hop | 10 / 9 / **10** /17 | 15 / 13 / **14** /17 | 1 / 1 / **1** /6 |
    | aggregation | 4 / 6 / **6** /16 | 16 / 12 / **12** /16 | 3 / 2 / **2** /6 |
    | structured_filter | 5 / 12 / **12** /16 | 17 / 13 / **13** /17 | 0 / 1 / **2** /6 |
    | disambiguation | 6 / 2 / **2** /6 | 3 / 3 / **3** /6 | 3 / 0 / **1** /8 |
    | negation_sensitive | 4 / 3 / **3** /7 | 4 / 4 / **4** /6 | 2 / 1 / **1** /6 |
    | lookup | 5 / 3 / **3** /6 | 5 / 3 / **4** /6 | 8 / 5 / **7** /9 |
    | **all** | 34 / 35 / **36** /68 | 60 / 48 / **50** /68 | 17 / 10 / **14** /41 |
    | paired R77 vs R78 (only R77 / only R78, p) | 0 / 1, p 1.0 | 0 / 2, p 0.5 | 0 / 4, p 0.125 |
    | paired R73b vs R78 | 16 / 18, p 0.864 | **13 / 3, p 0.021** | 5 / 2, p 0.453 |

  - Gained, none lost: F63 (the single price as a number), H65 (the year as a number), H37, G08, G11 (the
    reader read the chunk source instead of "No text was retrieved"), G06 ("mechanical seal" on either
    end) and **G07** ("Which pumps installed before 2015 had a seal failure?", reachable since R76, now
    answered). The five gold "none" answers stay right.
  - **Still lost against Step 3:** held-out 13 (H01, H06, H08, H12, H14, H17, H19, H28, H33, H43, H44, H53,
    H55: mostly record questions R73b's free text2cypher answered and whose plans now pick a wrong
    primitive, e.g. H01 asks claims for what is a recall's record field, H53 lists four dates for a year),
    furniture 16 (with 18 gained, as in R77), generality 5. These are the planner's choices; changing them
    is a prompt or plan-check change, not part of this step.
  - **Stop rule (task file section 4) still applies to the arm:** held-out stays below Step 3 beyond one
    sample's variation (p 0.021). R78 itself loses nothing against R77. The next step is the user's choice.
  - **R78 done.** Acceptance: the three fixes tested (each test failing before); answers measured, 7
    gained and none lost against R77; the "none" answers kept. Gate: 505 passed, `ruff check` clean.

### R79. Plans over records: per-label counts, the most frequent value, record fields first (in progress)
After R78, held-out stays below Step 3 (50 against 60 of 68, p 0.021). Its 13 lost answers, read from the
plans (not from guesses): the user chose a planner step before Step 7 (2026-10-05). One concern: a plan over
records must be able to say what the question asks of the records.
- **Scope** (`query/plan.py`, `query/plan_cypher.py`, `query/plan_run.py`, `query/planner.py`, tests):
  1. `list` and `count` of claims by what they are about take an optional `label`: only the records of that
     label (H12 counted 2 complaints and their 2 vehicles as 4; H28 2 for 1; H06 named a vehicle next to
     the complaint). Code checks the label is a record label.
  2. `rank` by a property with order "most" / "fewest": the value the most or fewest input records share,
     by the year of a date with operator "year" (H53 "in which year ... the most", H55 "which component
     appears in the most"); ties all reported, as rank does today.
  3. A `list` or `rank` answer of one value that is a number also gives that number (H33 listed "2361", a
     claim's object; R78 did this for property lists only).
  4. One planner rule: a thing the question names that a record's text property holds (its examples show
     such words) is filtered on that property with "contains", not searched among claims (H01, H43).
     No code check is possible: code cannot tell which words a question means as a field value; the
     measurement is the check.
  The prompt's primitive list and the plan's field descriptions change with 1, 2 and 4 (prompt-engineering
  skill: no dataset word; the example stays the invented hives).
- **Not in scope:** comparing two properties of one record (H44 "filed more than 30 days after"), read_check
  judgements (H08, H17), and H19's mixed-label list; noted for Step 8.
- **Runs:** the planner prompt changes, so every planner call misses the cache: `kg qa --system graph` on
  the three graphs costs about the R77 baseline ($1.0-1.3), asked first; judge; paired with R78 and R73b.
- **Part a: code and prompt (done 2026-10-05, no run).** `plan_cypher.claims_about(ids, label)` and the new
  `value_groups(ids, prop, by_year)`; `PlanRunner`: `list`/`count` "about" pass `step.label`, `rank` by a
  property with most/fewest ranks the values (`_value_scores`; the other ranks moved unchanged into
  `_record_scores`), and `_one_number` sets the number for any one-value `list` or `rank` (R78's
  property-list rule folded in). `check_plan`: the label must be a record label; rank's operator is none or
  "year", and "year" needs a date property. Prompt: the `list`, `count` and `rank` lines and one rule
  (field filter with "contains"); field descriptions of `label`, `operator`, `order`. Swept for dataset
  words: none in the prompt or the response schema. Tests: three Neo4j tests (per-label count and list; the
  most shared value, its year, a tie; one numeric claim object), each failing before; two check tests. One
  R74 assertion changed on purpose: rank by a property with "most" was refused, now it is the new rank
  (stated in the test). Gate: 510 passed, `ruff check` clean.

## Found along the way

(Add items here during a step instead of widening its scope.)

- **`planner_prompt_version` hashes the template only (found in R79).** The plan's field descriptions reach
  the model too (the response schema), but a change to them alone leaves the hash unchanged. In R79 the
  template changed as well, so its runs are told apart; a later step that changes descriptions only must
  add them to the hash first.
- **(Fixed in R76 part b, its own commit.) A name on another line of a quote held the claim (found in
  R76's generality run).** The extractor quotes several lines of a line-based note as one quote, and
  `key_in_sentence` searched the whole quote: "Tomasz Wren LOCATED_AT North Station", quoted from the shift
  notes' "Mon: HP40-1183 on duty ... Wed: Tomasz Wren visited ...", hung on pump HP40-1183. Path truth could
  not see it (the name is in the quote). Now the route and path truth read only the quote's sentences that
  name the claim's own subject or object (`core.text.claim_sentences`; the whole quote when none does).
- **A sentence listing several records attaches the claim to each (R76, as the task allows).** The recall
  quote "recalling certain 2020 Toyota Avalon Hybrid, Camry, Camry Hybrid and Lexus ES300h ..." hangs the
  claim "20V064000 AFFECTS_VEHICLE Camry" on the Lexus ES300h too. A candidate rule, for the user: within
  `key_in_sentence`, when one of the claim's own ends refers to a thing of a kind, other names of that kind
  in the sentence are context, not things the claim is about.
- **Keyed mentions identity could not link hold many claims (R76 held-out).** 1651 of held-out's 2782
  attachment edges go to individuals of the keyed type Vehicle ("THE VEHICLE", "MY CAR" of one complaint
  document), which stand apart from the document's vehicle record; they never displace the record (another
  kind), but they are noise for a query about vehicles as individuals. A matter for identity (Step 5's
  scope rule), not attachment.

- **Documents attached by their text get no derived claims (found in R76).** Derivation runs in `kg link`,
  before identity, so the derived fact types (furniture's `PART_OF` Product, held-out's `INSTALLED_IN`
  Vehicle) are written only for documents a file name or a record links; the text route comes later. No
  real dataset has such a document today (furniture 10 of 10, held-out 34 of 34 linked by `kg link`), and
  the generality schema derives nothing.
- **`kg link` alone leaves the attachments stale (R76).** It deletes the text ABOUT links but not the
  HAS_OBSERVATION edges, which `kg attach` recomputes; path truth reports such a graph (a `document` path
  without its ABOUT link), as it reported a stale link before.
- **The text2cypher prompt still says "the record a claim is about has [:HAS_OBSERVATION]" (R76).** Since
  R76 an individual may hold claims too. Left unchanged so that no prompt changes in a step without a
  measured comparison; text2cypher is only a logged fallback since R74.
- **The quote check counts a name inside the other side's longer name (found in R75 part d).** The furniture
  adjudicator joined "dimmer" and "dimmer function" (Örebro Lamp) with two quotes that both say "dimmer
  function"; `individuals.verified` accepts a quote for "dimmer" because "dimmer" is a substring of it. The
  user's R38 decision keeps "dimmer switch" and "dimmer function" apart. Candidates: the `CompoundName`
  guard also for pairs of individuals (it blocks this pair), and a quote names a side only where the name is
  not part of the other side's longer name. The one questionable join of 111.
- **The task's G05 count rests on mentions the extractor does not write (found in R75 part d).** The 9 June
  minutes' "J. Pike" and the field log's "M. Lopez" are no Person mentions in the cached R73b claims, so no
  identity rule can reach them; joining is bounded by extraction.

- **(Fixed in R75 part d, its own commit.) TRANSMISSION and TRANSMISSION BOX were joined again (found in
  R75's held-out run).** The recorded wrong merge of R63, the task file's own example of "a part and the
  whole it belongs to": no part-of claim joins the two, so `PartAndWhole` did not fire, and the adjudicator
  (Gemini) answered "the same kind" (resolve run `ea90633b`, identity apart rate 0.9: the one wrong pair).
  New guard `CompoundName`: one name is the other plus one word at its end ("transmission box" is a box),
  which a rule of English word order decides; a word added in front ("weighted base") and a phrase added at
  the end ("small dent on one edge") stay allowed. Checked before the change against every furniture and
  held-out gold pair: it touches only this pair. Failing test first
  (`test_a_name_with_one_more_word_at_its_end_names_another_thing`).

- **(Fixed in R75 part d, its own commit.) A key elsewhere in a sentence linked the wrong record (found in
  R75's held-out run).** Rule 3 of `resolution/records.py` linked a keyed mention to the one record whose key
  stood anywhere in a sentence naming it. Recall texts list many models in one sentence ("Toyota is recalling
  certain 2017-2019 Toyota Camry, Corolla, Rav4, Sienna, and Yaris iA vehicles"), so 156 vehicle mentions
  were linked by it, "Camry" to RAV4 and "2015 MKC" to ESCAPE among them (resolve run `4fb36808`, not used
  for any number). The key must now stand right next to the name ("pump HP40-1183", "the vehicle (RAV4)");
  failing test first (`test_a_key_elsewhere_in_a_listing_sentence_is_no_evidence`).

- **(Fixed in R75 b2.) The sentence splitter cut names at their initials (found in R75).** `core/text.py`
  split after every ". ", so "Talk by Dr. J. Pike (Soil Ecology)" was three sentences and no sentence named
  "Dr. J. Pike": the identity stage could not read the attribute next to it. A full stop after a capital
  initial or a form of address no longer ends a sentence. Side effect, unmeasured: a derived claim whose
  sentence holds such an abbreviation quotes the longer sentence now, and the adjudication context lines of
  such sentences change (new LLM calls). The coverage sample has its own splitter and is unchanged.

- **(Fixed with R75 b1's rewrite of the check.) The schema check refused every number (found in R75).**
  "consistency: every entity type is in the schema" compared entity types with `schema.entity_names()`,
  which leaves out the built-in `Value` (R66), so every graph with a number claim failed it. The mention
  check counts `Value` as known; no test covered the old failure.
- **Derived claims now carry their mention's own wording (R75 b1).** Before R75 a derived claim's subject
  was the merged entity's canonical name ("drawer rails PART_OF Linköping Bed" for the bed's "drawer
  slides", found in R64); derivation now runs before identity and uses the mention's name, which fixes
  that finding, but the derived fact ids of merged parts change, so verdicts of earlier runs carry over to
  those facts by rule only (as in R68).
- **Old gold files still query the pre-R64 or pre-R75 shapes.** `tests/gold/text_gold.json` (its R43/R46
  tests in test_validation.py build the edge graph of R62) and `tests/gold/r65` (`:Entity`) are kept as the
  record of their runs; the current questions are `tests/gold/r75`. Candidate for Step 9: remove the tests
  that exercise a shape no build writes any more.

- **A document that opens with a contents list gets "Contents" as its context (found in R73 a3).**
  `text/chunking.document_context` takes the first markdown heading, so `water/isolation_procedure.md`
  ("## Contents" before "# Isolating a pump at North Station") gives every chunk the context "Contents",
  and the extractor sees that as what the document is about. Not fixed in R73 (no output shows a failure
  yet); if the version-2 build or its answers fail on it, it is its own concern, as the task file says.
- **The model's critics do not accept a plan or schema for a three-domain corpus (found in R72).** Both
  refine loops ran their three rounds and refused: the plan critic asked for nodes the goal's words
  suggest ("places" -> Station, "events" -> event nodes) and called a plan without relationships "not a
  knowledge graph"; the schema critic kept finding new gaps each round. The user accepted the last
  proposals as reviewed. Candidates: the critic's checklist excludes "promote a property to a node" unless
  a question needs it; or a round limit that returns the last code-valid proposal for review instead of
  failing. The goal text was not changed after seeing this.
- **A serial number counts as a code and cannot be a name (found in R72).** `_CODE_LIKE` refuses
  "HP40-1183" as a `name_column`, yet the texts name the pumps only by it. Workaround in R72: `name_column`
  null, so the name falls back to the key. Candidate: a key column the texts write may be the name.
- **Candidate gold correction: "Soil Ecology team" (found in R72).** G01, G03, G04 expect "Soil Ecology"
  (the staff record's team); four list answers say "Soil Ecology team" (graph G01, G04; vector G01, G03).
  An alias would count them; not applied, as it was seen after the output (evaluation skill).

- **The router sends record questions to text (found in R71).** Route accuracy 0.61 (held-out) and 0.79
  (furniture); 8 of the graph's wrong answers were record questions read from text (F04 "What is the price
  in dollars of the product whose frame ... creaks ...?"). Candidate: the router sees which question words
  name record fields; measured against route accuracy before any prompt change.
- **Exact answers return nodes or joined names (found in R71).** H31's query returned whole `:Vehicle`
  nodes, which `rows_to_answer` writes as node text; H09 returned "FORD ESCAPE 2015" where the gold alias
  is "2015 Ford Escape" (the set rule compares names under `norm`, word order included). Candidates: a node
  value is read as its display name (the plan's name property); and whether set matching should ignore
  word order is a scoring decision to take before the next runs, not after seeing them.
- **The reader answers a "which" question in text (found in R71).** H23 ("NHTSA recall campaign
  22V254000") and F25 ("The Gothenburg Table") are right in substance and scored wrong by form, in both
  systems alike. Candidate: code reads a lone name out of a text answer only when the gold asks for a set;
  or leave it, as it costs both systems the same.

- **(Decided in R71: both numbers are reported.) Recall@k rests on few chunks (found in R70).** Only retrieval-route questions count, and they cite 15
  (furniture), 14 (held-out) and 14 (generality) evidence chunks, so a rate of 10 of 15 has a Wilson
  interval of 0.42-0.85. The questions with any chunk evidence cite 73, 36 and 27. Candidate for Step 2:
  report recall@k over every question with chunk evidence next to the retrieval-route number (the vector
  baseline answers every question by retrieval anyway), decided before the first `kg qa` run.

- **Star-rating polarity is unstable; it follows from the number (found in R66 part 2).** 11 of the 12
  furniture polarity errors are `HAS_RATING` "4/5" or "2/5" tagged neutral while the same rating in another
  review is tagged positive or negative. Candidate: code sets the polarity of a rating out of a maximum.
- **Circular causes (found in R66 part 2).** "AIR BAGS CAUSES AIR BAGS FAILED TO DEPLOY", "SPEEDOMETER CAUSES
  SPEEDOMETER FAILED": the component is the failing thing, not a cause. Candidate: `verify` rejects a
  CAUSES whose object name contains its subject name (a closed, domain-neutral rule).
- **The held-out schema has no part-vehicle relation (found in R66 part 2).** 15 of the 26 held-out recall
  misses. The schema prompt asks for every kind of claim; a problem-centred proposal still left parts
  unplaced. Candidate: a derived `Component <in> Vehicle` like R58's, or the R67 linking.
- **Gold questions filter on predicate names (found in R66 part 2).** A proposed schema renames them
  (`HAS_CONDITION` for `HAS_DEFECT`), and 5 of 6 furniture questions answer nothing. Candidate: questions
  filter on polarity and entity names, not predicates (a gold correction, to be listed).
- **Derivation mistargets record documents (found in R67 part 3).** A derived fact's object is the node
  the document is ABOUT; for a record document that is the record itself, so all 38 derived recall claims
  say "part PART_OF_VEHICLE 16V526000" with the campaign number as a Vehicle-typed entity (judge: 0/38
  true). Candidate: on a record document, derivation targets the record's related domain node (the
  recall's `AFFECTS_VEHICLE` vehicle), or skips the document. Same root cause: these entities are created
  after `link_graphs` ran, so they carry no REFERS_TO. Still present in R68: in the held-out coverage
  sample alone 33 derived `INSTALLED_IN` facts point at a recall id typed as a Vehicle
  (`ENGINE INSTALLED_IN 15V436000`).
- **Two recall documents extracted zero facts (found in R67 part 3).** `16V074000` and `17V472000`: 2
  well-formed chunks each, 0 triples proposed and 0 rejected in both passes (Gemini returned empty lists).
  Candidate: a repair pass for documents with 0 facts, or accept as model variance and measure its rate.
  In R68, on R66's schema, all 29 recall documents have claims, these two included.
- **R67's held-out graph used the R52 text schema, not R66's (found in R68).** `out/heldout/text_schema.json`
  is byte-identical to `tests/gold/heldout_nhtsa_text_schema.json` (R52: `Complaint REPORTS_PROBLEM
  Problem`, `INVOLVES_COMPONENT`, derived `PART_OF_VEHICLE`), and every claim of R67's judge sheet fits only
  that schema, while the R67 entry says "frozen R66 plan and text schema". The plan is the frozen one.
  R67's numbers stand, but they are not on the schema of R66 part 3 (Gemini, `INSTALLED_IN`); the
  layered-model baseline table must name the schema of each graph.
- **An embedding API error ends a command with a traceback (found in R68).** `GeminiClient.embed` calls
  the SDK without the retry loop and without turning the error into `LLMUnavailableError`, so the 402
  (credits depleted) during `resolve` printed the SDK's traceback instead of the CLI's one-line error,
  and the failed calls were not counted (`embed_calls` 0 on both failed runs). Candidate: `embed` goes
  through the same retry and error wrapping as `generate`; test first with a failing fake.
- **`path_truth` does not know section links (found in R68).** A path counts as true only when the
  observation's document is ABOUT the thing (`validation/paths.py`), but since R67 a claim also hangs on
  the record its section is ABOUT. On the R68 held-out graph all 113 "false" paths (of 622) are complaint
  claims on their own Complaint, e.g. `RUSTED AFFECTS_COMPONENT REAR WHEEL WELLS` on `10667633`, from the
  section `complaints/ford_escape_complaints.md#0`, which is ABOUT that complaint; R67's graph has 153 of
  360. Candidate: a path is also true when the observation's chunk is ABOUT the thing; test first.
- **A derived fact changes its id when a later document renames its entity (found in R68).** A derived
  fact has no wording of its own, so its id comes from the resolved names. Adding the recall texts renamed
  the held-out vehicles ("2015 FORD ESCAPE" -> "Escape", R44's shortest name): 28 derived `INSTALLED_IN`
  ids changed, their verdicts had to be carried over by a rule, and exact match lost 14 gold triples.
  Candidate: a derived fact's id from its subject's own wording and the thing's key, and exact match on
  aliases.
- **A maker's own recall number becomes a second Recall (found in R68).** The text of `22V254000` calls it
  "22S25" (the maker's number), and two claims have `22S25` as their Recall, so one recall is two
  entities. Candidate: the layered arm's identity step (records by key).

- **(Fixed in its own commit, R66 part 2.) `kg rescore` failed on number claims (found in R66 part 2).** The
  judge sheet shows a claim's own wording ("30-35 hardcover books", "25kg"); the `Value` entity is named
  canonically ("30 -35 hardcover books", "25 kg"), and numbers are never merged, which is where other
  entities gain their other spellings as aliases. So the sheet's entity list had no name for the wording,
  and rescoring the R66 furniture sheet stopped with "name ... is on no entity of the sheet". Fix: a Value
  entity keeps every wording it was written from as an alias (`text/subject_graph.py`); failing test first
  (`test_a_number_written_two_ways_can_be_rescored_from_its_judge_sheet`).

- **(Fixed in R66.) The corpus-quote test of the extraction prompt stopped at the first rule naming
  `<document>` (found in R66).** It split the rules at the first "<document>", which a rule itself
  contains, so the evidence rule, the closing rule and R66's new rules were never checked. It now splits
  at the `<document>` block and asserts that the last rule is in view.

- **A derived part claim takes the merged kind's name (found in R64).** The Linköping Bed review praises
  its "drawer slides"; resolution merged them into the "drawer rails" kind (the audit called this merge
  wrong), so the derived observation reads "drawer rails PART_OF Linköping Bed". No defect leaks any more,
  but the part name is another review's. Candidates: the polarity guard of R66 (praised slides and
  defective rails would not merge), or a derived claim worded with the name its own chunk uses.

- **Pass 2 names an entity "IT" (found in R61).** The prompt forbids pronouns, but nothing in code checks
  it. Candidate: `verify` rejects a subject or object that is only a pronoun (a closed word list, so it
  stays domain-neutral). That would remove 2 of the 12 held-out unsupported facts.
- **Pass 2 types instructions as a physical Component (found in R61, as in R55).** The type description
  excludes them, and the extractor does not follow it.
- **(Closed by R65: questions walk the thing's observations.) Questions 3 and 5 join through an entity's mentions, not the fact's own chunk (found in R58).** Entities
  are kinds shared across documents, so the Uppsala Sofa's "frame creaks" answers "which products squeak or
  creak" for the Linköping Bed too: the Linköping reviews mention a frame. Question 4 was fixed for this
  in R43/R44 (`f.chunk_id`). Fix the other questions the same way in their own step. That is a gold
  correction, to be listed.
- **Text vehicles never link to the Vehicle domain node (found in R58).** `entities_linked` fell from 25 to
  0 on the held-out data, because the 25 links were Complaint entities. The fuzzy match (`token_sort_ratio`
  ≥ 90) does not match "2019 Subaru Outback" to "OUTBACK". R60's containment rule (whole words, within
  the document's `ABOUT` scope) is the candidate for linking too.
- **`AFFECTS_VEHICLE` duplicates part-level problems (found in R58).** The held-out extractor states many
  problems both on the part and on the vehicle. These are true, but redundant, because
  part `INSTALLED_IN` vehicle already carries them to the vehicle. Candidate: an extraction or schema rule
  that keeps the vehicle-level fact only when the text names no part.

- **"The type documents are ABOUT" does not find the record type (found in R57, 2026-09-24).** On NHTSA the
  documents are `ABOUT` the `Vehicle`. Each complaint is a `## Complaint <number>` section inside a document,
  and the extractor's `Complaint` entities are named after those headings. A code rule built on `ABOUT`
  would point at the vehicle and miss the complaint hub. Whether a type is a *source* (a complaint, a
  report, a post) or a *subject* (a part, a product) is a question of meaning. The candidate for R58 is a
  prompt and critic rule (R56's "do not route a fact through the source a text comes from", which was never
  tested where a record type exists), measured on both datasets. A code rule built on section headings is
  not a candidate: furniture reviews name the product in their headings, and product facts are right there.

- **The schema proposer mirrors the plan's record nodes (found in R55, 2026-09-24).** With the whole text
  in view, the held-out proposer still made `Complaint` the hub of every problem fact, as it did from 12
  chunks; on furniture, where the plan has no record-like node, the proposal is as good as the pinned
  schema. The domain summary lists the plan's labels as existing types, which invites reusing them.
  Candidate, measured on furniture first: tell the proposer that plan labels are for linking, and that a
  fact type should connect what the text states (a problem to the part it is in), plus a critic check
  that every problem-like type can reach a component-like type.
- **Expressible is not extracted (R55).** The held-out whole-text schema can express 3 of the gold's
  causes; the extractor stated none of them (R54's pinned schema: 3 of 5). Worth a look at how fact-type
  descriptions steer the extractor.

- **The vehicle exists twice in the text graph (found in R54, 2026-09-24).** Derived `PART_OF` facts point
  at an entity named after the domain node ("OUTBACK", the plan's `name_column` `model`), the extractor
  names the vehicle "2019 Subaru Outback"; resolution does not merge them (different spellings, and
  derivation runs after resolution). Questions are unaffected (they go through the document's ABOUT
  node), but a query from a part's vehicle entity to its problems misses the vehicle-level facts.
  Candidate: derivation looks up an existing entity of the object type that REFERS_TO the ABOUT node.
- **The automatic arm's schema costs about 9 recall triples on the held-out data (R54).** Measured, not
  tuned: a schema-critic check that a problem type can reach a component type is the candidate from R53,
  to be tested on the development data first.

- **The automatic arm's schema decides where a problem lives (found in R53, 2026-09-24).** The proposed
  schema offers both `Complaint REPORTS_PROBLEM Problem` and `Problem OCCURS_IN_COMPONENT Component`; the
  extractor often chose only the first, so 11 gold claims lost their part (all of the Civic ACC / LKAS
  complaint). A root-cause question that goes from a problem to its part then fails although both are in
  the graph (joined only through the complaint). Not tuned on the held-out data (frozen pipeline).
  Candidates for a later, measured step on the development data: a schema-critic check that a problem
  type can reach a component type, or an extraction rule to state both facts when the text names the part
  (the "state both facts" rule exists, but only for one statement supporting two types).
- **Complaints are named by their bare number (found in R53).** The extractor called a complaint
  "10667633", the gold mapping "Complaint 10667633" (the heading). Exact match misses every complaint fact
  for that reason alone; the judge is unaffected. Next held-out gold: name such entities as the text's
  identifier only, decided before the run.
- **Arm and domain are confounded in the held-out comparison (R53).** Separating them needs one controlled
  run on the held-out data (a reviewed schema, pinned) or one automatic-arm run on the furniture data.

- **(Closed by R42.) Exact matching ignores the document (found in R41).** `gold.matches` compares predicate and names
  only; since kinds merge across products, a fact can match a gold triple of another review (the
  Västerås "veneer chipped" fact was attached to the Jönköping triple through the merged alias, and the
  Västerås triple was left unfound). Exact-match scores can be inflated and a gold triple marked found by
  another document's fact. Fix in its own step: match only facts from the triple's `doc_id`, test first.
  *Verified 2026-09-23 ($0, recomputed from the logged `judge_sheet.json` of eval runs `cbcf2764`,
  `9a79cfee`, `682b849d`).* Older than R41: entities are one node per type + name across documents, so
  R34 and R39 already marked Västerås triple 73 ("back panel thin") found by a Norrköping fact. Doc-aware
  exact scores: R34 and R39 recall 0.271 → 0.260 (26 → 25 of 96); R41 precision 0.307 → 0.299 (39 → 38
  of 127), recall 0.292 unchanged; in R41 3 facts point `gold_index` at another review's triple. Judge-
  validated P/R stay 0.976 / 0.906 (the missing verdicts are one obvious recall match per run), but
  R34 and R39 counted the Västerås "back panel quite thin" fact as a gold correction because triple 73
  never reached the judge: `gold_corrections` is 1 too high there. Failing test written (uncommitted):
  `test_a_fact_matches_only_gold_triples_of_its_own_document`.
- **(Closed by R44.) A merged kind takes its longest name (found in R41).** The canonical entity is the most mentioned,
  then the longest name, so the slats' crack reads "crack developing along the bottom" and the shade's
  dent "small dent on one edge", descriptions of other products. For kinds, the shortest name ("crack",
  "small dent") describes every member; the aliases keep the rest.
  *Verified 2026-09-23 from resolve run `4ba527f2`:* 24 groups, 16 span several reviews; in 5 of those
  the canonical name adds another review's detail ("slightly damaged" for Linköping's "damaged" pull,
  "extremely thin" for Västerås's "quite thin" and Norrköping's "thin", "chipped in several places" for
  Jönköping's corners, "small dent on one edge", "crack developing along the bottom"), in 3 more a
  milder shade ("pretty easily" vs "very easily", "perfectly aligned", "as smoothly as I'd like" vs "no
  longer"). About 10 of 127 in-scope facts display a wording their review does not use. Scores unaffected: the judge judges on name + aliases (8 R41 verdicts say "the object
  carries the name of its merged kind"). Derivation (R32) looks up name + aliases, so it is safe under any
  canonical rule.
- **(Closed by R43.) Gold questions read `name` only, not aliases (found while verifying F2, 2026-09-23).** Questions 3-5
  filter `toLower(f.name) CONTAINS 'wobbl' / 'hole' / 'squeak'`, so which name a merge makes canonical
  can change `question_accuracy` without any fact changing. Candidate: match on `[f.name] + f.aliases`
  (a gold change, its own step).
- **(Closed by R46.) Question 4 cannot be answered by the graph's shape (found in R43, 2026-09-23).** "Which products
  are reported with misaligned pre-drilled holes?" looks for a `Defect` whose name contains "hole"; the
  pinned schema stores the holes as a `Component` ("pre-drilled holes") with `HAS_DEFECT` to a wording
  such as "didn't line up properly", so it answers `[]` in every run (R41 `question_accuracy` 0.8 = 4 of
  5). Rewriting it now would be a gold correction made after seeing output: decide with the user.
- **(Closed by R47.) Pair 43 is an extraction naming problem (found in R45, 2026-09-23).** The Norrköping review says "the
  drawer sometimes sticks when i open it too fast"; the extractor named the failure with the whole clause,
  "sticks when i open it too fast", where Helsingborg's reviews give "stick". The extra words dominate the
  name, so it is neither a spelling nor a meaning neighbour of "stick" (below 70) and the pair is never
  asked; R45's second pass instead merged it into "no longer opens smoothly" (debatable). A fix belongs
  in the extraction prompt (name the failure, not the circumstance), a prompt change with a `quality`
  comparison; not a step yet.
- **Run-to-run variation is unmeasured (found in R47, 2026-09-23).** Every comparison since R34 reused the
  LLM cache, so extraction was identical; R47 changed the prompt and re-sampled all 70 chunks, and
  validated recall fell 0.906 → 0.865 (4 of 96 triples) while precision rose. Part may be the rule
  (degree words were dropped, "extremely thin and chipped" lost the thinness), part ordinary sampling.
  Measuring it needs repeated uncached runs of one prompt (about $0.08 each); needed anyway for the
  thesis's final numbers (see the methodology plan, R48 onwards).
- **Judge passes disagree on gold links (found in R48, 2026-09-23).** R34-R40 judged the same 127 facts
  (cache) in separate passes; the validated scores are identical, but `gold_corrections` ranges 22-25:
  whether a SUPPORTED fact is linked to a gold triple or counted as missing from the gold differs by
  pass. A measure of the judge's own consistency; the human spot-check proposed for the thesis would
  quantify it.
- **Windows blocks a new virtual environment's `kg.exe` (found in R49, 2026-09-23).** Smart App Control
  refused the launcher of a fresh worktree's venv (os error 4551), as it blocked pandas in R32. Workaround
  without touching the policy: `uv run python -c "from kgbuilder.cli import app; app()" ...`.
- **Merges raise exact-match scores through aliases (found while verifying F1, 2026-09-23).** A merged
  alias lets a same-document fact match gold wording it never used (R41: Västerås "extremely thin" now
  carries "thin" and matches triple 68). Correct by meaning, but exact scores of two resolve variants are
  not comparable; compare them on the judge-validated scores.
- **Embeddings are not cached (found while verifying F3, 2026-09-23).** `llm/cache.py` wraps the chat
  client only, so every `kg resolve --preview` makes one live embedding call and a rebuild from the cache
  still embeds. Cheap (about 100 short names), but not $0 and not offline. R68: it also makes a cached
  rebuild impossible while the key has no credits (resolve stopped at a 402).
- **(Closed by R45.) k = 2 splits large groups of wordings (found in R41).** Six wordings of misaligned holes became two
  groups of three: a name nominates only its 2 nearest neighbours, and the groups are not linked. Union-
  find joins chains, but only through nominated pairs. Candidate: k = 3 (cost bound 1.5x), measured.
  *Verified 2026-09-23 from previews `eeed9e07` (threshold 70) and `c1b4ad6c` (mutual k = 2):* the three
  "weren't … aligned" wordings met through the spelling matcher, the three "didn't …" wordings through
  meaning; each trio fills its members' 2 nearest slots, so the strongest bridge ("didn't align properly"
  / "weren't aligned properly", 93.2) is not mutual. Pair 43 ("stick" / "sticks when i open it too
  fast") scores below 70 and is not a blocking problem that k = 3 is likely to fix. Keep-apart pairs
  scoring >= 70 but not nominated at k = 2 (new risk under any wider blocking): 49, 61, 64 (86.8), 65.

- **(Closed by R39.) Adjudication prompts are not deterministic (found in R36).** `_llm_adjudicator` shows the LLM
  `head(collect(c.text))`, an unordered pick of a mentioning chunk, so a rerun can build another prompt:
  2 of 65 calls missed the cache. Fix with an `ORDER BY c.chunk_id` and a test.
- **(Closed by R38: the gold was wrong, the LLM right.) The adjudicator keeps "dimmer switch" / "dimmer function" apart (found in R36).** Nominated at 84.3,
  answered "not the same" (conservative prompt, one context sentence each). Candidates: show every
  mentioning sentence of the same document, or accept it as a conservative choice.
- **Lowering the ER threshold to 75 or 70 would not help on the gold (analysis in R36, no run).** Every
  "same" pair already scores >= 78; lower thresholds add 15 or 86 LLM questions and nominate more
  keep-apart pairs. Kept at 78; rerun `kg resolve --preview` for every new dataset or embedding model.
  Full list of the day's findings: local `docs/evaluation/experiments_2026-09-23.md`.
- **Embedding calls have no cost (found in R36).** Gemini embeddings report no tokens, so `cost_usd`
  excludes the `resolve_preview` / `resolve` embedding calls (2 per run, about 100 short names).

- **Prompt examples copied from the evaluation corpus inflate recall (found in R34).** R30's quoted
  hedges were phrases of the gold's own documents; the neutral rule loses exactly those 9 facts
  (validated recall 1.000 → 0.906). Rule for later prompt work: examples never come from a document
  the gold labels. Held-out datasets (proposed 2026-09-23) are the clean test.
- **The resolver keeps synonyms apart (found in R34).** "drawer rails" / "metal rails" (same dresser)
  and "dimmer switch" / "dimmer function" (same lamp) stay two entities; `er_accuracy_valid` 0.800.
  Candidates: give the adjudicator the document context, or block pairs within one product's reviews.

- **A second, smaller furniture hint in the extraction prompt (found in R34).** The pronoun rule says
  "the product or thing the document is about" and gives the example "this dresser". Harmless on other
  domains (it says "or thing"), but not neutral; reword before the other-dataset runs if the user agrees.

- **15 of 70 extraction traces lost to the pandas policy block (found in R30).** MLflow's trace export
  thread imports pandas; the blocked DLL made some exports fail (`FileNotFoundError ... pandas.libs`),
  so run `b1fa5eec` has 55 traces. Usage metrics are complete (counted in-process). Same fix as above.
- **The exhaustive rule lets subjective complaints in (found in R30).** "feels kinda cheap", "materials
  feel cheap", "wood feels kinda lightweight" are stated by the reviews (judged `SUPPORTED`, flagged vague)
  but the schema's Defect type excludes subjective complaints. Candidate: "a hedged *physical* complaint".
  Decide with the goal in mind; not a step yet.

- **(Resolved by 2026-09-23, R36: the 5 tests pass again.) Windows Smart App Control blocks pandas's `sparse` extension (found in R32, 2026-09-22 18:39).**
  `pandas._libs.sparse...pyd` fails to load with "An Application Control policy has blocked this file"
  (Code Integrity events 3033/3077; `VerifiedAndReputablePolicyState` = 1). MLflow imports pandas only for
  `search_runs`, which five tests use (`test_tracking.py` ×4, `test_cli.py` ×1); the pipeline and the
  other 144 tests do not touch it. Not a code problem: the same files passed earlier the same day. To
  clear it: allow the file in Windows Security (App & browser control → Smart App Control), or reinstall
  pandas from a signed wheel, then rerun `uv run pytest`.

- **Derivation re-creates an entity that resolution absorbed (found in R29).** Resolve merged
  "Västerås Bookshelf" into "Västerås Bookshelves" (longer name canonical); the derivation looked the
  product up by `entity_id(Product, name)`, found no node under that id and created it again: 11 product
  entities for 10 products. Fix in its own step (R32): find an existing entity of the object type whose
  name or aliases contain the product name before creating one; test with a merged product.
- **(Closed by R33, R35.) `er_accuracy` scores absence as "not merged" (found in R29).** Three of the four failing gold pairs
  name an entity the graph does not contain at all ("drawer rail", "dimmer", "predrilled holes"). For the
  next gold version: score a pair only when both names exist, or report "not extracted" apart.
- **The misaligned-holes gold question depends on the old naming (found in R29).** Its Cypher wants a
  Defect entity named with "hole"; the extractor now names the part "pre-drilled holes" and the defect
  "didn't line up". Through the Component name the question returns all six expected products. Gold
  correction candidate for the next gold version; not changed in the judging step.

- **Mixed line endings in the index (found in R26).** Some committed files are CRLF (`REFACTOR_PLAN.md`,
  `tests/test_pipeline.py`), most are LF, and there is no `.gitattributes`, so a script that rewrites a
  file with the platform default turns the whole file into a diff. Each file was restored to its own
  ending in R26. Open: add `.gitattributes` with `* text=auto eol=lf` and renormalise in one commit.

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
