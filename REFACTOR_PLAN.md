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

## Found along the way

(Add items here during a step instead of widening its scope.)

- **Pass 2 names an entity "IT" (found in R61).** The prompt forbids pronouns, but nothing in code checks
  it. Candidate: `verify` rejects a subject or object that is only a pronoun (a closed word list, so it
  stays domain-neutral). That would remove 2 of the 12 held-out unsupported facts.
- **Pass 2 types instructions as a physical Component (found in R61, as in R55).** The type description
  excludes them, and the extractor does not follow it.
- **Questions 3 and 5 join through an entity's mentions, not the fact's own chunk (found in R58).** Entities
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
  still embeds. Cheap (about 100 short names), but not $0 and not offline.
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
