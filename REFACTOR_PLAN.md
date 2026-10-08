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

### R77. Assertion: truth, modality and condition (layered-model Step 7; done 2026-10-06; revision parts d-f done 2026-10-06)
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
  R79 done: no dataset below Step 3 any more; Step 7's code resumes with R79 as its baseline.
- **Split (2026-10-05): gold, part a (code, $0, no run), part b (runs, asked first), one commit each.**
- **Gold (done 2026-10-05, `c773bc2`, before any code and any output).** Per dataset a sample drawn by code
  from the source text only (documents and chunks; seed 77): the random stratum (R68's 40 sentences for
  furniture and held-out, whose blind claims are kept as written; 40 new random sentences for generality),
  20 random sentences with a negation, modal or condition word (so the three rare kinds can be measured;
  rates over the whole text come from the random stratum only), and the done criteria's named sentences
  (the dresser's "couldn't get the drawers to slide right", the RAV4's "COULD CAUSE AN ACCIDENT", the
  Civic's two "WHEN ..." sentences). Three Fable 5.1 subagents listed and labelled the claims from blind
  files (text, chunk, context; no `out/`); the lead judge (Opus 5.5) reviewed every non-default label and
  note and changed none. Rules for both passes: `tests/gold/r77/assertion_rules.md` (invented examples).

  | | sentences | claims | negated | possible | conditional |
  |---|---|---|---|---|---|
  | furniture | 61 | 117 | 18 | 1 | 2 |
  | held-out | 63 | 100 | 12 | 12 | 13 |
  | generality | 60 | 108 | 12 | 0 | 4 |

  Three gold claims cannot pass the word checks of part a, by design of the checks, not of the gold:
  "rather than the stapled construction" (negated, no negation word), "without a plan" and the inverted
  "had the seal been replaced" (conditional, no condition word). The review found "maybe" missing from the
  modal words (furniture's "assembly took maybe 40 mins"); added in part a.
- **Part a: code and prompt (done 2026-10-05, no run).**
  - Extraction (`text/extraction.py`): `RawTriple` gains `truth` (affirmed / negated), `modality` (actual
    / possible / conditional) and `condition` (verbatim, only when conditional), each defaulting to what
    every claim before R77 and every derived claim is. Four prompt rules in domain-neutral words with
    invented examples (a kettle); "this dresser" and "the product" removed from the naming rule. `verify`:
    negated needs a negation word in the quote (a closed list plus any "n't" word and the apostrophe-less
    spellings), possible a modal word, conditional a condition that is words of the quote and contains a
    condition word; a condition on a claim that is not conditional is rejected (code cannot tell which
    label is wrong). New rejection reasons `negation_not_in_evidence`, `modality_not_in_evidence`,
    `condition_not_in_evidence`, `condition_not_conditional` (logged as `rejected_<reason>`). The repeat
    key within a chunk includes the three fields.
  - Id (`core.identity.observation_id`): each field joins the key only when not at its default, tagged
    with its name, so every existing id and verdict stays valid and "the lid leaked" / "the lid never
    leaked" in one chunk are two claims.
  - Graph and readers: the observation stores `truth`, `modality`, `condition`; the extract run logs
    `observations_negated`, `_possible`, `_conditional`. The fact reader (`coalesce` for older graphs), the
    flatten key, the judge sheet, `fact_id`, rescore and the coverage sheet carry the three fields.
  - Query plans: `find_claims` takes `truth` / `truth_words` and `modality` / `modality_words`; by default
    a plan finds only affirmed, actual claims (counts default to them, task file); another value stays
    only with the question's own words, else it is dropped and reported (Step 4's rule for tone and
    time). Planner prompt: the `find_claims` line and the tone/time rule widened to the two fields. The
    text2cypher schema text names the two fields. Swept for dataset words: none.
  - Scoring (`validation/assertion.py`, stage and command `kg assertion SHEET GOLD VERDICTS`, no graph):
    after a run the judge matches each gold claim to the observations of a coverage sheet of the sample
    (`kg coverage-sheet`) that state its content whatever their assertion, and says per field whether they
    keep the label. Code computes, per field and per gold value, **kept** (judge, by meaning: a negation in
    the names counts) and **exact** (stored field equals the label: what a count over the field needs),
    with Wilson intervals.
  - Tests: `tests/test_assertion.py` (17: each rejection, what passes, the old id unchanged and each label
    another id, a claim and its denial kept apart, the fields stored, flattened and read back by the judge
    sheet with the stored ids, a claim from before R77 read as affirmed and actual, the plan's drop and
    keep, a plan counting only the claims that hold unless asked, the scorer's kept and exact, its file
    checks, the stage without a graph, the extraction prompt free of corpus words and four-grams). One
    R74 assertion changed on purpose (`test_query_plan.py`): `find_claims` now always filters on the two
    fields, so its parameters and its bare query changed. Gate: 527 passed (510 before), `ruff check`
    clean.
- **Part b: runs (2026-10-05; the user's yes, estimate $2.1-2.6; spent $2.336).** The three graphs rebuilt on
  `5a75b7a` into `out/r77_<dataset>` with the R76 recipe (frozen plans and `tests/gold/r75/` text schemas,
  `EXTRACT_PASSES=2`), extraction on the real key this time (the prompt changed, so no call could be a cache
  hit); each dataset reset, built, extracted, linked, resolved, attached, evaluated, its assertion sheet
  written (`kg coverage-sheet` on the R77 sample) and `kg qa --system graph` (DeepSeek) run before the next
  reset. Tree dirty only by the user's `.claude/settings.json`.

  | Run (MLflow) | Cost | LLM calls (cache hits) | tokens in / out / thinking |
  |---|---|---|---|
  | furniture extract `7e3c00b0` | $0.370 | 140 (0) | 167450 / 64366 / 835 |
  | furniture resolve `9806b94e` | $0.057 | 323 (113) | 41677 / 2892 / 4044 |
  | furniture `qa_graph` `5fad52ea` | $0.467 | 116 (21) | 441739 / 9208 / 269403 |
  | held-out extract `81ea6657` | $0.536 | 162 (0) | 229362 / 93956 / 3056 |
  | held-out resolve `d943367e` | $0.013 | 268 (221) | 10251 / 673 / 753 |
  | held-out `qa_graph` `cba35652` | $0.284 | 98 (21) | 398649 / 6770 / 130423 |
  | generality extract `c45f2324` | $0.204 | 64 (0) | 111455 / 30356 / 1747 |
  | generality resolve `21a8f7e8` | $0.016 | 202 (160) | 9956 / 1567 / 671 |
  | generality `qa_graph` `74dd0128` | $0.389 | 108 (24) | 326190 / 7508 / 235202 |

  - **The graphs.** Claims furniture 502 (R76 508), held-out 547 (532), generality 212 (195); negated / possible
    / conditional 24 / 3 / 7, 2 / 76 / 34, 1 / 0 / 0. New rejections (negation, modality, condition, condition
    without conditional): furniture 9 / 3 / 2 / 0, held-out 0 / 3 / 7 / 1, generality 0 / 0 / 1 / 0.
    `path_truth` 1.0 on all three; identity precision 1.0 and apart 1.0 as R75 (recall furniture 0.867,
    held-out 0.714 against 0.667); `question_accuracy` 0.167 (furniture) and 1.000 (held-out) as before.
    MLflow eval `809c3121`, `96dbb298`, `fb821804`.
  - **Assertion (judge pass 2, Fable 5.1 subagents, lead judge Opus 5.5, no gold change).** Each gold claim
    matched to the observations of its chunk that state its content, whatever their assertion
    (`tests/gold/r77/<dataset>_assertion_verdicts.json`); scored by `kg assertion` (`dac2a65d`, `9ce35ef5`,
    `cb41affd`; three earlier scoring runs by the judges went to `kgbuilder-dev` without a preset and are not
    used). Kept = by meaning (judge), exact = the stored field equals the label (code).

    | | furniture | held-out | generality |
    |---|---|---|---|
    | claims matched | 79 / 117 | 34 / 100 | 22 / 108 |
    | truth / modality / condition kept (judge) | 79 / 79 / 79 of 79 | 34 / 34 / 34 of 34 | 22 / 22 / 22 of 22 |
    | truth exact: affirmed, negated | 64/64, **10/15** | 31/31, **0/3** | 21/21, 1/1 |
    | modality exact: actual, possible, conditional | 76/76, 1/1, 2/2 | 15/15, 10/10, 9/9 | 22/22, -, - |
    | condition exact (conditional claims) | 2/2 | 9/9 | - |

    Real examples: "absolutely no squeaking or movement" stored as `squeaking`, negated; "THE VEHICLE MAY
    BRAKE SUDDENLY ... WHEN APPROACHING EXIT RAMPS" conditional with "WHEN APPROACHING EXIT RAMPS"; "the
    drawer sometimes sticks when i open it too fast" conditional with its condition. Every non-exact truth is
    a negation the names carry with truth affirmed: the named sentence "we still couldn't get the drawers to
    slide right" is stored as the object "couldn't get the drawers to slide right", affirmed (kept by meaning,
    not by the field); also "doesn't close properly", "DOES NOT INDICATE FULL", "may not engage". No field is
    wrong by meaning on any matched claim. The match rates are the extraction's coverage under a strict
    reading (a relation must mean the claim's relation), not a property of the fields; generality's 1 negated
    and 0 conditional matches say little.
  - **Answers (judge on the free-text answers: 10 of 18 are word for word R79's and keep its verdict; 8 by a
    Fable 5.1 subagent with R71's rules, accepted by the lead judge: F38, H35, G17, G20 correct, H37, G08,
    G18, G33 wrong; F38 without "long dinner parties" and H35 given as a list item flagged and kept as
    correct, as before).** Verdicts `tests/gold/r77/<dataset>_step7_graph_verdicts.json`; scores `264145ae`,
    `f1da48d4`, `f93da3b2`; paired with R79 `b9b78618`, `104c9e61`, `c89a8080`.

    | Type | furniture R79 / **R77** | held-out R79 / **R77** | generality R79 / **R77** |
    |---|---|---|---|
    | multi_hop | 13 / **12** /17 | 15 / **15** /17 | 1 / **1** /6 |
    | aggregation | 8 / **7** /16 | 14 / **13** /16 | 2 / **2** /6 |
    | structured_filter | 14 / **13** /16 | 16 / **14** /17 | 2 / **2** /6 |
    | disambiguation | 3 / **3** /6 | 2 / **3** /6 | 2 / **1** /8 |
    | negation_sensitive | 4 / **1** /7 | 4 / **5** /6 | 2 / **1** /6 |
    | lookup | 4 / **4** /6 | 4 / **3** /6 | 7 / **6** /9 |
    | **all** | 46 / **40** /68 | 55 / **53** /68 | 16 / **13** /41 |
    | paired (only R79 / only R77, p) | 7 / 1, p 0.070 | 4 / 2, p 0.688 | 4 / 1, p 0.375 |

  - **Why the 15 lost answers fail** (read from the plans and the stored claims):
    - **5: the default filter drops conditional claims.** "Which products are reported to squeak or creak?"
      (F27; F17, F28 alike): the reports are now "the frame creaks whenever someone sits down" and "some
      squeaking when I lean back", both stored conditional, and a plan finds affirmed, actual claims only;
      F31 the desk's "wobbles slightly when I'm typing"; H06 "VEHICLE WILL NOT ACCELERATE WHEN PUSHING DOWN
      ON THE GAS PEDAL" (R79 kept the when-clause as a time). The extraction is right (the gold labels such
      claims conditional); the query default is wrong for them: a claim that holds whenever its condition
      holds reports that the thing happens, unlike a possible one. The task's "counts default to affirmed
      and actual" was implemented literally.
    - **10: new plans, the graph unchanged for them.** The planner prompt changed, so every plan was written
      anew: F03 (a refused plan), F09, F35, H01, H37 and G08 (both through the text2cypher fallback), H55,
      G13, G24 (the claim "Marek Hollis SERVICES HP40-2291" is in both graphs; the new plan added an object
      filter that misses it), G34. The 5 gains (F34, H24, H27, G20) are of the same kind.
  - **Acceptance, one by one.** Unit tests per rejection, field stored / flattened / queried, an old id
    unchanged: met (part a). "couldn't get the drawers to slide right" stored as negated: **met by meaning,
    not by the field** (the negation is in the object name); "COULD CAUSE AN ACCIDENT" possible: met; the
    Civic's "when ..." clauses conditional: met (9 of 9 matched held-out conditional claims with their
    condition). Negation-sensitive and aggregation better than Step 6: **not met**; the reasons are above
    (the default drops conditional claims; read_check already caught negated claims since R71, so the field
    adds little to answers). No type worse beyond one sample's variation: met (lowest per-type p 0.25;
    furniture overall p 0.070). `path_truth` 1.0: met. Stop rule (task file section 4): does not apply.
  - **Open, the user's decision:** the query default (see "Found along the way").
- **Part c: the default counts conditional claims (the user's decision, 2026-10-05; code 2026-10-06).** A
  plan's default modality "actual" now finds the claims that hold, actual or conditional; "possible" and
  "conditional" asked for by name stay narrow (`plan_cypher._MODALITIES`). Changed in code only: the plan's
  fields, their descriptions and the planner prompt are byte-identical to part b, so the planner's cached
  requests are reused and the run measures the default alone. The prompt's "Only claims the text states as
  holding come back" still reads right; its opt-in "conditional" now narrows. Test first:
  `test_a_plan_counts_the_claims_that_hold_unless_the_question_asks_for_others` gains a conditional claim
  and failed before (counted 1 for 2); the R74 parameter assertion follows the new parameter name. Gate:
  527 passed, `ruff check` clean (`787d1d8`).
- **Part c run (2026-10-06; the user's yes, estimate $0.05-0.30; spent $0.0003).** The three part b graphs
  rebuilt from the cache into `out/r77c_<dataset>` (invalid Gemini key on `extract`; every extract and
  resolve call a cache hit, same counts as part b: 502 / 547 / 212 claims), then `kg qa --system graph`:
  furniture `028f1938` $0.0003 (123 calls, 122 hits), held-out `388ff264` $0 (102, 102), generality
  `734eba35` $0 (107, 107). The plans are part b's (cached); the `read_check` calls for the newly found
  conditional claims were already cached from R79. No free-text answer changed: part b's verdicts carry
  over (`tests/gold/r77/<dataset>_partc_graph_verdicts.json`). Scores `5deab79e`, `e47c828c`, `fa1922b7`;
  paired with R79 `8030e720`, `ea9ba993`, `ca3f3609`; with part b `6120a517`, `7b8a2b21`, `eac115ea`.

  | Type | furniture R79 / b / **c** | held-out R79 / b / **c** | generality R79 / b / **c** |
  |---|---|---|---|
  | multi_hop | 13 / 12 / **12** /17 | 15 / 15 / **15** /17 | 1 / 1 / **1** /6 |
  | aggregation | 8 / 7 / **7** /16 | 14 / 13 / **13** /16 | 2 / 2 / **2** /6 |
  | structured_filter | 14 / 13 / **14** /16 | 16 / 14 / **15** /17 | 2 / 2 / **2** /6 |
  | disambiguation | 3 / 3 / **3** /6 | 2 / 3 / **3** /6 | 2 / 1 / **1** /8 |
  | negation_sensitive | 4 / 1 / **4** /7 | 4 / 5 / **5** /6 | 2 / 1 / **1** /6 |
  | lookup | 4 / 4 / **4** /6 | 4 / 3 / **3** /6 | 7 / 6 / **6** /9 |
  | **all** | 46 / 40 / **44** /68 | 55 / 53 / **54** /68 | 16 / 13 / **13** /41 |
  | paired b vs c (only b / only c, p) | 0 / 4, p 0.125 | 0 / 1, p 1.0 | 0 / 0 |
  | paired R79 vs c | 3 / 1, p 0.625 | 3 / 2, p 1.0 | 4 / 1, p 0.375 |

  - The five answers lost to the default came back, and nothing else changed: F17, F27, F28, F31 (e.g. F27
    now names the Stockholm Chair, "some squeaking when I lean back", and the Uppsala Sofa, "the frame creaks
    whenever someone sits down") and H06 (the RAV4's "WILL NOT ACCELERATE WHEN PUSHING DOWN ON THE GAS
    PEDAL"). Generality has no conditional claim, so nothing changed there.
  - Still lost against R79, all through plans the changed planner prompt wrote anew (part b's list): F03,
    F09, F35, H01, H37, H55, G08, G13, G24, G34; won the same way: F34, H24, H27, G20.
- **R77 done (2026-10-06).** Acceptance: the unit tests, the field storage and the unchanged ids met (part
  a); "COULD CAUSE AN ACCIDENT" possible and the Civic's "when ..." clauses conditional met; "couldn't get
  the drawers to slide right" met by meaning only (the negation is in the object name, truth affirmed).
  Negation-sensitive and aggregation better than Step 6: **not met**, with the reason: read_check has caught
  negated and possible claims since R71, so the fields add little to these answers (negation-sensitive 4 / 5
  / 1 against R79's 4 / 4 / 2, aggregation 7 / 13 / 2 against 8 / 14 / 2), and the remaining differences are
  new plans, not the graph. No type worse beyond one sample's variation: met (all p >= 0.375 against R79).
  `path_truth` 1.0: met. Total cost of R77's runs: $2.336 (part b) + $0.0003 (part c). Next: Step 8, the
  failure table by cause after Step 7 (task file).
- **Revision (the user, 2026-10-06): parts d (representation), e (navigation), f (runs, asked first); one
  commit each.** The graph is first a navigation layer for the query agent: it must lead to the right
  claims and chunks, and the chunks hold the nuance; counting is one use of it, not the only one. Three
  findings of part b drive it. "we still couldn't get the drawers to slide right" was stored affirmed with
  "couldn't" in its object, so a count over truth missed 5 of 15 furniture and 3 of 3 held-out denials (the
  bug). The closed negator list rejected 9 right claims ("prevents sagging"). And `find_claims` dropped
  denied and possible claims even when its claims went on to be read as text. **Decisions (asked before any
  code):** the bug is the negation left in names; truth is said of the statement (as the gold already
  reads it), its words are kept as verbatim cues, and code derives whether the stored triple itself is
  denied (`triple_truth`). A name may be the denied state: held-out's Problem "DO NOT LOCK"
  `AFFECTS_COMPONENT` TRUNK stays a triple that holds, so a count of problems keeps it. Claims that flow
  into a reading step come back whatever their assertion; claims that flow into list, count or rank keep
  the exact default.
- **Part d: representation (2026-10-06, code and prompt, no run).**
  - Extraction (`text/extraction.py`): `RawTriple` gains `negation` and `hedge`, the verbatim words that
    deny the statement or say it only may hold. Prompt: truth is of the statement, "in whatever words"
    ("never leaked", "prevents scale"); name the fact itself, and keep a denial in a name only when the name
    is the denied state ("will not switch off"); a condition in any wording, inverted too ("had the lid been
    shut"). The examples are invented (a kettle); swept for dataset words: none. `verify` drops the three
    closed word lists: a negated claim needs its `negation`, a possible one its `hedge` and a conditional one
    its `condition`, each as whole words of the quote (`core.text.contains_words`). A hedge may come with a
    conditional claim ("may stall if ..."). A cue on a claim not of its kind is rejected (new reason
    `cue_without_assertion`). The other reason names are unchanged, so the metrics stay comparable.
  - `extraction.triple_truth`: negated only when the statement is negated and its negation is in neither
    name. Known limit, in the docstring: a cue word that a name also holds for another reason ("no" in
    "No-Spill Kettle") reads as a denial carried by that name.
  - Graph (`text/subject_graph.py`): the observation stores `negation`, `hedge` and `triple_truth`. The id
    is unchanged: the cues are wording, not identity. A claim whose truth moves to negated (the bug cases)
    gets a new id, which is the point of the fix. New extract metrics: `observations_denied` and
    `observations_negation_in_name`.
  - Readers: `StoredFact` and the fact query read the three fields. A graph from before part d reads
    `triple_truth` as its stored truth, which was said of the triple. The coverage sheet shows them to the
    pass-2 judge. `kg assertion` is unchanged: its exact truth now compares the statement with the
    statement.
  - Counting (`plan_cypher.find_claims`): the default keeps claims whose stored triple holds
    (`coalesce(o.triple_truth, o.truth)`). A question's "negated" filters on the statement's truth, so both
    forms of a denial come back. The planner prompt and the plan's fields are byte-identical to part c, so
    the planner's cache is reused. The text2cypher schema text names the fields and says which one a count
    needs; this closes the Found-along-the-way item on its wording.
  - Tests (`tests/test_assertion.py`; the word-list tests are rewritten):
    - accepted in any wording: "never", "doesnt", "prevents", "eliminates", "cannot", "may", "could",
      "when", "unless", an inverted condition, and a state named by a denial;
    - each rejection, including a part of a word as a cue;
    - the triple truth;
    - the stored, flattened and read-back fields, and graphs from before R77 and before part d.
  - The exact-count test gains a denial in a name: the default counts it (3) and "negated" finds both forms
    (2). It failed before the fix (counted 2 for 3; shown by stashing `plan_cypher.py`). One R74 assertion
    changed on purpose: the bare `find_claims` Cypher now reads the triple truth.
  - Gate: 542 passed (527 before), `ruff check` clean. `ruff format --check` flags only
    `structured/profiler.py`, as before this step.
- **Part e: navigation (2026-10-06, code only, no prompt, no run).**
  - `plan_run.read_steps(plan)` decides from the plan's shape alone which steps' items are only read as
    text: every step using them is `retrieve_chunks` or `answer_from_chunks`, or a `read_check` whose own
    items are only read. Such a `find_claims` runs with `plan_cypher.find_claims(read_all=True)`: the
    defaults filter nothing, so a denied or possible claim leads the reader to its chunk. A truth or
    modality the question asked for still narrows it. A step whose claims are counted, listed or ranked,
    even through `read_check`, keeps part d's exact default. So does a step used both ways.
  - The step note says "every assertion: read as text". The planner prompt and the plan's fields are
    unchanged.
  - Tests (`tests/test_assertion.py`):
    - the plan-shape rule: read directly, through `retrieve_chunks` or `read_check`; counted directly or
      after `read_check`; ranked; used both ways;
    - two Neo4j tests on a denied claim ("could not get the spindle to slide right", its negation kept) and
      a possible one, each in its own chunk. A reading plan without any word asking for a denial reaches
      each claim's own chunk, directly and through `retrieve_chunks`. The same claims are counted 0 by
      default, also through `read_check` (no check call is spent on them), and 1 when the question asks
      for the denial.
  - Gate: 545 passed (542 after part d), `ruff check` clean.
  - Risk, for part f to show: a reading chain through `read_check` sees more candidates than before, up to
    `check_limit` (30), above which the plan fails and the planner retries.
- **Part f: runs (2026-10-06; the user's yes, estimate $1.3-2.3; spent $2.399).**
  - Recipe: the three graphs rebuilt on `13ee2b6` into `out/r77d_<dataset>` with part b's recipe (frozen
    plans and `tests/gold/r75/` text schemas copied from `out/r77c_*`, `EXTRACT_PASSES=2`, extraction on
    the real key). Each dataset was reset, built, extracted, linked, resolved, attached and evaluated; its
    assertion sheet was written, and `kg qa --system graph` (DeepSeek) ran before the next reset. The tree
    was dirty only by the user's `.claude/settings.json`.
  - The planner prompt is unchanged, but its schema text is read from the rebuilt graph. So most planner
    calls missed the cache and plans were written anew, as in part b. That is the largest confound in the
    answer comparison below.

  | Run (MLflow) | Cost | LLM calls (cache hits) | tokens in / out / thinking |
  |---|---|---|---|
  | furniture extract `7547a42c` | $0.417 | 140 (0) | 189470 / 72626 / 750 |
  | furniture resolve `808c4f99` | $0.043 | 300 (164) | 27698 / 1332 / 4640 |
  | furniture `qa_graph` `b2d5c21e` | $0.478 | 135 (34) | 444045 / 8107 / 278964 |
  | held-out extract `a2ece60c` | $0.607 | 162 (0) | 253577 / 104856 / 6343 |
  | held-out resolve `8d822850` | $0.014 | 266 (222) | 10244 / 383 / 1298 |
  | held-out `qa_graph` `a9e748b7` | $0.282 | 92 (15) | 392878 / 7026 / 129669 |
  | generality extract `9db7ee6d` | $0.218 | 64 (0) | 121568 / 32046 / 1894 |
  | generality resolve `789dd1e1` | $0.008 | 185 (166) | 4644 / 719 / 431 |
  | generality `qa_graph` `2b3ec058` | $0.332 | 132 (47) | 320634 / 7444 / 188923 |

  - **The graphs** (part b in brackets).
    - Claims: furniture 514 (502), held-out 532 (547), generality 212 (212).
    - Negated / possible / conditional: furniture 50 / 8 / 14 (24 / 3 / 7), held-out 3 / 79 / 40
      (2 / 76 / 34), generality 1 / 0 / 1 (1 / 0 / 0).
    - Negated with the denial in a name (`observations_negation_in_name`): 0 on all three.
    - Rejected: furniture 40 (45), held-out 26 (12), generality 6 (13).
      - Furniture: the 9 negation rejections of part b ("prevents sagging") are gone. 9 new
        `cue_without_assertion` rejections are degree words given as a hedge on an actual claim (Found
        along the way).
      - Held-out: 22 new `argument_not_in_chunk` are vehicles of two recall listing sentences named with
        their years ("2013-2016 Nissan LEAF"), extraction variance unrelated to the assertion.
    - `path_truth` 1.0 on all three; identity precision and apart 1.0. Identity recall: furniture 0.692
      (0.867), held-out 0.714 (0.714), generality 0.769.
    - Eval runs `c409e1a9`, `f375def2`, `9a4e74ca`.
  - **Assertion** (judge pass 2, Fable 5.1 subagents, lead judge Opus 5.5, no gold change).
    - Verdicts in `tests/gold/r77/<dataset>_partd_assertion_verdicts.json` against
      `<dataset>_partd_assertion_sheet.json`; scored by `kg assertion` (`7e4b4f5a`, `7e0f754e`,
      `3b89fed2`).
    - Kept = by meaning (judge); exact = the stored field equals the label (code). Part b in brackets.

    | | furniture | held-out | generality |
    |---|---|---|---|
    | claims matched | 81 / 117 (79) | 35 / 100 (34) | 22 / 108 (22) |
    | **truth exact, negated** | **13 / 15 (10 / 15)** | **0 / 3 (0 / 3)** | 1 / 1 (1 / 1) |
    | truth exact, affirmed | 64 / 66 (64 / 64) | 32 / 32 (31 / 31) | 21 / 21 (21 / 21) |
    | truth / modality / condition kept | 79 / 80 / 81 of 81 | 35 / 34 / 35 of 35 | 22 / 22 / 22 of 22 |
    | modality exact: possible, conditional | 1 / 1, 1 / 1 | 10 / 11, 9 / 9 | -, - |

    - Furniture, real examples:
      - The named sentence "we still couldn't get the drawers to slide right" is now
        `drawers EXHIBITS "slide right"`, truth negated, negation "couldn't". Its part b form was the
        object "couldn't get the drawers to slide right", affirmed.
      - Likewise "squeaking" negated with "no", "allow the drawers to slide smoothly" with "don't",
        "doesn't close properly".
      - The two remaining misses keep the negation in the name, affirmed and without a cue: "assembly
        wasn't too bad", "not as comfy as i hoped".
    - Furniture, new errors (3 fields judged false; none in part b):
      - "I expected much better quality and durability" stored as quality and durability negated with
        the cue "expected much better" (an unmet expectation, not a denial), twice.
      - "they seem poorly manufactured" stored as possible with the hedge "seem".
    - **Held-out: the fix did not take for named states.**
      - The 3 matched negated claims, and every complaint of the "DO NOT LOCK", "DID NOT STOP",
        "NO FEEDBACK", "DOES NOT INDICATE FULL" kind, keep the denial in the Problem's name, which is
        allowed, but with truth affirmed and no `negation` cue.
      - The prompt's "the fact is still negated" was ignored. Where the model gave a cue, the code
        path works ("NO VISIBLE CHIP DAMAGE APPARENT": "CHIP DAMAGE", negated, "NO").
      - One field false: "water entering may cause a loss of electric power steering assist" also
        stored as an actual loss.
    - Generality: no field false. 11 of its 12 negated gold claims and all 4 conditional ones are not
      extracted in any form (coverage, not the fields).
  - **Answers.** The free-text judge (Fable 5.1 subagents, R71's rules, lead judge Opus 5.5):
    - Answers word for word part c's keep part c's verdict.
    - Newly judged: F38, H37, G08, G16, G17, G33 correct; G18 wrong. G16 without the pump id was
      flagged and kept as correct, as R73 and R77 kept it.
    - Verdicts in `tests/gold/r77/<dataset>_partd_graph_verdicts.json`. Scores `518eca99`, `0685c21c`,
      `f50d234d`. Paired with part c `f94eec60`, `40a7cfae`, `66d01d6e`; with R79 `e03f6dcb`,
      `ba3a4b7a`, `a318f96e`.

    | Type | furniture R79 / c / **f** | held-out R79 / c / **f** | generality R79 / c / **f** |
    |---|---|---|---|
    | multi_hop | 13 / 12 / **11** /17 | 15 / 15 / **15** /17 | 1 / 1 / **2** /6 |
    | aggregation | 8 / 7 / **7** /16 | 14 / 13 / **13** /16 | 2 / 2 / **2** /6 |
    | structured_filter | 14 / 14 / **12** /16 | 16 / 15 / **15** /17 | 2 / 2 / **2** /6 |
    | disambiguation | 3 / 3 / **3** /6 | 2 / 3 / **3** /6 | 2 / 1 / **1** /8 |
    | negation_sensitive | 4 / 4 / **3** /7 | 4 / 5 / **2** /6 | 2 / 1 / **1** /6 |
    | lookup | 4 / 4 / **5** /6 | 4 / 3 / **3** /6 | 7 / 6 / **8** /9 |
    | **all** | 46 / 44 / **41** /68 | 55 / 54 / **51** /68 | 16 / 13 / **16** /41 |
    | paired c vs f (only c / only f, p) | 6 / 3, p 0.508 | 5 / 2, p 0.453 | 1 / 4, p 0.375 |
    | paired R79 vs f | 7 / 2, p 0.180 | 5 / 1, p 0.219 | 3 / 3, p 1.000 |

    - Every answer that changed against part c came with a new plan.
    - Furniture:
      - F27: the same 2 claims were found and verified, then the plan listed their subjects ("Frame")
        instead of the products they are about.
      - F17: the plan searched "frame" as subject.
      - F05, F47, F59, F64: other plans.
      - Gained F03, F35, F66.
    - Held-out:
      - H32 searched "trunk" as subject, where the claim is "DO NOT LOCK OCCURS_ON_VEHICLE Civic".
      - H28 dropped `read_check` and counted 2 complaints for 1.
      - H06: the claim is now named "VEHICLE WILL NOT ACCELERATE" (part c: "WILL NOT ACCELERATE"), so the
        linker missed "accelerate", and the new plan dropped part c's truth filter.
      - H27, H33: other plans.
      - Gained H01, H37.
    - Generality: gained G08, G16, G17, G33; lost one disambiguation question.
    - No type and no dataset differs beyond one sample's variation (lowest p 0.180).
  - **Acceptance.**
    - "couldn't get the drawers to slide right" stored as negated by the field: **met** (furniture
      negated exact 10/15 to 13/15).
    - The held-out named denials: **not met**. The representation and the counting are right (tested),
      but the extractor does not mark them.
    - Negating verbs and inverted conditions accepted: met in code. No such claim was in the matched
      sample.
    - Answers: no change beyond variation. They cannot measure the fields while every changed answer
      also has a new plan.
    - Total cost of part f: $2.399, above the estimate's $2.3, mostly the QA planner's cache misses.
    - Part e in use: a `find_claims` read every assertion in 5 / 1 / 5 answers (furniture / held-out /
      generality). Its risk did not show: one plan hit `check_limit` (H44), as in part c.

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

### R79. Plans over records: per-label counts, the most frequent value, record fields first (done 2026-10-05)
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
- **Part b: runs (2026-10-05; the user's yes, estimate $1.0-1.3; spent $1.082).** The three R76 graphs rebuilt
  from the cache into `out/r79_<dataset>` right before each run (no test run in between; every count as
  R76), then `kg qa --system graph` on `6052c3a` (`planner_prompt_version` `39f628183ebd`; tree dirty only
  by the user's `.claude/settings.json`): furniture `6ce7ea60` $0.424 (143 calls, 41 hits), held-out
  `d6050eef` $0.337 (106, 22), generality `d74edf67` $0.321 (106, 35); tokens in / out / thinking 405186 /
  8938 / 243092, 392125 / 7597 / 174910, 270171 / 6226 / 193759.
  - **Judge:** 13 of 18 free-text answers are word for word R78's and keep its verdict; the 5 changed ones
    by a Fable 5.1 subagent with R71's rules: F38, H20, H37 correct, G20 (no answer) and G33 (names only a
    station) wrong. H20 without "2.0L" was flagged and kept as correct, as R73 kept it. Verdicts in
    `tests/gold/r79/`. Scores: `bf710353`, `bf4d02cb`, `98f800c7`; paired with R78: `37c7cfdb`, `47248ebf`,
    `f00c6342`; with R73b: `a9eb46c4`, `121acff3`, `be1c0471`.

    | Type | furniture R73b / R78 / **R79** | held-out R73b / R78 / **R79** | generality R73b / R78 / **R79** |
    |---|---|---|---|
    | multi_hop | 10 / 10 / **13** /17 | 15 / 14 / **15** /17 | 1 / 1 / **1** /6 |
    | aggregation | 4 / 6 / **8** /16 | 16 / 12 / **14** /16 | 3 / 2 / **2** /6 |
    | structured_filter | 5 / 12 / **14** /16 | 17 / 13 / **16** /17 | 0 / 2 / **2** /6 |
    | disambiguation | 6 / 2 / **3** /6 | 3 / 3 / **2** /6 | 3 / 1 / **2** /8 |
    | negation_sensitive | 4 / 3 / **4** /7 | 4 / 4 / **4** /6 | 2 / 1 / **2** /6 |
    | lookup | 5 / 3 / **4** /6 | 5 / 4 / **4** /6 | 8 / 7 / **7** /9 |
    | **all** | 34 / 36 / **46** /68 | 60 / 50 / **55** /68 | 17 / 14 / **16** /41 |
    | paired R78 vs R79 (only R78 / only R79, p) | 2 / 12, **p 0.013** | 4 / 9, p 0.267 | 1 / 3, p 0.625 |
    | paired R73b vs R79 | 8 / 20, **p 0.036** (R79 better) | 8 / 3, p 0.227 | 6 / 5, p 1.000 |

  - The eight held-out answers aimed at all right: H12 and H28 count complaints only (`label`), H06 lists the
    complaint only, H53 ranks years (2020), H55 ranks component values, H01 and H43 filter the recall's
    component field with "contains"; H33 is right through the logged text2cypher fallback (both plans
    refused), not through this step. Also gained: H17, and 12 on furniture (F09, F17, F20, F27, F38, F47,
    F53, F57, F59, F64, F65, F67), G13, G24, G34. Lost against R78 (new plans, no cache): F55, F66, H11,
    H24, H27, H36, G20. Fallbacks to text2cypher 3 / 2 / 9 (R78: 4 / 3 / 9).
  - **Stop rule (task file section 4): no longer applies.** Against Step 3 no dataset is worse beyond one
    sample's variation (held-out 55 against 60, p 0.227; generality p 1.0); furniture is better (p 0.036).
    Still lost against Step 3 on held-out: H08, H11, H14, H19, H24, H27, H36, H44.
  - **R79 done.** Next: R77 (Step 7) code, which the user paused for R78-R79; its "better than Step 6"
    baseline is now R79's numbers.

**After R77 part f (the user, 2026-10-06): three steps, in this order, one commit each.** First freeze the
QA plans so that a later graph change is measured by answers alone (R80); then two small extraction
changes from part f's findings (R81, R82). After them R77 is not refined further unless Step 8 shows a
concrete retrieval or query failure that needs it. No broader lexical heuristics and no further semantic
fields.

### R80. Frozen QA plans: replay an earlier run's queries on a changed graph (done 2026-10-06, code only)
The planner's prompt carries the graph's schema text, so every rebuilt graph misses the planner cache and
every question is planned anew (Found along the way, R77 parts b and f): in part f every changed answer came
with a new plan, so the graph change could not be measured by its answers.
- **Scope.** `kg qa --plans DIR` reads the plan systems' answers files of an earlier run
  (`DIR/answers_<system>.jsonl`) and replays each question's query decisions instead of asking the model:
  the plan that ran to its end, else the text2cypher query that answered, else reading. No model writes a
  query in a frozen run: a frozen plan or query that the current graph refuses or fails goes to reading
  (that failure is the graph change's effect, and is logged as a refused plan). read_check and the reader
  still run: they read the graph's content, which is what changed. The vector baseline has no plan and
  ignores the option.
- Files: a new `query/frozen.py` (the frozen query per question, read from an answers file and checked to
  cover every gold question), `query/systems.py` (`PlanSystem` replays), `query/exact.py` (a frozen query
  checked and run without the model), `query/answers.py` (`PlanTrace.frozen`), the QA stage and `kg qa`
  (option, params `frozen_plans` and its hash). The reference plans of part f are copied to
  `tests/gold/r80/<dataset>/answers_graph.jsonl`, so that later steps replay the same plans.
- Not in scope: any change to the planner, its prompt, the primitives or the scoring; any run.
- **Done (2026-10-06, code only, no run).**
  - What is frozen per question (`query/frozen.py`): the last plan attempt that ran to its end; else, when
    text2cypher answered, its last query with its parameters; else nothing (the question is read again).
    The query's answer form is not stored in the answers file; code recovers it exactly from the answer
    (a list of entities, maybe empty, only for the "entities" form).
  - The file is refused (`FrozenPlansError`, new) when a line is of another system, a gold question is
    missing, or an answer has no plan trace; `--plans` naming the `--out` folder is refused too (the run
    would overwrite the plans it replays).
  - MLflow: params `frozen_plans` and `frozen_plans_hash`, metric `answers_frozen`, the frozen file as an
    artifact; `PlanTrace.frozen` on every replayed answer. In a frozen run `plans_proposed` counts the
    replayed plans and `plans_refused` the ones the changed graph refused.
  - Reference plans: part f's answers files (`out/r77d_<dataset>/answers_graph.jsonl`, MLflow `b2d5c21e`,
    `a9e748b7`, `2b3ec058`, built on `13ee2b6`) copied byte for byte to `tests/gold/r80/<dataset>/`.
    Loaded with the gold ($0, no model): furniture 64 plans, 3 text2cypher queries, 1 read (F04, F07,
    F20, F57 not by plan); held-out 68 plans; generality 32 plans, 9 text2cypher queries.
  - Tests (7 new): `tests/test_query_exact.py`: a frozen plan runs without the planner; a frozen plan the
    graph refuses, or that fails while running, is read with no new plan and no text2cypher; a frozen
    text2cypher query is checked and run again without the model (its LIMIT not added twice) and, when
    refused, read; a frozen read is read again; what an answer freezes (the plan that ran after a refused
    one, the query and its recovered form, nothing); the file's checks. `tests/test_query_graph.py`
    (Neo4j): `kg qa --plans` end to end, the model never asked, params, metric and artifact logged, the
    `--out` folder refused. Gate: 552 passed (545 before), `ruff check` clean.
  - Not verified by a run: replaying the reference plans on part f's own graphs should reproduce part f's
    answers from the cache at about $0; it is a full-dataset `kg qa` and is only proposed.

### R81. A stray hedge on an actual claim is dropped, the claim kept (done 2026-10-06, code only; behaviour change)
R77 part f rejected 9 right furniture claims as `cue_without_assertion` because the model gave a degree or
approximation word as their hedge ("about 2 hours", "a bit short", "kinda complicated") while labelling
them actual (Found along the way). A hedge carries no assertion on an actual claim, so it is dropped and the
claim kept, whatever its words (also when they are not words of the quote: they are not stored).
- **Scope.** `text/extraction.py`: `_accept` drops the hedge of an actual claim before verifying and storing
  it, counted in `ExtractionResult.hedges_dropped` (logged by the extract run as `hedges_dropped`);
  `verify` no longer judges a hedge on an actual claim. A negation cue on an affirmed claim stays rejected
  (`cue_without_assertion`), and a possible claim still needs its hedge. No prompt change.
- Known trade-off (the user's choice): an actual label that should have been possible ("may crack", hedge
  "may", modality actual) is now stored as actual instead of rejected.
- **Done (2026-10-06, code only, no run).**
  - Test first: `test_a_stray_hedge_on_an_actual_claim_is_dropped_and_the_claim_kept` (an ungrounded "a bit"
    and a grounded "may" on actual claims kept without their hedge, a possible claim's hedge kept,
    `hedges_dropped` 2) failed before the fix. The parametrized case "hedge on an actual claim is
    `cue_without_assertion`" was removed from the rejection test on purpose: it is the old behaviour.
  - Effect on part f's own rejections, read from `out/r77d_<dataset>/rejected.jsonl` ($0, no model): the 9
    furniture `cue_without_assertion` hedges ("kinda", "basically", "feel", "about" x3, "like", "a bit" x2)
    would be kept, 8 distinct claims ("cord EXHIBITS short" came in both passes); a tenth stray hedge was
    off-schema and stays rejected. Held-out and generality had none.
  - Gate: 552 passed (one new test, one parametrized case removed), `ruff check` clean.

### R82. One example: a denial inside a name is still negated and keeps its cue (done 2026-10-06, code only; prompt change)
In R77 part f held-out's named denials ("DO NOT LOCK", "DID NOT STOP", "NO FEEDBACK") kept the denial in the
name, as the prompt allows, but with truth affirmed and no `negation`: the rule's "the fact is still negated"
was ignored (Found along the way). Code cannot see the miss without a word list, which the user ruled out.
- **Scope (the user, 2026-10-06).** One domain-neutral example in the extraction prompt's `truth` rule,
  in the prompt's invented kettle domain: a state named by its denial, with truth "negated" and its
  `negation`. Nothing else: no lexical heuristic, no new field, no code check, no other prompt line.
- Measuring it needs a re-extraction (every extraction call misses the cache), which is a full-dataset
  run: proposed with its cost, not made in this step.
- **Done (2026-10-06, code only, no run).**
  - The `truth` rule's last sentence ("as a fault called "will not switch off"; the fact is still negated")
    became: the fact "is still negated and still has its `negation`: "the kettle will not switch off" can
    give the object "will not switch off" with truth "negated" and negation "will not"". The word "fault"
    went with it. Swept: no dataset word, no corpus four-gram (the existing prompt test).
  - Extraction `prompt_version` is now `990fea865d58`; the glean suffix is unchanged.
  - Test: `test_the_prompts_named_denial_example_is_what_code_accepts_and_counts_as_holding` (the example's
    own fields pass `verify`, and `triple_truth` keeps the state holding, so a count of such states still
    counts it). Gate: 553 passed, `ruff check` clean.
- **R77 refinement stops here** (the user, 2026-10-06): no further work on the assertion fields unless
  Step 8 shows a concrete retrieval or query failure that needs it. R81 and R82 are measured together, by
  one rebuild and a frozen-plan `kg qa` (R80), only when the user agrees to that run.

### R83. Step 8: the wrong answers after Step 7, by cause (done 2026-10-06, $0, no run; the user chose R84-R86)
Step 8 of the task file starts with a failure table: every wrong answer after Step 7, grouped by cause, so
that each addition proposed to the user has its failing questions as its reason.
- **Scope.** The reference run is R77 part f (`13ee2b6`, the last measured answers; R81 and R82 are not
  measured): 69 wrong answers, furniture 27 / 68, held-out 17 / 68, generality 25 / 41. No code change in
  `src/`, no gold change, no run.
- **Method.**
  - The rules are fixed before any classification (`tests/gold/r83/rules.md`): Step 2's query causes, R68's
    graph causes in R68's order, the reading causes, and a check for scoring artefacts first.
  - One cause per answer: the first step of the answer's own trace that goes wrong. At that step the cause
    is the query's when a plan that asks what the question says, with the existing primitives, would have
    found the needed items in this graph. Otherwise it is the graph's. Other causes that would also have to
    be fixed are listed under `also`.
  - Three Fable 5.1 subagents classified one dataset each from the files only: answers with plan traces,
    stored claims, rejections, identity decisions, source text. No Neo4j, which held test leftovers.
  - The lead judge (Opus 5.5) reviewed every row and confirmed the two code findings below in the code.
    Changed by the review: the fix of 9 rows, for which the closed list had only loose names. F17, F21,
    F23, F24, F32 became `query_code`; F06, F09, F11, F13 became `document_fields`. Both values were added to
    the rules. No cause was changed.
  - Files: `tests/gold/r83/<dataset>_failure_causes.json`. `tests/test_failure_causes.py` recomputes the
    wrong answers from the frozen answers (`tests/gold/r80/`), the gold and the part f verdicts. It checks
    that each wrong answer is classified exactly once, with its gold type and with a cause and fix from the
    closed lists.
- **The table** (cause of the first wrong step; furniture / held-out / generality):

  | Cause | F | H | G | all | questions |
  |---|---|---|---|---|---|
  | Q2 plan asked the wrong thing (expressible) | 6 | 9 | 3 | **18** | F12 F27 F36 F47 F59 F60 H23 H25 H26 H28 H32 H33 H36 H38 H55 G02 G06 G22 |
  | Q3 no primitive for it | 7 | 3 | 1 | **11** | F04 F05 F48 F52 F55 F58 F64 H11 H14 H44 G35 |
  | G2 no schema type | 5 | 1 | 4 | **10** | F06 F09 F10 F11 F13 H29 G12 G23 G25 G32 |
  | Q1 claim or entity words not linked | 5 | 2 | 0 | **7** | F17 F21 F23 F24 F32 H06 H27 |
  | Q4 every plan refused, fallback wrong | 0 | 0 | 5 | **5** | G01 G03 G15 G26 G28 |
  | G3 identity | 0 | 1 | 3 | 4 | H19 G04 G05 G30 |
  | G1 extraction miss | 0 | 0 | 4 | 4 | G13 G14 G18 G27 |
  | R1 read_check wrong | 1 | 1 | 1 | 3 | F30 H08 G24 |
  | G9 concept | 2 | 0 | 0 | 2 | F16 F26 |
  | G7 time or role | 0 | 0 | 2 | 2 | G21 G29 |
  | G5 assertion | 1 | 0 | 0 | 1 | F08 |
  | G6 attribution (speaker) | 0 | 0 | 1 | 1 | G31 |
  | S1 right by meaning, scored by form | 0 | 0 | 1 | 1 | G10 |
  | G4 attachment, G8 event structure, G10 sets, R2 ranking cut, R3 reader | 0 | 0 | 0 | 0 | |

  - The query layer accounts for 41 of 69, the graph for 24, reading for 3 and scoring for 1.
  - 16 of the 69 were right in R79 or R77 part c with another plan: 8 of the 18 Q2 rows and 3 of the 7 Q1.
    Part of the query share is planner variance, not a fixed defect.
  - `also`: Q2 6, G7 4 (a date only in a heading or an event's name: G04 G12 G23 G25), Q4 4, Q3 3.
- **Groups behind the counts, each with a real example:**
  - **Claim words never reach a record-linked subject (`query_code`, 5: F17 F21 F23 F24 F32).**
    `PlanRunner._claim_words` links a plan's claim words to concepts only (`linker.find(..., {"kind"})`).
    `find_claims` matches a claim's ends by the canonical id of their mentions. Since R75, "frame",
    "slats", "legs" and "drawers" in the reviews refer to Assembly records (`Assembly:A-1012` Frame, by
    name), so "frame creaks" can never be found by its subject. A bug of R75's mention graph, not of the
    planner.
  - **No step from claims to the records they are about (Q3, 3: F04 F05 H14).** The planner also wrote
    it in G01 G02 G22 G28 and was refused ("list: a property can be read only from records of one known
    label"). Example F04: "frame EXHIBITS creaks" found, then the price of its product could not be listed.
  - **Record operations, one question each (Q3, 7):** two properties of one record compared (H44 "filed
    more than 30 days after the incident"; F48 two relationship values); a multi-valued field ranked by its
    members (H11 "POWER TRAIN,ENGINE,FUEL/PROPULSION SYSTEM"); a hop after a rank (F52); a rank filtered on
    a relationship property (F55); records without a related record (F58); an intersection of two record
    sets (F64).
  - **A claim's value compared with a number (Q3 or Q4 with Q3, 3: G15 G26 G35; also G14).** For example
    "Station Road drainage, second phase HAS_COST 310,000 pounds" cannot be selected as "over 200,000".
  - **Actions and findings with no predicate (G2, 7: F10 H29 G12 G23 G25 G29 G32).** H29 "3RD TIME, PADS
    CHANGED AND ROTORS MACHINED" (no repair predicate), G12 "showed a slow drip from the mechanical seal"
    (no finding predicate), G25 seal replacements inside the broad SERVICES, G32 an award.
  - **Document header fields (G2, 4: F06 F09 F11 F13).** "## Rating: ★☆☆☆☆ (1/5)" and "- @scandi_lover
    (Minneapolis)" are in no claim and no record.
  - **read_check judges a chunk, not a claim (G24; also blocks G14 G26 G29).** The checker sees the
    statement and the candidate's chunks, never the candidate itself, so the 7 HP40-2291 claims of one
    work-orders chunk were all verified with one quote.
  - **Identity (G04 G05 G30):** "Jon Pike" and "J. Pike" are joined to each other, not to Staff S-131
    Jonathan Pike (`no_record`); there is also a Judith Pike in the corpus. **H19:** "PISTON SNAP RING
    RECALL" is an individual, not recall 16V074000 ("piston wrist pin circlip").
- **The task's candidates, measured:** open predicates 7 (G2 without the header fields), events 0 as a
  first cause (H29, G25 and G29 are event-like), sets and quantifiers 0, writing read_check results back
  0, an LLM-named attachment 0 (no G4), the speaker 1 (G31), `valid_time` 1 (G21; 4 more under `also`).
- **Gate:** 556 passed (553 + 3), `ruff check` clean. No run, $0.
- **The user's choice (2026-10-06):** three additions, one step each, done in this order: **R84** claim
  words reach mentions linked to records and individuals (the `query_code` group, code only); **R85**
  read_check judges each claim, not only its chunk (code only); **R86** a step from claims to the records
  they are about (a new primitive and a planner prompt change). Not chosen: open predicates for actions and
  findings (7), and the task's other candidates (events, sets, read_check write-back, LLM-named
  attachment, speaker, `valid_time`), each with 0-1 failures as a first cause.

### R84. Claim words reach a claim end that refers to a record (Step 8 addition 1; done 2026-10-06; furniture 41 -> 45 on frozen plans)
R83 found that a plan's claim words never reach a claim whose subject or object mention refers to a record:
`PlanRunner._claim_words` looked them up among concepts and individuals only, while R75 links "frame",
"slats" or "drawers" in the reviews to Assembly records. 5 furniture answers fail on it (F17 F21 F23 F24
F32). A bug of the query code, fixed alone; no prompt, no plan field, no graph change.
- **Scope.** `query/names.py`: `NameLinker.spelled` (the spelling part of `find`, now shared by it).
  `query/plan_run.py`: `_claim_words` adds the records spelled alike (by their names and the names of the
  mentions that refer to them) to the concepts and individuals it found before. Records are not added by
  meaning, because the nearest records to "frame" are whole products. `query/plan_cypher.py`: a claim end
  matches a mention by its canonical id, as before, or by the element id of the record it refers to
  (`_claim_end`).
- **Test first:** `test_claim_words_reach_a_claim_end_whose_mention_refers_to_a_record` (Neo4j; the notes'
  "spindle" refers to the part record "Spindle"). It failed before the fix: 0 claims, even on either end.
- **Expected effect, checked against part f's identity decisions ($0, no model):** the plans' words
  "frame", "drawer handle", "slats" and "drawer" now reach `Assembly:A-1012`, `A-1022`/`A-1071`, `A-1051`
  and `A-1021`/`A-1070`. "leg" does not reach "Legs" (`A-1014`): it is below the spelling score of 90, the
  linker's rule for every name, which is unchanged. So F24 stays out of reach by this fix.
- **Gate:** 557 passed (556 + 1), `ruff check` clean.
- **Measured (2026-10-06; the user's yes for R84 and R85 only, estimate $0.3-0.8 for both; spent $0.0022
  for R84).**
  - Recipe, one dataset at a time, with the normal guarded `kg` commands. The checkout was switched to
    `13ee2b6` and part f's graph rebuilt from the cache into `out/r84g_<dataset>`: frozen plan and text
    schema copied from `out/r77d_*`, `EXTRACT_PASSES=2`, extraction on an invalid Gemini key so that a
    cache miss would fail. Then the checkout was switched to `0449639` for `kg qa --system graph --plans
    tests/gold/r80/<dataset>` (R80), and back to `layered-model`.
  - The rebuild is part f's graph exactly, at $0: every extract and resolve call a cache hit (furniture
    140 and 300, held-out 162 and 266, generality 64 and 185), and the same claims and attachments
    (furniture 514 claims, 666 attached, 1207 attachments; held-out 532, 673, 3059; generality 212, 196,
    442).
  - Runs: furniture `qa_graph` `de37a529` $0.0022 (59 calls, 51 hits; tokens 3668 / 246 / 697), held-out
    `67d9d953` $0 (21, 21), generality `e465e414` $0 (60, 60). No plan refused; every answer frozen.
  - Judge: every free-text answer is word for word part f's (checked by code), so part f's verdicts carry
    over (`tests/gold/r84/`). Scores `95bdffaa`, `4aead2c9`, `81fbbb0d`; paired with part f `f447a697`,
    `b2e092fb`, `2dc61ea3`.

    | | part f | **R84** | only part f / only R84, p |
    |---|---|---|---|
    | furniture | 41 / 68 | **45 / 68** | 0 / 4, p 0.125 |
    | held-out | 51 / 68 | 51 / 68 | 0 / 0 |
    | generality | 16 / 41 | 16 / 41 | 0 / 0 |

  - Furniture gained exactly the four answers R83 expected, and lost none. F17 "Uppsala Sofa" (part f: no
    answer); F21 "Helsingborg Dresser"; F23 "Linköping Bed"; F32 "Helsingborg Dresser", "Norrköping
    Nightstand". By type: structured filter 12 -> 13, disambiguation 3 -> 5, negation-sensitive 3 -> 4.
    F24 ("leg") stayed out of reach, as expected.
  - Held-out and generality: no answer changed. Their linking failures (H06, H27) are not record-linked
    subjects.

### R85. read_check judges each claim, not only its chunk (Step 8 addition 2; done 2026-10-06; no answer changed on frozen plans)
R83 found that read_check gave every claim of one chunk the same verdict: the checker saw the statement and
the candidate's chunks, never the candidate. In G24 ("Who replaced the mechanical seal of pump
HP40-2291?") all 7 claims of the sentence "Mechanical seal of HP40-2291 replaced by technician Marek
Hollis" were verified with one quote, and the answer listed five subjects.
- **Decision (the user, 2026-10-06, asked before any code):** a code check and the claim shown to the
  checker. A code check alone cannot tell apart claims of one sentence (G24).
- **Scope.**
  - `query/read_check.py`: `CheckCandidate` (the claim as its own mentions word it, and its evidence).
    `build_prompt` shows it in a `<candidate>` block with one rule (`CLAIM_RULE`): yes only when this claim,
    read in its text, states the statement. `verify`: for a claim, the quote must hold the claim's evidence
    or lie inside it. A derived claim has no evidence and is checked by its chunk alone, as before.
  - For a record or a chunk the prompt is byte-identical to before (a test renders the old prompt), so
    those checks keep their cached requests.
  - `query/plan_cypher.claim_statements`, read by `PlanRunner._claim_candidates` for a read_check step's
    claims.
  - `pipeline/qa_stages.py`: `read_check_prompt_version` and the logged prompt include the claim rule and
    block, so the version changes.
  - Swept for dataset words: none. The prompt guard test now covers the claim parts and the reply schema.
- **Tests:** a record's check reads as before; a claim's check shows the claim and its rule; a quote from
  another sentence of the claim's chunk does not verify it, while a quote inside the evidence, or a passage
  holding it, does; a claim without evidence is checked by its chunk (pure). Neo4j: a plan's read_check over
  two claims of one chunk in two sentences verifies only the one whose sentence states the statement, and
  shows each claim to the checker. It failed before the fix: 2 verified, answer "Ada Rook" and "Spindle".
- **Gate:** 562 passed (557 + 5), `ruff check` clean.
- **Measured (2026-10-06; the user's yes; spent $0.0675).** The same rebuilt graphs and frozen plans as R84's
  measurement, with the checkout at `916d4bd`.
  - Runs: furniture `qa_graph` `546ffd5a` $0.0200 (59 calls, 12 hits; tokens 25934 / 1398 / 8761),
    held-out `ab02ac8b` $0.0159 (21, 4; 11100 / 995 / 9449), generality `4611b31e` $0.0316 (60, 12;
    28283 / 879 / 18395).
  - Judge: every free-text answer is word for word part f's (checked by code); verdicts carried over in
    `tests/gold/r85/`. Scores `61ae81d7`, `b7a5a098`, `d09d0174`; paired with R84 `25233e5b`, `4000e8b1`,
    `3a996d2c`.
  - **No answer's verdict changed** on any dataset: furniture 45, held-out 51, generality 16, each 0 / 0
    against R84.
  - read_check verified fewer claims: furniture 28 -> 27, held-out 14 -> 10, generality 18 -> 6, of the
    same 49, 17 and 48 checks. The checker now refuses claims whose own sentence does not state the
    statement.
  - G24 is the case R83 named: its verified subjects went from 5 ("mechanical seal", "HP40-2291", "Marek
    Hollis", "WO-318", "WO-320") to 3 ("mechanical seal", "Marek Hollis", "WO-318"). It is still wrong,
    because the plan lists the subjects of every claim that states the replacement.
  - Other changed answers: F09's count went from 1 to 0, still wrong; the question rests on star ratings
    no claim holds (R83: `document_fields`). Citations went from 45 / 17 / 32 to 44 / 13 / 20, all
    faithful.
  - Reading: R85 makes read_check stricter without costing an answer, but on these frozen plans it gains
    none either.

### R86. `about`: from claims to the records they are about (Step 8 addition 3; done 2026-10-06, code and prompt, no run)
R83 found no way for a plan to go on from the records claims are about: `list` and `count` could end a
plan with them ("about"), but no step could read their properties or follow their relationships. 3 answers
fail on it as the first cause (F04 "frame EXHIBITS creaks" found, then the product's price could not be
listed; F05; H14), and in G01, G02, G22 and G28 the planner wrote such a step and was refused.
- **Scope.**
  - `query/plan.py`: a new primitive `about(input, label?)`, rule `_About`. It takes claims and produces
    records, of `label` when given, which must be a record label. So `list` with a property, `related`,
    `rank` and `find_claims` can follow. `list`'s refusal of a property read from claims now names `about`,
    since that reason reaches the model in its retry. `CLAIM_OPS` lists it.
  - `query/plan_run.py`: `_about` runs the existing `plan_cypher.claims_about`, the same records `list` and
    `count` read for "about".
  - `query/planner.py`: one primitive line, with its intent comment. The `label` field description names
    `about`. Swept for dataset words: none; the prompt guard test covers the prompt and the plan's schema.
- **Tests:** the check (claims to records of a label, then a property list or a relationship, pass; a
  non-record label and a record input are refused; the list refusal names `about`). Neo4j: the price of
  the press a wobbling claim hangs on, and its parts. Both failed before: `about` was no primitive.
- **Gate:** 564 passed (562 + 2), `ruff check` clean.
- **Not measured, the user's decision (2026-10-06).** The planner prompt changed, so measuring needs fresh
  plans (about $1.1-1.4), not R80's frozen ones; the user chose not to measure it.
- **Step 8 done (2026-10-06):** the failure table and the user's three additions (R83-R86) committed, R84
  and R85 measured. Next: Step 9, finishing the arm.

### R87. Graph-correctness audit of the layered graph (started 2026-10-06; part a done; parts b-g replaced by the anchor-graph evaluation, R88)
Before more is built, measure whether the graph itself is right (the user, 2026-10-06): identity, claims,
mention-to-record links, attachment, provenance and traversal, on the last full build `out/r77d_*`
(`13ee2b6`; eval runs `c409e1a9`, `f375def2`, `9a4e74ca`). Steps 5-8 were measured by answers, and the
graph's own scores are either unmeasured since R68 (claim precision and recall) or cannot see a wrong link:
`path_truth` re-applies the rule that made each edge (`validation/paths.py:121-132` uses the same
`claim_sentences` + `contains_words` as `attachment.named_in_quote`), so it is 1.0 by construction. A
read-only look at `out/r77d_*` found 4 of 50 furniture record links on another product's part (the
whole-domain fallback of `records.py:150`; the Malmö Desk's "drawer" on the Norrköping Nightstand's
`Assembly:A-1021`), "drawer slides" on the record "Drawer Sides" (spelling 96), claims hung on things only
co-named in their sentence ("seat PART_OF Stockholm Chair" on "back angle"), and 29 held-out Vehicle
mentions named after recall keys (derivation).
- **Decisions (the user, 2026-10-06):** no architecture change, no rebuild, no Neo4j, no pipeline LLM call;
  QA planner, R86, agentic querying, hybrid RAG and new metadata are out of scope. The judge is **Claude Opus
  5.5** in the session (subagents for blind batches, the lead reviewing every INCORRECT, AMBIGUOUS and
  UNJUDGEABLE verdict and 10% of the VALID ones), with the verdict labels VALID, VALID_ALTERNATIVE,
  INCORRECT, AMBIGUOUS, UNJUDGEABLE. The graph comes from an **offline snapshot** rebuilt from `out/` and the
  data with the build's own pure functions, behind a fidelity gate.
- **Metrics** (plan: definitions, denominators, thresholds): M0 snapshot fidelity; M5 provenance (code);
  M3 mention-to-record links (code flags + judge); M1 identity, wrong merges and splits (judge); M4
  attachment (claim-centred sample, judge); M6 traversal, missing paths (code) and false paths (M1, M3, M4
  and the ABOUT links judged); M2 claim precision and recall on R68's scope and gold (judge, `kg rescore`).
- **Split: seven parts, one commit each, in order.** (a) snapshot, fidelity gate and code-only checks ($0,
  no judge); (b) audit sheets, verdict schema, scorers, the M2 converter, the rules file and the generality
  traversal targets; (c) judge M3; (d) judge M1; (e) judge M4 and the ABOUT links, compute M6; (f) judge M2
  and rescore; (g) results, failure classes ranked by impact, fix proposals for the user. Fixes come only
  after (g), each its own step with a failing test first.
- **Part a: snapshot, fidelity gate and code checks (done 2026-10-06, $0, no judge, no LLM, no Neo4j).**
  - Structural moves first, behaviour kept: `text/subject_graph._collect` is public as `collect_rows`;
    the pure body of `resolution/derivation.derive_facts` is `derive_rows` (with `DerivationSource`,
    `DerivedRows`), which `derive_facts` now calls. The derivation and subject-graph tests pass unchanged.
  - New package `audit/`: `inputs.py` (records and relationships from the staged tables under the plan,
    the importer's rules; the 2-hop scopes; the corpus through the build's loaders and chunker),
    `snapshot.py` (the graph rebuilt in stage order with the build's own pure functions: `collect_rows`,
    `match_document`, `match_chunk_records`, `derive_rows`, resolve.json as REFERS_TO, `dominant_record`,
    `attach`; things by record ref or canonical id), `fidelity.py` (M0), `scope.py`, `checks.py` (M5 and
    the flags), `reach.py` (M6 missing paths and foreign chunks). `pipeline/audit_stages.py`
    (`AuditSnapshotStage`, one MLflow run per build) and `kg audit-snapshot BUILD --data --logged
    [--reach-claims --reach-sample] --out`. Logged counts of the three builds, copied from their MLflow
    stage runs with the run ids: `tests/gold/r87/<dataset>_logged.json`.
  - Tests: `tests/test_audit.py` (11; an invented lamp-and-kettle build with one planted cross-scope link
    and one compound name: the importer's rules, the rebuilt links, derived claims and attachments, the
    gate passing and failing count by count and fact by fact, each flag, a label mismatch, a quote not in
    its chunk, reach with a chunk without claims left out, the stage's params, metrics and artifacts, a
    folder that is no build refused). Gate: 575 passed (564 before), `ruff check` clean.
  - **Runs** (`kg audit-snapshot`, $0): furniture `0e2399c6`, held-out `77e0f894`, generality `ad68e031`;
    reports in `out/r87a_<dataset>/` (snapshot, fidelity, code checks).
  - **M0 fidelity: passed on all three.** Every logged count equal (chunks 70 / 81 / 32, claims 666 / 673 /
    212 of which derived 152 / 142 / 0, mentions 607 / 520 / 255, attachments 1207 / 3059 / 442 with every
    route's count), the mention ids exactly resolve.json's, and fact by fact the same attachments as the
    build's judge sheets (665 furniture facts, 165 held-out). The 665-vs-666 gap is the sheet's `flatten`
    (a self-reference left out), not the graph. So M3-M6 can read the snapshot as the build's graph.
  - **M5 provenance (code): 1.0 everywhere.** Every claim's chunk exists and is of its document, every
    quote is in its chunk, every extracted claim passes `verify` again, every extracted mention's name is
    in a chunk that MENTIONS it (extracted 514 / 531 / 212, derived 152 / 142 / 0).

    | Code check | furniture | held-out | generality |
    |---|---|---|---|
    | record links (no scope) | 50 (0) | 76 (0) | 40 (40: no document has an anchor) |
    | `cross_scope_link` | **4** | 0 | n/a |
    | `label_mismatch` | 0 | **29** | 0 |
    | `fuzzy_name` (score < 100) | 6 | 0 | 0 |
    | `cross_scope_attachment` | **23** | 0 | n/a |
    | `compound_name` | 75 | 137 | 39 |
    | `key_in_sentence` edges to a non-end thing | 178 / 578 | 1908 / 2132 | 148 / 433 |
    | individual names split over several canonicals (extra nodes) | 12 (22) | 3 (3) | 9 (9) |
    | reach of R68 gold pairs, any pattern (P1 alone) | 31/31 (31/31) | 34/34 (34/34) | targets in part b |
    | foreign chunks reached, P1 / P4 | 7 / 5 | 0 / 0 | n/a |

  - Known answers found: the 4 cross-product links (the Malmö Desk's "drawer" and "drawers", the Linköping
    Bed's "frame", the Jönköping Coffee Table's "center support"); the 29 Vehicle mentions named after a
    recall key; "drawer slides" -> `Component:S-1076` "Drawer Sides" among the `fuzzy_name` flags.
  - Real examples of the rest (flags, not verdicts): `cross_scope_attachment` "lower shelf EXHIBITS
    sagging" (coffee table review) on the bed's `Assembly:A-1052` "Center Support"; `compound_name`
    "drawer rails EXHIBITS uneven metal edges" on `Assembly:A-1070` "Drawers" ("drawer" inside "drawer
    rails"); held-out `compound_name` flags are mostly recall listing sentences ("2016-2017 Nissan Maxima,
    2013-2016 Nissan Murano, ..."), where the judge must decide whether the claim is about each vehicle.
    Foreign chunks: `Assembly:A-1021` (the nightstand's drawer) reaches `malmo_desk_reviews.md#1` and `#5`.
  - Reach is complete where gold exists: every R68 (thing, chunk) pair with a claim is reached, and by P1
    alone, so the open question is false paths (parts c-e), not missing ones. The furniture split groups
    are generic parts ("instructions" in 5 reviews); generality's are places and people (Utrecht, North
    Station, Rosa Delgado), for the judge in part d.
- **Parts b-g replaced (the user, 2026-10-06).** The layered-model branch was merged into `main` (fast-forward
  to `0618228`) and the anchor-graph direction started on the branch `anchor-graph` (R88). Its evaluation
  reuses part a as it is (snapshot, fidelity gate, code checks) and takes over M1, M3 and M6 as criteria C3,
  C4, C6 and C8, applied to the anchor and layered arms. M2 and M4 are judged only if the ablation leaves the
  claim layer a role. R87 ends with part a.

### R88. The anchor-graph direction and its evaluation criteria (done 2026-10-06; document only, $0, no run)
The user, 2026-10-06, after a design discussion on R73, R77, R83 and R87: the graph's job is to locate the
entities, concepts and records a need is about and to lead correctly from them to their chunks, documents and
records; understanding the text is left to a query stage designed later. The graph is therefore judged as an
index, not by answers.
- **Scope.** A new direction document,
  [docs/direction/2026-10-06_anchor-graph/anchor-graph_2026-10-06.md](docs/direction/2026-10-06_anchor-graph/anchor-graph_2026-10-06.md):
  why (the leak of the claim-as-edge graph; the layered model's claim fields changing few answers and losing on
  generality), the goal, the model (the labels the build already writes, without the `:Observation` layer
  for navigation), the witness rule ("a shared node helps to find chunks; it never proves a fact"), identity
  classes, the navigation contract W1-W5, generality by topic and by form, and the criteria. No code, test or
  gold change.
- **Criteria** (section 7 of the document; thresholds fixed before measuring): C0 fidelity and C1 provenance
  (hard, from R87 part a); C2 findability (hit@1, hit@5, no LLM); C3 identity (hard: 0 wrong merges of
  records or individuals); C4 record linking (hard: 0 confirmed cross-scope links, judge precision ≥ 0.95;
  **fails today**: 4 of 50 furniture links on another product's part, 29 held-out `label_mismatch`); C5
  evidence reach at a fixed budget (recall@5, @10, against vector retrieval at the same *k*); C6 purity (hard
  ≥ 0.95 for record and individual starts); C7 selectivity (hubs above 20 % of the corpus listed); C8
  connectivity (hard: every hop witnessed); C9 generality and cost.
- **Ablation:** arm A (anchor walks only) against arm B (also through claims) on R87's offline snapshot of
  `out/r77d_*`, $0 and no LLM; arm C (vector retrieval) for C5 only. It replaces the `kg qa` ablation the user
  approved on 2026-10-06 ($0.2-0.4), which is not run.
- **Next (proposed order, section 9):** target gold for C2, C5, C8; the navigation contract in code over the
  snapshot with the code-computed criteria; vector retrieval for C5 (asked for first); judging C3, C4, C6;
  results and decision.
- **Gate:** 575 passed (baseline 575), `ruff check` clean. No run, $0.

### R89. Target gold for the anchor-graph criteria C2, C5, C8 (done 2026-10-06; gold and its checks, $0, no run)
Step 2 of the anchor-graph direction (section 7.4): for every question of the three QA gold files, the names
it starts from, the other names the sources write for them, and what each should reach. The QA questions
are read as information needs; their `chunks` and `records` stay the evidence and are not copied.
- **Format** (`validation/target_gold.py`): a target is a `name` (as the question writes it), `aliases`
  (a short form, a title, a key the text uses, a record's own name; never an inflection), and the nodes it
  reaches without any graph id: `records` (a staged row picked by its cells; one ref may pick several rows,
  "drawer rails" without a product = every Drawer Rails part) and/or `mentions` (`MentionRef`, document +
  names, as in the R75 identity gold: the thing those mentions refer to). A question without a named start
  ("Which products cost more than $500?") has no target and a note why. `check_target_gold` ties a file to
  its QA gold (every question once, no unknown id) and its corpus (each name in its question, each alias
  written in a chunk or a staged cell, each record ref picks a row, each mention's document names it).
  Structural move first: `qa_gold.rows_matching` made public from `_record_issues`, behaviour kept.
- **Gold** (`tests/gold/r89/`, written by Claude Opus 5.5 from the questions and the source data only; no
  `out/` file opened):

  | Dataset | Questions | Targets (records / mentions) | No target | Chunk questions with a target (C5) | multi_hop with a target (C8) |
  |---|---|---|---|---|---|
  | furniture | 68 | 86 (59 / 27) | 18 | 31 / 33 (F11, F13 rank over all) | 16 / 17 (F67) |
  | held-out | 68 | 65 (29 / 36) | 17 | 28 / 28 | 15 / 17 (H64, H67) |
  | generality | 41 | 62 (10 / 53) | 1 | 38 / 38 | 6 / 6 |

  Choices recorded in notes, for example: a part named with its product reaches only that product's part
  (F01 S-1085), without one every part of the name (F20 S-1078 and S-1085); states are concepts
  ("wobbling", "creaks", "dead battery"); a word written differently in each review names no concept
  ("misaligned", F08); "Pike" (G36) reaches Staff S-131 and the Judith Pike of the letter; each Maria Lopez
  reaches her own record (G01, G22 S-104; G02 S-219). Record-filter values (price, country, dates) are
  not starts.
- **Tests:** `tests/test_target_gold.py` (17): the three files against their QA gold and rebuilt corpus,
  fewer than a third of questions without a target, the identity traps pinned (Maria Lopez, the Pike
  spellings, both Drawer Rails parts, drawer slides reaching no record), the model rules, and each misfit
  the check reports on an invented corpus.
- **Stated limitation:** earlier sessions read `out/r77d_*`, so the gold is not blind to the build; gold and
  later verdicts come from one model family.
- **Gate:** 592 passed (baseline 575), `ruff check` clean. No run, $0. Next: step 3 of the direction, the
  navigation contract W1-W5 over R87's snapshot with C0-C2, C5 (graph arms), C7, C8, C9 computed.

### R90. The navigation contract and the code-computed criteria (started 2026-10-06; $0, no LLM, no Neo4j)
Step 3 of the anchor-graph direction. **Split into two parts, one commit each** (two concerns): (a) the
walks W1-W5 as pure functions over R87's offline snapshot, in two arms, and the target gold placed on a
build's nodes; (b) the criteria C0-C2, C5 (graph arms), C7, C8, C9 computed from them, a stage with one
MLflow run per dataset and arm, `kg anchor-eval`, and the six runs on `out/r77d_*`.
- **Part a: navigation and target placement (done 2026-10-06).** New package `anchor/`:
  - `navigation.py`, `AnchorGraph(snapshot, arm)`:
    - **W1** `find(names)`: exact hits first. A name hits a node when it equals, after `norm`, one of
      the node's names: its record name or key, its canonical name, or the name of a mention that
      refers to it. Then word-overlap hits, best Jaccard first. Ties go to the more-mentioned node, then
      to the node id.
    - **W2** `chunks_of`: MENTIONS + REFERS_TO, plus the document's and the chunk's ABOUT links.
    - **W3** `about`: the chunk's own ABOUT, else its document's.
    - **W4** `related`: any number of hops.
    - **W5** `context`.
    - `walk(starts)`: the composed breadth-first walk (W2, W3, W4; each step counts 1), with chunk ->
      walk length.
    - `thing_edges()`: every thing-to-thing hop with its witness.

    Arm B (`Arm.LAYERED`) adds the claim layer. A thing reaches the chunk of every claim attached to it
    or ending on it, and a claim joins its ends and its attached things pairwise. A join is witnessed
    only when its chunk concerns both things in arm A.
  - `targets.py`, `TargetPlacer`:
    - A record ref becomes the records its staged rows fed, under the build's plan; a relationship-only
      file gives none.
    - A mention ref becomes what its document's mentions refer to: by an equal name first; only when the
      document has no equal name, by mentions whose name holds the gold name as whole words (marked
      `loose`).
    - Unplaced refs are listed in `missing`.
- **Instrument correction (after the first placement on the snapshots, before any criterion was computed):**
  the containment tier was added. With equal names only, 81/86 furniture, 49/65 held-out and 36/62
  generality targets found a node, because the extractor writes longer names for the same thing
  ("low-pressure fuel pump", "institute committee, 12 May 2025", "Eastgate library roof"). With it:
  84/86, 54/65, 47/62. The rest are names the build never wrote ("crash" in the Escape complaints,
  "Brackwater fen", "Hensley Field-Work Award").
- **Tests:** `tests/test_anchor.py` (13). The invented snapshot has a lamp and a kettle, each with a part
  named "Switch", and one claim of the kettle's chunk attached to the lamp. The tests cover:
  - W1 ordering, and a node found by its mention's name;
  - W2 in both arms, W3 precedence, W4 by relation and by claim, W5;
  - the walk staying on the lamp's side in arm A and leaking into the kettle in arm B;
  - exactly the two unwitnessed joins;
  - record and mention placement, the containment tier and its fallback order.
- **Gate (part a):** 605 passed (592 before), `ruff check` clean. No run, $0.
- **Part b: the criteria, the stage and the runs (done 2026-10-06, $0).**
  - `anchor/criteria.py`:
    - **C2** `findability`: hit@1 and hit@5 of W1 on name + aliases, over every target. A target the build
      has no node for is a miss, counted as `unplaced`.
    - **C5** `evidence_reach`: in two start modes, `gold_start` (the placed nodes) and `end_to_end` (W1's
      best hit per target). Chunks are ranked by the direction's rule (`rank_chunks`). Reported: micro
      recall@5 and @10 over gold chunks, the questions with every gold chunk within the budget (the
      per-question outcome for pairing), and unbudgeted reach.
    - **C7** `selectivity`: W2 size as a share of the corpus for every node in W1's top 5 per target;
      median, p90, hubs above `anchor_hub_share`.
    - **C8** `connectivity`: the gold records and chunks of multi-hop questions reached from their starts;
      a gold record that is itself a start does not count. Also every thing-to-thing hop of the arm and
      how many are unwitnessed.
    - **C9** `size`: nodes and edges per chunk by the labels each arm walks, and the build's logged cost.
  - `anchor/report.py`: `AnchorReport` + `metrics()` (`c0_…`-`c9_…`; a rate without n logs nothing).
  - `pipeline/anchor_stages.py`, `AnchorEvalStage(arm)`: rebuilds the snapshot, runs R87's fidelity gate
    (C0) and provenance (C1), places the targets, and logs one MLflow run per build and arm.
  - `kg anchor-eval BUILD --data --logged --targets --arm`.
  - Settings `anchor_budgets` [5, 10] and `anchor_hub_share` 0.2.
  - `LoggedCounts.usage`: the build's per-stage cost and tokens, copied from MLflow into
    `tests/gold/r87/<dataset>_logged.json` from the same run ids as the counts (additions only).
- **Runs** (`kg anchor-eval` on `out/r77d_*`). First made in the `dev` experiment before part b was
  committed, then repeated in R91 at a clean commit, with identical metrics. The current runs are the
  R91 ones, anchor/layered: furniture `bac9c159` / `3a179c4b`, held-out `71b20532` / `aa172378`,
  generality `eae344b6` / `e5df0b20`. The reports are committed in `tests/gold/r90/`. C0 passed and
  C1 = 1.0 everywhere. Rates as k/n:

  | | furniture A | furniture B | held-out A | held-out B | generality A | generality B |
  |---|---|---|---|---|---|---|
  | C2 hit@1 / hit@5 (n targets) | 70 / 77 of 86 | same | 46 / 52 of 65 | same | 41 / 45 of 62 | same |
  | C2 unplaced targets | 2 | 2 | 11 | 11 | 15 | 15 |
  | C5 gold start: recall@5 / @10 (gold chunks) | 41 / 56 of 61 | 41 / 55 | 28 / 30 of 36 | 28 / 31 | 36 / 40 of 51 | 36 / 45 |
  | C5 gold start: all gold within 10 (questions) | 29/31 | 28/31 | 24/28 | 25/28 | 29/38 | 32/38 |
  | C5 end to end: recall@5 / @10 | 28 / 40 | 29 / 42 | 24 / 26 | 25 / 28 | 28 / 33 | 33 / 44 |
  | C5 unbudgeted reach (gold start) | 57/61 | 58/61 | 31/36 | 31/36 | 40/51 | 46/51 |
  | C7 median / p90 share; hubs > 20 % | 0.014 / 0.071; none | same | 0.012 / 0.049; none | same | 0.031 / 0.156; HP40-1183, HP40-2291 (0.22) | same |
  | C8 connections (multi-hop) | 13/14 | 14/14 | 16/16 | 16/16 | 7/12 | 12/12 |
  | C8 thing hops, unwitnessed | 328, **0** | 1874, **219** | 54, **0** | 18757, **875** | 0, **0** | 607, **0** |
  | C9 nodes / edges per chunk | 17.5 / 25.8 | 27.1 / 71.6 | 13.2 / 17.3 | 21.5 / 80.0 | 14.9 / 17.1 | 21.5 / 50.8 |
  | C9 build cost (USD, tokens) | 0.46, 297k | same | 0.62, 377k | same | 0.23, 161k | same |

  Read with care:
  - **C2 and C7 do not depend on the arm:** W1 is the same lookup, and claim ends are always mentioned in
    their claim's chunk, so claims add almost no chunks to the nodes a lookup shows.
  - **The C8 hard rule holds in arm A (0 unwitnessed) and fails in arm B.** All 219 / 875 unwitnessed hops
    come from `key_in_sentence` attachments: a thing hung on a claim because its name stands in the
    sentence, with no mention of it in that chunk. Example: "drawer rails EXHIBITS allow the drawers to
    slide smoothly" hung on the dresser's Drawers assembly A-1070. No claim end is ever unwitnessed.
  - **Where B reaches more, it is generality** (C5 @10 45 vs 40; C8 12 vs 7). The text there has no
    ABOUT anchor and the pump mentions do not link to the Pump records, so only the claim attachments
    lead from "KV12-0457" in the Harbour Station report to `Pump:KV12-0457` (G28, G34). *(Corrected in
    R97: the mention does link to the record, by key; what was missing is a walk from a chunk to the
    things it names.)* "Jon Pike" in
    the minutes is an individual apart from Staff S-131 (C3's known split, G04).
  - **Unbudgeted misses in A are mostly targets the build has no node for:** "customer service" (F10),
    "repair", "rear-end collision", "crashed" (H29-H31), "leaking", "Hensley Field-Work Award".
  - **Not yet done:** pairing A with B question by question (McNemar) waits for step 4, which adds arm C
    and the comparison of reports. C3, C4 and C6 are step 5 (judge).
- **Tests:** `tests/test_anchor.py` (+10, 23 in all):
  - the ranking rule; C2 with an unplaced target;
  - C5 in both modes, with the budget and a question without a start;
  - end to end starting from W1's best hit (the lamp's switch, not the kettle's);
  - C7 hubs, median and p90; C8 reaching the kettle only through arm B's unwitnessed claim;
  - C9 counts and cost; the report's metrics;
  - the stage in both arms on the audit's invented build (params, metrics, artifact).
- **Gate (part b):** 615 passed (605 before), `ruff check` clean. Six offline runs, no LLM, no Neo4j, $0.
  Next: step 4 of the direction, vector retrieval for C5 (arm C: question embeddings, a few cents; ask
  first) and the paired comparison of arms.

### R91. Runs name their uncommitted files; R90's runs repeated on a clean commit (done 2026-10-06, $0)
The user asked that everything be logged so it can be reviewed later. A check of R90's six MLflow runs
found two faults:
- **Not traceable to a commit.** Every run is tagged `git_sha = e792ae9-dirty`: they ran before part b was
  committed, so no commit holds their code. Even on a commit, every run of this checkout is `-dirty`,
  because the user's local `.claude/settings.json` (a permission rule, not code) is uncommitted.
- **Wrong experiment.** They went to `kgbuilder-dev`, the experiment of `.env`'s `KG_PRESET=dev`, while
  R87a's runs of the same builds are in each dataset's experiment (`--preset quality` / `heldout` /
  `generality`).

Fix:
- `cli.git_tags(sha, status)`, pure, called by `run_tags`: a dirty run also gets the tag
  `git_dirty_files` (the tracked files that differ from the commit, at most 20 named). A reviewer can
  then see that only `.claude/settings.json` differed and the code is exactly `git_sha`.
- Test in `tests/test_cli.py`.
- R90's six runs repeated at this commit (`a360797`) with the dataset presets, in R87a's experiments
  (`kgbuilder` for furniture, `kgbuilder-heldout`, `kgbuilder-generality`). Each is tagged
  `git_sha = a360797-dirty` with `git_dirty_files = .claude/settings.json`, so the code is exactly
  `a360797`. Every metric equals the superseded `dev` run's; only `duration_s` differs. The `dev` runs
  `29af8c28`, `772497c6`, `42608645`, `9a9835a5`, `def55168`, `73738c63` are kept, not deleted.
- **The results are committed:** the six reports in `tests/gold/r90/<dataset>/anchor_<arm>.json`
  (about 500 KB), and `tests/gold/r90/runs.json` mapping each one to its MLflow run id, experiment,
  `git_sha`, build and the hashes of the target gold, QA gold and logged counts it read. They are the
  reference results that step 4 (pairing arms) and step 6 (decision) read. `mlflow.db`, `mlruns/` and
  `out/` stay local and git-ignored.
- **Tests:**
  - `test_git_tags_name_the_uncommitted_files_of_a_dirty_tree` (`tests/test_cli.py`);
  - in `tests/test_anchor.py`, every committed report must load as `AnchorReport` with C0 passed, the
    gold and logged hashes must equal the committed files, and only `.claude/settings.json` may be dirty.
- **Gate:** 623 passed (616 after the code commit, 615 before R91), `ruff check` clean. Six offline runs,
  $0.

### R92. Arm C (vector retrieval) and the arms paired question by question (done 2026-10-06; about $0.004)
Step 4 of the anchor-graph direction. The user agreed to the embedding call on 2026-10-06 ("proceed",
after the estimate "a few cents").
- **Arm C must re-embed.** The builds did not save their chunk vectors, and the Neo4j graph that held
  them is gone (the test suite wipes it). So arm C embeds again:
  - the chunk texts the ingest stage embedded (`c.text`, `IngestTextStage`), rebuilt by
    `audit.inputs.read_corpus`;
  - the question texts;

  both with the build's model, `gemini-embedding-001` (logged by all three ingest runs).
- **Code:**
  - `anchor/vector.py`:
    - `cosine_rank` (cosine, ties by chunk id; keeps the cosine for review);
    - `vector_reach`: C5 in the graph arms' shape (`EvidenceReach`, mode "vector"), over the same
      questions as the graph arms' C5 pool, in their order.
  - `anchor/compare.py`:
    - `pair`: McNemar over two outcome maps, refusing different question sets, naming the questions
      only one side got;
    - `compare_arms`: A vs B on C5 "every gold chunk within k" (both start modes, k = 5, 10) and on C8
      "every connection reached"; A vs C on C5 (both modes);
    - `ArmComparison` with the rankings and the metrics.
  - `AnchorCompareStage` and `kg anchor-compare BUILD --data --targets --anchor-report --layered-report`:
    one MLflow run per build. Metrics: arm C's C5, every pairing's counts and p, the chunks, questions
    and characters embedded (the Gemini API reports no tokens for embeddings). Artifact:
    `anchor_compare.json`.
  - The run guard lists `anchor-compare` as a paid command (with an `ask_permission` preset it asks).
- **Tests:** 8 in `tests/test_anchor.py`:
  - the ranking and its ties; vector reach over the pool;
  - `pair`, including refusal of different question sets;
  - `compare_arms` on the invented snapshot: only arm C finds the kettle chunk from "sticking", and only
    arm B's leak connects it;
  - the stage with a word-count embedder (chunks embedded before questions, metrics, artifact), and
    reports passed in the wrong order refused.

  The run-guard test gains one asking case (`anchor-compare` with `quality`) and one silent case
  (`anchor-eval`).
- **Gate (code):** 631 passed (623 before), `ruff check` clean. Runs: next, committed separately.
- **Runs (done 2026-10-06, about $0.004).** `kg --preset <quality|heldout|generality> anchor-compare`, at
  `ac6d286` with only `.claude/settings.json` dirty, reading the committed R90 reports. MLflow:
  furniture `71cdd462`, held-out `f3360e62`, generality `15360b2b`.
  - **Size of the call:** 70 / 81 / 32 chunks and 31 / 28 / 38 questions embedded, 88,254 characters in
    6 calls (about 22k tokens). MLflow logs `cost_usd` 0 because the API reports no tokens.
  - **Committed:** `tests/gold/r92/<dataset>/anchor_compare.json`, with `tests/gold/r92/runs.json` (run
    ids, commit, model, the hashes of the reports paired). A test checks they load, pair the committed
    R90 reports, and that arm C answered exactly the graph arms' questions.
  - **Sanity:** for "Which products do reviews report with defective drawer rails?" arm C's top four are
    Helsingborg Dresser chunks; for F31 (Malmö Desk wobbles) the gold chunk `malmo_desk_reviews.md#4` is
    third.

  Questions with every gold chunk within the budget (C5, "complete"), McNemar exact p, A = anchor,
  B = layered, C = vector:

  | | furniture (31) | held-out (28) | generality (38) |
  |---|---|---|---|
  | C: recall@5 / @10 (gold chunks) | 40 / 51 of 61 | 34 / 34 of 36 | 44 / 49 of 51 |
  | gold start, k = 5: A vs C | 19 vs 21, p 0.69 | 22 vs 26, p 0.29 | 25 vs **34, p 0.012** |
  | gold start, k = 10: A vs C | 29 vs 26, p 0.38 | 24 vs 26, p 0.69 | 29 vs **37, p 0.008** |
  | end to end, k = 5: A vs C | 15 vs 21, p 0.070 | 19 vs 26, p 0.065 | 17 vs **34, p < 0.001** |
  | end to end, k = 10: A vs C | 22 vs 26, p 0.34 | 21 vs 26, p 0.18 | 22 vs **37, p < 0.001** |
  | gold start, k = 10: A vs B | 29 vs 28, p 1.0 | 24 vs 25, p 1.0 | 29 vs 32, p 0.25 |
  | end to end, k = 10: A vs B | 22 vs 22, p 1.0 | 21 vs 23, p 0.50 | 22 vs **31, p 0.004** |
  | C8 every connection reached: A vs B (questions) | 6 vs 7 of 7, p 1.0 | 7 vs 7 of 7 | 2 vs 6 of 6, p 0.125 |

  Read with care:
  - **On product-like data the anchor graph finds text about as well as vector retrieval.** Starting
    from the right node, it leads the 10-chunk budget on furniture (29 vs 26). Through its own lookup (end
    to end) it trails at 5 chunks by a margin near significance (p 0.065-0.070).
  - **On generality it loses clearly, in both modes.** Many targets have no node at all (15 of 62
    unplaced: "leaking", "Hensley Field-Work Award"), the documents have no ABOUT anchor, and the pump
    and staff mentions are not linked to their records (R90). *(Corrected in R97: they are linked, by key
    and by name; no walk led from a chunk to them.)*
  - **The claim layer (B) helps only there, through the attachments R90 found unwitnessed.** It never
    wins on furniture or held-out.
  - **By the direction's rule (section 7.5)**, "A does not beat vector retrieval on C5": the graph's value
    has to come from records, identity and connectivity (C4, C8), not from finding text. The decision is
    step 6's, after the judged criteria (step 5).
  - The pool is the graph arms' questions with a start: F11 and F13, which name no start, are left out
    of every arm.
- **Gate (results):** 634 passed, `ruff check` clean.

### R93. Judging C3 (identity), C4 (record linking) and C6 (purity) (done 2026-10-06; $0, no LLM call)
Step 5 of the anchor-graph direction. The judge is Claude Opus 5.5 (`claude-opus-5-5`) in the session.
It works on R87's offline snapshot of `out/r77d_*` (`13ee2b6`; C0 passed on all three builds) and follows
R87's judging decisions:
- blind subagent batches, then the lead's review;
- the labels VALID, VALID_ALTERNATIVE, INCORRECT, AMBIGUOUS, UNJUDGEABLE;
- a reason and a verbatim evidence quote per verdict.

No threshold or definition of the direction document is changed except as recorded under "Decisions".
- **Split: three parts, one commit each, in order** (code and its results may be two commits, as in
  R92).
  - (a) **Sheets, verdict models and scorer.** Code only, $0, then the sheets built offline and
    committed.
  - (b) **Judging.** Blind subagent batches per dataset and criterion, then the lead's review.
  - (c) **Scoring and results.** A stage and `kg` command logging the judged criteria, one MLflow run per
    dataset, and the results table.
- **Denominators** (counted on the snapshots before any judging):

  | | furniture | held-out | generality |
  |---|---|---|---|
  | C3 concept / individual nodes with > 1 mention | 77 / 4 | 41 / 10 | 8 / 31 |
  | C3 split groups (R87) | 12 | 3 | 9 |
  | C3 R75 identity pairs (code) | 67 | 23 | 24 |
  | C4 mention-to-record links | 50 | 76 | 40 |
  | C6 (start, chunk) pairs: record / individual / concept starts | 97 / 6 / 37 | 74 / 4 / 47 | 26 / 54 / 10 |
  | C6 pairs only arm B reaches | 6 | 0 | 0 |

- **Decisions (the user, 2026-10-06, on the part-a plan):**
  1. **C6 is judged as a census of every pair (355), not as flagged pairs plus a 10 % sample.**
     - The flag as written ("the chunk's document or section is ABOUT another record of the same
       label") fires on 0 furniture pairs. It misses the known suspect: `Assembly:A-1021` reaches
       `malmo_desk_reviews.md#1`, a document ABOUT a *Product*.
     - A 10 % sample (about 10 pairs per dataset) cannot show ≥ 0.95: even 10 of 10 has a Wilson lower
       bound of 0.72.
     - Purity is therefore plain k/n with a Wilson interval. The written flag (`same_label_about`) and
       R87's scope rule (`scope_foreign`: 6 furniture pairs) are both kept, for the table of
       disagreements with the code flags.
  2. **The 29 held-out `label_mismatch` mentions are not record links.** They are individuals typed
     Vehicle, named after a Recall key, with `REFERS_TO {no_record}`. So they stay out of C4's 76 links
     and its precision.
     - They are judged on the same C4 sheet, with the same question ("does this mention refer to this
       record?"). Their record is the one whose key they are named after.
     - They are reported apart, as missed links. A mention typed Vehicle that does refer to a Recall
       also has the wrong type.
  3. **C3's wrong merges of records are read from C4.** A record is never merged with another node;
     only `REFERS_TO` edges reach it. So a wrong record merge is exactly a C4 link judged INCORRECT, and
     it is not judged twice. C3 judges the individual and concept nodes with more than one mention, and
     R87's split groups. The R75 pairs are rescored by code on the snapshot.
- **Stated limitation:**
  - Earlier sessions read `out/r77d_*`, so neither the sheets nor the verdicts are blind to the build.
  - Gold and verdicts come from one model family.
- **Part a: sheets, verdict models and scorer (code done 2026-10-06, $0).**
  - **`anchor/sheets.py`:** the blind sheet models.
    - Items show source text and source data only: the mention, its sentence and chunk; a record's
      staged cells and one-hop plan relations ("PART_OF -> Product:P-1007 (...)"); the other records of
      the same name.
    - The code side (`CodeItem`: R87's flags, the edge's rule and score, the arms, linked or not) is a
      separate file the judge never sees.
  - **`anchor/sheet_builder.py`:** `build_sheets`, the three sheets from a snapshot, R87's code checks and
    the placed target nodes.
    - C6's two flags are `same_label_about` (the direction's) and `scope_foreign` (R87's).
    - Item ids are stable: `m:`, `s:`, `l:`, `p:` followed by the graph ids.
  - **`validation/anchor_verdicts.py`:** the verdict file.
    - The five labels, a reason and a verbatim quote per verdict (only UNJUDGEABLE may lack the quote).
    - A header naming the judge model, the snapshot, the sheet and its commit.
    - The lead's `reviewed` ids and `changes` (with the blind label before).
    - `verdict_issues` refuses missing, duplicate or unknown ids, an unreviewed INCORRECT, AMBIGUOUS or
      UNJUDGEABLE verdict, a change that does not end at the final label, and an unreviewed item of the
      seeded 10 % VALID sample (`REVIEW_SEED = 93`).
  - **`anchor/judged.py`:** the scores, each k/n with a Wilson interval.
    - The rates: accepted (VALID + VALID_ALTERNATIVE over those + INCORRECT), strict, and worst case.
    - `score_c3`, `score_c4`, `score_c6`, and `rescore_identity` (R75's scorer on the snapshot).
    - Every flag's agreement with the verdicts, and the INCORRECT items no flag raised.
    - `check_evidence`: each quote must stand in what its item showed.
  - **`AnchorSheetsStage` + `kg anchor-sheets`:** one MLflow run per build.
    - It refuses to write sheets when the C0 gate fails.
    - Metrics: the sheet sizes and the flag counts. Artifacts: the six files.
  - **Rules for the judge:** `tests/gold/r93/rules/c3.md`, `c4.md`, `c6.md`. Domain-neutral, with
    invented examples (an oven and its fittings, a baker).
  - **Tests:** `tests/test_anchor_judging.py` (19), on the lamp-and-kettle snapshot plus:
    - a cross-scope link, an unlinked mention named after a key, and a split individual;
    - the sheets and what they hide;
    - every verdict-file rule, and the evidence check;
    - each score and its pass rule;
    - the R75 rescoring;
    - the stage, including its refusal on a failed gate.
  - **Gate (code):** 653 passed (634 before), `ruff check` clean. Committed at `0f6c089`.
  - **Runs** (`kg --preset <quality|heldout|generality> anchor-sheets`, $0, no model, at `0f6c089` with only
    `.claude/settings.json` dirty): furniture `1b750c2f`, held-out `a5968167`, generality `a7cd6f22`, in each
    dataset's experiment. The C0 gate passed on all three.
  - **Committed:** `tests/gold/r93/<dataset>/c{3,4,6}_{sheet,code}.json` (about 1.3 MB) and
    `tests/gold/r93/runs.json` (run ids, commit, snapshot hash, input and file hashes). A test checks that
    they load, that each sheet's items equal its code side, that they name the committed R87 and R90 files,
    and that the denominators hold.

    | Sheet items | furniture | held-out | generality |
    |---|---|---|---|
    | C3 merges / splits | 81 / 12 | 51 / 3 | 39 / 9 |
    | C4 links / unlinked | 50 / 0 | 76 / 29 | 40 / 0 |
    | C6 pairs (only arm B) | 140 (6) | 125 (0) | 90 (0) |
    | flags | `cross_scope_link` 4, `fuzzy_name` 6, `scope_foreign` 6 | `label_mismatch` 29 | `same_label_about` 3 |

  - **Gate (sheets):** 656 passed, `ruff check` clean. Next: part b, judging.
- **Part b: judging (done 2026-10-06, $0, no pipeline call).**
  - **Blind batches.** The sheets were cut into 23 batches of at most about 70k characters (scratch, not
    committed). Each held only its items and the chunks they name, never a code side.
    - Each batch went to one subagent on Opus. All 23 report `claude-opus-5-5`.
    - A subagent read only the rules file and its batch, and wrote one verdict per item.
    - Code then checked every fragment: no missing, unknown or duplicate id, and every quote in its item.
      All passed on the first merge.
  - **Lead review** (Claude Opus 5.5 in the session): every blind INCORRECT, AMBIGUOUS and UNJUDGEABLE
    verdict, plus the seeded 10 % of VALID ones. That is 101 of 745 verdicts, with 8 changes, each recorded
    in its file's `changes` with the blind label:
    - **Furniture C4, `l:99eee1f9…:Product:P-1004`, INCORRECT -> AMBIGUOUS.** The mention is the
      document's title "Västerås Bookshelf Reviews". It reads as the bookshelf (the page's subject) or the
      page itself.
    - **Held-out C4, 5 Vehicle links -> VALID_ALTERNATIVE: one rule across batches.** "RAV4 Hybrid" ×3
      (INCORRECT), "2020 RAV4 Hybrid" and "2017-2018, 2021 Civic Type R" (AMBIGUOUS).
      - The blind batches disagreed: other batches accepted "2017-2019 Rogue Hybrid", "Civic Type R",
        "Civic Coupe", "Civic Sedan" and "Civic Hatchback" as VALID.
      - The rule applied: a Vehicle row is one row per model name (key `RAV4`; its relations reach
        recalls of several years), so a hybrid, trim or body variant of the named model refers to that
        row under the model-line reading.
      - "2017-2022 Rogue Sport" stays INCORRECT: the text names it as a separate model.
      - **This change decides the hard rule.** On the blind labels, held-out C4 precision is 70/74 =
        0.946 (below 0.95). After review it is 75/76. Part c reports both.
    - **Held-out C6, `p:Vehicle:ROGUE|…21V839000#0`, AMBIGUOUS -> VALID_ALTERNATIVE.** The same variant
      rule.
    - **Held-out C3, `m:1d76769229d5bc4c`, INCORRECT -> VALID_ALTERNATIVE.** "ISSUES" and "PROBLEMS"
      name the same generic kind. Other batches judged "crack" and "short circuit" on different objects
      as one kind. This is a concept merge, comparative only.
  - **Labels, blind -> final** (VALID / VALID_ALTERNATIVE / INCORRECT / AMBIGUOUS / UNJUDGEABLE):

    | | C3 | C4 | C6 |
    |---|---|---|---|
    | furniture | 71/20/2/0/0, unchanged | 42/0/8/0/0 -> 42/0/7/1/0 | 103/31/6/0/0, unchanged |
    | held-out | 34/19/1/0/0 -> 34/20/0/0/0 | 99/0/4/2/0 -> 99/5/1/0/0 | 116/7/0/2/0 -> 116/8/0/1/0 |
    | generality | 32/7/9/0/0, unchanged | 40/0/0/0/0 | 90/0/0/0/0 |

  - **Committed:** `tests/gold/r93/<dataset>/c{3,4,6}_verdicts.json`. Each header names the judge
    model, the snapshot hash, the sheet with its hash, and the sheets' commit `97ad11e`.
  - **Test:** each verdict file loads against its code side, passes the review rules and the quote check,
    and names its judge, snapshot and sheet.
  - **Limitation:** the sheets and verdicts are not blind to the build (earlier sessions read
    `out/r77d_*`), and the blind judges and the lead are one model family. No gold was changed: R75's
    pairs are only rescored by code in part c.
  - **Gate (verdicts):** 665 passed, `ruff check` clean.
- **Part c: scoring and results (code done 2026-10-06, $0).**
  - **`anchor/judged_report.py`:** `JudgedReport` scores C3, C4 and C6 twice, on the reviewed labels and
    on the blind ones (`blind_view`).
    - `impact` maps every item judged INCORRECT to the QA questions whose R90-placed targets hold its node.
    - `metrics()` logs:
      - each rate with `_low`, `_high` and `_n`;
      - the hard rules (`c3_hard_passed`, `c4_hard_passed`, `c6_<arm>_hard_passed`);
      - each flag's confirmed count;
      - the blind hard rules and rates, under `blind_`.
  - **Settings** `anchor_min_link_precision` and `anchor_min_purity` (0.95, the direction's), logged as
    params.
  - **Structural move:** the two judging stages left `pipeline/anchor_stages.py` (which was 368 lines) for
    `pipeline/judging_stages.py`, behaviour kept.
  - **`AnchorJudgedStage` + `kg anchor-judged BUILD --judged --identity-gold --data --logged
    --anchor-report`:**
    - It rebuilds the snapshot and refuses sheets built from another one.
    - It loads the committed verdicts (review rules, quote check) and rescores R75's pairs.
    - One MLflow run per build. Params: every sheet and verdict file by hash, plus the judge model.
      Artifact: `anchor_judged.json`.
  - **Tests:** +4 (35 in `tests/test_anchor_judging.py`): the blind view, impact, the report's metrics,
    and the stage on the invented build, including its refusal of a sheet from another snapshot.
    `README.md` lists both commands.
  - **Gate (code):** 669 passed, `ruff check` clean. Committed at `b15b3b9`.
- **Runs** (`kg --preset <quality|heldout|generality> anchor-judged`, $0, no model, at `b15b3b9` with only
  `.claude/settings.json` dirty): furniture `c4bcfbd1`, held-out `0cab5c85`, generality `c18e11ad`, each in
  its dataset's experiment.
  - **Committed:** `tests/gold/r93/<dataset>/anchor_judged.json`, indexed under `judged` in
    `tests/gold/r93/runs.json`.
  - A test recomputes C4 and C6 (final and blind) from the committed verdicts and code sides, and finds them
    equal to the committed reports.
- **Results** (judge: Claude Opus 5.5 in the session, `claude-opus-5-5`; k/n with a Wilson 95 % interval;
  "accepted" means VALID or VALID_ALTERNATIVE; AMBIGUOUS and UNJUDGEABLE leave the denominator):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | **C3 hard**: wrong merges of individuals | 0 of 4 nodes | 0 of 10 | 0 of 31 |
  | **C3 hard**: wrong merges of records (= C4 INCORRECT, decision 3) | **7** | **1** | 0 |
  | C3 hard rule | **fails** | **fails** | passes |
  | C3 concept merges right (comparative) | 75/77 | 41/41 | 8/8 |
  | C3 split groups rightly apart (comparative) | 12/12 | 3/3 | **0/9** (all real splits) |
  | C3 R75 pairs by code: precision / apart / recall | 1.0 (9) / 1.0 (31) / 0.69 (9/13) | 1.0 / 1.0 / 0.71 (5/7) | 1.0 / 1.0 / 0.77 (10/13) |
  | **C4** precision | **42/49 = 0.857** [0.73, 0.93] | 75/76 = 0.987 [0.93, 1.00] | 40/40 [0.91, 1.00] |
  | C4 on the blind labels | 42/50 = 0.840 | **70/74 = 0.946** [0.87, 0.98] | 40/40 |
  | C4 `cross_scope_link` confirmed | **4 of 4** | n/a (0 flags) | n/a |
  | C4 hard rule | **fails** | passes (blind: **fails**) | passes |
  | C4 unlinked mentions named after a key that do refer to it | none | 29/29 (missed links) | none |
  | **C6** arm A, record + individual starts, pooled | 93/97 = 0.959 [0.90, 0.98] | 77/77 [0.95, 1.00] | 80/80 [0.95, 1.00] |
  | C6 arm A, starts below 0.95 | **3**: A-1012 3/5, A-1021 7/8, A-1070 3/4 | 0 | 0 |
  | C6 arm B, record + individual, pooled | **97/103 = 0.942** [0.88, 0.97] | 77/77 | 80/80 |
  | C6 pairs only arm B reaches | 4/6 pure | none | none |
  | C6 concept starts, arm A (comparative) | 37/37 | 47/47 | 10/10 |

  Read with care:
  - **Every failure of a hard rule but one comes from the same cause: a text mention linked to another
    product's part.** The R87 suspects are all confirmed. They fail C4 (4 confirmed cross-scope links),
    C3 (record merges, decision 3) and C6 (the same records lead to the other product's chunks).
    Identity of individuals is clean on all three datasets.
  - **C6 is pooled over pairs.** The direction's wording ("of the chunks reached from a node") can also be
    read per start. Read that way, furniture arm A fails at three starts. Both readings are reported. Which
    one is the hard rule is the user's choice (Found along the way).
  - **Held-out C4 passes only after the lead's variant rule** (part b): 75/76 against 70/74 on the blind
    labels.
  - **Arm B is never purer than arm A.** On furniture it adds 6 pairs, 2 of them foreign (A-1021 reaches
    `malmo_desk_reviews.md#1` only through a claim).
- **Failures ranked by how many QA questions they affect** (`impact`: questions whose R90-placed targets
  hold the failed node):
  1. **Cross-product part links (furniture, 4 links, 6 C6 pairs; F04, F17, F32, F48).** "the drawer
     doesn't open as smoothly as I'd like" (`malmo_desk_reviews.md#5`) is linked to `Assembly:A-1021`
     (PART_OF Norrköping Nightstand). So the start A-1021 for F32 ("Which products have a drawer that
     reviewers say does not close properly?") reaches two Malmö Desk chunks. The other three: "frame"
     (Linköping Bed) -> the Uppsala Sofa's A-1012 (F04, F17); "drawers" (Malmö Desk) -> the Helsingborg
     Dresser's A-1070 (F32, F48); "center support" (Jönköping Coffee Table) -> the Linköping Bed's A-1052
     (no question). Cause: the whole-domain fallback of `resolution/records.py` (R87).
  2. **Individuals split across documents (generality, 9 groups, comparative; G22, G25, G34, G38, G40,
     G41).** For example "Harbour Station" in the May report and in the open-day article are two nodes,
     so G34's start reaches only one of them. Cause: individual identity is type + name + document by
     default (direction 3.2), and no evidence joined them.
  3. **A mention of a piece linked to its whole (furniture, 2 links, unflagged; F08, F21, F32).**
     "pre-drilled holes for the drawer handle" -> `Assembly:A-1022` Drawer Handle; "drawer slide
     mechanism" -> `Assembly:A-1021` Drawer. No row exists for the piece, so the rules call it INCORRECT.
  4. **A sibling model linked to the base model (held-out, 1 link, unflagged; H51).** "2017-2022 Rogue
     Sport" -> `Vehicle:ROGUE`. H51's count is not affected: the same recall also names "2014-2020 Rogue".
  5. **Close spelling (furniture, 1 link, no question).** "drawer slides" -> `Component:S-1076` "Drawer
     Sides", the one `fuzzy_name` flag of 6 that the judge confirmed.
  6. **Concept merges of two senses (furniture, 2, comparative, no question).** "smooth" (finish) with
     "smooth" (drawer slides); "proportions are perfect" with "perfect size".
- **Disagreement with the code flags:**
  - `cross_scope_link` 4/4 and `scope_foreign` 6/6 were confirmed, with no foreign C6 pair unflagged.
    R87's scope rule is a reliable detector.
  - The direction's C6 flag `same_label_about` fired 3 times (generality pumps) and was confirmed 0
    times. It missed all 6 furniture foreign pairs.
  - `fuzzy_name` was confirmed 1 of 6 times.
  - `label_mismatch`: the 29 held-out mentions are not wrong links. All 29 refer to the Recall whose key
    they are named after, so they are missed links typed Vehicle.
  - INCORRECT with no flag: 2 furniture links (cause 3) and 1 held-out link (cause 4).
- **Not decided here:** section 7.5's decision is step 6 of the direction. By its rule, "A fails a hard
  criterion: that defect is fixed first, in its own step". The defect is cause 1 (C4 fails today, as the
  direction expected).
- **Gate (results):** 672 passed, `ruff check` clean.

### R94. Record links stay inside the document's scope (done 2026-10-06; fix A of R93, $0)
The user, 2026-10-06, after R93: "do fix A". R93 found that every furniture failure of a hard rule but one
comes from one rule, the whole-domain fallback of `resolution/records.py` `_by_name`.
- **The defect.** When no record inside a document's scope matches a mention's name, the fallback matches
  the name against every record of the dataset.
  - Example: the Malmö Desk review's "drawer" fails against the desk's own `A-1062 "Drawer Unit"` (spelling
    score 71, threshold 90). It then finds the Norrköping Nightstand's `A-1021 "Drawer"` (score 100).
  - In documents with a scope the fallback made 4 links, all judged INCORRECT (R93).
  - In documents without a scope (generality) it made 13 name links, all right.
- **Fix A.** The whole-domain fallback is used only when the mention's document has no scope. With a scope
  and no match inside it, the mention links to no record and stands for itself (an individual).
- **A behaviour change, stated:** R11 meant the fallback for "a chair review that mentions the table"
  (`tests/test_records.py`). After the fix, a document that names another anchored thing by its full name no
  longer links it. In the three builds no such correct link exists (0 of 4 fallback links were right). The
  test's expectation flips, with this reason. Fix C (an LLM choosing among the in-scope records, which would
  find `A-1062`) is a later step, if the user wants it.
- **Split: two parts, one commit each.**
  - (a) The fix with a failing test first ($0).
  - (b) The measurement ($0, no rebuild):
    - The build's Neo4j graph is gone, and `kg resolve` also asks an LLM to join individuals. So the record
      matching, which is pure code, is replayed offline on each build's snapshot with the fixed rules.
    - The replay must reproduce resolve.json exactly, except the links the fix removes. Otherwise it is
      refused.
    - The criteria are then recomputed on the replayed builds. Only new or changed judging items are
      judged; unchanged items keep their R93 verdicts.
- **Part a: the fix (done 2026-10-06, $0).**
  - `_by_name` returns the in-scope matches, empty or not, whenever the mention's document has a scope. The
    whole-domain fuzzy match is reached only without one.
  - Rules 3-5 still run on an empty name match: a key written next to the name, or a telling attribute, is
    evidence wherever its record is.
  - The test came first and failed: the old code linked "drawer" in the desk's scope (with "Drawer Unit")
    to another product's "Drawer". `test_a_name_outside_the_scope_falls_back_to_a_unique_domain_match`
    became `test_a_name_outside_the_scope_of_its_document_links_no_record` (the behaviour change above).
  - Gate: 672 passed (672 before: one test changed, none added), `ruff check` clean.
- **Part b: the offline replay and the measurement (code done 2026-10-06, $0).**
  - **`audit/relink.py`:** `relink(snapshot, plan, schema, threshold)` feeds `match_record` what
    `resolution/particulars.py` reads from the graph, restated over the snapshot: the keyed mentions,
    their candidates with key attributes, the link stage's scopes, and the sentences naming them.
    - Every changed decision must be explained: the build linked by matching to a record outside the
      document's scope, and the replay links none. Anything else is listed as unexplained.
    - A mention that loses its record stands for itself (`no_record`). The LLM's joining of individuals
      is not replayed.
    - `relinked_counts` keeps the build's logged counts but takes those resting on identity (`resolve.*`,
      `attach.*`) from the replay, so the replayed build's C0 gate still checks ingest, extract and link.
  - **`AuditRelinkStage` + `kg audit-relink BUILD --data --logged --out`** (`pipeline/audit_stages.py`):
    - It refuses a build whose C0 fails, and a replay with an unexplained change.
    - It writes `build/` (the build's files with resolve.json's assignments replayed; no judge sheet),
      `logged.json` and `relink.json`.
  - **A dry run of the replay** on the three snapshots:
    - furniture: 124 keyed mentions, 4 changes (exactly R93's 4 cross-product links), 0 unexplained;
    - held-out: 260, 0 changes (every record link is by key);
    - generality: 72, 0 changes (no document has a scope).
    So fix A changes only the furniture build.
  - **Tests:** +3 in `tests/test_audit.py`:
    - the invented build's planted cross-scope "lid" is the one change;
    - a build that differs elsewhere is unexplained;
    - the stage writes a build its own gate passes, with only identity counts changed.
  - **Gate (code):** 675 passed, `ruff check` clean. Committed at `21356c5`.
- **Runs** ($0, no model, at `21356c5` with only `.claude/settings.json` dirty, the dataset presets):
  - `kg audit-relink`: furniture `d5130f7d` (4 changes), held-out `f963dbc4` (0), generality `fc4b82fd` (0).
  - On the replayed furniture build `out/r94_furniture/build`:
    - `kg anchor-eval`: anchor `916b77e4`, layered `10bcb2a9`. C0 passed: ingest, extract and link counts
      equal the build's; identity counts come from the replay.
    - `kg anchor-sheets`: `267d094e`.
    - `kg anchor-judged`: `248e2e4b` (at `3aa6500`).
  - Held-out and generality are not rerun: the replay changes nothing there, so every R90-R93 number of
    theirs stands.
- **No new judging was needed.** Every item of the replayed furniture sheets equals an R93 item byte for byte
  (id, content, chunk texts), so its R93 verdict, review and changes carry over unchanged.
  - The sheets only lost items: the 4 cross-product links (C4) and their 6 foreign pairs (C6), all judged
    INCORRECT in R93.
  - The seeded VALID samples did not change.
- **Committed:** `tests/gold/r94/` (the replay reports of all three builds, the furniture logged counts,
  anchor reports, sheets, carried verdicts and judged report) with `runs.json`. Three tests check:
  - only the 4 links changed;
  - the verdicts are R93's, and the report is their score;
  - the pairing with vector retrieval below.
- **Results, furniture** (judge: R93's verdicts by Claude Opus 5.5; Wilson 95 %; before = R93 / R90 / R92 on
  `out/r77d_furniture`, after = the replayed build):

  | Criterion | before | after |
  |---|---|---|
  | C4 precision | 42/49 = 0.857 [0.73, 0.93] | **42/45 = 0.933** [0.82, 0.98] |
  | C4 `cross_scope_link` confirmed | 4 | **0** |
  | C4 hard rule | fails | **still fails** (0.933 < 0.95) |
  | C3 wrong merges of records / individuals | 7 / 0 | **3** / 0 (still fails) |
  | C6 arm A, record + individual | 93/97 = 0.959, 3 starts below | **93/93 = 1.0**, none below |
  | C6 arm B | 97/103 = 0.942 (fails) | **97/97 = 1.0** (passes) |
  | C5 gold start: recall@5, complete@5 (A) | 41/61, 19/31 | **42/61, 20/31** (F17) |
  | C5 end to end: recall@5, complete@5 (A) | 28/61, 15/31 | **31/61, 17/31** (F17, F24, F32) |
  | C5 @10, both modes (A) | 56/61, 29/31; 40/61, 22/31 | unchanged |
  | C5 unbudgeted reach, end to end (A) | 50/61 | **48/61** |
  | A vs vector, end to end, complete@5 | 15 vs 21, p 0.070 | **17 vs 21, p 0.29** |
  | A vs vector, gold start, complete@5 | 19 vs 21, p 0.69 | 20 vs 21, p 1.0 |
  | C2, C7, C8 (A: 13/14, 0 unwitnessed) | | unchanged |

  Read with care:
  - **The fix does what it should and nothing else:** the 4 wrong links and the 6 foreign chunks are gone,
    and no remaining verdict changed. With them goes the open C6 question (pooled vs per start): no start
    is below 0.95 any more.
  - **C4 still fails, on 3 in-scope links.** "pre-drilled holes for the drawer handle" -> Drawer Handle and
    "drawer slide mechanism" -> Drawer (the `contained` rule takes a record's name inside a longer name for
    another thing), and "drawer slides" -> Drawer Sides (spelling 96). This is R93's causes 3 and 5, a
    separate defect for its own step.
  - **Ranking improves at a budget of 5,** because foreign chunks no longer crowd the top. For example,
    F32's start A-1021 no longer brings the two Malmö Desk chunks.
  - **Unbudgeted reach loses 2 gold chunks (F08, F32),** both reached before only by 7- and 12-step chains
    running through a wrong link (A-1021 -> the Malmö review -> ... -> the dresser's `#3`). Neither was
    within the 10-chunk budget, so no budgeted score fell.
  - **The vector pairing is not a new run.** It is R92's committed arm-C rankings paired with the new
    reports (no embedding call). The same code on the R90 reports gives back R92's logged numbers exactly.
- **Stated limitation:** the replay is the current matching code on the build's own inputs, not a rebuild.
  - The LLM's joining of individuals is not replayed. The 4 unlinked mentions ("drawer" of the Malmö
    review ...) stand alone, where a real `kg resolve` could join them to another document's individual,
    only on LLM evidence.
  - C0 on the replayed build confirms the identity counts only by construction.
- **Gate (results):** 678 passed, `ruff check` clean.

### R95. Code links a name only when it is sure; an LLM chooses among the near misses (done 2026-10-06; $0.0256)
The user, 2026-10-06, after R94: implement the earlier session's fix for the 3 in-scope links that still
fail C4 on furniture. Collecting candidates and deciding between them are one step in `resolution/records.py`:
a partial overlap or a close spelling decides alone.

| Wrong link (R93) | Rule | Why a lexical test cannot see it |
|---|---|---|
| "pre-drilled holes for the drawer handle" -> Drawer Handle | `contained` | the record's name is a complement; the head is "holes" |
| "drawer slide mechanism" -> Drawer | `contained` | the record's name is a modifier; the head is "mechanism" |
| "drawer slides" -> Drawer Sides | `name`, spelling 96 | a different word, one letter apart |
| the Malmö review's "drawer" (R94: now unlinked) | no rule | the right record, "Drawer Unit", is spelled differently |

- **Why not head-noun rules:** fitted to 13 cases of one dataset, English grammar in the identity layer, and
  still a list of generic heads ("system", "construction"). The direction's future forms (books, scientific
  text, other languages) would each need a new rule.
- **The containment rule's reason is gone:** R60 added it for "2019 Subaru Outback" against OUTBACK. Since
  R75 the key rule covers that (all 76 held-out links are by key); containment fires only on furniture parts,
  where 2 of its 7 links are wrong and 1 is ambiguous.
- **The tiers:**
  1. Code links alone, inside the document's scope (R94): the record's key (unchanged), or the record's name.
     A name is the record's when it is the same after normalisation, or the same words up to a short ending
     ("drawers" for "Drawer", "center supports" for "Center Support") and spelled at least
     `domain_link_threshold` alike. The ending test says where two names may differ, the score how much:
     without the score "pane" would link "Panel" (an ending apart, but 89 alike), a pair the old gate refused.
  2. Code proposes candidates but never links: a record's name inside the mention's or the reverse, any other
     near miss in spelling.
  3. An LLM chooses one candidate or none from a closed list; code checks the answer is a listed id and its
     quote is in the chunk. A failure or "none" is no link, so without an LLM tier 3 abstains.
- **Split: two steps, each measured.**
  - **R95a: tier 1 in code** (code only, $0). Containment and a spelling that differs inside a word stop
    linking. Measured by R94's offline replay and R93's verdicts. Expected on furniture: C4 42/45 -> 38/38;
    4 correct links lost ("weighted base", "adjustable shelves", "cable management system", "frame
    construction"); held-out and generality unchanged.
  - **R95b: tiers 2 and 3** (candidates and the constrained LLM choice). The prompt follows the
    `prompt-engineering` skill. Measured by one small paid replay of only the tier-3 mentions (furniture,
    about $0.01), only after the user's yes; the judge then rules on the new links. Success: C4 stays at or
    above 0.95 and at least the 4 links R95a gives up, plus the Malmö drawer, come back.
  - The earlier session's plan put the candidates in R95a with tier 3 off. They move to R95b: without a
    chooser they would be computed and never used.
- **Rejected:** head-noun rules or a parser (English only, a heavy dependency, tuned to 13 examples); asking
  the extractor for clean heads (a paid re-extraction of every dataset, and the matcher would still need the
  decision step); only raising the threshold ("slides"/"sides" scores 96, above the plurals' 92).
- **R95a, code (done 2026-10-06, $0).**
  - **`resolution/records.py`:** `name_score(name, record_name, threshold)` is tier 1, used by `name_matches`.
    - 100 for the same name after normalisation: the same words in any order, or the same letters with other
      spacing ("bed-side table" for "Bedside Table"), with or without a leading title.
    - Else the spelling score (token_sort_ratio, as before) when it reaches the threshold *and* the two
      names have the same words up to their endings: two words share at least 4 letters from their start and
      neither goes on for more than 3 letters (`_MIN_STEM`, `_MAX_ENDING`). A word with a digit has no ending
      ("Model 2019" is not "Model 2018": 90 alike, linked before).
    - Words are letters and digits of any script, so names in another script still match only exactly.
    - `contained_matches` and the `contained` reason are gone; `_LINK_REASONS` (the resolve stage's
      `linked_by_*` metrics) now follows `LinkReason`, so `linked_by_contained` is no longer logged.
  - **`audit/relink.py`:** a change is explained by a `cause`, one per rule change: `left_scope` (R94),
    `containment`, `spelling` (R95a). The retired `contained` reason still counts as a link made by matching:
    without it, a replay dropping such a link would show no change at all. The stage logs `changes_<cause>`.
  - **A behaviour change, stated:** a Neo4j test (`test_linking.py`) linked "2016 Honda Civic" to a vehicle
    `CIVIC` keyed `V1` by containment. Its vehicle now has the held-out shape (key = model, as `Vehicle:RAV4`),
    so the key rule links it; the attachment it tests is unchanged.
  - **Tests:** `test_records.py` 13 -> 16 (the containment tests became "is no link"; new: endings, digits,
    exact over inflected, other scripts); `test_audit.py` +2 (a link made by each retired rule is unlinked
    with its cause; the per-cause metrics).
  - **Dry run of the replay** (no MLflow, nothing written), every change explained, no link added:
    furniture 124 keyed mentions, 12 changes (`left_scope` 4 = R94's, `containment` 7, `spelling` 1);
    held-out 260 and generality 72, 0 changes.
  - **Gate (code):** 683 passed (678 before), `ruff check` clean. Committed at `f52fe17`.
- **R95a, runs** ($0, no model, at `f52fe17` with only `.claude/settings.json` dirty, the dataset presets; R94's
  commands with `r95a` folders):
  - `kg audit-relink`: furniture `ffcfdadd` (12 changes: `left_scope` 4, `containment` 7, `spelling` 1; 0
    unexplained), held-out `b07066a5` and generality `bf0408f5` (0 changes; their reports are byte-identical
    to R94's, so every R90-R94 number of theirs stands).
  - On the replayed furniture build `out/r95a_furniture/build`: `kg anchor-eval` anchor `25bd5e71`, layered
    `2d12bd20` (C0 passed); `kg anchor-sheets` `d9a21d2e`; `kg anchor-judged` `de0574ab` (at `41e1010`).
- **Judging: two new items, judged blind, then reviewed** (judge: Claude Opus 5.5, `claude-opus-5-5`):
  - Every other item equals an R94 item byte for byte, so its R94 verdict carries over. The sheets lost the 8
    unlinked C4 links and the C6 pair only the wrong "holes" link made (`A-1022` -> nightstand `#4`).
  - New: C3 split `s:Component:drawer slides` (the bed's and the desk's drawer slides, now two individuals)
    and the C6 pair of the "pre-drilled holes for the drawer handle" individual with its own chunk. A blind
    Opus 5.5 subagent read only the rules and the two items: both VALID. The lead agrees.
  - Removing and adding items shifts the seeded 10 % VALID sample: it named 5 verdicts no lead had reviewed
    (C3 "edges", C4 "center supports" and "Örebro Lamp", C6 the coffee table's "pre-drilled holes" and the
    sofa frame). The lead reviewed all 5: no change.
  - C4 has no change left: R94's one (P-1004, INCORRECT -> AMBIGUOUS) belonged to a link that is gone.
- **Committed:** `tests/gold/r95a/` (the three replay reports; furniture logged counts, anchor reports,
  sheets, verdicts and judged report) with `runs.json`. Two tests check that only links of the retired rules
  (and R94's) were unlinked, and that the verdicts are R94's plus the two new items and the report their
  score. The vector pairing is recomputed from R92's rankings by a helper both R94's and R95a's tests use.
- **Results, furniture** (judge: R94's verdicts plus 2 new, Claude Opus 5.5; Wilson 95 %; before = R94 on
  the R94 replay, after = the R95a replay):

  | Criterion | before (R94) | after (R95a) |
  |---|---|---|
  | C4 precision | 42/45 = 0.933 [0.82, 0.98] | **38/38 = 1.0** [0.91, 1.00] |
  | C4 on the blind labels | 42/46 = 0.913 | **38/38 = 1.0** |
  | C4 hard rule | fails | **passes** (blind too) |
  | C3 wrong merges of records / individuals | 3 / 0 (fails) | **0 / 0 (passes)** |
  | C3 split groups rightly apart (comparative) | 12/12 | 13/13 |
  | C3 R75 pairs by code: precision / apart / recall | 1.0 / 1.0 / 9/13 | 1.0 / 1.0 / **8/13** |
  | C6 arm A, record + individual | 93/93 | 91/91 (2 pairs now only through claims) |
  | C6 arm B | 97/97 | 97/97 |
  | C2 hit@1 / hit@5 | 70/86 / 77/86 | **68/86** / 77/86 |
  | C5 gold start: recall@5, complete@5 (A) | 42/61, 20/31 | unchanged |
  | C5 end to end: recall@5, complete@5 (A) | 31/61, 17/31 | **30/61, 16/31** (F17) |
  | C5 end to end @10 (A) | 40/61, 22/31 | **39/61, 21/31** (F17) |
  | C5 unbudgeted reach, end to end (A) | 48/61 | unchanged |
  | A vs vector, end to end, complete@5 | 17 vs 21, p 0.29 | 16 vs 21, p 0.18 |
  | C7 nodes | 108 | 109 |
  | C8 arm A: connections, unwitnessed hops | 13/14, 0 | unchanged |
  | C8 arm B: unwitnessed hops (comparative) | 219 of 1874 | 268 of 1936 |

  Read with care:
  - **The rule does what it should:** the 3 wrong in-scope links and the ambiguous title link are gone, and
    no link was added (the replay refuses any change it cannot explain, and found none). C4 and C3 now pass
    their hard rules on furniture; held-out and generality did not change.
  - **The price is the 4 right containment links,** as expected: "weighted base", "adjustable shelves",
    "cable management system", "frame construction". It shows in three places:
    - R75's pair "base" / "weighted base" (the lamp's base) is no longer joined (recall 9/13 -> 8/13);
    - "frame construction" and "cable management system" no longer lead from their records (`A-1012`,
      `A-1064`) to their chunks in arm A; arm B still reaches them through the claims;
    - **F17 end to end** ("Which products priced over $500 have a review reporting a creaking or squeaking
      frame?"). The question's "frame" names two nodes: the sofa's Frame record (`A-1012`) and the bed's
      lone "frame" (unlinked since R94). In R94 the record ranked first with 2 mentions, one of them "frame
      construction". Now each has one, the bed's node ranks first (C2 hit@1 F04 and F17: rank 1 -> 2), and
      F17 starts from the wrong product. With the gold start, F17 is unchanged.
  - R95b is meant to win these back: the four lost links are tier-2 candidates.
- **Gate (results):** 685 passed (683 before), `ruff check` clean. **R95a done 2026-10-06.** Next: R95b.
- **R95b, scope** (code and tests $0; the measurement is a paid replay, made only after the user's yes):
  - **Tier 2, code:** when code decided nothing for a mention (no link, no tie) and its document has a scope,
    the near misses are the in-scope records whose name shares a word with the mention's up to an ending
    (stem of 3 letters: a looser test than tier 1, since a candidate never links), or is spelled at least
    `er_borderline` alike. A tie is not a near-miss list (it stays ambiguous, as before); a document without
    a scope gets none (the whole domain vouches for nothing, R94).
  - **Tier 3, `resolution/record_choice.py`:** the LLM sees the mention's sentences and each near miss with its
    cells and one-hop relations (R93's sheet view), and answers one listed id or none, with a quote. Code links
    only when the id is listed, the quote stands in one of the mention's chunks and names it, and no other
    listed record has the same name (a choice between twins is a coin toss: 15 "Wooden Slat" rows share one
    furniture scope). A link is `reason: chosen`, `by: <model>`, the quote its evidence. None, a failure, a
    malformed answer, no sentence naming the mention, or too long a list means no link; without an LLM the
    tier abstains. Every decision is logged in resolve.json.
  - **Wiring:** `kg resolve` (resolve stage: prompt version, prompt artifact, metrics per outcome), and
    `kg audit-relink --choose` for the replay, which the run guard treats as an LLM command.
  - **Prototype on the three builds** (no LLM): furniture 32 mentions would be asked (95 candidates, at most
    16 in one list), held-out 1, generality 0.
- **R95b, structural move (done 2026-10-06, behaviour kept):** the name test (`name_score` and its word
  helpers) leaves `resolution/records.py` (330 lines) for `resolution/names.py`; its tests for
  `tests/test_names.py` (+1: word order, empty name). Gate: 686 passed, `ruff check` clean.
- **R95b, code (done 2026-10-06, $0, no LLM call).**
  - **`names.py`:** `near_name` (tier 2's name test: a word shared up to its ending from a 3-letter stem, words
    under 3 letters left out, or spelled at least `er_borderline` alike) and `same_name` (the twin test).
  - **`resolution/record_choice.py`** (new; 310 lines: one concern, the choice, with the one graph read it
    needs):
    - `near_misses`: tier 2, only without a link or a tie, only inside a scope, ordered by label and key.
    - `CHOICE_PROMPT` + `RecordChoice`: domain-neutral, examples from an invented telescope; a test checks it
      holds no evaluated-corpus word or four-gram.
    - `choose_records`: parallel calls; code's decision per reply is `chosen`, `none`, `not_listed`,
      `quote_not_verified` (the quote must stand in the mention's chunk and name it) or `twin`. Code does
      not ask when there is no sentence to quote (`no_sentence`), more than 20 near misses (`too_many`) or
      no LLM (`skipped`). A failed call is `failed`, logged, never a failed stage.
    - `read_candidate_views`: the near misses' plan property columns and one-hop relations, rendered as R93's
      sheets render them ("PART_OF -> Product:P-7 (Name)", "Component:S-3 (Name) PART_OF -> this").
  - **`records.py`:** reason `chosen`; `RecordLink.score` is None for a choice, `RecordLink.by` names the
    model. **`particulars.py`:** near misses are computed with the matches, choices made before units are
    formed (a chosen record's mentions are one unit, as any record's), and the edge carries `by`.
    **`identity.py`:** `IdentityReport.record_choices` (resolve.json).
  - **Resolve stage:** param `record_choice_prompt_version`, artifact `prompts/resolve_record_choice.txt`,
    metrics `record_choices` and `record_choice_<action>` (and `linked_by_chosen`).
  - **Replay:** `relink(s, plan, schema, (threshold, borderline), llm, model)`. The snapshot gives the same
    views (properties, sorted relations) and sentences ("[heading] sentence"). A change caused by a choice is
    `cause: chosen`, also when the record is the build's own but the build reached it otherwise
    (containment, an adjudicated join): its edge then says why it holds. A replayed choice's edge has no
    target (the build's graph is gone; the snapshot names records by ref).
  - **`kg audit-relink --choose`:** asks the resolve model with the resolve thinking level; refused without a
    provider (`LLMUnavailableError`). Params `choose`, `er_borderline`, and with `--choose` the model,
    thinking and prompt version. Metrics `choices`, `choice_<action>`, `changes_chosen`. The run guard
    (`.claude/hooks/run_guard.py`) treats `audit-relink --choose` as an LLM command (`FLAG_LLM_COMMANDS`),
    so a quality-preset replay asks the user first; without `--choose` it stays free.
  - **Tests (+16):** `test_record_choice.py` (11: near misses with R93's real cases, twins, lines, the
    prompt's language and rendering, every decision outcome); `test_identity.py` +1 Neo4j (a scripted chooser links "brass
    focuser" to the record "Focuser" and refuses "the thread of the focuser"; the edge, the prompt's view
    read from the graph, the stage's params, metrics and artifact); `test_audit.py` +2 (a replayed choice and
    its cause, "none" and no chooser; `--choose` wiring and its refusal); `test_run_guard.py` +2.
  - **Dry run of the replay without an LLM** (nothing asked): identical to R95a (furniture 12 changes, 0
    unexplained; held-out and generality none). With `--choose` it would ask 31 furniture mentions (one
    more, "Västerås Bookshelf Reviews", has no sentence naming it) and 1 held-out mention; generality none.
    The 31 prompts hold about 63k characters (about 16k input tokens); at gemini-3.8-flash prices with
    `low` thinking the furniture replay should cost well under $0.10.
  - **Gate (code):** 702 passed (686 before), `ruff check` clean.
  - Committed at `cd0ac2a`.
- **R95b, the paid replay** (the user's yes, 2026-10-06: "yes you may run a paid run"; at `cd0ac2a` with only
  `.claude/settings.json` dirty): `kg --preset quality audit-relink out/r77d_furniture --choose`, MLflow
  `17c7e197`. gemini-3.8-flash, thinking low, prompt `0d1d7d45e37d`: **31 LLM calls, 0 failures, 18,345
  input / 965 output / 2,203 thinking tokens, `cost_usd` $0.0256.**
  - Outcomes: 17 chosen, 14 none, 0 refused by code (no unlisted id, unverified quote or twin), 1 not asked
    (no sentence). 23 changes, 0 unexplained: `chosen` 17, `containment` 4, `left_scope` 2.
  - Then $0, no model: `kg anchor-eval` anchor `37b8702c`, layered `13c54c51` (C0 passed); `kg anchor-sheets`
    `8075e4ca`; `kg anchor-judged` `417aa906` (at `ec990d9`).
  - Held-out (1 mention would be asked) and generality (none) were not replayed: only furniture was paid for.
- **Judging** (judge: Claude Opus 5.5, `claude-opus-5-5`): byte-identical items keep their verdicts (R95a's;
  the 3 links the chooser re-made, "weighted base", "adjustable shelves", "cable management system", R93's).
  19 items are new: 8 C4 links and 11 C6 pairs. A blind Opus 5.5 subagent judged all 19 VALID. The lead
  reviewed every new link, the 2 pairs the blind judge was least sure of (S-1085 from the dresser review:
  the item shows only one hop, and the staged data confirms A-1070 "Drawers" is the Helsingborg Dresser's;
  the desk review's "i expected more drawers"), and the 10 items the shifted VALID sample named: no change.
- **Committed:** `tests/gold/r95b/` (the replay report with every choice, logged counts, anchor reports,
  sheets, verdicts, judged report, `runs.json`). Two tests check the changes by cause and the choices, that
  only the 19 new items were judged, and that the report is their score.
- **Results, furniture** (judge: Claude Opus 5.5; Wilson 95 %; before = R95a's replay, after = R95b's):

  | Criterion | R95a | R95b |
  |---|---|---|
  | C4 precision (blind labels equal) | 38/38 [0.91, 1.00] | **49/49** [0.93, 1.00] |
  | C4, C3, C6 hard rules | pass | pass |
  | C3 R75 pairs: precision / apart / recall | 1.0 / 1.0 / 8/13 | 1.0 / 1.0 / **11/13** |
  | C3 split groups rightly apart (comparative) | 13/13 | 11/11 |
  | C6 arm A / arm B, record + individual | 91/91 / 97/97 | **101/101 / 108/108** |
  | C2 hit@1 / hit@5 | 68/86 / 77/86 | **70/86** / 77/86 |
  | C5, both modes, @5 and @10; unbudgeted reach | (R95a) | unchanged |
  | A vs vector, end to end, complete@5 | 16 vs 21, p 0.18 | unchanged |
  | C7 nodes; C8 arm B unwitnessed hops | 109; 268 | 103; 219 |

  Read with care:
  - **No new wrong link.** All 17 choices were judged right. They split into 8 links no earlier replay had
    right, 3 re-made containment links and 6 former joins (below). The 8: the Malmö desk's "drawer",
    "drawers" and "drawer slides", "metal rails", "removable covers", "shelf", "cord", "bookshelf". Two of them
    R93 had seen linked wrongly: the desk review's "drawers" (R93: the dresser's Drawers, cross-product) now
    links the desk's own Drawer Unit, and "drawer slides" (R93: Drawer Sides) now links Drawer Rails ("The
    drawer slides are smooth").
  - **Recovered:** the Malmö desk's "drawer" -> A-1062 Drawer Unit (the link fix A had to give up), and 3 of
    the 4 right containment links R95a gave up. **Not recovered: "frame construction"**: the LLM answered
    none (R93 judged the containment link VALID), so F17 stays lost end to end. The success criterion is met
    for precision (C4 1.0 >= 0.95) and the Malmö drawer, and for 3 of the 4 lost links.
  - **The chooser declined both wrong readings R93 found:** "pre-drilled holes for the drawer handle" and
    "drawer slide mechanism" (candidates Drawer and Drawer Handle) are answered none.
  - **Six mentions the build had joined to their record by the individuals' LLM adjudication** ("dresser",
    "sofa", "nightstands", "slat system", "Västerås Bookshelves", "Västerås") are now linked by a verified
    choice to the same record; their verdicts carry, and their edges now hold the quote that shows it.
  - Ranking: F12 ("removable covers") and F33 ("cord") now start from their records (C2 rank 2 -> 1); F48's
    "drawers" slips from 2 to 3 (the desk's Drawer Unit now holds the desk's "drawers"); no C5 hit changed.
- **Gate (results):** 704 passed (702 before), `ruff check` clean. **R95 done 2026-10-06.**

### R96. The record chooser's prompt: a name with an aspect word, and a whole over its pieces (done 2026-10-06; $0.0239)
The user, 2026-10-06, after R95b: "yes you may pursue that and check in with another prompt but it is
important that you keep the prompt dataset domain neutral so that the system can always generalize."
- **The miss.** R95b's chooser answered none for "frame construction" (Uppsala Sofa review: "The frame
  construction utilizes proper joinery techniques with reinforced corner blocks - details typically found in
  much more expensive pieces."). It was shown the sofa's Frame (`A-1012`) and three of its pieces (Base Frame,
  Back Frame, Side Frames). R93 judged the old link to `A-1012` VALID: by the C4 rule, a careful reader says
  the text speaks of that row. The reply has no reason field, so the cause is not recorded. Two rules of the
  prompt can produce it:
  - "Answer none if ... a property of it": "construction" can be read as a property of the frame;
  - "Answer none also when several records fit equally well": the frame and its three pieces all hold "frame".
- **Why it matters beyond one link:** F17 ("Which products priced over $500 have a review reporting a
  creaking or squeaking frame?") starts end to end from the first node W1 finds for "frame". The sofa's Frame
  and the bed's lone "frame" both match exactly, and the tie goes to the node more mentions refer to, then to
  the node id (`anchor/navigation.py` `find`). With "frame construction" the sofa's Frame has 2 mentions and
  wins; without it both have 1 and "9b49..." sorts before "Assembly:A-1012", so F17 starts at the bed.
- **The change, one prompt, two rules, domain-neutral** (`prompt-engineering` skill; examples stay in the
  invented telescope; no word or four-gram of an evaluated corpus, checked by the existing test):
  1. Choose also when the name is the record's name with a word for the side of it the sentence talks about
     (its design, how it is made); "a property of it" leaves the none list, since it contradicted this case
     and the judge's question.
  2. When the name fits a whole and also pieces of that whole (the relations show which), it is the whole,
     unless the sentences name the piece; none only when several still fit equally.
  Both rules change at once, so a win cannot be split between them; stated in the results.
- **Measured** by one replay of the furniture build with `--choose` (the user's yes above; every prompt
  changes, so all 31 calls are new: about $0.03), judged by R93's procedure. The prompt is changed after the
  judge counted a miss and is measured by the same judge: said so in the results.
- **Success:** no wrong link (C4 stays 1.0 on the judged items, at least >= 0.95); "frame construction"
  chosen; the two wrong readings R93 found ("pre-drilled holes for the drawer handle", "drawer slide
  mechanism") still none. A changed prompt that adds a wrong link is reverted.
- **Code (done 2026-10-06, `a98855f`):** `CHOICE_PROMPT` in `resolution/record_choice.py`, version
  `0d1d7d45e37d` -> `e4f3d9cfa725`, with its intent comment. The corpus-language test passes (no banned word,
  no four-gram of an evaluation corpus); the only examples are the telescope's ("the focuser design").
  Gate: 704 passed, `ruff check` clean.
- **Run** (the user's yes above; at `a98855f` with only `.claude/settings.json` dirty): `kg --preset quality
  audit-relink out/r77d_furniture --choose`, MLflow `ae18c0f5`: **31 LLM calls (0 cache hits: every prompt
  changed), 0 failures, 20,471 input / 1,049 output / 1,233 thinking tokens, `cost_usd` $0.0239.** Then $0:
  `kg anchor-eval` anchor `9178820d`, layered `7b123a20` (C0 passed); `kg anchor-sheets` `c29f82a6`;
  `kg anchor-judged` `d32559c3` (at `d4e46fb`).
- **One decision of 32 changed:** "frame construction" none -> `A-1012` (Frame), quoting "The frame
  construction utilizes proper joinery techniques with reinforced corner blocks - details typically found in
  much more expensive pieces." The other 31 are R95b's, the 13 nones among them ("pre-drilled holes for the
  drawer handle", "drawer slide mechanism", "drawer hardware", "back angle", "leg attachment mechanism" ...).
- **Judging:** nothing new to judge. The new link is byte-identical to the item R93 judged VALID (carried
  through R94); every other item equals an R95b item; the seeded VALID sample holds only reviewed items.
  Committed in `tests/gold/r96/` with `runs.json`; a test checks the single changed decision and the scores.
- **Results, furniture** (judge: Claude Opus 5.5, verdicts carried; before = R95b, after = R96):

  | Criterion | R95b | R96 |
  |---|---|---|
  | C4 precision (blind labels equal) | 49/49 | **50/50** [0.93, 1.00] |
  | C4, C3, C6 hard rules | pass | pass |
  | C6 arm A / arm B, record + individual | 101/101 / 108/108 | 102/102 / 108/108 |
  | C2 hit@1 | 70/86 | **72/86** (F04, F17: "frame" rank 2 -> 1) |
  | C5 end to end: recall@5, complete@5 (A) | 30/61, 16/31 | **31/61, 17/31** (F17) |
  | C5 end to end @10: recall, complete (A) | 39/61, 21/31 | **40/61, 22/31** |
  | C5 gold start, unbudgeted reach | | unchanged |
  | A vs vector, end to end, complete@5 | 16 vs 21, p 0.18 | 17 vs 21, p 0.29 (R94's) |

  Read with care:
  - **The success test is met:** the declined link is chosen, no wrong link appeared, and both wrong readings
    stay none. With it, every link R95a gave up is back, plus the Malmö drawer, at C4 50/50.
  - **Which rule did it is unknown:** both rules changed at once, and the reply carries no reason.
  - **The prompt was changed after the judge counted a miss, and measured by the same judge** (one model
    family). The change is general (an aspect word; a whole over its pieces), but it was found on one case,
    and only furniture was replayed: held-out (its 1 near-miss mention) and generality (none) are not.
  - **F17 recovers through a tie, not a walk:** "frame" names the sofa's Frame and the bed's lone "frame"
    alike, and W1 orders equal exact hits by mentions, then by node id. "frame construction" gives the sofa's
    Frame its second mention, so it wins. One mention fewer and the id decides again.
- **Gate (results):** 705 passed (704 before), `ruff check` clean. **R96 done 2026-10-06.**

### Plan R97-R103: a connected, correctly identified, fully mentioned anchor graph (accepted 2026-10-06)
After R96 every hard rule passes. What remains are **missing connections**, worst on generality, where vector
retrieval beats the graph (every gold chunk in the top 10: 37 vs 22 of 38 questions, p < 0.001). The user
asked for three fixes, each at its root cause, domain-neutral, precision first, with provenance kept, tests,
a measured effect and regression checks. The causes, verified on the r77d snapshots:

| Aim | Root cause | Example |
|---|---|---|
| 1 Anchoring | A walk goes from a chunk only to what it is `ABOUT` (`anchor/navigation.py`). The graph stores what a chunk *names* (Chunk -> Mention -> REFERS_TO), but no walk reads it that way. `ABOUT` assumes one subject per document, so 28 of 32 generality documents are about nothing. | G28/G29 reach `harbour_station_may_2025.md#0` and the 9 June minutes, then stop, though the chunks name "KV12-0457" (-> `Pump:KV12-0457` by key) and "Aiko Tanaka" (-> `Staff:S-150` by name). |
| 2 Identity | All 9 generality split groups were nominated and answered apart. The adjudicator sees at most 3 lines per side, each the first sentence naming it, often a heading; record units show no cells; there is no "unsure". Outside a scope the record chooser gets no candidates. | "Harbour Station" in the May report shows only its heading. "Jon Pike"/"J. Pike" stay apart from Staff S-131 "Jonathan Pike". |
| 3 Coverage | Mentions exist only as ends of claims; the extractor skips what no fact type fits. | "Brackwater fen" appears only in its log's title; "No leaks were found on HP40-2291" has no state type. Unplaced targets: 2/86, 11/65, 15/62. |

- **The user's decisions (2026-10-06):** W3 is extended to the records and individuals a chunk names, never
  concepts; mentions get a separate mention pass; the measurement is two rebuilds (first reusing the r77d
  claims, unconfounded; then re-extracting with today's prompt, end to end).
- **No multi-subject `ABOUT` code.** Furniture and held-out documents are all anchored by `kg link`; each
  generality document is one chunk, so a title anchor reaches nothing the W3 hop does not; text anchors are
  never scopes. "Not one record per document" is met by W3-named.
- **Negations:** the denied thing becomes a reachable mention ("leaks" in "No leaks were found"), with no
  truth flag; the reader sees the sentence (stored truth is a non-goal of the direction, section 2).
- **Out of scope, stated as limits:** an acronym rule ("X (Y)", no gold needs it); the 29 held-out recall keys
  typed Vehicle; query ranking, tie-breaks and W1 inflections (query side).
- **Steps, strictly one after another** (`implement-step`; one commit per part, `step RNN: ...`):
  - **R97** W3 follows a chunk to the things it names ($0).
  - **R98** Identity replay offline, records and individuals, proven faithful against resolve.json ($0,
    structural).
  - **R99** Record candidates outside a scope: name variants and unique key-attribute values (small paid
    replay, est. < $0.05, asked).
  - **R100** Evidence-based individual adjudication that may answer `unsure` (paid replay, est. $0.3-0.6,
    asked).
  - **R101** A mention pass: the retrieval-worthy things the text names or talks about (gold first, then
    code; at most one `dev`/`smoke` run). Bounds fixed now, before measuring: judged mention precision
    >= 0.90, no new C7 hub, C9 growth reported, concept merges of pass mentions judged.
  - **R102** Rebuild with the r77d claims, and evaluate everything (paid, asked per dataset, est. $0.5-1.0).
  - **R103** Rebuild with re-extracted claims (paid, asked, est. $1.9-2.5).
  - **R104** (inserted 2026-10-07, done before R103) The mention pass states each thing's class apart from its
    type; title, use and address rules clarified; the sheet shows the stated class.
  - **R105** (inserted 2026-10-07, before R103) R104 measured by a pass-only replay on R102's builds, judged.
- **Regression checks at every measured step:** C0 passes; C1 = 1.0; C3 0 wrong merges of records or
  individuals and R75's apart pairs 1.0; C4 >= 0.95 with 0 confirmed cross-scope links; C6 >= 0.95 for record
  and individual starts; C8 arm A 0 unwitnessed hops; C9 no per-dataset code or setting, and the corpus
  four-gram and banned-word tests on every new or changed prompt; from R102 on, mention precision >= 0.90 and
  no new hub. Reported: C2, C5 paired against vector retrieval, C7, identity recall, mention recall and
  precision, and the cost of every run from MLflow.
- **Judging:** Claude Opus 5.5 in the session; blind subagent batches, then the lead reviews every INCORRECT,
  AMBIGUOUS and UNJUDGEABLE verdict and the seeded 10 % of VALID ones; byte-identical items carry their
  verdicts. Every paid run is asked for separately with its cost, as a plain guarded `kg` command.

### R97. W3 follows a chunk to the things it names (done 2026-10-06; $0, no LLM, no Neo4j)
- **Scope.** `anchor/navigation.py`: a new walk `named(chunk)`, the records and individuals the chunk's
  mentions refer to (`SnapshotMention.chunks` with the references of kind record or individual, i.e. W2's
  mention edges read backwards). The composed walk goes from a chunk to `about | named`. `about` keeps its
  meaning; W2 (`chunks_of`), which C6 and C7 read, is unchanged; no thing-to-thing hop is added (the chunk is
  the witness). Both arms get the hop: it is an anchor walk.
- **Why never a concept:** a kind ("leaks", "drawer rails") is shared by every chunk that names it, so
  following it from a chunk would join unrelated chunks through the kind, the leak section 3.1 forbids.
- **Correction of R90 and R92's note** "the pump mentions do not link to the Pump records" / "the pump and
  staff mentions are not linked to their records": they are. In the r77d generality snapshot "KV12-0457" in
  the May report refers to `Pump:KV12-0457` by key, and "Aiko Tanaka" in both minutes to `Staff:S-150` by
  name. What was missing is a walk from the chunk to them. (The split "Jon Pike" is a separate cause, R100.)
- **Direction document** (local): section 4's W3 row and a witness note; section 5's note on documents about
  several things.
- **Tests** (`tests/test_anchor.py`, +5): an invented document ABOUT nothing whose chunk names a record, an
  individual and two kinds; the walk reaches the record's other chunks in both arms; a named concept is never
  followed (the shared "shade" does not lead into the lamp's chunk); `named` is W2 read backwards, W2 and the
  relation hops are unchanged.
- **Gate (code):** 710 passed (705 before, Neo4j up), `ruff check` clean. Committed at `46b618c`.
- **Runs** ($0, no model, at `46b618c` with only `.claude/settings.json` dirty, the dataset presets as in
  R90-R93), on the current builds: furniture `out/r96_furniture/build`, held-out and generality
  `out/r77d_*`.
  - `kg anchor-eval`, anchor / layered: furniture `56daa619` / `8472ee9c`, held-out `06b827ee` / `409172bc`,
    generality `b7579fa9` / `e4f8336c`. C0 passed on all six. (Generality was first run with
    `--preset generality_gemini`, `d1596afe` / `575dd7fb`, same experiment and reports; superseded.)
  - `kg anchor-sheets`: furniture `3ac261d4`, held-out `f8ba998c`, generality `30eec913`. **Every C3, C4 and
    C6 sheet and code side is byte-identical to the committed one** (R96's furniture, R93's held-out and
    generality), so every verdict and judged score carries unchanged and nothing is judged; C3, C4 and C6
    hold as before (all hard rules pass).
  - The vector pairing is R92's arm-C rankings paired with the new reports (no embedding call).
- **Committed:** `tests/gold/r97/` (the three reports that changed: held-out anchor, generality anchor and
  layered) with `runs.json`, which names, for the three unchanged reports, the earlier committed file and its
  hash, and the hashes of the sheets the runs wrote. Four tests (+11 cases) reload them: unchanged metrics
  wherever every document is anchored, the generality gains and the one loss, the pairing with vector
  retrieval, and the sheet hashes.
- **Results.**
  - **Furniture and held-out: no metric changed in either arm.** Furniture's reports are byte-identical.
    Held-out arm A reorders the top 10 of 6 questions (H14, H17, H18, H25, H27, H30) with no hit changed:
    every document there is anchored by `kg link`, and what its chunks name is reached by W2/W4 already.
  - **Generality, arm A** (before = R90's report; C = R92's vector arm; McNemar exact p):

    | Criterion | before | after R97 |
    |---|---|---|
    | C8 connections (multi-hop, no budget) | 7/12 | **12/12** (G04, G28, G29, G34) |
    | C8 thing hops, unwitnessed | 0, 0 | 0, 0 (W3 adds no thing-to-thing hop) |
    | C5 gold start: recall@5 / @10 (51 gold chunks) | 36 / 40 | 36 / **46** |
    | C5 gold start: complete@5 / @10 (38 questions) | 25 / 29 | 25 / **33** |
    | C5 end to end: recall@5 / @10 | 28 / 33 | **32 / 45** |
    | C5 end to end: complete@5 / @10 | 17 / 22 | **21 / 32** |
    | C5 unbudgeted reach, gold start / end to end | 40 / 33 | 46 / 45 |
    | A vs C, gold start, complete@10 | 29 vs 37, p 0.008 | **33 vs 37, p 0.22** |
    | A vs C, end to end, complete@10 | 22 vs 37, p < 0.001 | **32 vs 37, p 0.125** |
    | A vs C, end to end, complete@5 | 17 vs 34, p < 0.001 | 21 vs 34, **p 0.001** |
    | A vs C, gold start, complete@5 | 25 vs 34, p 0.012 | 25 vs 34, p 0.012 |
    | A vs B, end to end, complete@10 | 22 vs 31, p 0.004 | 32 vs 31, p 1.0 |
    | C2 hit@1 / hit@5; C7 median / p90, hubs; C9 | 41 / 45 of 62; 0.031 / 0.156, HP40-1183, HP40-2291 | unchanged |

  - **Per question** (complete within the budget, arm A):
    - gold start, @10 gained: G04, G05, G15, G37; @5: G15 gained, **G36 lost**;
    - end to end, @10 gained: G02, G04, G05, G13, G15, G29, G30, G35, G37, G38; @5 gained: G13, G15, G29, G35;
      none lost.
  - **Generality arm B** reorders the top 10 of 4 questions (G13, G26, G27, G28) with no hit or metric
    changed: its claim attachments already reached these things. Arm A now reaches what arm B reached, with
    0 unwitnessed hops against arm B's claim joins.
  - Read with care:
    - **The expected connections are made.** G28 ("In which year was the pump installed whose intake screen
      was cleaned on 6 May 2025?") reaches `Pump:KV12-0457` from the May report's "KV12-0457"; G29 reaches
      `Staff:S-150` from the 9 June minutes' "Aiko Tanaka"; G04 reaches Staff S-131 and the spring newsletter
      ("Dr. Jonathan Pike"); G34 the pump, as G28.
    - **The one loss is a ranking (query-side) loss, as the plan expected.** G36 ("Which Pike wrote to the
      newspaper about the delay of the Harbour Street resurfacing?"), gold start: the "Harbour Street" target
      now walks through `news/flood_meeting.md#0` to "Jonathan Pike" (Staff S-131) and on to S-131's four
      institute chunks. Those are now reached by two targets, so the ranking rule ("more distinct targets
      first") puts them above the letter (rank 2 -> 9; still within 10). No edge is wrong; the ranking cannot tell the two
      Pikes apart.
    - **Generality still trails vector retrieval at 5 chunks** (21 vs 34 end to end, p 0.001; 25 vs 34 gold
      start, p 0.012); at 10 the gap is no longer significant. The remaining misses are unplaced targets (15
      of 62: names the build never wrote, R101) and split individuals (9 groups, R98-R100).
- **Gate (results):** 721 passed (710 before), `ruff check` clean. **R97 done 2026-10-06.** Next: R98.

### R98. The identity of particulars replayed offline, proven faithful (done 2026-10-06; $0, structural)
R94-R96 replay only the record matching; the LLM's joining of individuals was never replayed, so a change to
it (R99, R100) could only be measured by a rebuild. R98 makes the whole identity of records and individuals
replayable on a snapshot and proves the replay is the build's.
- **Split: three parts, one commit each.**
  - (a) **Structural move, behaviour kept:** the pure core of `resolve_particulars` (units -> nominate ->
    join -> one assignment per mention) becomes the public `assign_particulars(keyed, individuals, matches,
    texts, meaning, llm, settings)`; `resolve_particulars` is "read and match records, then this". The pairs
    near in meaning leave `nominate` for `individuals.meaning_pairs` (per type, as before), so a replay can
    give a build's logged pairs instead.
  - (b) **The replay:** `audit/reidentify.py` restates `read_mention_texts` over the snapshot and takes the
    record matches from the build (faithful) or from the record replay (measured), and the meaning pairs
    from the build's log (faithful) or the embedder (measured). `kg audit-relink --join` replays the
    individuals; a change is explained as `joined` / `unjoined` (or by its record replay's cause), anything
    else is refused; the written build carries the replayed `individual_decisions`. `--join --faithful` is
    the gate: it must reproduce resolve.json's `individual_decisions` and particular assignments field by
    field, or it fails.
  - (c) **The faithfulness gate** on the three r77d builds, answered from the `.cache/llm` hits (cache-only:
    a miss fails instead of paying). If it does not reproduce, R98 stops and is reported.
- **Part a (done 2026-10-06):** `resolution/particulars.py` `assign_particulars` and the `Meaning` callable;
  `resolution/individuals.py` `nominate(units, borderline, near)` and `meaning_pairs(units, embedding,
  blocking)`. One test call changed its arguments (`nominate(..., set())`), no assertion. Gate: 721 passed,
  `ruff check` clean. Committed at `b185b0d`.
- **Part b (code done 2026-10-06, $0):**
  - **`audit/reidentify.py`:** `particular_mentions` and `mention_texts` (the reads of `read_mentions` and
    `read_mention_texts` over the snapshot, in their order: the order decides the adjudicator's lines and so
    the cache keys); `built_matches` (the build's own record decision per keyed mention, from resolve.json's
    record edges with a matching reason and its `ambiguous` list; an adjudicated record edge is no match;
    `model_construct` keeps a retired reason such as `contained`); `logged_meaning` (the build's
    meaning-nominated pairs); `reidentify` (calls `assign_particulars`); `unfaithful` (every field that
    differs, decisions in order and every particular assignment); `identity_changes` (measured mode: a
    changed canonical entity explained by its group, `joined` / `unjoined`; a mention whose record replay
    changed keeps that cause); `with_build_targets` (a replayed record edge keeps the build's element id).
  - **`audit/relink.py`:** `MATCH_REASONS` public; `Relink.matches`, every keyed mention's replayed match.
  - **`AuditRelinkStage`:** `--join` (measured: the record replay's matches, the embedder's meaning pairs;
    refused without an embedder when the settings nominate by meaning; unexplained changes refused; the
    written build carries `individual_decisions`) and `--join --faithful` (the gate: no build written;
    `faithful`, `faithful_issues`; fails on any difference). Params `join`, `faithful`,
    `individual_prompt_version` (and the blocking rule in measured mode); metrics per decision action,
    `nominated_by_meaning` against the build's, `changes_joined` / `changes_unjoined`; artifact
    `reidentify.json` and the prompt. `kg audit-relink --join [--faithful]`.
  - **Run guard:** `audit-relink --join` is an LLM command (`FLAG_LLM_COMMANDS` now maps a command to a set
    of flags).
  - **Tests (+5):** `test_audit.py` +3 on the invented build with a person named in both reviews ("Ada Lin" /
    "A. Lin", joined by the build): the faithful replay reproduces the join (one adjudication, no build
    written); a build that logged the pair apart is refused with the differing fields named; the measured
    replay with an adjudicator answering "not the same" explains the undone join (`unjoined`, the founder
    unchanged), keeps R94's `left_scope`, writes the decisions and the build's element ids, and the written
    build passes its own gate. `test_run_guard.py` +2 (`--join`, `--join --faithful` ask).
  - **Dry check without any model** (scratch, nothing sent): on the three r77d builds the replay nominates
    exactly the build's pairs in the build's order (furniture 93, held-out 164, generality 183), and with the
    build's settings (gemini-3.8-flash, thinking low) every adjudication prompt it would send is in
    `.cache/llm` (83 / 144 / 181).
  - README: the commands and the module map. Gate: 726 passed, `ruff check` clean.
  - Committed at `5aa688a`.
- **Part c: the faithfulness gate (done 2026-10-06, $0).** The user agreed ("Yes, run all three"). At `5aa688a`
  with only `.claude/settings.json` dirty, each command with `GEMINI_API_KEY=invalid` so that a cache miss
  would fail instead of paying: `kg --preset <quality|heldout|generality_gemini> audit-relink out/r77d_<ds>
  --data ... --logged tests/gold/r87/<ds>_logged.json --join --faithful`.

  | | furniture | held-out | generality |
  |---|---|---|---|
  | MLflow run | `a74607dc` | `aba001d7` | `90dd6bea` |
  | particular mentions, individual decisions | 124, 93 | 260, 164 | 184, 183 |
  | joined / apart / different_records | 11 / 72 / 10 | 14 / 130 / 20 | 84 / 97 / 2 |
  | nominated by meaning (replay = build) | 34 | 100 | 40 |
  | LLM calls, all cache hits; `cost_usd` | 83; 0 | 144; 0 | 181; 0 |
  | **differences from resolve.json** | **0** | **0** | **0** |

  The replay with today's adjudicator (prompt `3793a4eaa358`, gemini-3.8-flash, thinking low) gives back
  every individual decision in order and every particular assignment field by field (canonical, name,
  reason, score, evidence, `by`, element id) on all three builds. Measured replays of the identity (R99,
  R100) can therefore rest on it: a difference they show is the rule change's, not the replay's.
  - **Committed:** `tests/gold/r98/<ds>/reidentify.json` (the replayed decisions) and `runs.json` (commands,
    run ids, calls and hits, hashes); a test reloads them.
- **Gate (results):** 729 passed (726 before), `ruff check` clean. **R98 done 2026-10-06.** Next: R99.

### R99. Record candidates outside a scope: name variants and unique attribute values (done 2026-10-06; $0.009)
A keyed mention in a document without a scope gets no near miss today (R95b: "the whole domain vouches for
nothing", R94), so the chooser is never asked. On generality, where no document has a scope, "Jon Pike" (the
12 May minutes) and "J. Pike" stay apart from Staff S-131 "Jonathan Pike", and the open-day "Kestrel V-12" is
`no_record` though one pump only is of that model.
- **Scope.** `resolution/record_choice.near_misses` gains the mention's type records (`domain`) and, only
  when the document has no scope, offers strict tier-2 candidates (`_unscoped`):
  - a record whose name is a variant of the mention's (`variants.compatible`: a title, an initial, a short
    form; never a shared word alone);
  - the one record whose key attribute (schema `key_attributes`) holds the mention's name exactly;
  - an attribute twin (a value two records hold: a model of two pumps, a team) offers nothing; a twin of
    names is refused at the choice, as before.
  The chooser's prompt is unchanged, abstention and the quote check stay. A choice outside a scope is linked
  with `scoped = False` (the request carries it; nothing reads the flag yet, but it must not lie).
  Callers: `particulars._match_records` (the type's records) and the replay (`_Replay.candidates_of`, now
  public).
- **A behaviour change, stated:** `tests/test_identity.py`'s Pike corpus has no scope, so "Jon Pike" and "J.
  Pike" are now first offered to the chooser (candidate S-131, never Judith Pike). Its scripted LLM answers
  none, so the R75 joining it tests runs as before; the test now also asserts the two choices.
- **Tests** (+3 in `tests/test_record_choice.py`, an invented observatory): a variant candidate (a short
  form; an initial fitting two staff offers both), a shared word alone offers nothing; the one record of an
  attribute value is a candidate, an attribute twin (two instruments of one model, a team of two) is not; a
  scoped document keeps tier 2 as it was; a choice outside a scope is linked unscoped. The near-miss test
  passes `domain`.
- **Dry run without a model** (record replay of the three r77d builds): furniture still asks 31 mentions (1
  without a sentence) and held-out 1, as in R95b: every document there has a scope. Generality asks 6:
  "M. Lopez" (fieldwork log; S-104 and S-219, twins of name, so no choice can link), "J. Pike" (fieldwork
  log) and "J. Pike" (9 June minutes) and "Jon Pike" (12 May minutes), each with S-131, and "Kestrel V-12"
  twice (shift notes, open day) with `Pump:KV12-0457`.
- **Gate (code):** 732 passed (729 before), `ruff check` clean.
- **Measurement (next, paid, asked):** `kg audit-relink --choose --join` per dataset. A cache count without
  sending anything: furniture's 31 choices are R96's cache hits and 9 adjudications are new (the units R94-R96
  changed); held-out 1 choice; generality 6 choices, then the adjudications the choices change.
  Committed at `7e6d329`.
- **Runs** (the user's yes for each dataset, 2026-10-06; at `7e6d329` with only `.claude/settings.json` dirty):
  `kg --preset <generality_gemini|quality|heldout> audit-relink out/r77d_<ds> --data ... --logged
  tests/gold/r87/<ds>_logged.json --out out/r99_<ds> --choose --join`, MLflow generality `9998428d`
  ($0.0045), furniture `ee6a8e9b` ($0.0037), held-out `c8759a09` ($0.0008): **$0.009 in all**. Then $0:
  `kg anchor-eval` (both arms) and `kg anchor-sheets` on `out/r99_<ds>/build`. C0 passed on all three
  replayed builds; no unexplained change. Sheets and replay outputs committed in `tests/gold/r99/<ds>/`;
  judging next (`d162cb1`).
- **Judging** (judge: Claude Opus 5.5, `claude-opus-5-5`). Every other item of the nine sheets equals an earlier
  item byte for byte (R96's furniture, R93's held-out and generality sheets), so its verdict, review and
  change carry over. Eight items are new: furniture C3 `m:a56b8c94...` ("lower shelf" + "shelf" of the coffee
  table review, the one new join); held-out C4 "2016 CIVICS" -> `Vehicle:CIVIC`; generality C4 the three Pike
  mentions -> `Staff:S-131` and C6 S-131 with their three chunks. A blind Opus 5.5 subagent (rules files and
  the 8 items only) judged all 8 VALID and named the Pike links close calls: the texts never write the first
  name. The lead reviewed all 8 and agrees: the staff table has one Pike only (S-131, Soil Ecology group
  leader), the corpus ties him to the Aldmoor bog work ("Her group leader, Dr. Jonathan Pike"; "Dr. J. Pike
  (Soil Ecology) ... the Aldmoor cores"), and the only other Pike (Judith) writes a residents' letter. The
  shifted seeded VALID sample named 9 carried verdicts no lead had reviewed (furniture C3 "clear", "45cm";
  generality C4 four pump key links; C6 S-219 in the June minutes, "seal", Core BF-2); the lead reviewed
  them: no change.
- **Committed:** `tests/gold/r99/<ds>/` (relink, reidentify, logged counts, anchor reports, sheets, code
  sides, verdicts, judged report) with `runs.json` (commands, run ids, calls, hits, cost, hashes). Two tests
  check that every earlier verdict carries, only the 8 new items were judged, the scores are the committed
  ones, and the generality choices.
- **The first measured join replay** (R98's machinery on R94-R96's record changes, which R94-R96 could not
  measure): furniture's six joins of the build that R95b's verified choices made redundant ("sofa",
  "dresser", "nightstands", "slat system", "Västerås Bookshelves", "Västerås") are not asked again, and one
  join is new: "lower shelf" with "shelf" in the Jönköping review (judged VALID). Nominated by meaning:
  furniture 28 (build 34), generality 38 (40), held-out 100 (100): the units changed, so the mutual-nearest
  pairs move with them. Every other decision is the build's (cache hits: furniture 99/110, held-out 143/144,
  generality 169/177 calls).
- **Results** (judge: Claude Opus 5.5; before = R96 furniture, R93 held-out and generality for judged
  criteria, R97 for the anchor reports; after = R99's replays):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | C4 precision | 50/50 (=) | 75/76 -> **76/77** | 40/40 -> **43/43** |
  | C4 cross-scope confirmed | 0 | 0 | 0 |
  | C3 wrong merges: individuals / records | 0 / 0 | 0 / **1** (R93's, unchanged) | 0 / 0 |
  | C3 hard rule | passes | **fails** (since R93: "2017-2022 Rogue Sport" -> `Vehicle:ROGUE`) | passes |
  | C3 individual merges judged (n) | 4 -> 5 (all right) | 10 | 31 -> 30 |
  | C3 R75 pairs: precision / apart / recall | 1.0 / 1.0 / 11/13 (=) | 1.0 / 1.0 / 5/7 (=) | 1.0 / 1.0 / **10/13 -> 13/13** |
  | C6 arm A / arm B, record + individual | 102/102 / 108/108 (=) | 77/77 / 77/77 (=) | 80/80 -> **83/83** both |
  | C2 hit@1 / hit@5; unplaced | 72 / 77 of 86; 2 (=) | 46 / 52 of 65; 11 (=) | 41 / 45 of 62; 15 (=) |
  | C5 gold start: recall@10, complete@10 | 56/61, 29/31 (=) | 30/36, 24/28 (=) | 46 -> **45**/51, 33 -> **32**/38 (G36) |
  | C5 end to end: complete@5 / @10 | 17 / 22 of 31 (=) | 19 / 21 of 28 (=) | 21 -> **22** (G30) / 32 -> **31** (G36) of 38 |
  | A vs vector, end to end, complete@10 | 22 vs 26 (=) | 21 vs 26 (=) | 32 vs 37, p 0.125 -> 31 vs 37, p 0.070 |
  | C7 hubs (> 20 % of the corpus) | none | none | HP40-1183, HP40-2291 + **Staff:S-131** (8 of 32 chunks) |
  | C8 arm A connections; unwitnessed | 13/14; 0 | 16/16; 0 | 12/12; 0 |
  | Cost (MLflow `cost_usd`) | $0.0037 | $0.0008 | $0.0045 |

  Read with care:
  - **The success test is met where R99 acts:** all four new generality links are judged right; "M. Lopez" is
    refused as a twin of two Maria Lopez records, as it must be. **One link is missed:** the open day's
    "Kestrel V-12" ("Technician Rosa Delgado showed them the station's Kestrel V-12 pump", Harbour Station) is
    KV12-0457, the one pump of that model and station, but the chooser answered none: a precision-first
    abstention (no reason is recorded), so the mention stays `no_record`. Identity recall on generality reaches
    13/13, precision and apart stay 1.0.
    Furniture and held-out get no new candidate, as the dry run said; held-out's one never-replayed choice
    (R95b's open item) links "2016 CIVICS" to the Civic row, judged right.
  - **A new hub that is right:** S-131 ("Jonathan Pike") is named in 8 of 32 generality chunks once "Jon
    Pike" and both "J. Pike" are his. C7 lists it; it is no rule breach (the "no new hub" bound is R101's,
    for pass mentions).
  - **The losses are ranking, not edges:** G36 ("Which Pike wrote to the newspaper ...?") starts from S-131
    and Judith Pike; S-131's three new chunks now outrank the letter within 10. The query names only
    "Pike", which the graph cannot resolve between the two; query-side, as in R97.
  - **Held-out's C3 hard rule fails, and did since R93**: one INCORRECT record link ("2017-2022 Rogue Sport" ->
    `Vehicle:ROGUE`, R93's cause 4). The plan's premise "after R96 every hard rule passes" missed it (R94-R96
    fixed furniture only). Not caused by R97-R99; recorded under "Found along the way" for its own step.
- **Gate (results):** 736 passed (732 before), `ruff check` clean. **R99 done 2026-10-06.** Next: R100.

### R100. Evidence-based individual adjudication that may abstain (done 2026-10-06; $0.398)
The adjudicator of individuals (`individuals.llm_adjudicator`) sees at most three lines per side, each the
first sentence naming it (`resolver.mention_lines`), often a heading; the sentence that holds the evidence
usually does not name the thing, `verified` wants each quote to name its side, record units show no cells, and
there is no "unsure". All nine generality split groups were nominated and answered apart (R93).
- **Split: two commits.**
  - (1) **A bug, its failing test first:** an `LLMResponseError` in one adjudication escaped `join` and failed
    the whole resolve stage (and the replay). It becomes action `failed`, logged, the pair kept apart.
  - (2) **The design:** per side the sentences naming it with one sentence of window each way, capped; a
    record unit's cells; the records both sides' chunks name besides their own; answers `same` / `different`
    / `unsure`; a quote must be one of that side's shown lines. Measured by a paid replay (asked).
- **Commit 1 (done 2026-10-06, $0):** `individuals.join` asks through `_ask`, which logs a failed call
  (`LLMResponseError`: the provider kept failing or the reply did not parse) and answers None; `_decide` tells
  a pair asked without an answer (`failed`, `by` the model) from one never asked (`skipped`, `by` code).
  `Action` gains `failed`; the resolve stage's `individual_<action>` metrics follow `Action`
  (`_INDIVIDUAL_ACTIONS = get_args(individuals.Action)`), so `individual_failed` is logged. The test came
  first and failed with the escaping error: `test_a_failed_adjudication_keeps_its_pair_apart_and_never_fails_the_others`.
  Gate: 737 passed (736 before), `ruff check` clean.
  Committed at `89b6264`.
- **Commit 2 (code done 2026-10-06, $0):**
  - **New `resolution/identity_evidence.py`** (pure): `side_lines` (every sentence of a side's chunks that
    names it, with the sentence before and after in the same chunk, each line once, at most 4 naming
    sentences, a sentence cut at 400 characters; each line marked whether it names the side);
    `build_evidence` (per unit its lines, its record and the record's data; per unit the records its chunks
    name, from the keyed mentions' links); `IdentityEvidence.shared` (the records both sides' chunks name,
    besides their own, at most 5).
  - **`individuals.py`:** `IDENTITY_PROMPT` (version `3793a4eaa358` -> new, logged as
    `individual_prompt_version`): the lines with "*" on those naming the side, a record unit's data, the
    shared records as context; "same" only when the lines state the same role, position, place, organisation,
    date or event for both; a similar name or a shared record or place is not enough; answers `same` /
    `different` / `unsure`; quotes one line per side; examples from an invented observatory.
    `SameIndividual.answer` replaces `same`. `verified`: each quote stands verbatim in one of the lines shown
    for its own side (a copied "[document]" prefix ignored) and each side shows a line naming it; the quote
    need not name its side. `join` takes the shown lines; a pair whose side has no naming line is not asked
    (`no_sentence`, before: asked and always refused); `different` -> `apart`, `unsure` -> `unsure`, both apart.
    `Action` adds `unsure` and `no_sentence` (resolve metrics follow `Action`).
  - **Wiring:** `assign_particulars(..., views)` builds the evidence; `resolve_particulars` reads the linked
    records' data (`read_candidate_views`, only when an LLM is asked); the replay gives the snapshot's
    (`relink.snapshot_views`, moved out of `_Replay.view`, behaviour kept).
  - **No second pass** (asking again with the evidence of an earlier pass's joins): to "Found along the way"
    until a replayed pair needs it.
  - **Tests** (`tests/test_individuals.py`, 21 -> 26): the lines of a side (window, heading two away not
    shown, cap); the evidence (a record unit's data, the shared record, the prompt's rendering); evidence only
    in the neighbouring sentence joins; a quote counts only among its own side's shown lines (a sentence of
    the chunk outside the window refused; a copied "[document]" prefix accepted); `unsure` and `different`
    keep the pair apart, counted apart; a side no sentence names is not asked; the corpus-language test passes
    on the new prompt and schema (no evaluated-corpus word, no shared four-gram). The scripted adjudicators of
    `test_identity.py` and `test_audit.py` answer with `answer` instead of `same` (no assertion changed).
  - README module map. Gate: 742 passed, `ruff check` clean.
  - **Size of the measurement, counted without sending anything** (the R99 replay's record decisions, every
    chooser prompt a cache hit): 76 / 143 / 171 adjudications, all new (the prompt changed), about 1.0M
    prompt characters (~250k input tokens): about $0.06 furniture, $0.15 held-out, $0.13 generality.
  - Committed at `4ea8f50`.
- **Runs** (the user's yes for each dataset, 2026-10-06; at `4ea8f50` with only `.claude/settings.json` dirty):
  `kg --preset <generality_gemini|quality|heldout> audit-relink out/r77d_<ds> ... --out out/r100_<ds> --choose
  --join`, prompt `48511723ea6e`: generality `713aa2c6` ($0.165, 177 calls, 6 cache hits: the chooser's),
  furniture `1f127c26` ($0.050, 109 calls, 31 hits), held-out `4101dba3` ($0.182, 144 calls, 1 hit): **$0.398
  in all**, 0 failed calls, 0 unexplained changes. Then $0: `kg anchor-eval` (both arms) and `kg
  anchor-sheets` on `out/r100_<ds>/build` (C0 passed on all three). Sheets and replay outputs committed in
  `tests/gold/r100/<ds>/`; 30 items new or changed against R99's sheets, all generality but two; judging
  next (`f999b84`).
- **Judging** (judge: Claude Opus 5.5, `claude-opus-5-5`). Items byte-identical to R99's keep R99's verdicts.
  30 items are new or changed: furniture C3 2, generality C3 9 (the grown North Station, Harbour Station,
  Rosa Delgado, Hydrology, council nodes, the new Finance Office, Utrecht and Aldmoor nodes, the new room B12
  split), C4 1 (the open day's "Kestrel V-12" -> `Pump:KV12-0457`, joined to the record's unit by the
  adjudicator), C6 18. Two blind Opus 5.5 subagents (C3+C4: 12 items; C6: 18) saw only the rules and their
  items: 25 VALID, 4 VALID_ALTERNATIVE (furniture "dimmer" + "dimmer switch"; "Aldmoor", whose minutes
  heading "Aldmoor overrun." is shorthand for the project; "Hydrology" with the heading "New hydrology flume";
  "council" twice by context), 1 INCORRECT: the split `s:Place:room b12` (the two seminar notices of one
  institute name one room, now two nodes). The lead reviewed all 30 and the 13 carried VALID verdicts the
  shifted seeded sample named: no change.
- **The prompt's effect, pair by pair** (R100 against R99's replay: the same units and the same nominated
  pairs; only the adjudicator differs):

  | | furniture (91 pairs) | held-out (163) | generality (173) |
  |---|---|---|---|
  | joined R99 -> R100 | 5 -> 2 | 14 -> 11 | 82 -> 92 |
  | apart -> joined / joined -> unsure / apart -> unsure | 1 / 4 / 2 | 0 / 3 / 17 | 21 / 11 / 28 |
  | `unsure` (new) | 6 | 20 | 39 |
  | `no_sentence` (not asked) | 1 | 0 | 0 |
  | failed calls | 0 | 0 | 0 |

- **Results** (judge: Claude Opus 5.5; before = R99's replay, after = R100's):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | C3 wrong merges of individuals | 0 | 0 | 0 |
  | C3 wrong merges of records | 0 | 1 (R93's, Found along the way) | 0 |
  | C3 split groups that are one thing (comparative) | 0 of 11 -> 0 of 12 | 0 of 3 | **9 of 9 -> 3 of 3** |
  | C3 R75 pairs: precision / apart / recall | 1.0 / 1.0 / **11/13 -> 10/13** | 1.0 / 1.0 / 5/7 | 1.0 / 1.0 / 13/13 |
  | C3 individual merges judged (n) | 5 -> 2 | 10 -> 9 | 30 -> 30 (larger nodes) |
  | C4 precision | 50/50 | 76/77 | 43/43 -> **44/44** |
  | C6 arm A / B (record + individual) | 102/102 / 108/108 | 77/77 / 77/77 | 83/83 -> 84/84 |
  | C5 gold start complete@5 / @10 (38) | (=) | (=) | 25 / 32 -> 25 / **31** (G15) |
  | C5 end to end complete@5 / @10 (38) | (=) | (=) | 22 -> **24** (G37, G38) / 31 |
  | A vs vector, end to end, complete@5 | (=) | (=) | 22 vs 34, p 0.002 -> 24 vs 34, p 0.006 |
  | C7 hubs; C8 arm A | none; 13/14 | none; 16/16 | 3; 12/12, 0 unwitnessed |
  | Cost (MLflow `cost_usd`) | $0.050 | $0.182 | $0.165 |

  Read with care:
  - **On generality the design does what it was for, with no wrong join.** Seven of R93's nine split groups
    are one node each now: Harbour Station, North Station, Utrecht, Rosa Delgado, Aldmoor, Finance Office,
    Hydrology. The evidence is the neighbouring line R75's prompt never showed, e.g. Harbour Station's May
    report "# Harbour Station monthly report, May 2025" joined with the shift notes' "Wed: the Kestrel V-12 at
    Harbour Station (serial KV12-0457) tripped twice ...", and the open day's Kestrel V-12 reached its record
    (the link R99's chooser missed). Mill Lane and town hall stay split.
  - **The price is caution: `unsure` replaces some right joins.** Generality's room B12 (judged INCORRECT as a
    split) and "summer quarterly round" / "quarterly inspection"; furniture's "cushions" / "cushion" (one
    sofa review; an R75 gold pair, so recall 11/13 -> 10/13), "dimmer" / "dimmer function", "storage" /
    "storage mechanism", "upholstery" / "fabric"; held-out's "2019-2022 Insight" / "2019-2022 Honda Insight".
    All were judged right in R99. The new prompt asks for a stated role, place, organisation, date or event,
    which a part named in the singular and the plural in one review does not give. Recorded under "Found
    along the way" for the user to weigh; no rule is changed here.
  - **G15 is a ranking loss** (gold start, `council` now also holds the flood meeting's and the 2026
    budget's council, so its chunks crowd the 2025 budget out of the top 10); G37 and G38 gain at 5 end to end.
  - The prompt is the R100 design's only LLM-facing change; it was written before the measurement and from an
    invented domain (the corpus-language test passes). Gold and verdicts come from one model family.
- **Committed:** `tests/gold/r100/<ds>/` (replay outputs, anchor reports, sheets, verdicts, judged reports) and
  `runs.json`; two tests in `tests/test_anchor_judging.py` check the carried verdicts, that only the 30
  items were judged and no join is wrong, the scores, the three remaining generality splits and the `unsure`
  counts.
- **Gate (results):** 746 passed (742 before), `ruff check` clean. **R100 done 2026-10-06.** Next: R101.

### R101. The mention pass: the retrieval-worthy things the text names or talks about (done 2026-10-07; $0.034)
Mentions exist only as ends of claims (`text/subject_graph.collect_rows`), and the extractor skips what no
fact type fits, so a thing named only in a title or only as denied ("No leaks were found on HP40-2291") has
no node: 2 of 86, 11 of 65 and 15 of 62 target names were unplaced (R90). R101 adds a separate pass that
lists index entries, not phrases: things a question could start from or ask about. Coverage must not turn
the graph into a noisy copy of the text, so the bounds are fixed before measuring (below).
- **Split: two parts, one commit each.** (a) the definition, the gold and its format, written before any
  pass output exists ($0); (b) the code (pass, fallback types, stage, snapshot, fidelity) and at most one
  `dev`/`smoke` run to prove the wiring (asked).
- **Bounds, fixed now, checked in R102 and again in R103:** judged precision of a seeded sample of pass
  mentions >= 0.90 (INCORRECT = outside the definition, a wrong name or a wrong type class); no new C7 hub
  (> 20 % of the corpus); C9 growth reported per dataset with the added mentions per chunk (median, p90);
  concept merges of pass mentions judged (C3, comparative). If precision or the hub rule fails, the prompt and
  checks get at most three `dev` rounds on `samples/`; then the user decides. Recall alone never keeps a pass
  that fails a bound.
- **Part a (done 2026-10-07, $0, no model call):**
  - **The definition**, `tests/gold/r101/rules.md`, one text for the prompt, the code checks, the gold and the
    judge. In: named particulars; kinds the text says something about (an object or a piece of one; a state,
    condition or fault, a property included; an incident or event; an action done to a thing or by it; a
    person or organisation known only by its role); a fault, state or incident of a thing or an action done
    to it written as a verb ("sticks"); each also when absent. Out: descriptive words alone; light or
    reporting verbs; clauses with no thing; quantities, dates and times; the document, or its writer named
    only as such; pronouns; generic words; everyday acts of people that are not a fault, an incident or a work
    done to a thing. Names verbatim, whole words, no leading article; one entry per thing per sentence (two
    names of one thing: the first); class `particular` or `kind`. Examples from an invented observatory and
    ferry line only.
  - **How the definition was settled** (before any pass output existed, so no gold correction after
    output): the first blind golds differed on two points the plan's In list left open, faults written as
    verbs (furniture kept "stick", generality and held-out left out "jammed", "SHOOK") and people known only
    by a role ("dealer", "council"). Both were settled as In for all three datasets; the verb rule then
    over-reached (held-out kinds 46 -> 97 with "contact" x6, "filed" x3), so it was narrowed to faults,
    states, incidents and works done to a thing, with everyday acts of people Out (8), and each gold revised
    once more.
  - **`kg coverage-sample --build BUILD --data D`:** the coverage sampler (validation/sentences.py,
    unchanged) draws from a finished build's corpus rebuilt offline (`audit/inputs.read_corpus` with the
    build's plan and profile), no graph needed; params log the build and the chunker.
  - **Samples:** 40 sentences per dataset, seed 101 (fixed before any sentence was seen), from the r77d
    builds' corpora: `tests/gold/r101/<ds>_sample.json` (populations 504 / 390 / 199 sentences).
  - **Gold:** `tests/gold/r101/<ds>_mentions.json`, written blind by one Opus 5.5 subagent per dataset
    (rules.md and the sample only), lead-reviewed in the session (no change): furniture 75 mentions (13
    particular, 62 kind), held-out 125 (47 / 78), generality 109 (40 / 69). Examples: "No leaks were found"-type
    denials keep their thing; "THE CONTACT STATED THAT THE EXTERIOR REARVIEW MIRRORS SHOOK AND RATTLED." ->
    EXTERIOR REARVIEW MIRRORS, SHOOK, RATTLED (the writer, CONTACT, is Out 5); "## Rating: ★★☆☆☆ (2/5)" -> none.
  - **`validation/mention_gold.py`:** the format (`MentionGold`, `SentenceMentions`, `GoldMention`) and
    `mention_gold_issues` / `load_mention_gold`: every sampled sentence answered once and unchanged, every
    name whole words of its sentence, none twice, none with a leading article.
  - **Tests** (`tests/test_mention_gold.py`, 7): the checks on an invented sample (each rule), a malformed file
    refused, the offline sampler on the audit's invented build (params, the same seed draws the same
    sentences), and the three committed golds against their samples.
  - Gate: 753 passed (746 before), `ruff check` clean.
  - Committed at `6ad9499`.
- **Part b, code (done 2026-10-07, $0):**
  - **`text/schema.py`:** two built-in fallback types, `Particular` (individual class) and `Kind` (concept
    class), handled like `Value`: `identity_of` gives their class, `validate_text_schema` refuses a schema
    that defines one.
  - **`text/mention_pass.py`** (new): `MENTION_PROMPT` (the definition's In and Out, "list only things not
    already listed" with the chunk's known names, the schema's types plus the fallbacks; examples from an
    invented observatory; response `FoundThings{things: [FoundThing{name, type}]}`); `verify_found`, one
    rejection reason per code-visible Out rule (`value_type`, `unknown_type`, `not_in_text`: whole words of
    the chunk or its document's name, the C1 rule; `quantity_or_date`: a bare number, a number with a
    measuring unit or a duration, calendar words and digits; `pronoun`; `function_words`; `too_long`: over 6
    words; `clause`: clause or list punctuation; `already_listed`; `duplicate`), a leading article dropped
    from the stored name; `find_mentions` (parallel calls, a failed call adds nothing and is counted); the
    pure `pass_rows` (one mention per document and normalised name: a claim's or derivation's mention of the
    name is reused and its type wins; only the chunk's new MENTIONS edge is added). The Out rules code cannot
    see (a describing word alone, a reporting verb, an everyday act) are measured by the judge, not by a word
    list.
  - **`text/subject_graph.py`:** `read_mention_rows` (every mention and MENTIONS pair); the pass writes
    through `write_mentions` (MERGE, parameterised).
  - **`MentionPassStage` + `kg mention-pass`:** after `link`, before `resolve` (also in the full pipeline).
    Params: model, thinking, prompt version, fallback types. Metrics: chunks, found, accepted, failed,
    `mention_nodes`, `mentions` (new edges), `reused`, `rejected_<reason>`. Artifacts: the prompt,
    `mentions.jsonl` (accepted findings, which the audit replays), `mentions_rejected.jsonl`. The run guard
    treats `mention-pass` as an LLM command.
  - **Audit:** `audit/snapshot.py` replays a build's `mentions.jsonl` with `pass_rows` after derivation
    (`SnapshotMention.found_by_pass`, `pass_mentions_edges`, `pass_reused`); `audit/fidelity.py` logs
    `mention_pass.mention_nodes`, `.mentions`, `.reused` and no longer counts pass mentions as extracted;
    `audit/checks.py` checks pass mentions' names in their chunks as `pass.mention_in_chunk` (C1);
    `kg audit-relink` copies the pass file into the build it writes. `core/values.py` exposes `UNIT_SYMBOLS`.
  - **Tests:** `tests/test_mention_pass.py` (21): each code-visible Out rule as a reason (14 cases), the pass
    with a scripted LLM (a failed chunk, a duplicate, the prompt's known names and types, never `Value`), the
    rows (one per document and name, the claim's type wins, a reuse counted once), the prompt's
    corpus-language test (no evaluated-corpus word or four-gram), the fallback types, the stage order, the
    snapshot replaying a pass file with its fidelity counts, and the stage on Neo4j (params, metrics,
    artifacts, the written mention). `test_pipeline.py`: the full pipeline answers the pass and lists its
    run; `test_run_guard.py` +1.
  - README: the command, its place in the stage order, the module map. Gate: 775 passed (753 before),
    `ruff check` clean.
  - Committed at `0453a05`.
- **The one `dev` run** (the user's yes, 2026-10-07; at `0453a05`, only `.claude/settings.json` dirty): `kg
  reset`, then `kg --preset dev run samples/dev --goal "Which products have problems, in which parts?"
  --out out/r101_dev` (gemini-3.5-flash-lite), MLflow `kgbuilder-dev`, pass run `3eee36df`, pipeline
  `a3048387`: **$0.034 for the whole pipeline**, of it $0.0077 for the pass. The wiring works against the
  real API: 12 chunks, 12 calls, 0 failed; 98 things found, 93 accepted, 5 refused as already listed (the
  model sometimes lists a known name; code catches it); 78 new mentions, 93 new MENTIONS edges, 9 reaching a
  claim's mention. Seen in `mentions.jsonl`: "drawer rails" and "drawers" typed with the schema's keyed
  types, "@furniture_lover92" and "Seattle" as `Particular`, but also unnamed kinds typed `Particular`
  ("supplier", "garage", "trash day"): a wrong class is INCORRECT under the definition, so R102's judged
  precision (on the quality model) decides whether the prompt needs a `dev` round.
- **A bug the run found, fixed with its failing test first:** validation's "every mention type is in the
  schema" knew `Value` as built in but not the fallback types, and failed on 61 pass mentions. The check now
  counts `Value` and `FALLBACK_TYPES` (`validation/checks/consistency.py`); test
  `test_the_mention_passs_built_in_types_are_schema_types` (Neo4j) failed before ("2 mentions of unknown
  type"). Gate: 776 passed, `ruff check` clean.
- **R101 done 2026-10-07.** The noise and size bounds are checked in R102. Next: R102 (part a, $0, then the
  rebuild, asked per dataset).

### R102. Rebuild with the r77d claims, and evaluate everything (done 2026-10-07; $1.832)
The replays of R94-R100 were not rebuilds: each restated one stage over a snapshot. R102 builds the three
graphs again with every change of R97-R101 in place (the anchor walk is evaluation-side; record candidates,
the adjudicator and the mention pass are build-side), reusing the r77d claims so that the comparison with
r77d is not confounded by a new sample of the extractor (R61-R62).
- **Split, one commit each:** (a) `kg extract --from-build` ($0); (b) the rebuilds (paid, asked per dataset);
  (c) the evaluation ($0, judging only new and changed items); (d) the results table.
- **Part a (done 2026-10-07, $0):** `ReplayExtractStage` (`pipeline/stages.py`, an `ExtractStage` whose run
  is still named `extract`) and `kg extract --from-build BUILD`: reads the build's `triples.jsonl`, refuses a
  build whose `text_schema.json` differs from the run's, verifies every triple again with `extraction.verify`
  against the ingested chunks (a missing chunk or a failed rule is a rejection, logged), and writes the rest
  with `write_subject_graph`. Params: `source`, `triples_hash`, `model`. Metrics: the subject graph's counts
  (as `kg extract`), `replayed`, `rejected`. Artifacts: `triples.jsonl`, `rejected.jsonl`. Tests
  (`tests/test_extract_replay.py`, 2, Neo4j): the invented build's 4 claims written again and a fifth with a
  quote not in its chunk rejected; a build of another schema refused with nothing written. README. Gate:
  778 passed (776 before), `ruff check` clean.
  Committed at `36c0506`.
- **Part b: the rebuilds (done 2026-10-07; $1.832).** The user agreed per dataset (furniture est. $0.35-0.5,
  held-out $0.45-0.65, generality $0.3-0.4). At `36c0506` with only `.claude/settings.json` dirty, each into
  `out/r102_<ds>` with r77d's `plan.json`, `text_schema.json` and `profile.json` pinned (`kg build` rewrites the
  profile's sampled example values, DuckDB sampling, so the r77d profile was copied back before
  `ingest-text`): `kg reset`, `build`, `ingest-text`, `extract --from-build out/r77d_<ds>`, `link`,
  `mention-pass`, `resolve`, `attach`, presets `quality` / `heldout` / `generality_gemini`.

  | | furniture | held-out | generality |
  |---|---|---|---|
  | chunks; claims replayed (rejected); facts | 70; 514 (0); 514 | 81; 532 (0); 531 | 32; 212 (0); 212 |
  | everything up to `link` equal to r77d's logged counts | yes | yes | yes |
  | pass: found / accepted / refused | 481 / 474 / 7 (too long) | 705 / 615 / 90 (60 date or number, 12 not in text, 12 clause, 6 too long) | 259 / 259 / 0 |
  | pass: new mentions (claims' mentions) / reused | 394 (607) / 26 | 563 (491) / 1 | 259 (255) / 0 |
  | resolve: mentions to records / individuals / concepts | 64 / 244 / 693 | 77 / 400 / 606 | 47 / 211 / 256 |
  | resolve: individual pairs joined / apart / unsure | 288 / 91 / 44 | 255 / 1292 / 31 | 119 / 62 / 78 |
  | cost: pass + resolve (MLflow) | $0.076 + $0.336 = **$0.411** | $0.115 + $1.136 = **$1.251** | $0.044 + $0.126 = **$0.170** |
  | MLflow: pass, resolve | `209c28ad`, `98e4533f` | `48c82bce`, `6d2ad084` | `89dae7b1`, `162e17c2` |

  - **Held-out cost twice its estimate**, all in `resolve` (1802 calls). 1057 of its decisions are between
    pass mentions typed `Recall` (recall numbers such as "15V-246", "16V-643"): spelling nominates every pair
    of them and the adjudicator answered 668 apart. The estimate counted mentions, not pairs. The user was told
    before generality's resolve and agreed to it with a counted estimate (an offline count of the pairs it
    would ask, $0.30-0.45; it cost $0.126 with 157 cache hits). Recorded under "Found along the way".
  - **Committed:** `tests/gold/r102/<ds>_logged.json` (counts and usage copied from the six stage runs of each
    rebuild) and `runs.json` (commands, run ids, costs, approvals). A test checks that ingest, extract and
    link counts equal r77d's, the pass counts are there, and the cost matches.
- **Part c: the evaluation (done 2026-10-07, $0: no API call; offline stages, judging in the session).**
  - **`kg mention-eval BUILD --dataset N --data D --logged L --gold-dir tests/gold/r101 [--verdicts V]`**
    (`validation/mention_eval.py`, `pipeline/mention_stages.py`): rebuilds a build offline behind the C0 gate
    and scores its mentions against R101's gold. A gold mention is a `hit` when a mention of the sentence's
    chunk has its name after `norm`, a `candidate` when only a near name (one holds the other as whole words)
    stands there, the judge deciding whether it names the same thing (the judged paraphrase mapping), else a
    `miss`. Precision: a seeded sample of 60 pass mentions (seed 102, fixed before any verdict), each with
    its chunk; the class shown is the one its type gives (a concept type: kind; any other: particular).
    Params: build, gold, sample and verdicts with hashes, sample size and seed, chunker. Metrics: recall
    exact and mapped (with Wilson intervals), exact recall by class, precision, pass mentions, pass mentions
    per chunk (median, p90). Artifacts: `mention_sheet.json`, `mention_report.json`. Tests
    (`tests/test_mention_eval.py`, 10). Committed with the sheets at `0bd6b2b`.
  - **Sheets** (committed at `0bd6b2b`, before any verdict): C3, C4 and C6 sheets of the three rebuilds with
    their code sides, both arms' anchor reports, fidelity and code checks; the mention sheets of r77d and of
    the rebuild per dataset.
  - **Judging** (Claude Opus 5.5, `claude-opus-5-5`; rules `tests/gold/r93/rules/c3.md`, `c4.md`, `c6.md`
    unchanged, and `tests/gold/r102/mention_judge_rules.md`, the R101 definition plus the two questions).
    C3/C4/C6: items byte-identical to R100's carry R100's verdicts; 352 are new or changed (furniture C3 76,
    C4 21, C6 12; held-out C3 85, C6 35; generality C3 60, C4 3, C6 60), judged in 26 blind batches of at
    most 60k characters (each subagent saw only the rules and its batch): 303 VALID, 36 VALID_ALTERNATIVE,
    9 INCORRECT, 4 AMBIGUOUS. Mentions: 6 blind batches (the recall candidates of both sheets, one verdict
    per distinct candidate list, and the precision sample). The lead reviewed every INCORRECT, AMBIGUOUS and
    UNJUDGEABLE verdict and the seeded 10 % of VALID ones (68 anchor items, 25 mention items): **no change**.
    Two checks in review: the gold (written blind in R101) leaves out titles of named people ("Councillor
    Priya Nandakumar" gives "Priya Nandakumar") and lists "recalling" as a kind, as the precision judges did.
  - **`kg anchor-judged`** on the three rebuilds; `kg mention-eval --verdicts` on the six builds.
    Committed: the verdict files, `anchor_judged.json`, `mentions_<build>_report.json`. Tests: the R102
    verdicts answer their sheets, carry R100's for byte-identical items, and score as reported
    (`tests/test_anchor_judging.py`, `tests/test_mention_eval.py`). Gate: 794 passed, `ruff check` clean.
- **Part d: results** (judge: Claude Opus 5.5; before = R100's replay of r77d, after = the R102 rebuild; C5
  pairing against R92's vector rankings, McNemar; n per row):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | C0 fidelity; C1 provenance | pass; 1.0 | pass; 1.0 | pass; 1.0 |
  | **Mention recall of the R101 gold, exact** (r77d -> R102) | 23/75 -> **57/75** | 32/125 -> **94/125** | 37/109 -> **80/109** |
  | Mention recall with the judged mapping | 39/75 -> **70/75** | 45/125 -> **111/125** | 49/109 -> **97/109** |
  | Exact recall, particular / kind | 3/13 -> 13/13 / 20/62 -> 44/62 | 13/47 -> 35/47 / 19/78 -> 59/78 | 27/40 -> 35/40 / 10/69 -> 45/69 |
  | **Pass precision (judged, 60 sampled; AMBIGUOUS left out)** | **45/57 = 0.789** [0.667, 0.875] | 51/55 = 0.927 [0.827, 0.971] | **41/58 = 0.707** [0.580, 0.808] |
  | Pass mentions; per chunk median / p90 | 394; 6 / 9 | 563; 7 / 12 | 259; 8 / 13 |
  | C9 nodes / edges per chunk, arm A | 17.6 -> 26.2 / 25.8 -> 38.2 | 13.2 -> 23.5 / 17.3 -> 31.9 | 14.6 -> 28.2 / 17.1 -> 33.2 |
  | **C7 hubs** (> 20 % of the corpus) | 0 -> 0 | 0 -> **1** (FORD, 20 of 81 chunks) | 3 -> **4** (+ North Station, 10 of 32) |
  | C2 target found at rank 1; targets with no node | 0.837 -> 0.860; 2 -> 0 | 0.708 -> 0.800; 11 -> 0 | 0.661 -> 0.855; 15 -> 1 |
  | C3 individual merges judged (n); wrong | 2 -> 26; 0 | 9 -> 42; 0 | 30 -> 34; 0 |
  | C3 concept merges judged (n); wrong | 77 -> 114; 2 (R100's) | 41 -> 82; **1** ("CONTACT") | 8 -> 48; **1** ("trip") |
  | C3 split groups that are one thing | 0 of 12 -> **2 of 17** | 0 of 3 -> **1 of 6** | 3 of 3 -> **7 of 9** |
  | C3 record merges wrong; R75 pairs precision / apart / recall | 0; 1.0 / 1.0 / 10/13 -> 11/14 | 1 (R93's); 1.0 / 1.0 / 5/7 | 0; 1.0 / 1.0 / 13/13 |
  | C4 precision | 50/50 -> 64/64 | 76/77 -> 76/77 | 44/44 -> 47/47 |
  | C6 arm A / B (record + individual starts) | 109/109 / 112/112 | 85/85 / 85/85 | 101/101 / 101/101 |
  | C8 arm A connections; unwitnessed hops | 13/14 -> 14/14; 0 | 16/16; 0 | 12/12; 0 |
  | C5 gold start complete@10 (A vs vector, p) | 29 vs 26 (0.375 -> 0.453) | 24 -> 25 vs 26 (0.688 -> 1.0) | 31 -> **36** vs 37 (0.070 -> 1.0) |
  | C5 end to end complete@5 (A vs vector, p) | 17 vs 21 (0.289) | 19 -> 21 vs 26 (0.065 -> 0.180) | 24 -> 26 vs 34 (0.006 -> 0.021) |
  | C5 end to end complete@10 (A vs vector, p) | 22 -> 23 vs 26 (0.344 -> 0.549) | 21 -> 23 vs 26 (0.180 -> 0.453) | 31 -> **34** vs 37 (0.070 -> 0.375) |
  | Cost of the rebuild (MLflow, part b) | $0.411 | $1.251 | $0.170 |

  Against r77d as built (R90), generality end to end complete@10 is 22 -> 34 of 38 (vector 37; p < 0.001 ->
  0.375).

  **Construction against query-time failures.** A question arm A leaves incomplete end to end at 10 is a
  construction failure when a target has no node or a gold chunk cannot be reached from the gold start (the
  graph lacks the path), and a query-time failure when the path exists but the walk ranks the chunk below 10,
  or the name lookup starts elsewhere while the gold start is complete:

  | Incomplete @10, arm A (construction / query-time) | furniture (31) | held-out (28) | generality (38) |
  |---|---|---|---|
  | r77d as built (R90) | 2 / 7 | 3 / 4 | 14 / 2 |
  | R100 (replay with R97-R100) | 2 / 7 | 3 / 4 | 6 / 1 |
  | **R102 (rebuild with R97-R101)** | **0 / 8** | **0 / 5** | **0 / 4** |
  | R102 query-time: ranked below 10 from the gold start | F06, F32 | H19, H29, H31 | G25, G36 |
  | R102 query-time: the name lookup starts elsewhere | F08, F16, F22, F25, F26, F27 | H08, H28 | G12, G23 |

  Construction failures judged elsewhere, which leave every question complete or only rank it lower: the 7
  new wrong C3 splits and 2 new wrong concept merges (all involving pass mentions, below), R100's three
  generality splits and two furniture concept merges, held-out's R93 record link (H51), and the precision
  errors of the pass.

  Read with care:
  - **What R102 shows.** With every R97-R101 change in place and the r77d claims, the anchor graph reaches
    every gold chunk from the gold start on all three datasets (C5 gold start reached 1.0), places every
    target but one, and finds no remaining construction failure among the incomplete questions. The C5 gap to
    vector retrieval is no longer significant anywhere at 10 (generality end to end p 0.375; at 5 it still is,
    26 vs 34, p 0.021). What is left is on the query side: ranking within the walk and the start chosen by
    name lookup (Found along the way, R96's tie-break).
  - **Two R101 bounds fail.** (1) Pass precision >= 0.90 fails on furniture (0.789) and generality (0.707);
    held-out passes (0.927). The judged errors: furniture 12 = 9 mentions typed with a keyed type, so shown as
    particular, that name a piece or a kind ("back rest", "seams", "covers" as `Component`; "chair",
    "furniture" as `Product`), 1 kind typed `Particular` ("home office"), 2 Out words ("short", "space");
    generality 17 = 7 common nouns given an individual type ("street", "café", "bakery", "control room" as
    `Place`; "vote", "resurfacing" as `Event`; "contractor" as `Organization`), 6 titles of named people
    ("Councillor" x2, "Mayor", "technician", "structural inspector", "chair"), 1 kind typed with a keyed type
    ("standby pump" as `Pump`), "winter" and "Sir" as particulars, "surface"; held-out 4 = "recalling" typed
    `Recall` x2, "START", "DRIVING". (2) "No new C7 hub" fails on held-out (FORD: 10 pass mentions, one per
    Ford document, all judged one company) and on generality (North Station: 6 -> 10 of 32 chunks, its merge
    judged right). Both hubs are named particulars the text really names that often, not generic words; the
    bound counts them anyway. R101's rule applies: at most three `dev` rounds on the prompt and checks, then
    the user decides (Found along the way).
  - **The class of a keyed-type mention** is an artefact of the sheet, found in review: the sheet shows every
    mention of a keyed type as particular, while the definition makes a piece ("back rest") a kind, even
    though the graph rightly links it to the product's record. Read on the name only, as a check made after
    the verdicts and not as the score: furniture 54/57, generality 42/58 (Found along the way).
  - **The new wrong splits are the pass's same-named individuals** ("Austin", "Seattle", "Toyota",
    "Riverton", "children's section", "Harbour Street", a second "Tomasz Wren"): one document each, nominated
    and answered apart or unsure by R100's adjudicator, which asks for a stated role, place or event. Both
    wrong concept merges join a pass mention with another sense of the same word ("THE CONTACT", the
    complainant, with "Contact with the ECM bracket"; a conference "trip" with a pump that "tripped").
  - Gold and verdicts come from one model family (Claude); the gold was written blind, before any pass
    output (R101).
- **R102 done 2026-10-07** ($1.832, all in part b). Next: the user's decision on the failed bounds (R101's
  rule: up to three `dev` rounds on the pass), then R103 (paid, asked).

### R104. The mention pass states each thing's class apart from its type (done 2026-10-07; $0.039 dev run)
Inserted before R103 (the user's decision on R102's failed bounds, 2026-10-07: fix the pass first; a narrow
fix, no change to resolution, hubs or concept merging). R102's 33 judged pass errors, traced to their cause:
20 are a wrong class that came from the type (the prompt asked for a type only and showed no class; "Give it
the type below that fits it" put type fit before being named, so "café", "street" and "control room" became
`Place` individuals, "vote" an `Event`, "back rest" a keyed `Component` shown as particular); 6 are titles of
a listed person ("Councillor" before "Priya Nandakumar", who was already on the prompt's "already listed"
line in 5 of the 6); 7 are Out rules broken, partly where two rules clash ("START VEHICLE" is both "an action
done to a thing", In, and an everyday act, Out 8; "THE CONTACT" read as a role, not as the writer).
- **Scope.** `text/mention_pass.py`: the response is `FoundThing{name, mention_class, type}`, the class
  (`particular` | `kind`, a `Literal` the response schema enforces) asked as its own answer before the type,
  the type one of the schema's or `none`; the prompt explains the class apart from the types (how the text
  refers to the thing: its own name or identifier, else kind, also for a common noun meaning one thing), no
  longer lists the fallback types, and clarifies the rules that read two ways (below). Code decides the
  stored type (`filed_type`): the proposed type when its identity class can hold the stated class (keyed:
  both, since its records also stand for the pieces a text names by a common noun, "back rest" -> the
  product's part record; individual: particular only; concept: kind only), else the class's fallback
  (`Particular`, `Kind`). A proposed fallback type is now `unknown_type` (the fallbacks are code's to give).
  `mentions.jsonl` keeps both answers (`mention_class`, `proposed_type`) next to the stored `type`; a pass
  file written before R104 still parses (both None). `MentionClass` moved to `text/schema.py` (the gold
  format and the sheet import it). Not changed: resolution, hubs, concept merging, the extractor.
- **The rules, clarified** (prompt and `tests/gold/r101/rules.md` alike; examples invented). Each reads the
  way the R101 gold, written blind, already reads it, so **no gold entry changes** and there is no gold
  correction:
  - Out 9, a title, role or common noun next to a name or in apposition with it: the gold gives "Councillor
    Priya Nandakumar" -> Priya Nandakumar (note: "Title 'Councillor' is part of how the named person is
    called"), "The council's structural inspector, Daniel Okafor" -> Daniel Okafor, "The station's only
    pump, KV12-0457" -> KV12-0457 ("the id is the entry").
  - In 2/3 against Out 8, a work done to a thing (makes, fits, repairs, cleans, tests, replaces or withdraws
    it) against ordinary use: the gold keeps "install", "wash", "tested", "replace", "recalled" and leaves
    out "VALET PARK", "HIGHWAY DRIVING", "FILLING my tank", "setting down my laptop".
  - Out 5, the writer or reader referred to only as such, forms of address: the gold leaves out "THE
    CONTACT" (the complaint's writer), keeps "architect" (a writer called by a profession) and keeps the name
    in "Dear Riverton Water".
  - Out 4 seasons ("winter" was a pass `Particular`); Out 7 generic words also as subjects or objects (the
    gold calls "issues", "number", "object" generic).
  - The class is how the text refers to the thing: the gold gives "street", "café" and "bakery" the class
    kind ("'street' refers back to Harbour Street but is not named here, so kind").
- **The sheet** (`pipeline/mention_stages.py`): a precision item shows the class the pass stated
  (`SnapshotMention.stated_class`, carried from the pass file by `pass_rows` -> the snapshot); for a pass file
  without classes (R102's builds) the class is still read off the type, so R102's sheets rebuild as judged.
  This closes "The mention sheet's class for keyed types" below, decided before any sheet of a new pass
  exists.
- **Stage metrics added:** `particular` (findings stated particular) and `retyped` (accepted findings filed
  under their class's fallback because the proposed type cannot hold the class). Params unchanged; the
  `prompt_version` changes with the prompt.
- **Tests:** `tests/test_mention_pass.py`: the Out-rule reasons with the new answers (a proposed fallback type
  is `unknown_type`), `filed_type` for every identity class and both classes (8 cases), the pass with a
  scripted LLM (a kind typed with an individual type is filed `Kind`; the prompt no longer lists the
  fallbacks and offers `none`), `pass_rows` keeping the first finding's stated class, the snapshot carrying
  it, the stage on Neo4j (`particular`, `retyped`, the stored type and both answers in the pass file), and
  the prompt's corpus-language test on the new prompt; `tests/test_mention_eval.py`: a precision item shows a
  keyed-type piece as the kind the pass stated, and R102's class for a pre-R104 pass file. Gate: 805 passed
  (794 before), `ruff check` clean.
- Committed at `8e84baa`.
- **The one `dev` run** (the user's yes, 2026-10-07; at `8e84baa`, only `.claude/settings.json` dirty): `kg
  reset`, then `kg --preset dev run samples/dev --goal "Which products have problems, in which parts?" --out
  out/r104_dev` (gemini-3.5-flash-lite, R101's command), MLflow `kgbuilder-dev`, pipeline `76b67ec5`, pass
  `361e0f7a`: **$0.039 for the whole pipeline** (54,196 prompt / 9,136 completion / 0 thinking tokens), of it
  $0.0103 for the pass (12 calls, 11,960 / 2,671 tokens; R101's pass: $0.0077, 8,500 prompt tokens: the
  prompt is longer). All validation checks pass. Pass: 96 found, 93 accepted, 3 refused (clause, too long,
  already listed), 0 failed calls; 86 new mentions, 1 reused; `particular` 23, `retyped` 0.
  - **What it shows (wiring and behaviour on the cheap model; dev output is not judged, so no precision):**
    every finding carries both answers; the 23 stated particulars are all names (user handles, cities,
    "Stockholm Chair"); R101's dev run typed 5 kinds `Particular` ("instructions", "Customer service",
    "supplier", "garage", "trash day"), all 5 now stated kind and stored `Kind`. `retyped` is 0 because this
    schema (`Product`, `Assembly`, `Part` keyed; `Issue` concept) has no individual type and no particular was
    given a concept type. Keyed kinds keep their type as designed: "back rest", "cover", "screws" as `Part`;
    also "chair", "dresser", "sofa" as `Product` (the open item below).
  - **Still seen** (Flash-Lite, unjudged): Out words "issues" (listed in the prompt's own Out example),
    "space", "firm", "price"; "purchase" typed `Product` (buying, Out 8); the act "Assembly" typed with the
    keyed type of the same name.
  - Not comparable claim for claim with R101's run: the plan and schema were proposed again (no cache hits;
    the profile's sampled values change, Found along the way), so the claims, and the names the pass was
    told were listed, differ.
- **Not measured yet: judged precision.** The bound (>= 0.90 on all three datasets) needs pass output of the
  quality model on the full datasets, judged: R103's rebuild, or a pass-only run on R102's three builds
  (isolates the prompt change from re-extraction; needs new code to run the pass offline on a snapshot's
  pre-pass state), each asked first.
- **R104 done 2026-10-07** (one `dev` round of R101's three). Next: the user's choice of how to measure it,
  then R103.

### R105. The R104 pass measured on R102's builds: a pass-only replay, judged (done 2026-10-07; $0.318)
The user chose to measure R104 by running only the pass on R102's three builds (2026-10-07, "option 1",
about $0.3): same claims, same derived mentions, same text schema, so a difference in precision or recall
is the pass's, not a new sample of the extractor (R103 re-extracts and would confound the two). Resolution,
hubs and concept merging are not measured (they need a resolve, out of this round's scope).
- **Split, one commit each:** (a) the offline replay and the scoring of replayed findings ($0); (b) the three
  replays with the quality model (paid); (c) sheets, blind judging, results ($0).
- **Part a (done 2026-10-07, $0):**
  - `audit/snapshot.py`: `build_snapshot(..., findings=None)`: given findings replace the build's pass file
    (an empty list: the graph as the pass found it). Identity stays the build's `resolve.json`, whose lines
    for mentions absent from the snapshot are skipped as before. `text/mention_pass.read_findings` reads a
    pass file (the snapshot's inline reader moved there).
  - `pipeline/stages.py`: `MentionPassStage` is a Template Method (`read_inputs` / `write_rows`, a frozen
    `PassInputs`), behaviour unchanged for `kg mention-pass`.
  - `pipeline/mention_stages.py`: `ReplayMentionPassStage` (`kg mention-pass --from-build BUILD --data D
    --logged L`): the build's graph rebuilt offline behind the C0 gate, then rebuilt again without its pass;
    the prompt's known names are the claims' and derivation's mentions of each chunk, the schema the build's;
    nothing is written to a graph, the findings files go to `--out`; the run is `mention_pass` with the live
    params plus build, logged counts and chunker. `kg mention-eval BUILD --pass-file FILE`: the C0 gate on the
    build as built, then the sheet and scores on the build with FILE's findings in place of its own pass
    (params `pass_file`, `pass_file_hash`). `_build_params` and `_gated_snapshot` are shared by both.
  - **Judge rules** `tests/gold/r105/mention_judge_rules.md`, written before any replay output: R102's rules
    with R104's definition (Out 4, 5, 7, 8, 9, In 2 and 3, the class sentence) and one sentence on the
    precision item: the type is the graph's category and not judged (R102's question never asked about it).
  - Tests: `tests/test_mention_pass.py`: findings in place of the pass file (none, other), the replay on the
    audit's invented build with a scripted LLM (known names exactly the claims' and derivation's, not the
    old pass's "crack"; params; counts; files), and the replay refusing a build whose logged counts its
    snapshot does not meet; `tests/test_mention_eval.py`: `--pass-file` scores the replayed findings, not the
    build's own, and the gold "hinge" becomes a hit. README. Gate: 808 passed (805 before), `ruff check`
    clean.
- **Part b, estimate (counted before asking):** R102's passes cost $0.234 (furniture $0.076, held-out $0.115,
  generality $0.044; 0 thinking tokens). The R104 prompt is 933 characters longer (about 230 tokens on each
  of 183 calls: +$0.032) and each finding carries a class (about 10 tokens on each of about 1,450: +$0.054):
  **about $0.32** in all, likely $0.27-0.40. Commands, each with its preset (R102's): `kg --preset quality
  mention-pass --from-build out/r102_furniture --data data --logged tests/gold/r102/furniture_logged.json
  --out out/r105_furniture`; `heldout` with `heldout/nhtsa/data`; `generality_gemini` with
  `tests/fixtures/generality`.
- **Part b: the replays (done 2026-10-07; $0.318).** At `729f793` (only `.claude/settings.json` dirty),
  prompt `d2a8f2cd9a6f`, gemini-3.8-flash, thinking low, every call a cache miss, 0 failed; each passed the
  C0 gate on its R102 build.

  | | furniture | held-out | generality |
  |---|---|---|---|
  | chunks (calls); found / accepted / refused | 70; 453 / 445 / 8 (too long) | 81; 656 / 566 / 90 (60 date or number, 12 not in text, 12 clause, 6 too long) | 32; 255 / 255 / 0 |
  | stated particular; retyped by code | 143; 0 | 167; 0 | 32; **87** |
  | new mentions / reused (R102: 394 / 26, 563 / 1, 259 / 0) | 367 / 24 | 528 / 1 | 255 / 0 |
  | cost (R102's pass) | $0.105 ($0.076) | $0.153 ($0.115) | $0.060 ($0.044) |
  | MLflow run | `0dfe0b92` | `70e8d988` | `535f774f` |

  - Generality's 87 retyped findings are the class doing its job: stated kind and typed with an individual
    type, stored `Kind`: "bakery", "street", "library" (`Place`), "vote", "resurfacing", "meeting" (`Event`),
    "council", "committee" (`Organization`); its stated particulars are names ("Brackwater fen", "Harbour
    Street", "Priya Nandakumar", "KV12-0457"). Furniture and held-out have no individual type, so code never
    had to retype there. Held-out's refusals equal R102's in count by reason but are other findings.
  - Committed: `tests/gold/r105/runs.json` (commands, runs, costs, counts, approval) and each dataset's
    `pass_findings.jsonl` / `pass_rejected.jsonl` (copies of the out/ files, so the sheets rebuild without
    out/). Test `test_r105_replays_committed_the_findings_their_runs_logged`.
- **Part c: sheets and judging (done 2026-10-07, $0: no API call).** `kg mention-eval out/r102_<ds> ...
  --pass-file tests/gold/r105/<ds>/pass_findings.jsonl` (C0 passed on each R102 build): sheets committed at
  `103b145` before any verdict. Judge Claude Opus 5.5 (`claude-opus-5-5`), rules
  `tests/gold/r105/mention_judge_rules.md`. Recall candidates byte-identical to R102's carry R102's verdicts
  (13 / 15 / 12); the 9 other candidates and all 180 precision items were judged in 3 blind batches (one per
  dataset, each judge saw only the rules and its batch): 162 VALID, 26 INCORRECT, 1 AMBIGUOUS. The lead
  reviewed every INCORRECT and AMBIGUOUS verdict and the seeded 10 % of VALID ones (50 items, carried ones
  included): **no change**. Committed: `mentions_r105_verdicts.json`, `mentions_r105_report.json` per
  dataset; test `test_mention_verdicts_answer_their_sheets_and_score_as_reported` (R102's, now with R105).
- **Part d: results** (judge: Claude Opus 5.5; R102 = the R101 pass on these builds, R105 = the R104 pass
  replayed on the same builds; precision on a seeded sample of 60 pass mentions, AMBIGUOUS left out; Wilson
  intervals; the two samples are of different mentions, so precision is compared by Fisher's exact test):

  | | furniture | held-out | generality |
  |---|---|---|---|
  | **Pass precision, judged** (R102 -> R105) | 45/57 = 0.789 -> **53/60 = 0.883** [0.778, 0.942] | 51/55 = 0.927 -> **51/59 = 0.864** [0.755, 0.930] | 41/58 = 0.707 -> **53/60 = 0.883** [0.778, 0.942] |
  | Fisher p (R102 vs R105) | 0.213 | 0.365 | **0.022** |
  | Mention recall of the R101 gold, exact | 57/75 -> 51/75 | 94/125 -> 98/125 | 80/109 -> 85/109 |
  | Recall with the judged mapping | 70/75 -> **66/75** | 111/125 -> 112/125 | 97/109 -> 99/109 |
  | Exact recall, particular / kind | 13/13 -> 12/13 / 44/62 -> 39/62 | 35/47 -> 34/47 / 59/78 -> 64/78 | 35/40 -> 35/40 / 45/69 -> 50/69 |
  | Pass mentions (new nodes); stated particular | 394 -> 367; 143 | 563 -> 528; 167 | 259 -> 255; 32 |
  | Cost of the pass | $0.076 -> $0.105 | $0.115 -> $0.153 | $0.044 -> $0.060 |

  Read with care:
  - **The groups R104 targeted are gone from the whole output, not only from the samples** (names only,
    unjudged): titles of named people ("Councillor", "Mayor", "technician", "structural inspector", "chair",
    "site engineer") 16 -> 0 in generality; "Sir" 1 -> 0, "winter" 1 -> 0; "DRIVING", "START" 4 -> 0 and
    "THE CONTACT" 5 -> 3 in held-out; "café", "street", "bakery", "control room", "vote", "resurfacing"
    are stated kinds stored `Kind` (were `Place` / `Event` individuals); furniture's keyed parts ("back
    rest", "seams", "covers") are stated kinds and judged VALID. Among R105's 22 INCORRECT precision
    verdicts, R102's groups as they were (a common noun made a named individual through its type, a title of
    a named person, a form of address, a season, everyday use) do not recur; two relatives do: a class the
    model itself stated wrong ("March inspection" as particular) and an identifier kept with its common noun
    ("Core AB-19", "Core AB-20", the Out 9 case of "ferry T-4471").
  - **The bound (>= 0.90) is still missed on all three**, though every interval holds 0.90 and only
    generality's change is beyond chance at n = 60. The 22 INCORRECT precision verdicts are new kinds, one or
    two each: generic words or stand-ins ("stuff", "unit", "FAILURE", "safety risk"), fragments of a longer
    name ("SYSTEM" of "FRONT CAMERA SYSTEM", "paper" of "standard paper sizes"), an idiom ("out of the box"),
    the scraped source URL's id ("B0BQJWJWJW", x2), an evaluation ("centerpiece"), a light verb
    ("APPEARED"), a time ("night shift"), ordinary operation ("switched to duty", "pumped"), a degree word
    in the span ("slow drip"), a month-described common noun as particular ("March inspection"), an
    identifier kept with its common noun ("Core AB-19", "Core AB-20": Out 9 says the id alone).
  - **Two held-out pairs follow R104's wording where the R101 gold reads otherwise** (a check after the
    verdicts, not the score): "vehicles" after model names (x2: Out 9 as worded excludes it; the gold keeps
    "Rogue ... vehicles" -> vehicles), and "Ford customer service" / "Toyota customer service" as particular
    (R104's class sentence makes them kinds; the gold lists them as particulars). Read the gold's way,
    held-out would be 55/59 = 0.932 (Found along the way).
  - **Recall:** furniture loses 4 gold mentions the R101 pass listed ("support", "comfort", "online
    photos", "@familyfirst" in one chunk); held-out loses the manufacturer lines ("Nissan North America,
    Inc." x2, "Subaru of America") and gains "VEHICLE" x5; generality loses "2025 works budget", "slipped",
    "reopened", "ran" and gains "site", "entry", "Seminar", "closed".
  - Not measured here (they need resolve): hubs (C7: held-out's Ford mentions 20 -> 12 pass mentions),
    concept merges and splits of pass mentions.
  - Gold and verdicts come from one model family (Claude); the gold was written blind in R101, the judge
    rules before any replay output.
- **R105 done 2026-10-07** ($0.318, part b). Next: the user's decision (the bound is still missed, within its
  intervals); options and the open definition questions are under Found along the way, then R103.

### R106. Two R104 wordings brought back to the gold (done 2026-10-07; $0, no run)
The user's choice after R105 (2026-10-07, "option 1"): fix the two definition wordings R105 showed reading
otherwise than the R101 gold, then R103 (asked with its own estimate). Wording only; no code path changes.
- **Out 9** now applies only when the noun names that very thing: "a title, role or common noun written next
  to a name or in apposition with it when it names that very thing ... A noun for the many things of a named
  model or class names other things and stays ('the Skylark 30 ferries': Skylark 30, ferries)". The gold
  keeps "vehicles" after model names ("Rogue and 2017-2022 Rogue Sport vehicles" -> Rogue, Rogue Sport,
  vehicles) and drops the noun before an identifier ("Core BF-1" -> BF-1; "The station's only pump,
  KV12-0457" -> KV12-0457); both still hold.
- **The class of a named organisation's unit:** "particular ... also a unit of a named organisation called
  with that name ('Lakeside Ferries ticket office')", as the gold reads "Honda / Subaru / Toyota / Ford
  customer service" (all particular); "the ticket office" alone stays a kind (furniture's gold "customer
  service" is a kind). Chosen over a gold correction because the gold was written blind, before any pass
  output; changing the definition to it is the direction that cannot be bent toward the output.
- Changed in `text/mention_pass.py` (prompt `56ebdf0b5b51`, the intent comment with the reason no code check
  sees either rule) and `tests/gold/r101/rules.md` (Out 9, the class, a revision note); no gold entry.
  Gate: 814 passed, `ruff check` clean; the corpus four-gram guard passes on both.
- **No run:** the wiring is unchanged and was exercised by R104's dev run and R105's replays; R103 measures
  the wording on its own output. Closes the first R105 item under Found along the way.
- **R103's estimate, counted now** (for the user's yes): R103 re-extracts with r77d's settings (two passes,
  gemini-3.8-flash, thinking low; r77d's extraction cost $0.417 / $0.607 / $0.218 = $1.243), then the pass
  (R105: $0.318) and resolve (R102: $0.336 / $1.136 / $0.126 = $1.598, held-out's mostly recall-number
  pairs nominated by spelling): **about $3.2, likely $2.8-3.8**, above the plan's $1.9-2.5. The "identifiers
  nominate pairs by spelling" item below would save about $1.0 of held-out's resolve, but it changes
  resolution.
- **R106 done 2026-10-07.** Next: R103, asked with this estimate.

### R103. Rebuild with re-extracted claims, and evaluate everything (done 2026-10-07; $2.374)
The second rebuild of the R97-R103 plan, end to end: the claims extracted again with today's extraction
prompt (R81, R82 since r77d) and every change of R97-R106 in place, so the graph is the one the pipeline now
builds. R102 reused the r77d claims to isolate R97-R101; R103 shows the whole pipeline. The user agreed on
2026-10-07 ("option A") at the counted estimate of about $3.2 (likely $2.8-3.8, R106).
- **Pinned as in R102:** r77d's `plan.json`, `text_schema.json` and `profile.json` (copied back after `kg
  build`, which rewrites the profile's sampled values), so the schema is not a new sample of the proposer;
  extraction with r77d's settings (two passes, gemini-3.8-flash, thinking low; `EXTRACT_PASSES=2`).
- **Split, one commit each:** (a) the rebuilds (paid): `kg reset`, `build`, `ingest-text`, `extract`,
  `link`, `mention-pass`, `resolve`, `attach` per dataset into `out/r103_<ds>`, presets `quality`,
  `heldout`, `generality_gemini`; logged counts and usage copied from the stage runs into
  `tests/gold/r103/<ds>_logged.json`, the runs index `runs.json`. (b) the evaluation ($0 but the vector
  arm's embeddings, a fraction of a cent): `kg audit-snapshot` (C0, C1, code checks), `kg anchor-eval`
  (both arms), `kg anchor-compare` (arm C), `kg anchor-sheets` (C3, C4, C6), `kg mention-eval` (recall of
  the R101 gold, the pass's precision sample); sheets committed before any verdict; blind Opus 5.5 judging
  (items byte-identical to R102's carry its verdicts; mention items judged by
  `tests/gold/r103/mention_judge_rules.md`, R105's rules with R106's two wordings, written before any R103
  output), lead review, `kg anchor-judged`, `kg mention-eval --verdicts`. (c) the results against R102 and
  r77d, with R101's bounds (pass precision >= 0.90, no new hub) and the plan's regression checks.
- **Part a: the rebuilds (done 2026-10-07; $2.374, below the $2.8-3.8 estimate).** At `6d7ab17` (only
  `.claude/settings.json` dirty), extraction prompt as at HEAD, mention prompt `56ebdf0b5b51` (R106):

  | | furniture | held-out | generality |
  |---|---|---|---|
  | chunks; claims stored (rejected) (R102: the r77d claims) | 70; 531 (28) (514) | 81; 530 (9) (531) | 32; 216 (6) (212) |
  | derived claims; claims' mentions | 154; 621 | 135; 489 | 0; 266 |
  | pass: new mentions / edges / reused (R102) | 394 / 479 / 20 (394 / 474 / 26) | 551 / 594 / 2 (563 / 615 / 1) | 258 / 258 / 0 (259 / 259 / 0) |
  | resolve: records / individuals / concepts (R102) | 63 / 240 / 712 (64 / 244 / 693) | 83 / 428 / 558 (77 / 400 / 606) | 50 / **192** / **282** (47 / 211 / 256) |
  | attach: claims attached / total; attachments | 685 / 685; 1277 | 665 / 665; 3831 | 206 / 216; 476 |
  | cost: extract + pass + resolve (MLflow) | $0.425 + $0.115 + $0.099 = **$0.639** | $0.607 + $0.154 + $0.622 = **$1.383** | $0.223 + $0.060 + $0.068 = **$0.352** |
  | MLflow: extract, pass, resolve | `7d30cca9`, `2f56b3e1`, `622190d4` | `15fb1843`, `608d3b40`, `c25f03f1` | `76c69d65`, `4068f894`, `5316cfe9` |

  - Resolve cost less than R102's ($0.336 / $1.136 / $0.126): its prompts are unchanged, so the
    adjudications R102 already paid for are cache hits; only new pairs are asked.
  - Generality's individuals fall 211 -> 192 and concepts rise 256 -> 282: the pass's common nouns of an
    individual type ("café", "street") are now stated kinds and resolve as concepts (R104).
  - Committed: `tests/gold/r103/<ds>_logged.json` (counts and usage copied from the six stage runs) and
    `runs.json` (commands, runs, costs, approval). Test
    `test_r103_rebuild_logged_the_pinned_corpus_every_stage_and_its_cost`: documents and chunks equal r77d's,
    every stage counted, the cost adds up.
- **Part b: the evaluation (done 2026-10-07, $0: no API call).** `kg audit-snapshot`, `kg anchor-eval` (both
  arms), `kg anchor-sheets` and `kg mention-eval` on `out/r103_<ds>`, offline; **C0 passed on all three**.
  Arm C (vector) is not re-embedded: the chunks are r77d's (the part a test), so C5 pairs with R92's own
  rankings, recomputed from the committed reports (`compare_arms`; the same computation reproduces R102's
  table). Sheets committed at `916545b` before any verdict.
  - **Judging** (Claude Opus 5.5; rules `tests/gold/r93/rules/c3.md`, `c4.md`, `c6.md` unchanged and
    `tests/gold/r103/mention_judge_rules.md`): items byte-identical to R102's carry its verdicts (C3 76 / 81 /
    60, C4 47 / 101 / 45, C6 138 / 132 / 116, mention recall candidates 10 / 13 / 13); the other 456 (C3 170,
    C4 32, C6 62, mentions 192) were judged in 19 blind batches, each judge seeing only its rules and batch.
    The lead reviewed every INCORRECT, AMBIGUOUS and UNJUDGEABLE verdict and the seeded 10 % of VALID ones
    (165 items, carried ones included): **one change**, held-out's pass mention "OUTBACKS" ("HAD MULTIPLE
    OUTBACKS IN THE PAST", stated kind) INCORRECT -> AMBIGUOUS: the class rule makes a particular one
    individual thing called by its own name, the cars here are several, while the gold treats model names as
    particulars; the text supports both.
  - `kg anchor-judged` and `kg mention-eval --verdicts` on the three rebuilds. Committed per dataset:
    `anchor_anchor.json`, `anchor_layered.json`, `fidelity.json`, `code_checks.json`, the C3 / C4 / C6 sheets,
    code sides and verdicts, `anchor_judged.json`, the mention sheet, verdicts and report. Tests:
    `test_r103_verdicts_answer_their_sheets_carry_r102s_and_score_as_reported` (tests/test_anchor_judging.py)
    and R103's rows of `test_mention_verdicts_answer_their_sheets_and_score_as_reported`.
- **Part c: results** (judge: Claude Opus 5.5; R102 = the rebuild with the r77d claims, R103 = re-extracted;
  arm A unless named; C5 paired against R92's vector rankings, McNemar; n per row):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | C0 fidelity; C1 provenance | pass; 1.0 | pass; 1.0 | pass; 1.0 |
  | **Pass precision, judged (bound >= 0.90)** (R102 -> R103) | 45/57 = 0.789 -> **54/59 = 0.915** [0.816, 0.963] | 51/55 = 0.927 -> **55/59 = 0.932** [0.838, 0.973] | 41/58 = 0.707 -> **49/60 = 0.817** [0.701, 0.894] |
  | Mention recall of the R101 gold, exact | 57/75 -> 53/75 | 94/125 -> **100/125** | 80/109 -> **88/109** |
  | Recall with the judged mapping | 70/75 -> 68/75 | 111/125 -> **116/125** | 97/109 -> **103/109** |
  | **C7 hubs** (bound: no new hub against r77d) | 0 -> 0 | 1 (FORD) -> **0** | 4 -> 4 (North Station 10 -> 11 of 32) |
  | C2 targets at rank 1; with no node | 0.860 -> 0.837; 0 -> 0 | 0.800 -> 0.800; 0 -> 0 | 0.855 -> **0.726**; 1 -> 2 |
  | C3 individual merges judged; wrong | 26 -> 27; 0 | 42 -> 52; 0 -> **1** | 34 -> 33; 0 |
  | C3 concept merges judged; wrong | 114 -> 121; 2 -> 1 | 82 -> 67; 1 -> 2 | 48 -> 49; 1 -> 1 ("trip") |
  | C3 split groups that are one thing | 2 of 17 -> 2 of 16 | 1 of 6 -> 2 of 10 | 7 of 9 -> 5 of 7 |
  | C3 record merges wrong | 0 -> 0 | 1 -> **2** | 0 -> 0 |
  | R75 identity pairs: precision / apart / recall | 1.0 / 1.0 / 11/14 -> **11/12 / 32/33** / 11/14 | 1.0 / 1.0 / 5/7 -> 1.0 / 1.0 / 4/7 | 1.0 / 1.0 / 1.0 (unchanged) |
  | C4 precision | 64/64 -> 63/63 | 76/77 -> 81/83 | 47/47 -> 50/50 |
  | C6 arm A / B | 1.0 / 1.0 | 1.0 / 1.0 | 1.0 / 1.0 |
  | C8 connections; unwitnessed hops | 14/14; 0 | 16/16; 0 | 12/12; 0 |
  | C9 nodes / edges per chunk | 26.2 / 38.2 -> 26.2 / 38.8 | 23.5 / 31.9 -> 23.2 / 31.4 | 28.3 / 33.3 -> 28.9 / 33.9 |
  | C5 gold start complete@10 (A vs vector, p) | 29 vs 26 (0.453), unchanged | 25 -> 26 vs 26 (1.0) | 36 vs 37 (1.0), unchanged |
  | C5 end to end complete@5 (A vs vector, p) | 17 vs 21 (0.289), unchanged | 21 -> 22 vs 26 (0.180 -> 0.289) | 26 -> 27 vs 34 (0.021 -> 0.039) |
  | C5 end to end complete@10 (A vs vector, p) | 23 vs 26 (0.549), unchanged | 23 -> 24 vs 26 (0.453 -> 0.688) | 34 vs 37 (0.375), unchanged |
  | Cost of the rebuild (MLflow) | $0.411 -> $0.639 | $1.251 -> $1.383 | $0.170 -> $0.352 |

  Read with care:
  - **R101's bounds:** pass precision now meets 0.90 on furniture and held-out, on the pass's own output
    with today's claims; generality misses (0.817; the interval reaches 0.894). Its 11 INCORRECT: ordinary
    operation of a thing ("switched to duty", "duty", "tagged"), generic words or reference points ("budget"
    in "under budget", "surface", an agenda heading "Travel"), a time ("night shift"), common nouns stated
    particular ("Aldmoor cores", "2025 travel budget"), the document itself ("field log"), and "Core AB-19"
    (Out 9). The hub bound is met on held-out (no node is in more than 20 % of the chunks; R102's FORD hub is
    gone) and furniture; generality keeps the four hubs it had in R102, North Station among them (a real
    place the text names in 11 of 32 chunks, as in R102's decision item).
  - **A hard rule fails on held-out, from the pass:** one wrong merge of individuals, "CARS" ("DEAD BATTERY IF
    THE CARS SITS PARKED 2 DAYS") and "CAR" ("CAR HAS LESS THAN 400 MILES ON"), two complaints' cars. Both are
    pass mentions stated **kind** and typed with the keyed `Vehicle`: no record fits, so resolve made them
    `no_record` individuals and the adjudicator joined them. This is R104's open item ("a kind typed with a
    keyed type that no record fits becomes an individual"), now with a wrong merge. Held-out's C3 hard rule
    already failed since R93 (the Rogue Sport link); a second record-link error joins it: "OUTBACKS" (the
    owner's past cars) linked to the 2019 Outback record (C4 81/83 still passes).
  - **R75's apart pairs: 32/33 on furniture.** The re-extraction names a plain "dimmer", and resolve joins
    "dimmer", "dimmer switch" and "dimmer function" into one individual; R75's gold (the user's R38 decision)
    keeps the switch and the function apart, while the blind C3 judge accepted the merge (VALID_ALTERNATIVE:
    "the dimmer function ... a bit stiff to turn", the physical control). Not a C3 wrong merge as judged;
    reported against the identity gold.
  - **Recall:** the re-extracted claims and the R104/R106 pass reach more of the R101 gold on held-out and
    generality (mapped 116/125, 103/109); furniture loses two (68/75: "support", "comfort" in "the perfect
    balance between support and comfort", as in R105).
  - **C2 on generality falls 0.855 -> 0.726, a query-side ranking effect:** "committee" (4 questions) now
    ranks second behind another committee node, "mechanical seal" (3) behind the broader "seal" concept,
    and "works budget" and "travel claims" lost their exact node in the new extraction; C5 end to end is
    unchanged (34 of 38 complete at 10).
  - **C5 against vector retrieval** is unchanged or slightly better everywhere; at 10 no gap is significant
    (p >= 0.375); at 5 generality still trails (27 vs 34, p 0.039).
  - Gold and verdicts come from one model family (Claude); the gold was written blind in R101, the mention
    judge rules before any R103 output.
- **R103 done 2026-10-07** ($2.374). The R97-R103 plan is complete. Next: the user's decision on what R103
  leaves open (Found along the way): the keyed-kind individuals (now a wrong merge), generality's pass
  precision, and the dimmer join.

### R107. A stated kind that no record fits resolves as a concept (done 2026-10-07; $0.0132)
The user's decision after R103 (2026-10-07, "R107 is approved"): resolve follows the class the mention pass
states. Today the class lives in `mentions.jsonl` only; resolve routes by the type's identity class, so a
mention the pass stated a kind and typed with a keyed type becomes a `no_record` individual when no record
fits (`resolution/particulars.py`, `_individual`), and individuals are joined pairwise. In R103 that made 122
kinds named things (held-out 76: "recall" x57 `Recall`, "vehicle", "CAR" `Vehicle`; furniture 29: "parts",
"hardware"; generality 17: "contractor", "residents" `Person`), one wrong join ("CARS" / "CAR" of two
complaints, C3's hard rule), splits ("contractor" both a `Kind` concept and a `Person` individual) and 1,639
of held-out's 2,192 individual pair decisions.
- **Split, one commit each:** (a) the rule, everywhere identity is decided ($0, tests); (b) the measurement,
  a resolve of the three R103 graphs with the rule (paid, asked with its counted estimate), judged as R103.
- **Part a scope:**
  - The pass writes the stated class on the `:Mention` it creates (`stated_class`; `write_mentions` takes the
    pass's classes); `read_mentions` reads it (`MentionRecord.stated_class`, None for a claim's or
    derivation's mention, whose behaviour does not change).
  - `resolution/particulars.py`: records are matched first, as before (rules and chooser); then a keyed
    mention stated `kind` whose match links no record (none fits, or several tie) leaves the particulars
    (`names_a_kind`, the one rule); `Particulars.kinds` lists them. A stated kind that a record fits still
    links it ("back rest" -> the part record).
  - `resolution/identity.py`: those mentions join the concept mentions under the built-in `Kind` type, the
    fallback R104 gives a kind in the pass, so "contractor" stated kind and typed `Person` is the same
    concept as "contractor" typed `Kind`. Report field `kinds_without_record`, metric of the same name.
  - The offline replays follow the rule: `audit/reidentify.py` carries the snapshot's stated class into the
    pure core (a faithful replay of a build made before R107 now lists those mentions as differences, which
    is right: today's code would not build it); its measured mode and `audit/relink.py` refuse a stated kind
    the replay moves to a concept that the build had as a particular, since only `kg resolve` decides
    concepts. Not changed: the pass, the record rules, the joining of individuals, concept resolution.
- **Part a (done 2026-10-07, $0, no run):** as scoped. `names_a_kind(stated, match)` in
  `resolution/particulars.py` is the one rule; `assign_particulars` sets those mentions aside before the
  units (their chunks leave the evidence too) and keeps them in `ambiguous` when records tie; `identity.py`
  retypes them `Kind` and resolves them with the concept mentions, in id order (same graph, same prompts);
  the assignment's `type` is the type resolved under (comment in `identity_graph.py`). Audit:
  `reidentify.undecided_kinds`, `Relink.to_concepts`, both refused in `kg audit-relink`; a measured replay
  drops a build's concept edge for a mention it now links to a record. README's identity paragraph.
  - Tests: `tests/test_identity.py`: the pure rule (R103's "CARS" / "CAR" set aside and never paired, a tied
    kind set aside and still ambiguous, a stated kind a record fits linked, a stated particular and a claim's
    mention individuals; the same inputs without a class nominate "CARS" / "CAR" as a pair) and on Neo4j
    ("colleague" typed `Person` and typed `Kind` in two documents, both stated kind, one `Kind` concept;
    "Priya Shah" stated particular an individual; `kinds_without_record`); `tests/test_mention_pass.py`: the
    stage writes `stated_class` on its new mentions, none on a claim's; `tests/test_audit.py`: a build with a
    pass kind as a `Kind` concept passes the faithful gate and keeps its edge in a measured replay, one made
    before R107 (an individual) is a faithful difference and refused measured, and a record replay that
    unlinks a stated kind is refused. The two resolve tests fail on the code before part a. Gate: 828 passed
    (823 before), `ruff check` clean.
- **Part b, the estimate (counted before asking):** per dataset into `out/r107_<ds>`, R102's command line
  with R103's claims: `kg reset`, `build`, `ingest-text`, `extract --from-build out/r103_<ds>` (the claims
  replayed, no call), `link`, `mention-pass` (the same graph gives R103's prompts: answered from the LLM
  cache), `resolve`, `attach`; plan, text schema and profile pinned from r77d as in R103. Only resolve asks
  new questions: concept pairs of the about 122 kinds' concepts (furniture 29, held-out 76 under about 12
  names, generality 17) and pairs whose concept gained members (new context lines); every individual pair
  and record choice R103 asked is unchanged and cached. At R102's cost per resolve call (about $0.0005):
  **about $0.15, likely $0.05-0.35**; $0.33 more if the pass were not answered from the cache. Then the
  evaluation of R103 part b ($0) and blind judging of the changed C3 items. It answers: is the C3 hard rule
  held again on held-out, do the splits close, and does C7 gain hubs ("recall" 28 and "recalling" 27 of 81
  held-out chunks).
- **Part b, the runs (done 2026-10-07; $0.0132, the user's yes "yes run it").** At `5dce401` (only
  `.claude/settings.json` dirty), the commands above into `out/r107_<ds>`, presets `quality`, `heldout`,
  `generality_gemini`; the pass on an invalid Gemini key so a miss would fail: every pass call a cache hit and
  each pass file byte-identical to R103's; every count before resolve equal to R103's.

  | | furniture | held-out | generality |
  |---|---|---|---|
  | resolve: records / individuals / concepts (R103) | 63 / 212 / 740 (63 / 240 / 712) | 83 / 353 / 633 (83 / 428 / 558) | 48 / 175 / 301 (50 / 192 / 282) |
  | `kinds_without_record` | 28 | 75 | 19 |
  | attach: attachments (R103) | 1227 (1277) | 3519 (3831) | 467 (476) |
  | resolve: calls / cache hits; tokens in / out / thinking | 789 / 773; 4,773 / 147 / 666 | 727 / 719; 3,458 / 120 / 106 | 354 / 346; 1,496 / 40 / 505 |
  | cost (MLflow; only resolve paid) | $0.0066 | $0.0034 | $0.0032 |
  | MLflow: pass, resolve | `2f256493`, `f1a771d9` | `7a43b490`, `40d1460a` | `82d20b49`, `8443ff9c` |

  - Far below the estimate: the concept pairs of the new `Kind` concepts were mostly pairs R103 had asked
    already (same names, same context), and held-out's 1,639 individual pairs of these kinds are gone.
  - Generality's two record mentions fewer: "pump" ("the pump had run dry", after "Pump HP40-1183 showed a
    slow drip") and "Pump" ("Pump returned to duty the same day", after "Bearings of HP40-1183 regreased"),
    stated kinds that R103's adjudicator joined to the record HP40-1183 as variants. The text supports both
    joins: the kind word refers back to the one pump. R107 makes them `Kind` concepts (Found along the way).
  - Committed: `tests/gold/r107/<ds>_logged.json`, `runs.json`; test
    `test_r107_resolved_r103s_graphs_again_and_changed_only_identity`.
- **Part b, the evaluation (done 2026-10-07, $0: no API call).** `kg audit-snapshot`, `kg anchor-eval` (both
  arms), `kg anchor-sheets` on `out/r107_<ds>`, offline; **C0 passed on all three**. Sheets, code sides,
  anchor reports, fidelity and code checks committed at `09c67cf` before any verdict. No `kg mention-eval`:
  the pass files are byte-identical to R103's, so pass precision and mention recall are R103's by
  construction. Arm C (vector) as in R103: R92's rankings, the chunks are r77d's.
  - **Judging** (Claude Opus 5.5; rules `tests/gold/r93/rules/c3.md`, `c6.md`): items byte-identical to
    R103's carry its verdicts (C3 344, C4 223, C6 441); the other 21 (C3 13, C6 8, C4 none) were judged in 7
    blind batches, each judge seeing only its rules and batch: 18 VALID, 3 VALID_ALTERNATIVE ("unit" for a
    nightstand and a bookshelf; the 2016 Civic piston ring recall group; "Legacy" as the 2019 Legacy). The
    lead read all 21 and reviewed every INCORRECT, AMBIGUOUS and UNJUDGEABLE verdict and the seeded 10 % of
    VALID ones (116 items, 115 carried): **no change**. `kg anchor-judged` on the three graphs. Committed:
    the verdict files and `anchor_judged.json` per dataset; test
    `test_r107_verdicts_carry_r103s_and_the_kind_rule_removes_the_wrong_join_of_two_cars`.
- **Part c: results** (judge: Claude Opus 5.5; R103 -> R107, same claims, same pass; n per row):

  | Criterion | furniture | held-out | generality |
  |---|---|---|---|
  | C0 fidelity; C1 provenance | pass; 1.0 | pass; 1.0 | pass; 1.0 |
  | C3 individual merges judged; wrong | 27 -> 27; 0 | 52 -> **27; 1 -> 0** | 33 -> 33; 0 |
  | C3 concept merges judged; wrong | 121 -> 124; 1 | 67 -> 68; 2 | 49 -> 53; 1 ("trip") |
  | C3 split groups; wrong (one thing kept apart) | 16 -> 11; 2 | 10 -> 4; **2 -> 1** | 7 -> 5; 5 |
  | C3 record merges wrong (hard rule) | 0 | **2 (still: Rogue Sport, OUTBACKS)** | 0 |
  | R75 identity pairs: precision / apart / recall | 11/12 / 32/33 / 11/14 (unchanged) | 1.0 / 1.0 / 4/7 (unchanged) | 1.0 / 1.0 / 1.0 |
  | C4 precision | 63/63 | 81/83 | 50/50 -> 48/48 |
  | C6 arm A / B | 1.0 / 1.0 | 1.0 / 1.0 | 1.0 / 1.0 |
  | C7 hubs | 0 -> 0 | 0 -> 0 | 4 -> 4 |
  | C2 targets at rank 1; C5 end to end complete@10 (A vs vector) | 0.837; 23 vs 26 (unchanged) | 0.800; 24 vs 26 (unchanged) | 0.726; 34 vs 37 (unchanged) |
  | C9 nodes per chunk | 26.24 -> 26.17 | 23.25 -> 22.65 | 28.91 -> 28.75 |
  | attachments (claims hung on things) | 1277 -> 1227 | 3831 -> 3519 | 476 -> 467 |

  Read with care:
  - **The wrong join is gone:** held-out's "CARS" / "CAR" of two complaints are mentions of one `Kind`
    concept "CAR" (5 mentions, judged VALID: "a motor car" in every one). Held-out's 75 stated kinds are now
    6 concepts ("recalling" 55 mentions, judged VALID: every one a manufacturer's safety recall; "vehicles"
    12; "VEHICLE" 3; "CAR" 3), not 75 individuals asked about pairwise.
  - **Held-out's C3 hard rule still fails**, on its two record links (R93's Rogue Sport, R103's OUTBACKS),
    which R107 does not touch; R107's recommendation said it would lift the failure, which was right only for
    the individuals' part.
  - **One wrong split closed:** held-out's two "Legacy" nodes are one (VALID_ALTERNATIVE). The furniture and
    generality wrong splits are R103's named places and reviewers, unchanged.
  - **No new hub:** the broad `Kind` concepts ("recalling" in 27+ held-out chunks) are not what any gold
    question's name finds, so C7 does not list them. C2, C5, C8 are unchanged everywhere.
  - **Cost of the rule:** generality's "pump" / "Pump" lose their correct record link to HP40-1183 (C4 n
    50 -> 48; Found along the way), and the claims a kind's individual held by `key_in_sentence` are no longer
    attached to it (held-out 312 fewer attachments, to "VEHICLE" / "recall" individuals of one complaint
    each); no measured criterion moved with them.
  - The dimmer pair (R75 apart 32/33 on furniture) is unchanged: the "dimmer" join is not a pass kind.
  - Gold and verdicts come from one model family (Claude).
- **R107 done 2026-10-07** ($0.0132). Next: the user's choice among the open items: the record-link errors
  that keep held-out's C3 hard rule failing (Rogue Sport, OUTBACKS), the narrower kind rule (a kind may join
  a record's unit), identifiers nominated by spelling, the dimmer pair, generality's pass precision.

### R108. Record links that only context can confirm go to the chooser (done 2026-10-07; $0.0872)
The user's choice after R107 (2026-10-07: the two bad record links next, not the narrower kind rule; "yes"
to the scope below with its cached rebuild). Held-out's C3 hard rule fails on two record links made by code
alone:
- "2017-2022 Rogue Sport" -> Vehicle:ROGUE by rule 1 (`key`): the key is a whole word of the name. For an
  identifier ("pump HP40-1183") that is safe; held-out's Vehicle keys are plain words (the model), so the
  rule is the containment R95a retired for names. In R107's held-out graph it made 40 such links, 39 judged
  right ("Civic Type R", "Rogue Hybrid", "Civic coupe" are versions of their record) and this one wrong (a
  line of its own): code cannot tell them apart.
- "OUTBACKS" ("HAD MULTIPLE OUTBACKS IN THE PAST", the owner's earlier cars) -> Vehicle:OUTBACK (the 2019
  model) by rule 2 (`name`), a plural. The pass stated it a kind; of the 3 stated kinds linked by a name
  only up to an ending, it is the one wrong (furniture "drawer", "center supports" are right).
- **Scope (one commit for the rules, then the measurement):**
  - `resolution/records.py`: `key_decides(name, record)`, rule 1's test: a key with a digit (an identifier)
    decides alone as before; a key that is a plain word decides only when every other word of the name is a
    number or a value the record's own key attributes hold ("2015 Ford Escape": a year and the make).
    Otherwise no link by key: the record is a near miss. Held-out: 24 such links stay (all judged right), 11
    go to the chooser (10 right, Rogue Sport wrong). `match_record` takes the stated class: a stated kind
    keeps only exact name matches (score 100), so one named by a record's name only up to an ending is a near
    miss (3 links).
  - `resolution/record_choice.py`: outside a scope, a record whose key is a word of the name is a near miss
    too (the old key rule nominates where it no longer decides). The chooser's prompt gains two refusals
    (domain-neutral, invented examples): an added word that makes the name of another line of its own; other
    things of the record's kind than the ones it stands for. Today the prompt counts "a plural" and "the name
    with a describing word" as the record, so it would accept both errors. It also names a version (a size,
    a finish, a variant of the same line) as the record. No code check is possible for a refusal (world
    knowledge); a choice is still verified.
  - `audit/relink.py`: the replay passes the stated class and explains the two losses (`word_key`,
    `kind_ending`). Not changed: the name test, scopes, the other rules, joining, concepts.
- **Measurement (the user's yes, counted estimate about $0.08, likely $0.05-0.20):** R107 part b's cached
  rebuild of the three graphs with R108; the chooser asks every question again (its prompt changed, about 75
  calls); the changed C4 and C3 items judged blind.
- **Part a, the rules (done 2026-10-07, $0):** as scoped. `records.key_decides` and `keys_in_name`;
  `match_record(..., stated)`; near misses add the records whose key is a word of the name (inside a scope
  and, outside one, from the domain: rule 1's old reach); the chooser prompt (intent comment updated) gains
  "a version of it" among the right links and the two refusals, its examples an invented telescope line
  ("Corvid ED", "Corvid Voyager", "the Corvids I owned before"); relink's causes `word_key`, `kind_ending`.
  README's identity paragraph.
  - Tests: `tests/test_records.py` (a word key decides beside numbers and the record's own values and not
    beside other words, an identifier as before; a stated kind links by the very name only, a claim's or a
    particular's mention keeps the ending rule), `tests/test_record_choice.py` (a word key in a longer name
    nominates its record inside a scope and outside; the corpus four-gram guard passes on the new prompt),
    `tests/test_audit.py` (a stated kind's plural link lost with `kind_ending` and listed for the concepts;
    `word_key` for "Corvid Voyager", none for "2021 Corvid"). `tests/test_linking.py`'s invented held-out
    vehicle gets its make as a key attribute, as held-out's schema has (the old fixture had none, so "2016
    Honda Civic" now went to the chooser). The new tests fail on the code before part a. Gate: 839 passed
    (834 before), `ruff check` clean.
  Committed at `6bac112`.
- **Part b, the runs (done 2026-10-07; $0.0872).** At `6bac112` (only `.claude/settings.json` dirty), R107
  part b's commands into `out/r108_<ds>`: claims replayed from `out/r103_<ds>`, the pass on an invalid key
  (every call a cache hit, each pass file byte-identical to R103's), resolve and attach. Every count before
  resolve equals R107's.

  | | furniture | held-out | generality |
  |---|---|---|---|
  | resolve: records / individuals / concepts (R107) | 63 / 212 / 740 (same) | **77** / 358 / 634 (83 / 353 / 633) | 48 / 175 / 301 (same) |
  | chooser: asked / chosen | 49 / 27 | 13 / 7 | 10 / 7 |
  | resolve calls / cache hits | 790 / 742 | 744 / 725 | 354 / 344 |
  | cost (MLflow; only resolve paid) | $0.0506 | $0.0267 | $0.0099 |
  | MLflow resolve run | `88f81dc2` | `d38e63cf` | `bdfd2877` |

  - **Held-out, the 13 choices:** "2017-2022 Rogue Sport" and "OUTBACKS" -> none (both wrong links gone);
    chosen: "2017-2019 Rogue Hybrid", "Civic Coupe", "Civic Sedan", "Civic Hatchback", "2016 Civic 2-Door",
    "2016 Honda Civic two door and four door ... vehicles", "2016 CIVICS"; but also none for four links R107's
    judges called right: "Civic Type R", "2017-2018, 2021 Civic Type R", "2017-2021 Civic hatchback",
    "2016-2020 Civic coupe" (the record's data gives model year 2016; the chooser seems to read the years
    against it, and is not consistent: "Civic Hatchback" chosen, "2017-2021 Civic hatchback" not). They are
    claims' mentions and stand for themselves.
  - **Furniture and generality:** the new prompt changed no answer; one link, "center supports" (a stated
    kind named by "Center Support" up to an ending), now reaches the same record through the chooser.
  - Committed: `tests/gold/r108/<ds>_logged.json`, `runs.json`; test
    `test_r108_resolved_r107s_graphs_again_and_changed_only_identity`. Then (b2, $0) `kg audit-snapshot`,
    `kg anchor-eval` (both arms), `kg anchor-sheets`: **C0 passed on all three**; sheets committed before any
    verdict.
- **Part c: judging and results (done 2026-10-07, $0).** Every C3, C4 and C6 item of the three graphs is
  byte-identical to one R107 judged (the six lost links left held-out's C4 sheet, 112 -> 106 items; the
  unlinked mentions stand alone and make no new merge or split), so **no item was judged anew**: all
  verdicts carry R107's. The lead reviewed the seeded sample again (113 items, 9 of them held-out record links
  new to the sample, all VALID): no change. `kg anchor-judged` on the three graphs.

  | Criterion (R107 -> R108) | furniture | held-out | generality |
  |---|---|---|---|
  | **C3 hard rule** | pass -> pass | **fail -> pass** | pass -> pass |
  | C3 record links wrong | 0 | **2 -> 0** (Rogue Sport, OUTBACKS) | 0 |
  | C4 precision (record links judged) | 63/63 | 81/83 -> **77/77** | 48/48 |
  | C3 individual merges wrong; wrong splits | 0; 2 | 0; 1 | 0; 5 |
  | R75 identity pairs: precision / apart / recall | unchanged | unchanged | unchanged |
  | C2, C5 (gold start, end to end), C7 hubs, C8 | unchanged | unchanged | unchanged |
  | C9 nodes per chunk | 26.17 | 22.65 -> 22.73 | 28.75 |

  Read with care:
  - **Held-out passes C3 for the first time since R93**: no wrong record link and no wrong join of
    individuals in any judged item.
  - **The price: four right links lost on held-out** ("Civic Type R" x2, "2017-2021 Civic hatchback",
    "2016-2020 Civic coupe": judged VALID in R107, refused by the chooser, which reads the record's model
    year against the ranges and does so inconsistently). They are recall of links, not precision: C4 counts
    only links made. The project's rule since R75 holds: a wrong link answers questions about the wrong
    record, a missing one leaves the mention to stand for itself. Retrieval (C5) is unchanged.
  - The new prompt changed no answer on furniture and generality (59 choices asked again).
  - Committed: verdict files and `anchor_judged.json` per dataset; test
    `test_r108_verdicts_are_r107s_and_held_out_passes_c3_without_a_wrong_record_link`.
- **R108 done and accepted 2026-10-07** ($0.0872). The user accepted it because every C3 hard rule passes
  on the three datasets, every remaining record link is judged right, and no retrieval or graph-quality
  criterion regressed. **The refinement arm stops at R108** (the user's decision, 2026-10-07): what it
  leaves is recorded under "Known limitations" below as future work, not optimised now.

### R109. A derived claim's object is a record of its object type (done 2026-10-07; $0.0037; bug fix)
A bug fix the user chose on 2026-10-07 ("option 1"), separate from the closed refinement arm (its four known
limitations are not touched). `resolution/derivation.py` writes, for each fact type the text schema marks
`derived`, a claim from every subject mention of a document to the node that document is ABOUT. On held-out
the derived type is `Component INSTALLED_IN Vehicle`, and a recall's document (`record/Recall/<id>`) is ABOUT
its `Recall` record, so code wrote "seatbacks INSTALLED_IN 17V472000": a recall number as a Vehicle. In R108's
held-out graph 97 of the 135 derived claims are of this kind (the other 38 are on the 5 complaint files, ABOUT
their `Vehicle`), and they make 29 Vehicle individuals named after recall numbers; furniture's 154 are all
on reviews ABOUT a `Product`, generality derives nothing. Checked on R108's snapshot before the fix: removing
them cuts no source link (all 29 recall documents stay ABOUT their record through the link stage; each
subject mention of the 97 claims is MENTIONED by its chunk through an extracted claim, 97 of 97). The "Found along the
way" entry "Derivation mistargets record documents" (R67 part 3) is this bug.
- **Split, one commit each:** (a) the rule and its tests ($0); (b) the held-out rebuild (paid) and its
  offline evaluation, sheets committed before any verdict; (c) judging and results.
- **Part a scope:** `derivation.derives_into(object_type, label)` is the one rule: a document ABOUT a node
  derives only when the node's label is one of the object type's `record_labels`; an object type with no
  record labels (not keyed, or absent from the schema) keeps the old rule, every ABOUT node. `derive_rows`
  takes the object type and the named domain nodes (with their labels) and applies it, so the link stage
  (`derive_facts`, from `read_domain_nodes`) and the offline snapshot (`audit/snapshot.py`, from the
  records) share it, and the C0 fidelity gate compares two runs of one rule. The skipped sources are counted
  (`skipped_other_label`, a link-stage metric). Not changed: linking, the mention pass, resolve, attach, any
  prompt. Pointing the claim at the recall's vehicle through `AFFECTS_VEHICLE` is out of scope (Found along
  the way).
- **Part a (done 2026-10-07, $0, no run):** as scoped; README's derived-facts paragraph.
  - Tests: `tests/test_derivation.py`: the pure rule on held-out's case (a recall document ABOUT a `Recall`
    derives nothing, a complaint file ABOUT a `Vehicle` still derives "battery -> OUTBACK"; an object type
    without record labels, or none, derives onto both) and on Neo4j (`derive_facts`: one claim, one created
    mention, `skipped_other_label` 1, no mention named "17V472000", the seatbacks mention still MENTIONED in
    the recall's chunk); `tests/test_audit.py`: the snapshot derives nothing onto `Product` records for an
    object type naming another label, and derives as before for one naming none. The snapshot test fails on
    the code before part a (it derives four claims); the two others cannot import it (`derives_into` is
    new). Gate: 852 passed (849 before), `ruff check` clean.
- **Part b, the estimate (counted before asking; the user's yes, 2026-10-07):** R108 part b's commands into
  `out/r109_heldout` (claims replayed from `out/r103_heldout`, plan, text schema and profile pinned from
  r77d). The mention pass runs on the real key: 54 of the 81 chunks lose a derived recall-number name from
  their "already listed" names, so their prompts change (R103 paid $0.154 for 81 pass calls: about $0.10);
  resolve asks only the pairs that touch changed pass mentions (R108's held-out resolve $0.027, mostly
  cached: about $0.02-0.10). **About $0.15, likely $0.10-0.25, at most about $0.45.** Furniture and
  generality are checked offline ($0): `kg audit-snapshot` with the new code on copies of their R108 builds
  must still pass C0.
- **Part b, furniture and generality ($0, no LLM).** `kg audit-snapshot` with part a's code on
  `out/r108_furniture` and `out/r108_generality` (read only, `--out out/r109_c0/<ds>`, R108's logged
  files): **C0 passed on both** (MLflow `e20b87fa`, `6e50de0c`), and the rebuilt `snapshot.json` and
  `code_checks.json` are byte-identical to R108's: the rule changes nothing there (furniture's 154 derived
  claims are all on reviews ABOUT a `Product`, generality derives nothing).
- **Part b, the held-out run (done 2026-10-07; $0.0037, far below the estimate).** At `584f952` (only
  `.claude/settings.json` dirty), R108 part b's commands into `out/r109_heldout`, the pass on the real key.

  | | R108 | R109 |
  |---|---|---|
  | link: derived claims; created object mentions; `skipped_other_label` | 135; 29; - | **38; 0; 97** |
  | pass: calls / cache hits; findings kept | 81 / 81; 594 | 81 / **79**; 595 |
  | resolve: mentions; records / individuals / concepts | 1069; 77 / 358 / 634 | 1041; 77 / **329** / 635 |
  | resolve: calls / cache hits; record choices asked / chosen | 744 / 725; 13 / 7 | 724 / **724**; 13 / 7 |
  | attach: claims attached / total; attachments | 665 / 665; 3560 | 568 / 568; 3263 |
  | cost (MLflow) | $0.0267 (resolve) | **$0.0037** (pass: 2 calls, 2,090 / 568 tokens in / out) |
  | MLflow: link, pass, resolve | `19733591`, `e410d14e`, `d38e63cf` | `21b28508`, `f52a625a`, `2581be2c` |

  - **Why only 2 pass prompts changed, not 54:** the prompt's "already listed" line holds names, not
    mentions. In 52 of the 54 recall chunks an extracted `Recall` mention has the same name as the removed
    derived `Vehicle` mention ("15V406000"), so the line is unchanged and the cached answer applies. In
    `record/Recall/17V472000#1` and `19V503000#1` the recall number was listed only through the derived
    mention: the first gave the same findings, the second one more, "placement" (`Kind`, stated kind).
  - Every count before link equals R108's (corpus, claims: `triples.jsonl` byte-identical); resolve asked
    nothing new: the 29 recall-number individuals and their spelling pairs are gone, every other question
    was R108's.
  - Committed: `tests/gold/r109/heldout_logged.json`, `runs.json`; test
    `test_r109_rebuilt_held_out_without_the_recall_documents_derived_claims` (tests/test_derivation.py).
- **Part b, the evaluation ($0, no API call).** `kg audit-snapshot`, `kg anchor-eval` (both arms),
  `kg anchor-sheets`, `kg mention-eval` (R101 gold) on `out/r109_heldout`, offline: **C0 passed**. Against
  R108's sheets: every C3 (102) and C6 (156) item is byte-identical; C4 keeps its 77 links byte-identical
  and loses 29, the links of the derived recall-number mentions to their own Recall record (R108 judged all
  29 VALID: "the heading is exactly the record key"); the mention sheet's 125 recall items equal R103's,
  while its seeded precision sample of 60 keeps 34 items and draws 26 new ones (the sample is
  `random.Random(102).sample` over the pass mentions in id order, so one new mention shifts every later
  position). Sheets, code sides, anchor reports, fidelity and code checks committed in
  `tests/gold/r109/heldout/` before any verdict; test
  `test_r109_sheets_keep_r108s_items_but_the_links_of_the_recall_number_mentions`.
- **Part c: judging and results (done 2026-10-07, $0).** Judge: Claude Opus 5.5; rules
  `tests/gold/r93/rules/c3.md`, `c4.md`, `c6.md`, `tests/gold/r103/mention_judge_rules.md`. Every C3 (102),
  C4 (77) and C6 (156) item carries R108's verdict; the mention sheet's 16 recall candidates and 34 kept
  precision items carry R103's. The 26 new precision items were judged in 2 blind batches of 13 (one Opus
  subagent each, seeing only the rules and its batch): 23 VALID, 3 INCORRECT ("PARKED" in "THE CARS SITS
  PARKED 2 DAYS", ordinary use; "OBJECT" in "MISTOOK THE BRIDGE FOR AN OBJECT", generic; "SYSTEM", a
  fragment of "FRONT CAMERA SYSTEM"). The lead read all 26 and reviewed every INCORRECT, AMBIGUOUS and
  UNJUDGEABLE verdict and the seeded 10 % of VALID ones (52 items, 45 carried): **no change**. `kg
  anchor-judged` (MLflow `91cb1170`) and `kg mention-eval --verdicts` (`4ba4b210`) on `out/r109_heldout`.

  | Held-out (R108 -> R109) | R108 | R109 |
  |---|---|---|
  | derived claims | 135 | **38** |
  | derived claims whose object is not a record of the object type (the bug) | 97 | **0** |
  | derived claims judged right (R110's blind claim verdicts; the 38 kept are byte-identical) | 38/135 | **38/38** |
  | Vehicle individuals named after a recall number | 29 | **0** |
  | code check: label-mismatch flags (a `Vehicle` mention named like its recall's key) | 29 | **0** |
  | C0 fidelity; C1 provenance | pass; 1.0 | pass; 1.0 |
  | **C3 hard rule**; record links wrong; individual / concept wrong merges; wrong splits | pass; 0; 0 / 2; 1 | pass; 0; 0 / 2; 1 |
  | C4 precision (hard rule) | 77/77 (pass) | 77/77 (pass) |
  | C6 purity arm A / B (hard rule) | 1.0 / 1.0 (pass) | 1.0 / 1.0 (pass) |
  | C2 targets at rank 1 | 52/65 = 0.800 | **53/65 = 0.815** |
  | C5 end to end complete@10 / @5, A vs vector (p) | 24 vs 26 (0.688) / 22 vs 26 (0.289) | unchanged |
  | C5 gold start complete@10, A vs vector (p) | 26 vs 26 (1.0) | unchanged |
  | C7 hubs | 0 | 0 |
  | C8 gold connections reached; unwitnessed hops | 16/16; 0 | 16/16; 0 |
  | C9 nodes / edges per chunk | 22.73 / 31.38 | 22.04 / 30.38 |
  | mention recall of the R101 gold, exact; judged mapping | 100/125; 116/125 | 100/125; 116/125 |
  | pass precision, judged sample | 55/59 = 0.932 [0.838, 0.973] (R103's) | 52/59 = 0.881 [0.775, 0.941] |
  | attachments (claims attached / total) | 3560 (665 / 665) | 3263 (568 / 568) |

  Furniture and generality: C0 passes on their R108 builds with the new code, snapshots byte-identical
  (part b); every other criterion is R108's by construction.

  Read with care:
  - **The bug is gone and nothing else moved:** the 97 removed claims are exactly the ones R110's blind
    judges called INCORRECT ("fuel pump assembly INSTALLED_IN 20V682000"); the 38 kept, on the 5 complaint
    files, are R108's byte for byte ("PADS INSTALLED_IN 2015 FORD ESCAPE", from "3RD TIME, PADS CHANGED AND
    ROTORS MACHINED."). Every judged C3, C4 and C6 score equals R108's but the 29 label-mismatch items: in
    R108 the code flagged each derived `Vehicle` mention "17V472000" as named like the record
    `Recall:17V472000` of its own document, and the judges confirmed each refers to that recall.
  - **No source link lost:** all 29 recall documents are ABOUT their own Recall record; each of the 97
    removed claims' subject mentions is still MENTIONED by its chunk, all through an extracted claim (18 as
    its subject, 79 as its object); the recall numbers stay findable as the `Recall` records and their
    extracted `Recall` mentions.
  - **C2 gains one target:** "20V373000" (question H38) ranked second in R108, behind the fake `Vehicle`
    individual "20V373000"; now the recall record is first. Two others ("22S25", "19V493000") move from rank
    3 to 2 for the same reason.
  - **Pass precision 0.932 -> 0.881 is a resampling, not a change of the pass:** 594 of the 595 pass
    findings are R103's and the one new ("placement") is not in the sample; the seeded sampler redrew 26 of
    the 60 items (Found along the way), 3 of which are wrong. Both draws together, 86 items of one pass:
    78/85 = 0.918. The intervals overlap; R101's bound (0.90) is met by R103's draw and by the union,
    missed by this draw.
  - **Why only 2 pass prompts changed:** see part b; the estimate ($0.10-0.25) assumed 54 changed prompts.
  - Gold and verdicts come from one model family (Claude).
  - Committed: verdict files, `anchor_judged.json`, the mention verdicts and report in
    `tests/gold/r109/heldout/`; tests `test_r109_verdicts_are_r108s_and_only_the_recall_number_mentions_left`
    (tests/test_anchor_judging.py) and R109's row of `test_mention_verdicts_answer_their_sheets_and_score_as_reported`.
- **R109 done 2026-10-07** ($0.0037). Every "done when" holds: 0 of 38 derived claims point at a node of
  another label (was 97 of 135) and the complaint documents' 38 are unchanged; furniture and generality pass
  C0 on their R108 builds; no source link is lost; held-out passes every hard rule (C3, C4, C6); C2, C5 and
  C8 are not worse (C2 gains one target); mention recall is R108's.

### R110. Today's claims judged: every stored claim, strict and content precision (done 2026-10-07, corrected the same day; $0, no run)
The user, 2026-10-07: keep the claim layer (it may make structured querying more robust) and judge the
correctness and validity of the claims of the latest run. Those are R103's re-extracted claims, replayed
unchanged into R107 and R108 (`out/r108_<ds>/triples.jsonl` byte-identical to R103's). Claim precision was
last judged in R66 / R68 (judge precision 0.960 held-out Gemini, 0.990 furniture); the extraction changed
since (R77's truth, modality and condition, R81, R82), and R87's M2 was never judged. Numbered R110 because
another session holds R109 (a derivation fix).
- **Split, one commit each:** (a) the offline claim sheet and its scoring, the judge rules, the three sheets
  ($0); (b) blind judging of every claim, the lead's review, the scores and the results ($0: Claude in the
  session, no API call, no pipeline run).
- **Validity by code** is already measured and not judged again: R103's C1 provenance is 1.0 on all three
  (every claim's chunk exists, every quote is in its chunk, every extracted claim passes `verify` against the
  schema again). The judge decides what code cannot see: whether the text states the claim as stored.
- **Part a scope:**
  - Structural move first, behaviour kept: `_build_params` and `_gated_snapshot` of
    `pipeline/mention_stages.py` move to `pipeline/offline.py` (`build_params`, `gated_snapshot`), so a new
    offline stage reuses the C0 gate instead of copying it.
  - `validation/claim_eval.py` (pure): the sheet (every stored claim once, `origin` extracted or derived, every
    stored field: ends and their types, relation, quote, polarity, time, truth and negation words, modality
    and hedge words, condition; each chunk's text and context once; the schema's relation and type
    descriptions), `claim_verdict_issues` (an INCORRECT verdict names at least one known fault, every quote
    is in the claim's chunk), `score_claims`: per origin, strict precision (every stored field right) and
    content precision (the triple right: no `not_in_text`, `wrong_entity`, `wrong_relation` or `truth`
    fault), Wilson intervals, the count of every fault (also `modality`, `condition`, `polarity`, `time`,
    `type`), strict precision per relation.
  - `validation/anchor_verdicts.py`: the shared verdict file gains the criterion `claims` and an optional
    `faults` list, for INCORRECT verdicts only (as `outliers` / `together`); old files read unchanged.
  - `pipeline/claim_stages.py`, `ClaimEvalStage` + `kg claim-eval BUILD --dataset --data --logged
    [--verdicts]`: the build's graph rebuilt offline behind R87's C0 gate, the sheet written; with verdicts,
    the shared review rules, the claim rules, the scores. One MLflow run per build (params: build, data,
    logged hash, chunker, dataset, verdicts hash; metrics: counts and, judged, both precisions per origin
    with bounds and n, AMBIGUOUS / UNJUDGEABLE counts, every fault; artifacts: sheet and report).
  - Judge rules `tests/gold/r110/claim_judge_rules.md`, written before any verdict: the question, how to read
    a claim with its fields, the writer (R68's reading), lists, numbers, derived claims, the five labels, the
    nine faults with invented examples (an observatory), the output format.
- **Part a (done 2026-10-07, $0, no LLM, no Neo4j):** as scoped. Tests `tests/test_claim_eval.py` (4): the
  sheet (every field, each id once with the merge counted, the schema's descriptions, the chunk's context),
  the claim rules (an INCORRECT verdict without a fault, an unknown fault, a quote outside the chunk, a
  fault on a VALID verdict refused by the model), the scores (content against field faults, AMBIGUOUS out of
  the denominator), the stage on the audit's invented build (counts, params, metrics, the report, a quote
  outside its chunk refused). README: the command and the module map.
  - **Sheets** (`kg claim-eval` on `out/r108_<ds>`, C0 passed on all three; committed as
    `tests/gold/r110/<ds>/claim_sheet.json` before any verdict): furniture 685 claims (531 extracted, 154
    derived; 70 chunks), held-out 665 (530 / 135; 79 chunks), generality 216 (216 / 0; 31 chunks); no id
    shared by two claims. MLflow `claim_eval` runs `449a5306` (furniture), `a0225324` (held-out),
    `d7df0e3a` (generality).
  - Committed at `76cc57f`.
- **Part b: judging, review, scores and results (done 2026-10-07, $0: no API call, no pipeline run).**
  - **Blind judging:** 20 batches of whole documents (23-100 claims each), one Claude Opus 5.5 subagent per
    batch, each seeing only the rules and its batch; a batch checker (every claim answered in order, known
    labels and faults, every quote in its chunk by `norm`) passed on all 20. Blind labels (VALID /
    VALID_ALTERNATIVE / INCORRECT / AMBIGUOUS): furniture extracted 472 / 5 / 53 / 1, derived 138 / 0 / 16 / 0;
    held-out extracted 494 / 3 / 33 / 0, derived 38 / 0 / 97 / 0; generality 200 / 1 / 15 / 0.
  - **Lead review** (every INCORRECT, AMBIGUOUS and UNJUDGEABLE verdict and the seeded 10 % of the blind VALID
    ones: 184 / 243 / 35 items). Two consistency rulings where the batches split, applied to every claim of
    the pattern, each change recorded with its blind label:
    1. A recall's own link (`ADDRESSES_PROBLEM`, `COVERS_COMPONENT`) stored possible or conditional because the
       defect is hedged ("may") or the remedy says "if necessary": INCORRECT, `modality` (and `condition` when
       one is stored). The recall's link is certain; only the defect is hedged ("20V218000 ADDRESSES_PROBLEM
       inoperative", possible, from "The low pressure fuel pump may become inoperative"). 32 claims; 17 blind
       VALID changed (the batches split 4 strict, 2 lenient, 1 mixed).
    2. A quality only of the assembly stored as a quality of the product ("Linköping Bed EXHIBITS
       straightforward" from "Assembly was straightforward"): INCORRECT, `wrong_entity`; read as a sentence the
       claim says the bed is straightforward. 11 claims; 7 blind VALID changed (4 batches lenient, 2 strict).
       A claim whose object keeps the aspect ("assembly was super easy") stays VALID.
    Also a `condition` fault added to three single past events stored conditional ("WHEN THE CONTACT
    ATTEMPTED TO SHIFT ... THE GEAR SHIFTER CAME OUT": R77 reads such a when-clause as time), no label change.
    Every other reviewed verdict confirmed. The review sample was first drawn from the labels after the
    rulings; `load_verdicts` refused the file, and the sample of the blind labels was then reviewed.
  - **Scores:** `kg claim-eval --verdicts` on `out/r108_<ds>`, run in a worktree at `76cc57f` (clean): R109's
    `584f952` changes how derivation is replayed, so at the branch head the snapshot of R108's held-out build
    no longer passes C0. MLflow `claim_eval` runs `6a95cec4` (furniture), `1ec7cfcd` (held-out), `959090dd`
    (generality); verdicts and reports committed as `tests/gold/r110/<ds>/claim_verdicts.json`,
    `claim_report.json`. Test `test_r110_verdicts_answer_their_sheets_and_score_as_reported`.

  | Judge: Claude Opus 5.5; precision with Wilson 95 % | furniture | held-out | generality |
  |---|---|---|---|
  | extracted, **strict** (every stored field right) | 470/530 = **0.887** [0.857, 0.911] | 480/530 = **0.906** [0.878, 0.928] | 201/216 = **0.931** [0.889, 0.957] |
  | extracted, **content** (the triple right) | 490/530 = **0.925** [0.899, 0.944] | 520/530 = **0.981** [0.966, 0.990] | 207/216 = **0.958** [0.923, 0.978] |
  | derived, strict / content | 138/154 = 0.896 / 140/154 = 0.909 | **38/135 = 0.281** [0.212, 0.363] | none derived |
  | AMBIGUOUS left out | 1 | 0 | 0 |
  | weakest relation (strict) | `HAS_MEASUREMENT` 24/47 | `INSTALLED_IN` 38/135, `ADDRESSES_PROBLEM` 24/46 | `SERVICES` 5/7 |

  Read with care:
  - **Held-out derived claims, 97 of 135 wrong, one cause:** on the 29 recall records the document is about a
    Recall record, and derivation states "part INSTALLED_IN <recall number>" with the recall number typed
    `Vehicle` ("fuel pump assembly INSTALLED_IN 20V682000"); all 38 derived claims of complaint documents are
    right ("TRANSMISSION INSTALLED_IN 2015 FORD ESCAPE"). R109 (`584f952`, another session) removes exactly
    these: a derived claim's object must be a record of its object type.
  - **Held-out extracted: 38 of its 50 errors are modality or condition only** (ruling 1, and three when-clauses
    stored as conditions); the triples themselves are right in 520 of 530.
  - **Furniture: 23 of the 60 extracted errors are a reviewer's assembly time stored as a measurement of the
    product** ("Linköping Bed HAS_MEASUREMENT 2 hours" from "Assembly took about 2 hours with two people"; the
    bed "measures" 2, 2.5, 4 and 2 hours); 19 are type only (things that are no piece typed `Component`:
    "instructions", "tools", "fabric", "wood", "back angle"), and derivation repeats them as 16 wrong
    `PART_OF` claims ("instructions PART_OF Västerås Bookshelf"). Also: qualities of one aspect lifted onto the
    product ("Malmö Desk EXHIBITS perfect" from "The dimensions are perfect"), the review page as the subject
    ("Helsingborg Dresser Reviews EXHIBITS somewhat functional").
  - **Generality: numbers that mean something else** ("HP40-1183 HAS_MEASUREMENT 65 °C", the alarm limit, the
    reading being 71 °C; "works HAS_COST 12,000 pounds", a saving) and **times of another event** (6: "Rosa
    Delgado SERVICES HP40-1183, within two weeks", the deadline of a later replacement).
  - **Not comparable one to one with R66 / R68** (judge precision 0.990 furniture, 0.960 held-out Gemini;
    Claude Fable 5.1, the triple only): another judge model, rules that also judge truth, modality,
    condition, tone, time and end types, and a re-extracted build with R77's assertion fields. Content
    precision is the nearest number.
  - Precision only: no gold, so no recall of claims here (R68 / R77 measured it on sentence samples). Every
    claim was judged against its own text by one model family (Claude), which also wrote the rules.
  - **The sheets showed one type pair per relation** where the schema declares several (Found along the way).
    *This bullet was wrong* (corrected below): the judges read claims about a whole product, a pump or an
    event with another pair's description; 29 verdicts change when judged against their own pair.
- **Correction (2026-10-07, found in R111 part b; $0, no run).** The part b reading above missed that the hidden
  pairs have their own descriptions, and that the judges used the pair the sheet showed: "Product
  -[HAS_MEASUREMENT]-> Value: ... overall weight, **assembly duration**, or capacity metrics" (the sheet showed
  the Component pair's "specifications ... load ratings, or power values"), "Product -[EXHIBITS]->
  QualityAspect: ... or **assembly experience** of a product" (ruling 2 contradicted it), "Event -[HAS_COST]->
  Value: ... or **overrun**". Held-out declares every predicate once and is unchanged.
  - Every claim whose type pair the sheet did not show (furniture 308: Product `EXHIBITS` 267, Product
    `HAS_MEASUREMENT` 35, Component `MADE_OF` 6; generality 95) that was not VALID (furniture 39, generality 10),
    and a seeded 10 % of those VALID (27, 9; seed 110), were judged again blind by a fresh Opus 5.5 subagent per
    dataset on sheets regenerated with every pair (`0d5b30c`'s fix; the claims are byte-identical, only
    `relations` grew). The lead read every verdict and agrees; each change is recorded with the blind label.
  - Furniture: 28 of 39 INCORRECT -> VALID (the assembly durations stated as such, "Jönköping Coffee Table
    HAS_MEASUREMENT 30 minutes" from "took about 30 minutes", and the assembly qualities of ruling 2, which is
    withdrawn); 11 stay INCORRECT (a bound stored as the value: "less than 20 minutes" stored as 20 minutes, x4;
    a failed attempt as the duration: "spent 3 hours trying to assemble this dresser before GIVING UP", x2; the
    review page as the subject, x2; aspect praise lifted onto the product, x2; "leg attachment mechanism MADE_OF
    metal-to-metal fasteners"). Generality: 1 -> VALID ("Aldmoor peat project HAS_COST 18,000 euros", an
    overrun); 9 stay INCORRECT (the 65 °C alarm limit, the 4.5 mm/s vibration limit: the Pump pair holds
    measured values; "no run went above 60 l/s" stored negated; the saving; the three times of another event).
    All 36 sampled VALID stay VALID.
  - Rescored (`kg claim-eval --verdicts`, code at `76e7f22`, corrected sheets and verdicts uncommitted then;
    MLflow `1ab1bd20` furniture, `5c12b696` generality); held-out's scores are part b's.

  | Corrected (judge: Claude Opus 5.5) | furniture | held-out | generality |
  |---|---|---|---|
  | extracted, **strict** | 470 -> **498/530 = 0.940** [0.916, 0.957] | 480/530 = 0.906 (unchanged) | 201 -> **202/216 = 0.935** [0.894, 0.961] |
  | extracted, **content** | 490 -> **518/530 = 0.977** [0.961, 0.987] | 520/530 = 0.981 (unchanged) | 207 -> **208/216 = 0.963** [0.929, 0.981] |
  | derived, strict | 138/154 = 0.896 (unchanged) | 38/135 = 0.281 (R109 fixes the 97) | none |

  The "Found along the way" item "Extraction errors R110 measured" is corrected with it: assembly time stored
  as the product's measurement is what the schema declares, not an error.

### R111. Recall of today's claims against a reader's (done 2026-10-07; $0, no run)
The user, 2026-10-07 ("yes scope it", then "go"): R110 measured precision only; how much of what a careful
reader finds does today's graph store as claims, and is a miss the model's or the schema's? The last numbers
are R77's on the r77d builds (claims matched furniture 81/117, held-out 35/100, generality 22/108) and R68's
on older builds (coverage 0.486 furniture, 0.313 held-out; most misses had no fact type to go in).
- **Inputs, all committed:** R77's sample and gold (`tests/gold/r77/<ds>_assertion_{sample,gold}.json`: 61 /
  63 / 60 sentences, 117 / 100 / 108 reader claims, of them 72 / 67 / 66 in the random strata, written from
  the text before any output) and R110's claim sheets (`tests/gold/r110/<ds>/claim_sheet.json`: every stored
  claim of R108's builds, i.e. R103's claims). R103 kept r77d's documents and chunks (its test), so the
  sample's chunk ids are the sheets'. No build, graph or snapshot is read.
- **Split, one commit each:** (a) the recall sheet, its verdict rules and scores, the command, the matching
  rules, the three sheets ($0); (b) blind matching, the lead's review, scores and results ($0: Claude in the
  session, no API call, no pipeline run).
- **Part a scope:**
  - Structural move first, behaviour kept: `assertion._exact` becomes `exact_field`, typed by a small
    `StoredAssertion` Protocol, so a claim sheet's items are compared with the gold's labels by R77's code.
  - `validation/claim_recall.py` (pure): `recall_sheet` (each sentence with its reader claims and every
    stored claim of its chunk; a chunk with no stored claim shows none and no text); `RecallVerdict` (per gold
    claim: the matched stored claims whatever their truth, modality and condition, or R68's first cause with
    the fact type that could hold it); review rules as the shared verdict files (every miss and a seeded 10 %
    of the blind matches reviewed, changes kept with the blind outcome); `score_recall`: recall overall, on
    the random strata, per stratum, within the schema (no `no_schema_type` misses), misses per cause, and for
    matched claims the stored truth, modality and condition against the gold labels (exact, code).
  - `ClaimRecallStage` + `kg claim-recall --claims --gold --sample --dataset [--verdicts]`: one MLflow run per
    dataset (params: the three files and the verdicts with hashes; metrics: the sheet's counts and, judged,
    every rate with bounds and n, misses per cause; artifacts: sheet and report).
  - Matching rules `tests/gold/r111/recall_rules.md`, written before any verdict: R68's pass-2 causes in
    their order and R77's "whatever their truth, modality and condition", invented examples only.
- **Part a (done 2026-10-07, $0, no LLM, no Neo4j):** as scoped. Tests `tests/test_claim_recall.py` (5): the
  sheet (the join by chunk, a chunk without stored claims, a gold out of the sample's order refused), the
  verdict model (matched or a cause, a miss's schema type), the rules (a missing, changed or foreign answer, an
  unreviewed miss, a change that does not end at its outcome), the scores (strata, within the schema, causes,
  fields by code), the stage on invented files. README: the command and the module map.
  - **Sheets** (committed as `tests/gold/r111/<ds>/recall_sheet.json` before any verdict): furniture 61
    sentences, 117 reader claims, 601 stored claims shown (every sentence shows all its chunk's claims), no
    sentence without stored claims; held-out 63, 100, 414, one ("Owners may contact Nissan customer service at
    1-80...", a chunk storing no claim); generality 60, 108, 444, one ("The contractor finished on 25 August,
    a week ahead...").
  - Committed at `76e7f22`.
- **Part b: matching, review, scores and results (done 2026-10-07, $0: no API call, no pipeline run).**
  - **Blind matching:** 9 batches of whole sentences (16-22 sentences, 28-42 reader claims), one Opus 5.5
    subagent each, seeing only `recall_rules.md` and its batch; a batch checker (every reader claim answered in
    order and copied exactly, matched ids of the sentence's chunk, a cause or a match, a miss's schema type)
    passed on all.
  - **The sheet bug, found here:** the furniture and generality judges reasoned from the one type pair per
    relation the sheets showed (R110's "Correction"). Those two datasets' sheets were regenerated from the
    corrected claim sheets (only `relations` changed) and matched again blind by 6 fresh subagents; the first
    round (furniture 71/117, generality 23/108 blind) is set aside, not committed. Held-out declares each
    predicate once and kept its round.
  - **Cross-check against R110 (code):** a stored claim R110 judged wrong in its content (`not_in_text`,
    `wrong_entity`, `wrong_relation`) cannot be the claim that holds a reader's claim. 2 matches rested on one
    and became misses ("the gas gauge does not indicate full after the auto-shutoff" on the gauge-fault claim;
    "assembly took less than 20 minutes" on "Stockholm Chair HAS_MEASUREMENT 20 minutes"). A `truth` fault does
    not block a match (R111 matches whatever the truth and code compares it): "no run went above 60 litres per
    second in May" stays matched to the flume's 60 l/s claim, whose stored denial R110 reads as wrong.
  - **Lead review** (every miss and a seeded 10 % of the blind matches: furniture 51, held-out 76, generality
    88): consistency rulings where the batches split, each recorded: an overall evaluation of a product ("so
    satisfied with my purchase", "highly recommend") is `extraction` like "I absolutely love my Stockholm
    Chair" (Product `EXHIBITS` carries sentiment; star ratings stay `no_schema_type`); an item supplied with a
    product ("they give you a little allen key") is `no_schema_type`, as R110 judged "tools PART_OF" wrong; a
    job title kept only as an employer ("Ines Barros is Calder Pumps' service manager") is a miss like
    Okafor's; a cause between a defect and an experience ("assembly was frustrating because the holes didn't
    align") is `extraction` like its twin. 8 changes in all; every other verdict confirmed.
  - **Scores:** `kg claim-recall --verdicts` (code at `df7b471`; the regenerated sheets and the verdicts
    uncommitted then, committed here), MLflow `claim_recall` runs `92a987a3` (furniture), `35345dce`
    (held-out), `6925abd1` (generality); reports `tests/gold/r111/<ds>/recall_report.json`. Test
    `test_r111_verdicts_answer_their_sheets_and_score_as_reported` (each sheet rebuilds from its inputs).

  | Judge: Claude Opus 5.5; recall with Wilson 95 % | furniture | held-out | generality |
  |---|---|---|---|
  | **random strata** (the estimate over the text) | **42/72 = 0.583** [0.468, 0.690] | **19/67 = 0.284** [0.190, 0.401] | **12/66 = 0.182** [0.107, 0.291] |
  | random strata, **within the schema** (`no_schema_type` left out) | 42/61 = 0.689 [0.564, 0.791] | 19/35 = 0.543 [0.382, 0.695] | 12/27 = 0.444 [0.276, 0.627] |
  | all strata (with the cue sentences) | 74/117 = 0.632 | 27/100 = 0.270 | 23/108 = 0.213 |
  | random misses: no fact type / the model's own (`extraction`) / other | 11 / 17 / 2 | 32 / 5 / 11 | 39 / 8 / 7 |
  | matched claims keeping the reader's truth, modality, condition (code, exact) | 69/74, 73/74, 1/1 | 25/27, 26/27, 9/9 | 23/23, 22/23, - |
  | R77 (r77d build, Fable 5.1, all strata) | 81/117 | 35/100 | 22/108 |

  Read with care:
  - **Most misses are the schema's, not the model's, except on furniture.** Held-out's random misses are 32 of
    48 outside every fact type (filing dates, phone numbers, remedies such as "dealers will replace the audio
    display unit if necessary", who issued a recall), generality's 39 of 54 (job titles, awards, budgets and
    deadlines, what a baker makes, a valve's state). Furniture's schema holds nearly all of a review, and 17
    of its 30 misses are the model's: a half dropped ("resistant to water rings and scratches" stored as water
    rings only), a capacity dropped ("sturdy enough to support my husband (who's over 250 lbs)" stored as
    "sturdy"), overall evaluations not stored ("The Linköping Bed is a great addition to our guest room").
  - **Held-out's other misses lose a detail no slot carries** (`role`, 7: the build windows of "recalling ...
    2015 Ford Escape vehicles manufactured April 1, 2014, to June 12, 2015", as R68 found) and **counts**
    ("braked on its own on two separate occasions", stored once).
  - **What is stored keeps its assertion:** of the 124 matched claims, 117 keep the reader's truth and 121 the
    modality exactly; the misses are about what is not stored, not about stored claims losing "not" or "may".
  - **Against R77** (all strata, the r77d build's claims, Fable 5.1 judging, R77's matching): furniture 81 ->
    74, held-out 35 -> 27, generality 22 -> 23; a re-extracted build, another judge and R111's cross-check
    against R110 make it no paired comparison, and the intervals overlap.
  - ~66-72 random reader claims per dataset: about ±10 points. The reader's claims, the matching and the
    verdicts come from one model family (Claude); the reader's claims were written before R103's claims
    existed. No gold correction: every reader claim judged as written (the judges flagged none as wrong).
- **Results snapshot** of R110 (corrected) and R111 for readers outside this roadmap:
  [docs/evaluation/results_2026-10-07_claims.md](docs/evaluation/results_2026-10-07_claims.md).
- **R111 done 2026-10-07** ($0). Next: the user's decision. Recall within the schema is 0.44-0.69; what limits
  it most is the schema on held-out and generality (no fact type for remedies, titles, dates of documents) and
  the model's dropped halves and evaluations on furniture.

### R112. Part 1 frozen: the final builds named, the tests on their own Neo4j (done 2026-10-08; $0, no run)
The user, 2026-10-08: the graph builder (Part 1) is judged by whether it builds correctly (merges and
deduplication), and on that reading it is done; work moves to the query engine (Part 2). Before that, freeze
Part 1: tag the commit, name the three final builds, and give the tests their own Neo4j, because a query
engine queries the live working graph and `uv run pytest` emptied it (the open item of R11; it cost one void
$0.166 `kg qa` run). The old query engine's check on the final graph was proposed and declined by the user
("not interested in the old query engine right now").
- **Scope:** `docker-compose.yml` (a second service `neo4j-test`: bolt on 7688, APOC, no named volume, small
  memory), `tests/conftest.py` (the `driver` fixture connects to 7688 with fixed credentials, never to
  `Settings().neo4j_uri`, and fails before any wipe when `NEO4J_URI` is the test server), README (setup,
  development, the final builds), CLAUDE.md (the two command comments). No source code changes, so no
  behaviour of the pipeline changes.
- **The final builds** (also in README, "Evaluation results"): furniture `out/r108_furniture` (R108),
  held-out `out/r109_heldout` (R109; R108's held-out build fails C0 at today's code since R109 changed
  derivation), generality `out/r108_generality` (R108). Their commands and MLflow runs are in
  `tests/gold/r108/runs.json` and `tests/gold/r109/runs.json`; `out/` is git-ignored, so the builds exist in
  this checkout only. Git tag `part1-final` on this step's commit (local, not pushed).
- **C0 at the frozen code** (before any edit, at `6ba7a9a` with only the user's `.claude/settings.json`
  dirty; `kg audit-snapshot`, offline, no LLM, no Neo4j, into `out/r112_c0/<ds>`): fidelity passed on all three
  (MLflow `f3a6311d` furniture, `acb838b4` held-out, `83e5f31a` generality); the rebuilt `snapshot.json` and
  `code_checks.json` are byte-identical to each build's own `audit/` files. Source code is unchanged since,
  so this holds at the tag.
- **Verified:** baseline with the working Neo4j stopped 780 passed, 88 skipped (every `neo4j` test skips);
  after the change, with both servers up, **868 passed, 0 skipped**: every `neo4j` test ran on 7688. A marker
  node written to the working graph before the run was still there after it (8 nodes, 1 marker; removed
  after). With `NEO4J_URI=bolt://localhost:7688` the fixture fails with "the tests would wipe the working
  graph" and the test database keeps its nodes (7 before and after). `uv run ruff check .` clean.
- **R112 done 2026-10-08** ($0). Part 1 is frozen at `part1-final`; its four known limitations stay as recorded
  below. Next: Part 2, the query engine, scoped with the user.

### R113. The working graph: the held-out final build loaded into Neo4j (done 2026-10-08; $0 logged)
The user, 2026-10-08 ("alright go ahead" to loading one of the final builds into Neo4j): Part 2 queries a live
graph, and after R112 the working Neo4j held only 7 test nodes. Neo4j Community holds one database, so one
dataset at a time; held-out first: the most varied sources (CSV, JSON, NDJSON, prose, record documents) and
the cheapest rebuild (R109: $0.0037).
- **How:** R109's recipe into a new folder `out/r113_heldout`, so the frozen `out/r109_heldout` is only read:
  plan, text schema and profile pinned from it, claims replayed from `out/r103_heldout` as in R109, the mention
  pass on an invalid key (a cache miss would fail instead of paying), then resolve and attach. Commands, runs
  and checks in `tests/gold/r113/runs.json`; logged counts `tests/gold/r113/heldout_logged.json`; test
  `test_r113_the_working_graph_is_the_frozen_held_out_build` (tests/test_audit.py).
- **Result:** every LLM call a cache hit (mention pass 81/81, resolve 724/724); MLflow logs $0 (the 9 Gemini
  embedding calls report no tokens, so `prices.yaml` cannot price them: well under a cent). All 28 logged
  counts equal R109's; `triples.jsonl`, `mentions.jsonl` and `audit/code_checks.json` byte-identical; C0
  passed (`kg audit-snapshot`, MLflow `469018d3`); `resolve.json` and `audit/snapshot.json` equal but for the
  77 record references' Neo4j element id (below). Neo4j now holds 1,041 Mention, 568 Observation, 327
  Concept, 243 Individual, 81 Chunk, 34 Document, 29 Recall, 25 Complaint and 5 Vehicle nodes, 7,428
  relationships.
- **R113 done 2026-10-08.** Next: Part 2, scoped with the user.

### R114. PostgreSQL tables as a structured source (done 2026-10-08; $0, no run)
The user, 2026-10-08 ("Make a plan to support SQL ... we would rather use postgreSQL as it has pgvector"):
read tables from a database, not only from files; PostgreSQL, because pgvector can hold vectors later. No
append or update layer (the user: "not desirable right now"). Branch `postgres-source`, after `anchor-graph`
was fast-forwarded into `main` and pushed (Part 1 on GitHub, tag `part1-final`).
- **Scope:** staging is the only entry of structured data: every later consumer (profiler, plan check,
  importer, record documents, audit, anchor, build folders) reads `out/staging/<name>.csv`. So a database
  enters at staging and nothing downstream changes; files keep their behaviour exactly.
- **How:** a `TableSource` port in `structured/staging.py` and its adapter `structured/postgres.py`
  (`PostgresTables`, the only psycopg user): one schema's tables and views, sorted, each exported with
  `COPY ... TO STDOUT (FORMAT csv, HEADER)` in a read-only session in UTC and ISO dates, arrays as JSON text
  (the form `json_to_csv` gives a list). With `POSTGRES_SCHEMA` set, `kg profile` stages the schema and
  reports the data dir's CSV/JSON files as skipped; documents still come from the data dir. A table name that
  is no safe file name is skipped and reported; an unreachable server, a missing or an empty schema raise
  `DataSourceError` (an empty profile would make the plan stage skip without a word), before the last
  staging is wiped. Settings `postgres_url` (default: the new `postgres` service; refused in presets, it can
  hold a password) and `postgres_schema`; the profile stage logs `structured_source` (the server, database
  and schema, never the password). Docker: `postgres` (port 5434, volume) and `postgres-test` (5435), image
  `pgvector/pgvector:pg17`. Prompts unchanged: a table arrives as `<table>.csv`, so the plan prompt's "CSV
  files" stays true and its hash does not move.
- **Verified:** 12 new tests (11 in `tests/test_postgres_source.py`, one in `tests/test_config.py`); the
  `postgres` marker and the `pg_schema` fixture (a fresh schema per test in the test server) as for Neo4j.
  The furniture CSVs loaded into PostgreSQL as text give the same profile as the files but for the sample
  values (an open issue, below) and the same domain graph: every node and relationship with its properties
  (`construct_domain_graph` with `tests/gold/domain_plan.json`). Typed columns (integer, numeric, boolean,
  date, timestamptz, jsonb, text[], text with a comma, a quote and a line break) come out as BIGINT, DOUBLE,
  BOOLEAN, DATE, TIMESTAMP WITH TIME ZONE and VARCHAR. CLI check, no LLM: the furniture tables in the working
  PostgreSQL (schema `furniture`, left there to try), `POSTGRES_SCHEMA=furniture kg profile --out
  out/r114_pg_profile`: 5 tables, 358 rows, the 4 foreign keys at 100 %, 5 sample CSVs reported skipped,
  MLflow param `structured_source = postgres localhost:5434/kgbuilder schema furniture`.
- **R114 done 2026-10-08.** Next: R115.

### R115. Declared keys from the PostgreSQL catalog (code done 2026-10-08; $0, no run; plan comparison open)
The user, 2026-10-08, chose this as the second step of PostgreSQL support: a database states its primary and
foreign keys, where the profiler only guesses them from the data (CLAUDE.md: what can be computed exactly is
computed in code).
- **How:** `PostgresTables.declared_keys()` reads the schema's primary and foreign keys from `pg_constraint`
  (not information_schema, which cannot pair the columns of a composite key). Only single-column keys inside
  the schema fit one column of a staged file; composite keys and foreign keys to another schema are listed
  in `skipped` with the reason. Staging writes the keys of the staged tables, renamed to their files, to
  `out/staging/declared_keys.json` (build folders copy `staging/`, so offline replays keep them). The
  profiler marks `ColumnProfile.primary_key` and `ForeignKeyCandidate.declared`; both are left out of the
  JSON when unset (pydantic `exclude_if`), so a profile of files is byte for byte what it was. A declared
  foreign key the value search misses (dangling rows, other inferred types, a target with nulls) is added
  with its inclusion measured as text; declared keys sort first. Rows are exported in primary key order.
  The plan prompt has a `{declared_rule}` slot filled with `DECLARED_KEYS_RULE` ("prefer them over
  undeclared candidates, even when a declared foreign key's inclusion is below 1") only when the profile
  carries declared keys: the prompt for files renders exactly as before, so its cached replies stay valid,
  while `prompt_version` (a hash of the template) changes once. New params `declared_keys`,
  `declared_keys_rule_version`; new profile metrics `declared_primary_keys`, `declared_foreign_keys`;
  `kg profile` prints "(primary key)" and "(declared)".
- **Verified:** 7 new tests (`tests/test_declared_keys.py`) and the rule added to the corpus-word test:
  staged key names and skips, the marks and a dangling declared key (inclusion 0.6667, kept), a file
  profile without marks whose plan prompt equals the pre-R115 template's rendering, the rule present for a
  declared profile, the constraints PostgreSQL reports (composite primary key and cross-schema foreign key
  skipped), primary key order, and the furniture tables with their 4 primary and 4 foreign keys declared:
  the same profile as the files but for the marks (and the samples, an open issue). CLI check, no LLM: the
  working PostgreSQL's schema `furniture_keys` (the furniture tables with those constraints, left there),
  `POSTGRES_SCHEMA=furniture_keys kg profile --out out/r115_pg_profile`: 4 primary keys and 4 foreign keys
  declared (MLflow `f067b7ec`).
- **Open:** the prompt rule is unmeasured. The comparison is one `kg plan` on `furniture_keys` against the
  file-based plan (Gemini plan stage, about $0.05), only with the user's permission. On furniture the value
  search already finds the 4 foreign keys at 100 %, so the expected result is the same plan: the run checks
  that the rule does no harm, not that it helps.
- **R115 code done 2026-10-08.** Next: the plan comparison if the user agrees; then Part 2.

### Plan R116-R125: hybrid retrieval, and two node representations compared fairly (accepted 2026-10-08)
The user, 2026-10-08, starting Part 2: hybrid search and retrieval (graph, dense, maybe lexical), and what to
embed besides the chunks; then "make a plan to implement that in a modular way as we will also experiment
with another approach ... and later we will run both approaches against a benchmark to measure fairly"; then
the second approach: LLM-generated node summaries, compared against deterministic node cards ("Do not
implement LLM summaries immediately. Complete the existing deterministic approach first, but design the
architecture now"). The full plan with files, types and tests: `docs/tasks/hybrid-retrieval.md` (local).

Why (a real failure): held-out H17 ("Which recall campaigns concern the back-up camera of a vehicle whose
owner complains that the back-up camera stopped working?", expected none). R73b's vector system retrieved
only recall chunks and answered 19V576000 and 25V695000, camera recalls of vehicles whose owners never
mention a camera. The question names no vehicle, so the name linker has nothing to link; the fact that
answers it is a claim on the Outback, `SYSTEM FREEZES UP COMPLETELY -AFFECTS_COMPONENT-> BACK UP CAMERA`
(via `HAS_OBSERVATION`), whose recalls are FUEL PUMP, STRUCTURE, FUEL PUMP. Bare node names embed poorly
(`resolution/matchers.py`: "short names of different things often have very similar embeddings").

- **What is embedded besides chunks:** a text per record and canonical entity (its title, label, aliases,
  key properties, relations with neighbour names, a few claims) and one sentence per claim. Lexical (BM25)
  indexes over chunk, card and claim text; the ranked lists are fused by rank (reciprocal rank fusion),
  never by a similarity threshold, the rule of `query/names.py`.
- **Two node representations, one engine.** A, deterministic node cards: code renders the node's verified
  graph evidence with a fixed template (built and measured first, R118-R122). B, LLM node summaries: code
  gathers the same evidence, an LLM writes a grounded summary (R123-R125, after A; hypothesis: richer
  vectors find the right starting nodes better). B is one `NodeRepresentation` (name, version,
  render(evidence) -> card texts); evidence gathering, index writer, embedding, retrievers, fusion, reader
  and benchmark are shared. Card and summary text only ranks: the reader sees source chunks only.
- **The user's decisions (2026-10-08):** (a) the indexes live in Neo4j as additive, deletable
  `:RetrievalUnit` nodes and indexes; frozen Part 1 nodes are never modified; (b) each approach is tuned on
  furniture only; held-out and generality are measured, never tuned on; (c) B is designed now, built after
  A is measured; (d) A and B share the evidence, the embedding model, the retrieval settings and the
  benchmark.
- **Storage:** `(:RetrievalUnit:NodeCard:<Rep>Card)-[:CARD_OF]->(record | :Concept | :Individual)`, one
  label and one vector and full-text index per representation (Neo4j 5 vector indexes are one label each and
  cannot pre-filter), so A and B coexist on the same graph; `(:RetrievalUnit:ClaimSentence)-[:SENTENCE_OF]->
  (:Observation)`, shared. Every card stores the hash of its evidence. The planner's schema and the graph
  digest leave the index layer out (`query/graph_schema._labels` lists every label).
- **Claim semantics:** truth is never left to the vector (the English analyzer even drops "not"). The claim
  retrievers read `truth`, `negation`, `modality`, `hedge`, `condition`, `triple_truth` from the
  `:Observation` and carry them in the trace; a filter on them applies only when the caller knows the
  question's polarity (the retrieval-only system filters nothing); claims of the opposite truth on the same
  canonical subject, predicate and object stay next to a returned claim. Card claim lines carry code-made
  tags (`denied`, `conditional: ...`, `hedged`) and conflict counts.
- **Metrics:** Seed Recall@K (the R89 targets placed by `anchor/targets.TargetPlacer`; placed nodes are
  record refs and canonical ids, the ids the cards use), Evidence Recall@K and Complete@K (gold chunks),
  multi-hop answer accuracy (exact and judge, with n), groundedness (A by construction and a test; B a code
  check and Claude's verdicts on a stratified sample), retrieval latency p50/p95, cost; exact McNemar on
  each paired measure.
- **Fairness contract:** the same graph (`graph_digest` param), the same evidence (per-card hash), the same
  questions and targets (hashes; a gold chunk missing from the loaded graph refuses the run), one reader and
  k for every system, the same embedding model and sealed retrieval settings. Tuning is recorded, not
  enforceable: grids pre-registered here, the choice committed as `config.py` defaults (the seal) and in
  `tests/gold/r121/` and `r124/tuning.json`; held-out and generality runs take no overrides and descend from
  the seal; their cards and summaries are not opened before their final run. Known bias: the shared fusion
  settings are sealed with A, as B does not exist yet; so the headline A-vs-B number is Seed Recall@K of card
  retrieval alone, which no fusion setting touches, with the full hybrid reported beside it.
- **Pre-registered grids (fixed before any number):** R121, A and the shared settings: mixes M1 chunk dense
  + chunk lexical (the no-graph control), M2 = M1 + claims, M3 = M1 + cards, M4 = all six, M5 = M4 + the
  name-linker route; `rrf_k` in {10, 60}; depth 20; criterion furniture Complete@5, ties to Seed Recall@5,
  then the smaller mix, then `rrf_k` 60. R124, B's own knobs only (the summary length cap, at most two
  prompt variants); criterion furniture Seed Recall@5 of card dense retrieval alone.
- **Steps, strictly one after another** (`implement-step`, one commit each):
  - **R116** QA systems built by name from shared parts ($0, refactor).
  - **R117** The retrieval benchmark without the reader, `kg retrieve-eval`: evidence and seed recall,
    latency, the graph digest, the name-linker route as `graph_retrieval`; part b the furniture baselines
    (guarded, about $0).
  - **R118** Node evidence, the representation seam, deterministic cards and claim sentences, `kg units`
    ($0, no LLM).
  - **R119** `kg index --cards <rep>`: embedded units and indexes in Neo4j; unchanged units keep their
    vectors (one guarded smoke run).
  - **R120** Hybrid retrievers, claim semantics, fusion, the `hybrid` system (one guarded smoke run).
  - **R121** A and the shared settings tuned on furniture, then sealed (asked).
  - **R122** A measured on held-out and generality against vector and graph retrieval (asked per dataset).
  - **R123-R125** (later) B: summaries with a code grounding check and a fallback to the card; tuned on
    furniture; A against B on held-out and generality (each run asked).
- **Defaults chosen:** the working Neo4j holds furniture from R117 part b until R122 (Community holds one
  database); cards map to chunks per card in turn (round-robin), as pooling all reached chunks by cosine
  would bring back H17's camera recalls; `kg qa` keeps asking the original three systems by default.

### R116. QA systems built by name from shared parts (done 2026-10-08; $0, no run, refactor)
- **Scope:** how a QA system is assembled, behaviour unchanged. `pipeline/qa_stages.py` chooses a system by
  an `if` ladder (`build_system`) and two name tuples (`SYSTEMS`, `PLANNED`) that `_system_params`,
  `_log_prompts` and `_frozen_file` each test again; every new approach would add a branch in four places.
  A new `pipeline/qa_systems.py` (Factory) maps a name to a `SystemSpec`: how to build the system from the
  parts every system shares, the params and prompts that decide its answers beyond the shared ones, and
  whether it is a plan system. The shared parts (reader, k, embedder, store, model) are built once, so two
  systems differ only in what their spec builds on top. `query/systems.py`: a `ReadingSystem` (the source's
  best k chunks, read by the reader) replaces the `answer` of `VectorBaseline` and `GraphRetrieval`, which
  become chunk sources only.
- **How:** `SystemSpec(name, build, params, prompts, planned)`; `prompts` maps a short name to a prompt,
  logged as `prompts/qa_<name>.txt` and versioned as `<name>_prompt_version`, the names the runs already
  used. `reading(name, source)` makes a spec for a reading system (today `vector`). `qa_parts` builds the
  model at the settings' thinking level, the reader, the store, the embedder and the plan once per stage;
  `system_params`, `log_prompts`, `check_system` and `build_system` read the spec. `qa_stages.py` lost the
  tuples, the ladder and the per-system branches (336 to 232 lines: the stages and their metrics); `kg qa
  --system` and `kg ask --system` list the registry's names in their help.
- **Verified:** 7 tests written first and green on the old code (`tests/test_qa_systems.py`), unchanged after
  the move but for the prompt test calling the now public `log_prompts`: each system's params equal a dict
  written from the prompt constants and the settings (not from the registry); each system's prompt artifacts
  and their texts, in order; only the plan systems replay `--plans`. `tests/test_query.py` builds the reading
  systems as `ReadingSystem(name, source, reader, k)` with the same assertions; `test_query_graph.py` and
  `test_query_exact.py` unchanged and green. 896 passed (889 + 7), ruff clean. `kg qa --help` defaults
  unchanged (graph, vector, records_vector).
- **One difference, in an error path only:** with neither an embedding model nor a construction plan,
  `records_vector` used to report the missing plan first; `qa_parts` now reports the missing embedder first
  (both stop the run before any call).
- **R116 done 2026-10-08.** Next: R117, the retrieval benchmark without the reader.

### R117. The retrieval benchmark without the reader: `kg retrieve-eval` (done 2026-10-08; $0 logged)
Plan R116-R125, step 2: measure retrieval apart from answering, so a retrieval change (R120's hybrid) shows
in what it retrieves before the reader's own variation can hide it, and the baselines exist before any
hybrid code.
- **Scope:** a new stage pair and its scorer; the sources and the graph store give what it needs; `kg qa`
  gains the graph check. No hybrid or card code, no prompt change.
- **How:**
  - `validation/retrieval_scores.py` (pure): `RetrievalOutcome` (gold chunks, ranked chunks up to the largest
    budget, seeds, placed targets, latency); `score_retrieval` gives at every budget K (`retrieval_budgets`
    = [5, 10], new setting) Evidence Recall@K (gold chunks in the top K, pooled), Complete@K (questions with
    all of theirs), Seed Recall@K (R89 targets with a node among the top K seeds; a target the build lacks
    is a miss, as in C2) and Seed found@K, each with n and Wilson interval overall and n per type, plus
    latency p50/p95 (nearest rank). A system without seeds (vector) has no seed scores rather than zeros.
    `compare_retrieval(a, b, k)` pairs Complete@K and Seed found@K with McNemar, and refuses two reports
    whose fingerprint (gold hash, targets hash, `graph_digest`, embedding model) differs.
  - `validation/paired.py`: public `compare_pairs(pairs, a, b)`; `compare_outcomes` delegates to it (same
    report, tested). `anchor/criteria.py` is not imported.
  - `graph/digest.py` `graph_digest`: a 12-hex hash of the label counts, relationship type counts and every
    chunk's id and text; content, not element ids, so a rebuild of one recipe gives one digest.
    `pipeline/qa_graph.py` `check_graph`: the digest, after refusing (`EvaluationError`) a gold whose
    evidence chunks the loaded graph lacks (`GraphStore.chunks` drops unknown ids without a word).
  - Seeds: `NodeName.ref` (required): `record_ref(label, key)` for a record (`read_domain_nodes` now also
    reads `toString(key)`, as `read_record_keys` does), the canonical id for a kind. `RetrievalTrace.seeds`
    (additive, default empty, so older answers files load): `GraphRetrieval` gives its links' refs in link
    order (spelling first, then meaning).
  - `qa_systems.py`: `SourceParts` (store, embedder, plan, settings; `QAParts` extends it with the model
    and reader); a source builder takes a depth (`kg qa` passes k, pinned by a test written first and green
    on the old code; retrieve-eval passes max(budgets)); `graph_retrieval = reading(GraphRetrieval)` with
    its params (link fuzzy, neighbours, hops); `DEFAULT_SYSTEMS = (graph, vector, records_vector)`, the
    `kg qa` default, so a registered system never adds a paid run by itself; `check_source` refuses a plan
    system (no single ranked list).
  - `pipeline/retrieval_stages.py`: `RetrieveEvalStage(system)` (run `retrieve_eval_<system>`): checks the
    graph first, places the targets with `audit.build_snapshot` + `anchor.TargetPlacer` on `--build` (and
    refuses a target gold written for another QA gold), ranks the questions one at a time (Gemini embed has
    no retry), counts `embedded_texts`/`embedded_chars` through a counting decorator (Gemini reports no
    embedding tokens); params: the source's settings, budgets, gold/targets with hashes, build, data, chunk
    settings, `graph_digest`; artifact `retrieval_<system>.json` with every outcome. `RetrieveCompareStage`
    (`kg retrieve-compare A B`): every shared budget, `retrieve_compare.json`. CLI: `kg retrieve-eval GOLD
    --targets T --build B --data D --system S` (`--data`: the snapshot needs the dataset);
    `.claude/hooks/run_guard.py` adds `retrieve-eval` (retrieve-compare reads files only).
- **A small behaviour change in `kg qa`:** it now logs `graph_digest` and refuses, before any model or
  embedding call, a gold whose evidence chunks the loaded graph lacks (another dataset loaded would have
  scored every system against chunks it cannot return). Checked before committing: every cited chunk is
  in each final build (furniture 33 questions with chunk evidence, 42 chunks; held-out 28, 26; generality
  38, 24; none missing, from each build's `audit/snapshot.json`), and the working graph (held-out, R113)
  passes the stage's own check (`graph_digest` bc7c5a1efe3d) while furniture's gold on it is refused (42 of
  42 missing). On that graph every one of the store's 59 record refs and 570 canonical ids is an id of the
  build's snapshot, none missing either way.
- **Verified:** 24 new tests: the pin (`kg qa`'s vector system asks for exactly k), a source asked for 10,
  defaults and the CLI default, `graph_retrieval` params and prompts, plan systems refused
  (`test_qa_systems.py`); seeds by stable ref, spelling first (`test_query.py`); the store's refs
  (`test_query_graph.py`); `kg qa` logs the digest and refuses a missing chunk with no model call; the
  scorer (`test_retrieval_scores.py`, 10); the stage end to end on a hand-made graph of a small build,
  refusal before any embedding, the digest equal over a rebuild and changed by a text, a label, an edge or
  a lost chunk, and the compare stage (`test_retrieval_stages.py`, 4); the guard. 920 passed (896 + 24),
  ruff clean. `kg qa --help` defaults unchanged.
- **Part b** (the user, 2026-10-08: "yes run it"): the furniture baselines before any hybrid code. Commands,
  runs and checks in `tests/gold/r117/runs.json`; tests `test_r117_the_working_graph_is_the_frozen_furniture_build`
  (tests/test_audit.py) and `test_r117_baselines_load_pair_again_to_the_committed_comparison_and_seed_by_stable_ids`
  (tests/test_retrieval_scores.py).
  - **The load:** R108's recipe into `out/r117_furniture` (preset `quality`; plan, text schema and profile
    pinned from `out/r77d_furniture`, claims replayed from `out/r103_furniture`, the mention pass on an
    invalid key). Every LLM call a cache hit (mention pass 70/70, resolve 790/790); MLflow logs $0 (10
    unpriced embedding calls). All 27 logged counts equal R108's (`tests/gold/r117/furniture_logged.json`);
    `build_report.json`, `triples.jsonl`, `mentions.jsonl` and `audit/code_checks.json` byte-identical;
    `resolve.json` equal but the 63 record targets' element ids; C0 passed (MLflow `509ce19f`). The working
    Neo4j now holds furniture (1,015 Mention, 685 Observation, 448 Concept, 107 Individual, 88 Component, 70
    Chunk, 64 Assembly, 20 Supplier, 10 Document, 10 Product; 5,996 relationships; `graph_digest`
    392a170ecc10) until R122.
  - **The baselines** (`kg retrieve-eval`, MLflow `366fc0d5` vector, `81ee3aa8` graph_retrieval; reports
    copied to `tests/gold/r117/`). 68 questions, 33 with chunk evidence (73 gold chunks counted per
    question), 50 with targets (86 targets, none unplaced). Rates over those n, no judge involved:

    | System | Evidence@5 | Evidence@10 | Complete@5 | Complete@10 | Seed Recall@5 | Seed Recall@10 | Seeds found@5 | latency p50 / p95 |
    |---|---|---|---|---|---|---|---|---|
    | vector | 0.548 (40/73) | 0.726 (53/73) | 0.606 (20/33) | 0.788 (26/33) | - | - | - | 444 / 581 ms |
    | graph_retrieval | 0.644 (47/73) | 0.795 (58/73) | 0.727 (24/33) | 0.848 (28/33) | 0.849 (73/86) | 0.942 (81/86) | 0.780 (39/50) | 584 / 847 ms |

  - **Paired** (`kg retrieve-compare`, MLflow `e84ea03d`): Complete@5 only graph 4 (F16, F20, F24, F26), only
    vector 0, p = 0.125; Complete@10 only graph 3, only vector 1 (F06), p = 0.625. Not beyond one sample's
    variation at n = 33. Per type at 5, graph_retrieval completes 2 more disambiguation questions (6/6
    against 4/6) and 1 more structured_filter and negation_sensitive question each.
  - **What the seeds show** (for R120): the name linker's spelling links come in node order, not by
    relevance, so a name many records share fills the top 5 seeds with the wrong ones. "drawer rails" in
    F01, F02 and F07 links Assembly:A-1021, A-1062, A-1070 and Component:S-1078 before the Helsingborg
    Dresser's own rails, Component:S-1085, which comes 6th: 13 of the 86 targets are missed at 5, 5 at
    10. A seed ranking (the card retriever of R120) is where hybrid retrieval can gain on seeds.
  - **Cost:** $0 logged. Embeddings unpriced: vector 68 calls (4,446 characters), graph_retrieval 76 calls
    (737 node names once and 68 questions, 14,342 characters).
- **R117 done 2026-10-08.** Next: R118, node evidence and deterministic cards.

### R118. Node evidence, the representation seam, deterministic cards and claim sentences: `kg units` (done 2026-10-08; $0, no model)
Plan R116-R125, step 3: the text each node is found by, built from the node's verified graph evidence, with
the seam where LLM summaries (B, R123) will plug in. Nothing is embedded or written to the graph yet (R119).
- **Scope:** a new feature package `hybrid/` (pipeline -> hybrid -> graph, llm.base, core and other packages'
  models; nothing imports it but the pipeline), the stage `kg units`, three settings. No query or prompt
  change.
- **How:**
  - `hybrid/evidence.py`: `NodeEvidence(ref, kind, label, title, aliases, properties, relations, claims,
    claims_total)`, `Neighbours(type, outgoing, label, count, names)`, `EvidenceClaim(id, predicate,
    sentence, stated, denied, modality, hedge, condition, chunk_id)`, `RenderedCard(ref, text, evidence_hash,
    truncated)`, `evidence_hash` (16 hex over the sorted JSON). Against the plan's sketch, a claim carries
    `stated`/`denied` counts instead of one `truth` and a `conflicts` count: the observations of one
    canonical triple (with the same modality and condition) on one node are one claim line, and "stated 2,
    denied 1" needs both numbers.
  - `hybrid/unit_sources.py` (Repository, read-only): records per plan node rule (key and plan columns as
    text, but the name and the prose columns `text/record_documents.prose_columns` finds, which the stage
    passes in), the names their mentions write, relationships per plan rule and direction with the names
    sorted and cut to `index_card_names` in Cypher and the full count; individuals and concepts with their
    names and the claims they are an end of (grouped by predicate, direction and the other end's type);
    the claims records and individuals hold (`HAS_OBSERVATION`; concepts hold none), read with canonical
    ends, `triple_truth`, `modality`, `hedge`, `condition` (defaults for an observation without them) and
    the chunk of the FROM edge; capped at `index_card_claims`. One `ClaimSentence` per observation.
  - **Claim order (a design choice made on the furniture cards, the tuning set):** each predicate's best
    supported claim first, then the second of each. By support alone the Helsingborg Dresser's three lines
    were two derived "part of" claims, which its `PART_OF <- Assembly (8)` line already says, and one quality
    claim; now they read "Drawer Rails (Component) part of Helsingborg Dresser (Product)", "Helsingborg
    Dresser (Product) exhibits pain to put together (QualityAspect)", "Drawer Rails (Component) made of
    metal (Material)".
  - `hybrid/claims.py`: `claim_text` = "{subject} ({type}) {predicate in words} {object} ({type})" from the
    canonical names; `predicate_words` ("HAS_DEFECT" -> "has defect"). Truth stays out of the text.
  - `hybrid/cards.py` `TemplateCards` (A, Strategy): title (label), "Also called", properties, one line per
    relation `TYPE -> Label (n): a, b (+k more)` (`<-` for incoming), "Claims (shown of total):" and claim
    lines tagged from the fields (`denied`, `hedged` for modality possible, `conditional: <condition>`,
    "stated s, denied d" when they disagree). Over `index_card_max_chars` it drops claims (last first), then
    relations (smallest first), then cuts at a space. `version` = hash of `CARD_TEMPLATE` (every format).
  - `hybrid/representation.py`: the `NodeRepresentation` Protocol (`name`, `version`, `render`),
    `REPRESENTATIONS = {"template": TemplateCards}`, `representation(name, max_chars)`.
  - `pipeline/index_stages.py` `UnitsStage` (not in `FULL_PIPELINE`), `kg units --cards template --out
    BUILD`: params `cards`, `representation_version`, the three caps, `graph_digest`; metrics `cards`,
    `cards_record/individual/concept`, `claims`, `claims_skipped` (claims the claims cap leaves out),
    `cards_truncated`, `card_chars_mean/max`, `claim_chars_mean`; artifact `index/units.jsonl` (cards, then
    claim sentences, each with `unit`). Settings `index_card_names` 5, `index_card_claims` 3,
    `index_card_max_chars` 1500 (the plan's values).
  - `tests/graphs.py`: the hand-made Press/Part graph moved out of `test_query_graph.py` unchanged (its 13
    tests green after the move, before any new code).
- **Verified:** 13 new tests. `tests/test_hybrid_cards.py` (8, no Neo4j): the layout line by line, a hub's
  "(+4 more)", every tag and the disagreement counts, truncation order and the cut at a space, determinism
  and the hash, every card word the template's, the evidence's or a count code computes from it (grounded
  by construction), no four words of a corpus in the template, the claim sentence, the registry.
  `tests/test_hybrid_units.py` (5, Neo4j 7688): the shared graph plus a denied twin, a conditional claim,
  a claim of another predicate, a hedged claim of an individual and three more parts; titles, columns,
  names, capped relations, grouping and counts, each predicate's best first (o1, o5, o3 where support and
  sentence alone give o1, o3, o5), concept and individual relations, FROM chunks, defaults, sentences, and
  the stage's params, metrics and file. 935 passed (922 + 13), ruff clean.
- **CLI check (no model, no write; the working graph is furniture, the tuning set):** `kg units --out
  out/r117_furniture` (MLflow `9e3eba80`, 7.6 s): 737 cards (182 records, 107 individuals, 448 concepts: the
  737 names `graph_retrieval` embedded in R117), 685 claim sentences, cards 130.9 characters on average, at
  most 511, none truncated; 735 claims left out by the cap of 3 (the Helsingborg Dresser alone holds 60).
  Held-out and generality cards were not built (plan: not before R122).
- **R118 done 2026-10-08.** Next: R119, embedding the units into Neo4j (`kg index`, one guarded smoke run).

### R119. `kg index --cards <rep>`: the retrieval units embedded into Neo4j (done 2026-10-08; $0 logged, about $0.005 unpriced)
Plan R116-R125, step 4: the cards and claim sentences of R118 written into the graph as an additive,
deletable index layer with vectors and indexes, which R120's retrievers search.
- **Scope:** the layer's names, its writer and indexes, the stage `kg index`; the digest and the planner's
  schema leave the layer out. Part 1 nodes are never changed; no query or prompt change.
- **How:**
  - `graph/index_layer.py`: `RetrievalUnit`, `NodeCard`, `ClaimSentence`, `CARD_OF`, `SENTENCE_OF`,
    `card_label(rep)` ("template" -> `TemplateCard`), `card_indexes(rep)`, the claim and chunk index names,
    `ANALYZER = "english"`, `RESERVED_LABELS`/`RESERVED_TYPES`, `index_names()`, and in its header the
    Cypher that drops the layer.
  - `hybrid/unit_graph.py`: `card_rows`/`claim_rows` (unit id `<rep>:<ref>` / `claim:<observation id>`,
    text hash, version: the representation's or `CLAIM_VERSION`, the hash of the new `CLAIM_TEMPLATE`);
    `write_units` MERGEs by id, embeds only the units whose (text hash, version) differ or whose vector
    another embedding model made, sets a vector and its `embed_model` only where one was made now, links
    `CARD_OF` (to the element id `read_targets` gives in this run) and `SENTENCE_OF`, dropping an edge to a
    node the unit no longer stands for, and removes the stale cards of this representation only and the
    stale claim sentences; `ensure_indexes` creates the missing indexes (a vector and an English full-text
    index per representation's cards and for the claim sentences, a full-text index on `Chunk.text`) and
    recreates one whose label, dimensions or analyzer differ, then waits for them.
  - `graph/digest.py` and `query/graph_schema.py` (`_labels`, `_relationships`) skip `:RetrievalUnit` and
    its edges.
  - `pipeline/index_stages.py`: `IndexStage` beside `UnitsStage`, sharing `read_units`; refuses a plan
    whose labels or relationship types use the layer's names (before any read or write); params `cards`,
    `representation_version`, the three caps, `embed_model`, `analyzer`, `graph_digest`; metrics R118's plus
    `units_written`, `units_reused`, `stale_units_removed`, `embedded_texts`, `embedded_chars`,
    `indexes_recreated` (`duration_s` from the tracker); artifact `index/units.jsonl`. The counting embedder
    of R117 moved to `llm/counting.py` (`CountingEmbedder`) for both stages. CLI `kg index`; run_guard adds
    `index`.
- **Verified:** 9 new tests. `tests/test_hybrid_index.py` (7, Neo4j 7688): every unit with its vector, model,
  target and evidence hash and the 5 indexes (dimensions 2, English analyzer); a second run embeds nothing
  (0 written, 15 reused); renaming one part re-embeds exactly 2 cards (the part's and the press's, whose
  PART_OF line names it); a second representation ("other", a name in the test only) beside the template
  cards, and a template write without one card removes that card and no other card; Part 1 nodes, the digest
  and the planner's schema text identical before and after; a plan label `TemplateCard` or `RetrievalUnit`
  refused with nothing embedded or written; an index rebuilt for other dimensions. The guard asks before
  `kg index` and not before `kg units` (2). 944 passed (935 + 9), ruff clean.
- **The index run** (the user, 2026-10-08: "yes"; the plan named a `smoke` run, but no free key is set and
  the units are the whole furniture graph, so it was asked as a paid embedding of the whole dataset):
  `kg --preset quality index --cards template --out out/r117_furniture`, twice.
  - First (MLflow `1030c6bf`): 1,422 units written (737 cards, 685 claim sentences), 15 embedding calls,
    139,732 characters, vectors of 3,072 dimensions, 0 reused, 0 stale; the 5 indexes online (the card and
    claim vector indexes at 3,072, the three full-text ones `english`). 306 s, of which 299 s waiting on the
    embedding calls (about 20 s per batch of 100). MLflow logs $0 (embeddings are unpriced); at the list
    price about $0.005.
  - Second (MLflow `5df53f95`): 0 written, 1,422 reused, 0 embedding calls, 1.0 s.
  - Part 1 unchanged: `graph_digest` 392a170ecc10 before and after, the planner's schema text (7,147
    characters) byte-identical.
  - The indexes answer (read-only check): full text "drawer rails helsingborg" ranks the dresser's rails
    `Component:S-1085` second, after `Assembly:A-1070`; claim full text "drawer rails stick" returns "Drawer
    Rails (Component) exhibits stick (QualityAspect)" with its chunk `helsingborg_dresser_reviews.md#0`;
    S-1085's own vector finds S-1085 first (0.999).
- **Fixed along the way (separate commit `4086718`):** naming `:RetrievalUnit` in the digest's and the
  schema's queries made Neo4j warn "label does not exist" on every read of a graph without the layer, so
  every `kg qa` and `kg retrieve-eval` would have logged it (seen on the working graph before the run). The
  label is now tested as a value (`NOT $layer IN labels(n)`), same rows. No test can reproduce it: the test
  database keeps the label's token from earlier tests and never warns. `kg index`'s own first lookup of
  existing units still warns once, on a graph never indexed.
- **R119 done 2026-10-08.** Next: R120, the hybrid retrievers, fusion and the `hybrid` system.

### R120 split (2026-10-08, as the plan allows "if it grows")
R120 holds a store of six queries, seven retrievers, fusion, the source, the registry entry, settings and a
check run: too much for one reviewable step. It is split in two, done one after the other:
- **R120a. The unit store and the retrievers** ($0, no run): `words()` moved to `core/text.py`;
  `hybrid/lucene.py`; `hybrid/unit_store.py` (`UnitStore` + `Neo4jUnitStore`: card, claim and chunk search
  by vector and by full text, a claim filter applied in Cypher with over-fetch, opposite-truth siblings,
  card starts, the index state); `hybrid/retrievers.py` (`ChunkDense`, `ChunkLexical`, `ClaimRetriever`,
  `CardRetriever` with round-robin per card and seeds, `SourceRetriever` for the name-linker route given
  the question's vector); `ClaimHit` in `query/answers.py`. Tested with fakes and on Neo4j.
- **R120b. Fusion, the hybrid source and the `hybrid` system** (one check run, asked): `hybrid/fusion.py`
  `rrf`; `hybrid/source.py` (`HybridSettings`, `build_hybrid`: refuses a missing or stale index or another
  embedding model, embeds the question once, trace with the lists, seeds and claims); `hybrid =
  hybrid_spec("template")` in `qa_systems.py`; the `hybrid_*` settings; the system-level tests (only
  listed retrievers called, the reader gets exactly k, no card text in the reader prompt, an H17-shaped
  synthetic case).

### R120a. The unit store and the retrievers (done 2026-10-08; $0, no run)
- **Scope:** the queries over the R119 layer and one retriever per kind of list; no system uses them yet
  (R120b), so no `kg` command changes.
- **How:**
  - `words()` moved from `query/names.py` to `core/text.py` (the name linker and the lexical retrievers
    share it), alone and green first (98 query tests).
  - `query/answers.py` `ClaimHit` (observation id, chunk, `truth`, `negation`, `modality`, `hedge`,
    `condition`, `triple_truth`, opposite-truth `siblings` with their `sibling_chunks`).
    `GraphRetrieval.ranked_for(question, vector)`: the route from a vector the caller has (`ranked` calls
    it), so hybrid retrieval embeds a question once.
  - `hybrid/lucene.py` `lucene_query`: each word quoted once, joined by spaces (OR), so Lucene operators in
    identifiers ("hp40-1183") are plain text; None for a text without words.
  - `hybrid/unit_store.py`: `UnitStore` + `Neo4jUnitStore`. Against the plan's sketch (`nearest_units`,
    `search_units`, `claims(ids, filter)`) the store has one method per unit kind and search mode
    (`nearest_cards`, `search_cards`, `nearest_claims`, `search_claims`, `search_chunks`), so a claim search
    joins its observation, applies `ClaimFilter(triple_truth, modality)` and finds the siblings (same
    canonical subject, predicate and object, opposite `triple_truth`) in one query, over-fetching 4x when a
    filter is set; `card_starts` (a record's card starts the traversal as a thing by element id, an
    individual's or a concept's as a kind by canonical id); `index_state` (card and claim counts,
    versions, embedding models, online indexes), reading labels as values so a graph without the layer gets
    no server warning.
  - `hybrid/retrievers.py` (Strategy, all given the question, its vector and a depth): `ChunkDense`,
    `ChunkLexical`, `ClaimRetriever(mode)` (each claim's chunk, then its siblings' chunks, each chunk once),
    `CardRetriever(representation, mode)` (one `reach` per card, one read of all reached chunks, each card's
    chunks ranked by similarity to the question, the cards in turn; seeds: the cards with a node),
    `SourceRetriever` (`graph_route`, over a `LinkedRoute` protocol, so `hybrid` imports no `query` class);
    `round_robin`.
- **Verified:** 14 new tests. `tests/test_hybrid_retrievers.py` (7, fakes): the quoted query, list order and
  depth, sibling chunks right after their claim, the filter passed through, no search without a word, a
  hub of five chunks and a second card taking turns (h0, b0, h1, b1, h2), a card without a node no seed,
  the name-linker route without a second embedding. `tests/test_hybrid_store.py` (7, Neo4j 7688, the
  shared graph indexed with a keyword embedder): cards by vector and by words ("quill" finds exactly the
  six cards that write it; BM25 ranks the short ones high), chunks by words, stop words alone find nothing,
  siblings (o1 stated against o2 denied; o2 against o1 and o3, a condition's claim), the filter with
  over-fetch (asking for one denied claim of four), card starts, the index state, the card retriever end to
  end. 958 passed (944 + 14), ruff clean.
- **R120a done 2026-10-08.** Next: R120b, fusion, the hybrid source and the `hybrid` system.

### R120b. Fusion, the hybrid source and the `hybrid` system (done 2026-10-08; $0.0017)
- **Scope:** the fused chunk source, its system and settings; `kg qa` defaults unchanged (`hybrid` is asked
  only when named).
- **How:**
  - `hybrid/fusion.py` `rrf(lists, k)`: sum of 1 / (k + rank), ranks from 1, an item once per list; ties by
    best single rank, then the earlier list, then the item.
  - `hybrid/source.py`: `RETRIEVERS` (the six of the grid and `graph_route`), `HybridSettings(retrievers,
    rrf_k, depth, cards)` (unknown names refused, one order), `HybridSource` (a `ChunkSource`: the question
    embedded once, each retriever asked to the depth, chunk lists and seed lists fused, the best `depth`
    chunks read from the store; trace `candidates`, `seeds`, `lists`, `claims`), `build_hybrid` with
    `check_layer`: refuses cards missing or of another version, claim sentences missing or of another
    version, vectors of another embedding model, or the needed indexes offline (`MissingInputError`, "run kg
    index"), before any question is embedded; `graph_route` without a built route refused.
  - `query/answers.py` `RetrievalTrace.lists` and `.claims` (additive); `RetrievalOutcome.lists` in the
    retrieval report, so the benchmark shows which retriever brought each chunk.
  - `pipeline/qa_systems.py`: `SourceParts.units` (the `Neo4jUnitStore`), `hybrid_spec(name, cards)` and
    `hybrid = hybrid_spec("hybrid", "template")`: a reading system whose depth is at least `hybrid_depth`;
    params `hybrid_retrievers`, `hybrid_rrf_k`, `hybrid_depth`, `hybrid_cards`, `representation_version`,
    the linker's and the traversal's settings. The name-linker route (which embeds every node name) is built
    only when `graph_route` is listed. `qa_systems.py` is now 340 lines, over the guideline: still the one
    registry, which B adds one line to.
  - `config.py`: `hybrid_retrievers` (the six: M4 until R121 seals a mix), `hybrid_rrf_k` 60, `hybrid_depth`
    20. The plan's `hybrid_cards` setting is not added: the representation is part of the system
    (`hybrid` -> template, later `hybrid_summary` -> summary).
  - Two tests that used "hybrid" as an unknown system's name use "oracle" now (the refusal they test is
    unchanged).
- **Verified:** 15 new tests. `tests/test_hybrid_source.py` (11, fakes): fusion's order and ties, a small k
  rewarding one first place and k = 60 agreement, one embedding for every retriever, the fused list, its
  lists, seeds and claims, only the listed retrievers asked, unknown retriever names refused, five ways a
  layer does not fit (each refused with nothing embedded), `graph_route` without a route refused, the
  `hybrid` reader given exactly k = 3 of 6 fused chunks. `tests/test_hybrid_system.py` (2, Neo4j): an
  H17-shaped synthetic case (invented words): the words alone rank the other press's feed-roller notice
  before the right press's notice, while the claim leads to the ticket and the right press's card to its
  own notice, both read; the claim hit carries its truth fields; and on the shared graph no card line or
  claim sentence reaches the reader's prompt, only the shown chunks. 973 passed (958 + 15), ruff clean.
- **The check run** (the user, 2026-10-08: "yes"; the working graph, furniture, the tuning set; untuned:
  the six retrievers, `rrf_k` 60, depth 20, which is the grid's M4 cell at k = 60):
  - `kg --preset quality retrieve-eval tests/gold/qa/furniture_qa.json --targets
    tests/gold/r89/furniture_targets.json --build out/r117_furniture --data data --system hybrid --out
    out/r120_check` (MLflow `751398c2`, `graph_digest` 392a170ecc10, 68 embedding calls, 4,446 characters,
    $0 logged). Code-computed rates, n as given (no judge):

    | System | Evidence@5 | Complete@5 | Complete@10 | Seed Recall@5 | Seeds found@5 | latency p50 / p95 |
    |---|---|---|---|---|---|---|
    | vector (R117) | 0.548 (40/73) | 0.606 (20/33) | 0.788 (26/33) | - | - | 444 / 581 ms |
    | graph_retrieval (R117) | 0.644 (47/73) | 0.727 (24/33) | 0.848 (28/33) | 0.849 (73/86) | 0.780 (39/50) | 584 / 847 ms |
    | hybrid (M4, k 60) | 0.712 (52/73) | 0.727 (24/33) | 0.848 (28/33) | 0.779 (67/86) | 0.640 (32/50) | 1,661 / 3,367 ms |

  - Paired (`kg retrieve-compare` against `tests/gold/r117/`, same gold, targets, digest and embedding
    model): against vector, Complete@5 only hybrid 5, only vector 1, p = 0.219; against graph_retrieval,
    Complete@5 2 and 2, p = 1.0, Seeds found@5 only hybrid 6, only graph_retrieval 13, p = 0.167. No
    difference beyond one sample's variation at these n.
  - What the seeds show (for R121): the cards fix R117's crowding, the Helsingborg Dresser's rails
    `Component:S-1085` are found at 5 in F01, F02 and F07 (9 targets found only by the hybrid), but 15
    targets the name linker spells (the "Helsingborg Dresser" product in F06 and F09, assemblies in F04,
    F05, F17) are lost: the pre-registered mix M5 (adding `graph_route`) answers whether both can be had.
    Latency is about 3x the name linker's: two card retrievers traverse from up to 20 cards each.
  - `kg --preset quality ask --system hybrid "Which products do reviews report with defective drawer rails?"
    --out out/r117_furniture` (MLflow `a88f39c3`, 1 reader call, 1,142 / 228 tokens, $0.001711): answer
    Helsingborg Dresser, three cited quotes. The trace (`out/r117_furniture/ask.json`) holds every
    retriever's list (all six put the dresser's chunks first), the fused seeds (a drawer-rails concept, then
    `Component:S-1085` second) and 28 claim hits with their truth fields (for example chunk #2,
    `modality: possible`, hedge "seem").
- **R120b done 2026-10-08** ($0.0017). Next: R121, A tuned on furniture and sealed, asked first.

### R121. A and the shared retrieval settings tuned on furniture, then sealed (done 2026-10-08; $0 logged, about $0.002 unpriced)
Plan R116-R125, step 6: the pre-registered grid run on the tuning set, the criterion applied, the choice
committed as the `config.py` defaults before any held-out or generality run.
- **Scope:** ten `kg retrieve-eval` runs, three pairings, the seal (`config.py` defaults, `tests/gold/r121/`,
  a test). No retrieval code, prompt or template change; no reader run (the user, asked: not needed by the
  criterion).
- **The runs** (the user, 2026-10-08, in this session: "Yes, run all 10"): one command per cell, all at
  code `54053e4` (dirty only by the user's `.claude/settings.json`), on `out/r117_furniture`
  (`graph_digest` 392a170ecc10), questions ranked one at a time, each into `out/r121/<cell>`:
  `HYBRID_RETRIEVERS='<mix>' HYBRID_RRF_K=<k> uv run kg --preset quality retrieve-eval
  tests/gold/qa/furniture_qa.json --targets tests/gold/r89/furniture_targets.json --build out/r117_furniture
  --data data --system hybrid --out out/r121/<cell>`. MLflow logged each cell's mix, `rrf_k`, depth 20 and
  `representation_version` 54d556f3ea08 as params. The M4/k60 cell repeats R120b's check and gives its
  numbers exactly (Evidence@5 52/73, Complete@5 24/33, Seed Recall@5 67/86): the ranking is deterministic,
  only latency moves (p50 1,282 ms against 1,661).
- **Every cell** (code-computed rates, no judge; n: 73 gold chunks over 33 questions, 86 targets over 50
  questions; M1 and M2 list no seed-giving retriever, so their seed scores are 0 by design):

    | Mix | rrf_k | Evidence@5 | Evidence@10 | Complete@5 | Complete@10 | Seed Recall@5 | Seed Recall@10 | Seeds found@5 | latency p50 / p95 | MLflow |
    |---|---|---|---|---|---|---|---|---|---|---|
    | M1 | 10 | 0.685 (50/73) | 0.808 (59/73) | 0.758 (25/33) | 0.818 (27/33) | 0.000 (0/86) | 0.000 (0/86) | 0.000 (0/50) | 550 / 948 ms | `82d2cabf` |
    | M1 | 60 | 0.671 (49/73) | 0.808 (59/73) | 0.697 (23/33) | 0.818 (27/33) | 0.000 (0/86) | 0.000 (0/86) | 0.000 (0/50) | 535 / 754 ms | `c74d7bd7` |
    | **M2** | **10** | **0.795 (58/73)** | 0.863 (63/73) | **0.818 (27/33)** | 0.879 (29/33) | 0.000 (0/86) | 0.000 (0/86) | 0.000 (0/50) | 562 / 920 ms | `1a4d1f8a` |
    | M2 | 60 | 0.767 (56/73) | 0.877 (64/73) | 0.788 (26/33) | 0.879 (29/33) | 0.000 (0/86) | 0.000 (0/86) | 0.000 (0/50) | 540 / 811 ms | `cfbaa089` |
    | M3 | 10 | 0.685 (50/73) | 0.808 (59/73) | 0.697 (23/33) | 0.848 (28/33) | 0.779 (67/86) | 0.895 (77/86) | 0.640 (32/50) | 1,300 / 1,871 ms | `8ac9489a` |
    | M3 | 60 | 0.658 (48/73) | 0.822 (60/73) | 0.667 (22/33) | 0.848 (28/33) | 0.779 (67/86) | 0.884 (76/86) | 0.640 (32/50) | 1,166 / 1,599 ms | `cb035278` |
    | M4 | 10 | 0.740 (54/73) | 0.822 (60/73) | 0.727 (24/33) | 0.879 (29/33) | 0.779 (67/86) | 0.895 (77/86) | 0.640 (32/50) | 1,285 / 1,790 ms | `93b2fe8c` |
    | M4 | 60 | 0.712 (52/73) | 0.808 (59/73) | 0.727 (24/33) | 0.848 (28/33) | 0.779 (67/86) | 0.884 (76/86) | 0.640 (32/50) | 1,282 / 1,703 ms | `aa81f15c` |
    | M5 | 10 | 0.740 (54/73) | 0.836 (61/73) | 0.758 (25/33) | 0.909 (30/33) | 0.942 (81/86) | 0.977 (84/86) | 0.900 (45/50) | 1,363 / 1,783 ms | `a0970c69` |
    | M5 | 60 | 0.699 (51/73) | 0.836 (61/73) | 0.727 (24/33) | 0.909 (30/33) | 0.919 (79/86) | 0.977 (84/86) | 0.860 (43/50) | 1,311 / 1,848 ms | `44e0d50c` |

- **The choice** (the pre-registered criterion, applied in code and again by the test): Complete@5 first,
  and M2 (chunks and claim sentences, each by vector and by words) with `rrf_k` 10 is alone at the top with
  27 of 33; the runner-up is M2 with `rrf_k` 60 (26). No tie, so the later rules did not decide. n is 33
  questions: one question separates the first two cells, so the choice is coarse.
- **Paired** (`kg retrieve-compare`, files only, $0; MLflow `cd1f3268`, `86b8c742`, `0075b83b`): against
  vector (R117) Complete@5 only M2/k10 7 (F12, F16, F20, F24, F26, F28, F29), only vector 0, p = 0.016;
  Complete@10 4 and 1, p = 0.375. Against graph_retrieval (R117) Complete@5 3 (F12, F28, F29) and 0, p =
  0.25; Complete@10 2 and 1, p = 1.0. Against the runner-up M2/k60 Complete@5 1 and 0, p = 1.0. Per type at
  5, M2/k10 completes all 7 negation_sensitive questions (vector 4). The chosen cell is the best of ten on
  the tuning set, so these p-values are optimistic: R122 on held-out and generality is the test.
- **What the grid shows:**
  - Claim sentences add complete questions in both k (M2 over M1: 27 against 25, 26 against 23). F29
    ("Which products do reviews report as sagging?", expected none: the reviews deny it): chunk vector
    search ranks none of the 4 gold chunks in its 20, while the claim vector list ranks them 1 to 4 through
    "Shelves (Component) exhibits sagging (QualityAspect)" and three more such sentences (truth stays out of
    the text; the reader decides it).
  - Cards do not add complete questions here: M3 completes fewer than M1 (23 against 25 at k 10), M4 fewer
    than M2 (24 against 27). They are what gives seeds (67/86 at 5) and they cost latency (p50 about 1.3 s
    against 0.55 s: the card retrievers traverse from up to 20 cards each).
  - Both seed sources together answer R120b's question: M5/k10 finds 81 of 86 targets at 5, more than the
    name linker alone (73, R117) or the cards alone (67), so the cards' "drawer rails" fix and the linker's
    spelled product names can be had together; it completes 25 questions, 2 fewer than M2/k10.
  - `rrf_k` 10 completes at least as many questions as 60 in every mix.
- **What the choice means for R122 and B** (recorded, not acted on): the sealed hybrid lists no card
  retriever, so the cards (representation A) no longer change its chunk ranking and it gives no seeds;
  R122's `hybrid` measures chunks plus claim sentences. `kg index --cards template` is still needed (it
  writes the claim sentences; the cards are written too). The plan's A-against-B headline, Seed Recall@K of
  card retrieval alone, is untouched by the seal, but no sealed system measures it yet, and R122 takes no
  `HYBRID_*` override: whether card retrieval alone is measured on held-out (a fixed, untuned system or
  report, added before R122) is the user's decision, asked with R122.
- **The seal:** `config.py` `hybrid_retrievers` = `["chunk_dense", "chunk_lexical", "claim_dense",
  "claim_lexical"]`, `hybrid_rrf_k` = 10, `hybrid_depth` = 20 (unchanged). `tests/gold/r121/tuning.json`:
  the grid, the criterion, the n, every cell's numbers, run id, embedding counts and cost, the choice and the
  runner-up, the three pairings with run ids, the git sha, `graph_digest`, the gold and targets hashes, the
  embedding model and the representation version; `tests/gold/r121/retrieval_hybrid.json`, the chosen
  cell's report. README's `hybrid` paragraph names the sealed defaults.
- **Verified:** `test_r121_the_config_defaults_are_the_cell_the_pre_registered_criterion_chose`
  (`tests/test_retrieval_scores.py`): the recorded grid equals the pre-registered one written in the test,
  every cell is there once with its mix's retrievers and depth 20, the criterion applied to the recorded
  numbers picks the recorded choice, the `config.py` defaults equal it, the committed report shares R117's
  fingerprint and the hashes of today's gold and targets files, scores 27/33, and pairs with R117's vector
  report to the recorded counts. On the old defaults the test fails (the two card retrievers). 974 passed
  (973 + 1), ruff clean.
- **Cost:** $0 logged (no reader). Embeddings unpriced: 2,154 texts, 64,252 characters over the ten runs
  (MLflow `embedded_texts`/`embedded_chars`: 68 question texts per cell, 805 in each M5 cell, which embeds the
  737 node names); about $0.002 at the list price R119 used. 884 s of runs.
- **Further analysis** (2026-10-08, the user asked; offline, $0, no run): the results snapshot
  [docs/evaluation/results_2026-10-08_r121_hybrid.md](docs/evaluation/results_2026-10-08_r121_hybrid.md)
  (local) with its scripts. Depths 1, 3 and 20 were recomputed from the stored per-retriever lists with the
  system's own `rrf`; the recomputed top 10 equals every report's.
  - **K = 5 separates the systems most:** Complete 20 (vector) to 27 (M2/k10).
  - **At K = 20, chunks alone (M1) complete 31, as M2 does:** claims mostly move evidence up.
  - **Cards cost completeness at every K** (29-30 at 20).
  - **Seeds:** the name linker finds 46 of 86 targets at K = 1 against the cards' 36. The cards pass it at
    20 (83 against 82), and both fused find all 86 by 20.
  - **The linker alone (15 targets)** finds names spelled in questions that also mention opinions, parts or
    neighbours, whose cards then win (`Product:P-1007` in F06, the `Frame` assembly in F04).
  - **The cards alone (9 targets)** find same-named parts and described targets (`Component:S-1085` in F01,
    `Legs` in F24).
  - **The ceiling:** F08 and F13 cite 6 and 8 chunks, so Complete@5 is at most 31 of 33.
- **R121 done 2026-10-08.** Next: R122, A measured on held-out and generality, asked per dataset.

### Plan change (2026-10-08): B before R122, then one step per dataset with A and B together
The user, 2026-10-08: "We still need to test against the llm-generated node summaries embeddings", then, asked
in the session that starts R123, chose each of the following.
- **Order:** R123 (B's code) -> R124 (B tuned on furniture and sealed) -> R122 (A and B on held-out) -> R125 (A
  and B on generality). Furniture is the working graph now and B is built and tuned on it; measuring A and B
  together per dataset loads held-out and generality once each. The plan's R122 (A alone on both datasets)
  and R125 (B against A on both) become one step per dataset.
- **What R121's seal means for B:** the sealed `hybrid` lists no card retriever, so it ranks the same chunks
  whichever representation is indexed. A `hybrid_summary` system (the plan's sketch) would equal `hybrid`: it
  is not registered, and neither is paid for twice. A and B differ only where the cards are used: the card
  retrievers' seeds and their chunk lists.
- **Measures, fixed before any B number exists:**
  - headline (pre-registered in the plan): Seed Recall@K of `card_dense` alone, A against B, K = 1, 3, 5, 10,
    20, exact McNemar on Seeds found@5;
  - secondary (a): Evidence@K and Complete@K (K = 5, 10, 20) of the `card_dense` list alone;
  - secondary (b): Seed Recall@K of `card_lexical` alone (a summary changes the words too);
  - secondary (c): the fused seeds of `card_dense` + `card_lexical` + `graph_route` at the sealed `rrf_k` 10,
    per representation (the seeds of R121's M5/k10, the best seed finder: 81/86 at 5).
  Each is a registered retrieval-only system per representation whose retriever list is fixed in code, not
  set through `HYBRID_*` overrides, so the held-out and generality runs stay override-free; A's numbers on
  furniture are measured before B's.
- **The summary model:** `gemini-3.5-flash-lite` (the user's choice, the cheapest Gemini; the builder is
  `gemini-3.8-flash`).
- **Retrieval only:** A and B are compared on seeds and chunks (`kg retrieve-eval`); no answer-level system
  that reads the cards' chunks is built, and no reader run is made for B.
- **The K of the headline are computed by code:** `retrieval_budgets` becomes 1, 3, 5, 10, 20 (R117 fixed 5
  and 10; R121 computed 1, 3 and 20 offline). Every `kg retrieve-eval` from R123 on scores all five, with no
  override; a source ranks to 20, so the vector system now asks its index for 20 chunks instead of 10 (R121
  section 5.9: the counts at 5 and 10 were equal on furniture).

### R123. B, LLM node summaries as a second representation (code done 2026-10-08; the real-API check asked)
Plan R116-R125, B's code: a model writes each node's text from the same evidence as A's cards, code checks
it, and the card retrievers can search either representation.
- **Scope:** the representation seam (its own structural commit first), `hybrid/summaries.py`, the summary
  units' provenance, B's settings, the six card systems, the five budgets. No change to A's texts, to the
  claim sentences, to the sealed `hybrid` or to any Part 1 node.
- **How:**
  - **The seam** (`567d3fb`, behaviour unchanged, 974 passed): `REPRESENTATIONS` maps a name to a factory of
    `(RepresentationOptions, LLMClient | None)`; the protocol adds `params()` and `prompts()` (empty for the
    template); stages and systems build a representation through `index_stages.card_representation(settings,
    cards, llm)`, so all compute one version from one settings object; `kg units` never passes a model.
  - **`hybrid/summaries.py` `SummaryCards`** (Strategy over the `LLMClient` port, given the composition
    root's `CachedLLM`): the evidence as numbered facts F1..Fn in the card's own line formats (aliases,
    properties, relations, claims with the code-made tags; `cards.qualified_claim` split out of `claim_line`,
    same output), the prompt below, a `NodeSummary(text, facts_used)` reply. The grounding check
    (`grounding_issues`): the title or an alias in the text, every cited id a fact, every standalone number
    in the evidence, every name-like word (a capital not at a sentence start, a capital after the first
    letter, or a digit inside a word) found in the evidence's words (labels also split at their capitals),
    the length cap. A refused reply is asked again once with its issues (`llm/refine.py`, `ROUNDS` 2); a node
    refused twice gets its template card with `fallback` true. Eight nodes are asked in parallel; a provider
    failure stops the run (the cache keeps what was written). `version` hashes the prompt, the fact format,
    the reply schema, the model, temperature, thinking level, length cap and the template version.
  - **The prompt** (`PROMPT`, version `prompt_version(PROMPT)`, logged as `prompts/summary.txt`): what the
    text is for (a vector a question finds; never an answer), the node, its numbered facts, how to read the
    relation notation and the tags, eight rules (facts only; the name first, then the kind; connections, then
    claims; every qualifier kept, never plain fact; names and numbers copied exactly, relation types in
    words; little facts, little text; the cap; the cited ids), and one example from an invented domain
    (string instruments) with every tag. No word of a dataset under evaluation; a test checks that no four
    consecutive words occur in a corpus.
  - **Units:** `RenderedCard` gains `facts_used`, `fallback` and `rejected` (the refused replies with their
    issues), None for the template, so A's lines of `index/units.jsonl` are unchanged (`exclude_none`);
    `:SummaryCard` units store `facts_used` and `fallback` beside the evidence hash. `kg index` metrics add
    `summaries_rejected` (nodes whose first reply was refused) and `summaries_fallback`; params add
    `summary_model`, `summary_temperature`, `summary_thinking`, `summary_max_chars`, `summary_prompt_version`.
  - **Settings:** `index_summary_model` gemini-3.5-flash-lite, `index_summary_thinking` "" (the model's
    default; MLflow shows 0 thinking tokens on every earlier Flash-Lite run), `index_summary_max_chars` 600;
    the temperature is `llm_temperature` (0).
  - **The card systems** (`qa_systems.CARD_SYSTEMS`): `card_dense_<rep>`, `card_lexical_<rep>`,
    `card_seeds_<rep>` for `template` and `summary`, through `hybrid_spec(name, rep, retrievers)` with the
    list fixed; `hybrid_rrf_k` and `hybrid_depth` are the seal's. No `hybrid_summary` (see the plan change).
  - `.claude/hooks/run_guard.py`: `index` already asks; its comment says a model writes the summaries.
- **Verified:** 30 new tests, 3 changed; 1004 passed (974 + 30), ruff clean. `tests/test_hybrid_summaries.py`
  (20, no Neo4j): the numbered facts
  with their tags, a grounded text passing (an alias, a possessive, a label in words), each rule refusing its
  failure (empty, no name, an unknown fact id, invented numbers, invented names and a fact id in the text, the
  cap), a grounded reply kept with A's evidence hash, the retry with the issues and the earlier reply in its
  prompt, the fallback after two refusals, the order kept in parallel, no render without a model, the cache
  answering a second render, the version changing with each knob and hashing what it should, no corpus
  quote in the prompt or the field descriptions. `tests/test_hybrid_summary_index.py` (3, Neo4j 7688):
  summary and template cards of every node with equal evidence hashes, the claim sentences reused, the
  fallback and cited facts stored on the unit and absent on a template unit, the summary metrics, params and
  prompt artifact, both indexes online; dropping the template cards leaves the summaries with their vectors;
  `kg units --cards summary` refused with no model call. `tests/test_qa_systems.py` (+7): each card system's
  list fixed under an override of `HYBRID_RETRIEVERS`, and `card_dense_summary` asking only the summary
  cards. Changed: the registry test (two representations), `read_units` called with a representation, R117's
  stage test pinning the five budgets.
- **Offline render** ($0, no model; the working graph, furniture, read-only): the 737 prompts hold 1,990,844
  characters (2,701 on average, 2,589 of them the fixed text); the facts 99 characters on average, median 51,
  at most 448 (records 193, individuals 183, concepts 41 on average); 1,760 facts in all.

## Known limitations (the refinement arm stopped at R108)
The user's decision, 2026-10-07: the anchor-graph refinement arm (R97-R108) stops at R108; what it leaves is
recorded here as known limitations and future work, not optimised now. The state it stops in, on R103's
re-extracted claims resolved by R108 (judge: Claude Opus 5.5, the gold and the verdicts from one model
family): every C3 hard rule passes on furniture, held-out and generality; every record link made is judged
right (C4 63/63, 77/77, 48/48); C6 purity is 1.0 everywhere; against R103, retrieval (C2, C5 against
vector search), hubs (C7), connections (C8) and graph size (C9) did not regress. The four limitations, each
with its evidence and the direction a later step would take:

1. **Four right record links lost on held-out (R108).** The chooser now decides a word key inside a longer
   name, and it refused "Civic Type R" (x2), "2017-2021 Civic hatchback" and "2016-2020 Civic coupe" -> the
   record `Vehicle:CIVIC`, all judged VALID in R107, while it accepted "Civic Coupe", "Civic Hatchback",
   "Civic Sedan" and "2016 Civic 2-Door". It seems to read the record's model year (2016) against the
   ranges, and not consistently. Cost: link recall, not precision (C4 counts the links made); retrieval is
   unchanged. Direction: a chooser rule that a range of years is no reason to refuse a version, or the
   year left out of what the chooser sees; measured by a replay of the chooser alone (cents).
2. **Two right record links lost on generality (R107).** "the pump had run dry" and "Pump returned to duty
   the same day" refer back to the pump HP40-1183 named just before; R103 joined both to the record, R107's
   kind rule makes them `Kind` concepts. Direction: the narrower kind rule, a stated kind may join a
   record's unit (a reference back to one thing) but never another individual; it must keep held-out's
   "CARS" / "CAR" apart.
3. **Identifiers nominate pairs by spelling (R102).** Recall numbers typed with a keyed type ("15V-246",
   "16V-643") are spelled alike, so a fresh build asks the adjudicator hundreds of pairs that are always
   apart (668 in R102, about $1.1 of held-out's resolve). A cost only: no wrong join. Direction: leave names
   with a digit out of spelling nomination, as `names.name_score` already gives such a word no ending.
4. **The dimmer ambiguity (R103).** On furniture "dimmer" joins "dimmer switch" and "dimmer function" into
   one individual; R75's identity gold (the user's R38 decision) keeps the switch and the function apart
   (R75 apart pairs 32/33), while the blind C3 judge accepted the join from the text ("the dimmer function
   ... a bit stiff to turn"). Unresolved whether the text or the gold is right. Direction: decide the
   reading first, then either a resolve rule or a gold revision listed as a gold correction.

## Found along the way
- **`EvaluationError` says "verdicts do not match the graph" whatever the issue (found in R117, 2026-10-08;
  open).** `core/errors.py` prefixes every message with it, so R117's gold-chunk refusal reads "verdicts do
  not match the graph: 42 of the gold's 42 evidence chunks are not in the loaded graph ...", and paired.py's
  "outcomes a hold a question more than once" reads the same way. The issues after the prefix are right; a
  neutral prefix ("evaluation inputs do not fit: ") is a one-line change to messages only, left for its own
  commit.
- **Build files hold Neo4j element ids, which change on every rebuild (found in R113, 2026-10-08; open, a
  rule for Part 2).** `resolve.json` and the audit snapshot keep each record reference's `target`, the
  element id of the record node in the database that build wrote. A rebuild of the same content gives the 77
  held-out record nodes new ids, so the two files differ in those fields only. Comparisons between builds
  ignore `target` (as R113 did); the query engine must address records by label and key, never by a stored
  element id. R117 follows it: retrieval's seeds are record refs and canonical ids (`NodeName.ref`).
- **The claim sheet shows one type pair per relation (found in R110 part b, 2026-10-07; fixed in its own
  commit: `relations` is keyed by the type pair, "Product -[EXHIBITS]-> QualityAspect"; test
  `test_the_sheet_shows_every_type_pair_a_relation_is_declared_for` failed before the fix).** `claim_sheet`
  keyed `relations` by predicate, so a predicate the schema declares for several type pairs kept only the
  last: furniture `EXHIBITS`, `HAS_MEASUREMENT`, `MADE_OF` (Product and Component subjects), generality
  `LOCATED_AT` (four subject types), `HAS_MEASUREMENT`, `HAS_COST`. Extraction refuses any type pair the schema
  lacks (`text/extraction.py`, `allows_extraction`), so every stored claim fits one. It did change verdicts:
  the judges read claims with the shown pair's description (R110's "Correction": 29 verdicts changed, the
  furniture and generality sheets regenerated with every pair; R111's first matching of those two datasets
  redone).
- **Extraction errors R110 measured (found in R110 part b, 2026-10-07; open, the user's decision):**
  ~~a reviewer's assembly time stored as a measurement of the product~~ (withdrawn by R110's correction: the
  Product pair of `HAS_MEASUREMENT` declares assembly duration; only bounds stored as values remain wrong, 4);
  a recall's own link stored with the defect's hedge (held-out, 32 claims, ruling 1); things that are
  no piece typed `Component` ("instructions", "tools", "fabric", "wood", 19 extracted claims, repeated by
  derivation as `PART_OF`); numbers that are limits, overruns or savings stored as values, and times of
  another event (generality). The held-out recall-number `INSTALLED_IN` claims (97) are R109's.
- **R101's precision and hub bounds fail on the rebuilds (found in R102, 2026-10-07; the user's decision).**
  Pass precision 0.789 (furniture) and 0.707 (generality) against >= 0.90; new hubs FORD (held-out) and
  North Station (generality). The precision errors that are the pass's own: individual or fallback types for
  common nouns, titles of named people, a few Out words. R101's rule gives the prompt and checks at most three
  `dev` rounds on `samples/` before the user decides; the hub bound counts named particulars the text names
  in over 20 % of the chunks, which the bound was not meant to catch, so whether it stays as written is part of
  the decision. **The user's decision (2026-10-07): fix the pass first (R104); the hub bound, resolution and
  concept merging are not in that round.** Precision is measured again on the next full pass output.
- **Out 9 reaches further than the gold, and the class of "<brand> customer service" (found in R105,
  2026-10-07; closed by R106: both read as the gold reads them).** Out 9 as worded ("a common noun written next to a thing's name
  ... names the same thing") excludes "vehicles" after model names, which the R101 gold keeps: the model
  names a model, "vehicles" the cars of it, another thing. Rewording Out 9 to the gold's reading ("when it
  names that very thing: a title of a person, a common noun before an identifier") changes the prompt and
  the definition. Separately, the gold lists "Ford customer service" as particular while R104's class
  sentence reads it as a kind: either a gold correction or one more class example. Both decide 4 of
  held-out's 8 INCORRECT verdicts in R105.
- **"dimmer" joins the switch and the function (found in R103, 2026-10-07; deferred: Known limitation 4).** The
  re-extracted furniture claims name a plain "dimmer"; resolve joins it, "dimmer switch" and "dimmer
  function" into one individual, which R75's identity gold (the user's R38 decision) keeps apart, while the
  blind C3 judge accepted it from the text ("the dimmer function ... a bit stiff to turn"). Either the R38
  decision stands and the join is a resolve error, or the text supports the join and R75's pair is revisited.
- **The pass's remaining precision errors (found in R105, 2026-10-07; open).** One or two of each kind in
  180 judged mentions: generic stand-ins, fragments of a longer name, the scraped source URL's id (every
  furniture document's "Scraped from <url>" line), idioms, times written as nouns ("night shift"), ordinary
  operation verbs, an identifier with its common noun. A prompt round on them would chase single cases; the
  source URL line is the one a code check could see (a name inside a URL of the chunk).
- **R107 also drops a kind word that refers back to one record (found in R107 part b, 2026-10-07; deferred:
  Known limitation 2).**
  In generality, "the pump had run dry" and "Pump returned to duty" mean the pump HP40-1183 named just
  before; R103's adjudicator joined both to the record, R107 makes them `Kind` concepts (2 record mentions
  lost). The same rule removed held-out's wrong "CARS" / "CAR" join. A narrower rule would let a stated kind
  be joined to a record's unit (a reference back to one thing) but never to another individual.
- **`kg resolve --preview` does not see R107's kinds (found in R107, 2026-10-07; open).** The preview lists
  the concept pairs of the concept types' mentions without matching records, so a stated kind of a keyed
  type that no record fits, which `kg resolve` resolves as a `Kind` concept, is missing from its pairs.
- **A kind typed with a keyed type that no record fits becomes an individual (found in R104, 2026-10-07;
  closed by R107 part a: resolved as a `Kind` concept; R103: it made a wrong merge).** In R103's held-out graph, "CARS" and "CAR" of two complaints,
  both stated kind and typed `Vehicle`, became `no_record` individuals and were joined (C3 hard rule).
  Since the pass states the class, resolve could treat a pass mention stated kind that no record fits as a
  concept; the class lives in `mentions.jsonl` only, not on the graph's Mention, so this changes the pass's
  write and resolve. R105 shows its size: held-out's pass states "recalling" a kind 27 times with the keyed type
  `Recall` (generality: "contractor" stated kind, typed `Person`). R104 keeps a keyed type for a mention stated `kind` ("back rest" links to the product's part
  record), but resolve turns a keyed mention that no record fits into a `no_record` individual
  (`resolution/particulars.py:330`), so a generic "chair" or "recalling" of a keyed type would still be a
  named-thing node. For a mention stated `kind`, a concept would be the right fallback; that is resolve's
  rule, not built in R104 (the user kept resolution out of the round).
- **The mention sheet's class for keyed types (found in R102, 2026-10-07; closed by R104).** `MentionEvalStage`
  gave a mention of a keyed type the class particular; the definition makes a piece or an unnamed object a
  kind whatever its type. Changing it changes R102's numbers, so it was not done after the verdicts. R104
  settles it before any sheet of a new pass exists: the pass now states the class, and the sheet shows the
  stated one; R102's pass files state none and keep their judged sheets.
- **Near-duplicate pass mentions in one sentence (found in R102, 2026-10-07; open).** "may not engage" and
  "not engage" are two mentions of one sentence, merged into one concept (judged right); `already_listed`
  compares whole normalised names, so a name holding another passes.
- **Identifiers nominate pairs by spelling (found in R102, 2026-10-07; deferred: Known limitation 3).** The
  mention pass types recall numbers ("15V-246", "16V-643", "17V-210") with a keyed type; as `no_record`
  individuals they are spelled at least `er_borderline` alike, so held-out's resolve asked 668 such pairs and answered every one apart ($1.14 in
  all). Two identifiers that differ in a digit are two things; a nomination rule that leaves names with digits
  out of spelling (as `names.name_score` already treats a word with a digit as having no ending) would save
  the calls without losing a join. Not built: it changes resolve, a separate step.
- **R100's adjudicator answers "unsure" for some right joins (found in R100, 2026-10-06; the user's choice).**
  Seven joins judged right in R99 are lost: furniture "cushions" / "cushion" (one review; an R75 gold pair, so
  identity recall 11/13 -> 10/13), "dimmer" / "dimmer function", "storage" / "storage mechanism", "upholstery" /
  "fabric"; held-out "2019-2022 Insight" / "2019-2022 Honda Insight"; generality "room B12" in two seminar
  notices and "summer quarterly round" / "quarterly inspection". The prompt asks for a stated role, place, organisation, date or event, which a thing named in the
  singular and the plural in one document does not give. Options: accept (precision first), or one more
  prompt rule for names of one document, measured by a replay (about $0.4).
- **A second adjudication pass for individuals (R100, 2026-10-06; open until a replayed pair needs it).** A
  pair is decided once, from its own lines. A join made in the pass could bring a side new evidence (the
  other mentions of the group it joined); asking again with it is not built until a replay shows a pair that
  needs it.
- **Held-out's C3 hard rule fails since R93 (found again in R99, 2026-10-06; closed by R108: a word key in a
  longer name goes to the chooser, which declines the link).** One
  record link is judged INCORRECT: "2017-2022 Rogue Sport" -> `Vehicle:ROGUE` (R93's cause 4: a sibling model
  linked to the base model by key; the recall text names the Rogue Sport as a separate model). A wrong record
  merge fails C3 (decision 3 of R93). R94-R96 fixed only furniture, so the R97-R103 plan's premise "every hard
  rule passes" was wrong for held-out. Not caused by R97-R99. A fix belongs to record linking (a model name with
  a further word is not the base model's key), in its own step.
- **Per-section subject anchoring of long documents (found in R97, 2026-10-06; open until a dataset needs it).**
  `ABOUT` gives a document or a section one subject. W3-named (R97) leads from a chunk to every record and
  individual it names, so the current corpora need nothing more; a long document whose sections are about
  different things named only once (a book, a long report) would need per-section anchoring. Built only when a
  dataset of that form is added (direction, section 5).
- **The chooser declined "frame construction" (found in R95b, 2026-10-06; fixed in R96).** R93 judged its old
  containment link to the sofa's Frame VALID; the LLM answered none. R96's prompt chooses it, with no other
  decision changed. A prompt change changes every call (the prompt is part of each cache key), so measuring
  one costs a full replay (about $0.03 on furniture), not only the changed cases.
- **End-to-end starts are decided by a tie-break (found in R96, 2026-10-06; open).** W1 orders equal exact
  hits by how many mentions refer to a node, then by node id (`anchor/navigation.py` `find`). F17's "frame"
  matches the sofa's Frame and the bed's lone "frame" alike, so one mention more or less flips its start and
  its end-to-end result. The question names no product; nothing in the graph tells the two frames apart for
  it. A rule that uses the question's other constraints (here "priced over $500") belongs to the query side,
  not to record linking.
- **Held-out's one near-miss mention was not replayed (R95b).** Only the furniture replay was paid for; the
  held-out build has 1 mention the chooser would be asked about, generality none. Their R95a numbers stand.
- **`ruff format --check` flags `structured/profiler.py` (found in R95a, pre-existing).** One line ruff would
  wrap differently; `ruff check` passes. R95 did not touch the file (a stray reformat was reverted).
- **C6's hard rule: pooled over pairs, or per start? (found in R93, 2026-10-06; the user's choice; moot for the
  current builds since R94, where no start is below 0.95).** The
  direction (section 7.2) says "of the chunks reached from a node, how many concern it ... hard for record and
  individual starts: >= 0.95". R93 scores it pooled over all (start, chunk) pairs and lists the starts below
  0.95. On furniture arm A the pooled rate passes (93/97 = 0.959), but three starts fail (A-1012 3/5, A-1021
  7/8, A-1070 3/4), all from the cross-product links that already fail C4. The reading is a definition, so
  it is the user's decision before step 6.
- **The direction's C6 flag misses what it was meant to find (found in R93).** `same_label_about` (a chunk's
  document is ABOUT another record of the start's label) fired on 0 furniture pairs, because a part's
  foreign chunk sits in a document ABOUT a product, which has another label. It fired 3 times on generality,
  each refuted. R87's scope rule (`scope_foreign`) was confirmed 6 of 6, with no miss. If the flag is used
  again, the scope rule is the one to use.
- **29 held-out mentions of a recall key are typed Vehicle and left unlinked (found in R87, judged in
  R93).** All 29 refer to the Recall record whose key they are named after (judge 29/29). Each sits in that
  recall's own document, which is already ABOUT the record, so no chunk becomes unreachable. They are missed
  links with a wrong type, not wrong links.

(Add items here during a step instead of widening its scope.)

- **`plan.CLAIM_OPS` is used nowhere (found in R86).** Its comment says the claim layer's primitives are
  left out of the records-only system, but that system is restricted by `PlanSchema.claims` and the
  `find_claims` rule, not by this set. Either use it or remove it in Step 9's clean-up.
- **(Fixed in R84.) Claim words cannot reach a mention that refers to a record (found in R83).** `plan_run._claim_words`
  searches concepts only, while `find_claims` matches mentions by their canonical id, which is a record or
  an individual for keyed and named things (R75). 5 furniture answers fail on it (F17 F21 F23 F24 F32).
  To be fixed in its own step, with a test that fails before the fix, if the user chooses it.
- **(Fixed in R85.) read_check verifies all candidates of one chunk together (found in R83).** `_read_check` asks the
  checker about the statement and each candidate's chunks, never about the candidate claim itself, so
  claims sharing a chunk get one verdict (G24: 7 of 28 verified with one quote).
- **(Addressed in R82 by one prompt example; not yet measured.) Held-out's named denials carry no cue (found in R77 part f).** Gemini keeps a Problem named by a denial
  ("DO NOT LOCK", "DID NOT STOP", "NO FEEDBACK") as the prompt allows, but leaves truth affirmed and
  `negation` empty, ignoring "the fact is still negated". Furniture's denials all moved into the field.
  Code cannot see it without a word list (the user's choice: none). Candidates, for the user: a second
  invented example in the prompt showing a named state with its `negation`; or reject a claim whose name
  holds the given negation words while truth is affirmed (catches only the case where a cue was given).
- **(Fixed in R81: the hedge is dropped, the claim kept.) Degree words given as a hedge reject good claims (found in R77 part f).** 9 furniture claims ("about 2
  hours", "a bit short", "kinda complicated") came with `hedge` on an actual claim and were rejected as
  `cue_without_assertion`. Candidate: on an actual claim, drop the hedge and keep the claim (it carries no
  assertion), or say in the prompt that degree and approximation words are no hedge.
- **An unmet expectation read as a denial (found in R77 part f).** "I expected much better quality and
  durability" became quality and durability negated with the cue "expected much better"; "they seem poorly
  manufactured" became possible with "seem". Both are the model's reading, grounded in the quote, so no
  code check applies; the judge counts them.
- **(Fixed in R80: `kg qa --plans`.) Every rebuild re-plans the QA questions (R77 parts b and f).** The planner prompt's schema text is read
  from the graph, so a rebuilt graph misses the planner cache and every plan is written anew: in part f
  every changed answer had a new plan. To measure a graph change by answers alone, the plans would have to
  be frozen (replayed from the earlier answers file) and only execution rerun.
- **The planner prompt says "Only claims the text states as holding come back" (R77 part d).** Since part d
  that is true of the claims a plan counts or lists; claims that go on to be read (part e) come back
  whatever their assertion. Left unchanged so that the planner's cached requests are reused and the
  change is measured alone; align it at the next change of the planner prompt.
- **(Changed in R77 part c.) The query default left out conditional claims (found in R77 part b).**
  `find_claims` kept affirmed, actual claims unless the question's words asked for others, as the task file's
  "counts default to affirmed and actual" says; 5 answers were lost to it (F17, F27, F28, F31, H06: "creaks
  whenever someone sits down"). A change of the planner prompt re-plans every question (R77: 10 answers lost
  and 5 gained by new plans alone), so part c changed code only. (The text2cypher prompt's "a claim that
  holds is affirmed and actual" was aligned in R77 part d.)
- **(Fixed in R77 part d: cues instead of word lists.) The negation check knows no negating verbs (found in R77 part b).** 9 furniture claims were rejected as
  negated without a negation word, although the model read them right: "prevents sagging", "eliminates
  flickering", "resistant to water rings and scratches". The closed list holds negators only; such verbs
  would widen it, with a re-extraction to measure.
- **(Fixed in R77 part d: truth of the statement, `triple_truth` derived by code.) A negation in the names instead of the field (found in R77 part b).** 5 furniture and 3 held-out negated
  claims are stored affirmed with the negation in the object ("doesn't close properly", "DOES NOT INDICATE
  FULL"): right by meaning, but a count over `truth` misses them. The prompt's example has a positive
  object; no code check can tell a fault named by a negation from a denied fact.

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
- **Derivation mistargets record documents (found in R67 part 3; closed by R109, 2026-10-07).** A derived
  fact's object is the node the document is ABOUT; for a record document that is the record itself, so all
  38 derived recall claims say "part PART_OF_VEHICLE 16V526000" with the campaign number as a Vehicle-typed
  entity (judge: 0/38 true). Candidate: on a record document, derivation targets the record's related domain
  node (the recall's `AFFECTS_VEHICLE` vehicle), or skips the document. Same root cause: these entities are
  created after `link_graphs` ran, so they carry no REFERS_TO. Still present in R68: in the held-out coverage
  sample alone 33 derived `INSTALLED_IN` facts point at a recall id typed as a Vehicle
  (`ENGINE INSTALLED_IN 15V436000`). **R109 skips the document** (the user's "option 1"): a document derives
  only onto a node of one of the object type's record labels (`derivation.derives_into`).
- **A recall's parts could be derived onto the vehicles it affects (found in R109, out of its scope).** R109
  derives nothing on a recall document, so "seatbacks" of recall 17V472000 is no longer said to be installed
  in any vehicle, while the recall record names the vehicles it covers through `AFFECTS_VEHICLE`. Candidate:
  on a document ABOUT a record of another label, derive onto the records of the object type that record is
  related to, when exactly one is; a recall covering several models would give one claim per model, which
  the text does not state, so the rule needs its own measurement.
- **The pass's precision sample is redrawn by any change in its mention set (found in R109).**
  `mention_eval.precision_sample` is `random.Random(102).sample` over the pass mentions in id order, so one
  added mention shifts every later position: R109's one new finding redrew 26 of held-out's 60 items, and
  precision read 52/59 against R103's 55/59 for the same pass output (both draws together 78/85). A
  comparison of pass precision between builds then mixes the pass's change with sampling. Candidate: draw
  by a seeded hash of each mention id (the 60 lowest), so an unchanged mention keeps its place; a change of
  the sample, so it needs its own step and keeps the committed sheets as they are.
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
- **A smoke run checks the whole pipeline but fails on a graph left by the tests (found in R15; closed by
  R112 with the item below).** The
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
  Still open in R114 (2026-10-08): five profiles of the same `data/` gave five different sample sets, so
  R114's test compares the PostgreSQL and the file profile without the samples.
- **The test suite wipes the working graph (found in R11; closed by R112: the tests use their own server,
  `neo4j-test` on bolt 7688).** `neo4j` tests share the one database with
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
