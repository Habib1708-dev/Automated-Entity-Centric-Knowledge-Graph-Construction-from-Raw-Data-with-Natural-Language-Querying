# kgbuilder

Automated knowledge graph construction from CSV/JSON tables and text documents, into Neo4j.

The design follows the course notes in `docs/lessons/` (an LLM proposes a declarative plan, rules execute
it), with one change: everything that can be computed exactly is computed in code, and the LLM only
decides what is left. Every LLM output is parsed into a pydantic model and validated in code before use;
every extracted fact carries a verbatim evidence quote that is verified against its source chunk.

## Setup

```
uv sync
docker compose up -d        # Neo4j 5 + APOC on bolt://localhost:7687, browser on http://localhost:7474
copy .env.example .env      # then set GEMINI_API_KEY
```

## Usage

```
uv run kg run data/ --goal "supply chain root cause analysis"    # whole pipeline, needs GEMINI_API_KEY
uv run kg run data/ --goal "..." --review                         # pause for review after plan and text schema
uv run kg run data/ --goal "..." --gold gold.json                 # also checks recall against labelled triples
uv run kg eval gold.json                                          # precision/recall/F1, ER accuracy, questions; writes out/judge_sheet.json
uv run kg eval gold.json --verdicts out/judge_verdicts.json       # plus the judge's validated precision/recall (see below)
uv run kg reset                                                   # clear Neo4j before a clean rerun
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db          # inspect runs, params, metrics, traces
```

### Model presets and datasets

A **preset** from [presets.yaml](presets.yaml) chooses the models *and the dataset*: two cheap ones that only
show the pipeline works, on small committed subsets of `data/` in `samples/`, and one for the results you
report, on the whole dataset. Without a directory argument a command reads the preset's dataset. Pick a
preset per command, or set a default with `KG_PRESET=dev` in `.env`:

```
uv run kg --preset smoke   run --goal "..."   # tiny subset, free Gemini key: does the code run?
uv run kg --preset dev     run --goal "..."   # small subset, paid key: does it run with real Gemini?
uv run kg --preset quality run --goal "..."   # the whole dataset, proper models: reported results (asks first)
```

Everything about a preset lives in [presets.yaml](presets.yaml) and nowhere else: models, thinking levels,
which key, the dataset and how it is sampled, the MLflow experiment, whether a run needs permission, and
a one-line description with the expected cost. What a run really cost is the `cost_usd` metric of its
MLflow run, computed from the token counts and the list prices in [prices.yaml](prices.yaml) (update the
prices there when Google changes them; a test checks that every preset's models have a price).

Graphs from `smoke` and `dev` say nothing about quality: too little data, a weak model.
The subsets are described in `presets.yaml`: the `sample` block of a preset names the source, the root
rows (for example `product_id` P-1000) and the documents with how many sections each keeps.
`uv run kg sample` rebuilds every subset from those blocks (`uv run kg sample dev` just one). It follows
the foreign keys the profiler finds: down from the root rows to every row that belongs to them, then up to
every row they point at, so no key dangles. The subsets live outside `data/`, because every stage reads
its directory recursively, and a test fails if the committed files ever differ from what the config says.

**Thinking levels.** Gemini 3 Flash reasons at length before it answers unless told otherwise, and that
reasoning is billed as output: on `data/` it was $1.00 of a $1.25 run. `SCHEMA_THINKING` and
`EXTRACT_THINKING` (`minimal`, `low`, `medium`, `high`; empty = the model's default) set the level per role;
the quality preset uses medium for the plan and the text schema and low for extraction, which brought a
run to $0.17 (see R16 in REFACTOR_PLAN.md).

**Permission for comprehensive runs.** Presets with `ask_permission: true` (today `quality`) and any
LLM run on a directory outside `samples/` are comprehensive runs: Claude Code asks you before it starts
one, through the hook `.claude/hooks/run_guard.py` (registered in `.claude/settings.json`). Commands you
type yourself are not affected.

**The free key.** `smoke` sends its requests with `GEMINI_FREE_API_KEY`, a key from a Google AI Studio
project *without billing*: requests cost nothing but are capped per day, and Google may use them to improve
its products (fine for the synthetic data here). Create it in AI Studio in a new project, then put it in
`.env` next to `GEMINI_API_KEY`. If it is missing, `smoke` stops with an error instead of falling back to
the billed key.

Priority: a variable set in the terminal > the preset > `.env` > the defaults, so one value can still be
changed for a single run (`$env:EXTRACT_MODEL="..."` in PowerShell); a directory given on the command line
beats the preset's dataset. Every run is tagged with its preset in MLflow. An unknown preset or a
misspelled key in `presets.yaml` stops with an error instead of being ignored.

### A local model (Ollama)

`LLM_PROVIDER=ollama` runs a local model instead of Gemini (no preset uses it). It needs
[Ollama](https://ollama.com) with two models, pulled once:

```
ollama pull qwen2.5:7b-instruct ; ollama pull nomic-embed-text
```

Then set `SCHEMA_MODEL` and `EXTRACT_MODEL` to `qwen2.5:7b-instruct` and `EMBED_MODEL` to `nomic-embed-text`.
Small local models are much weaker than Gemini: on `data/`, a 7B model could not design a valid
construction plan, about 80 % of its facts failed the evidence checks, and a run took about 25 minutes on
an 8 GB laptop GPU. That is why the smoke preset moved to the free Gemini key.

- `qwen2.5:7b-instruct` follows the JSON schemas reliably; `qwen3.5:4b` did not (it thinks at length and
  then breaks the JSON). The adapter always sends `think: false` and a 16k-token context window
  (`OLLAMA_NUM_CTX`), because Ollama silently cuts longer prompts.
- When the plan stage fails, continue with the reviewed plan and run the stages one by one:
  `copy tests\gold\domain_plan.json out\plan.json`, then `kg build data/`, `kg ingest-text data/` and so on.

Stages can also be run one at a time, with human review points in between:

```
kg profile data/  ->  kg plan data/ --goal "..."   (review out/plan.json)
                  ->  kg build data/
                  ->  kg ingest-text data/
                  ->  kg text-schema --goal "..."   (review out/text_schema.json)
                  ->  kg extract  ->  kg resolve [--undo]  ->  kg link  ->  kg validate [--gold gold.json]
```

`text-schema` and `extract` work on the chunks stored by `ingest-text`, never on re-chunked files, so
chunk ids in the graph and in the provenance of facts always agree.

Inputs in the data dir: CSV and tabular JSON/NDJSON (staged to CSV), plus md/txt/pdf (documents).
JSON files that are not tabular are reported as skipped, not silently ignored.

## Graph model

- Domain graph: labels and relationships from the approved plan (Product, Part, Supplier, ...).
- Lexical graph: `(Chunk)-[:PART_OF]->(Document)`, `(Chunk)-[:NEXT_CHUNK]->(Chunk)`, optional embeddings.
- Subject graph: `(:Entity {type, name, aliases})`, `(Chunk)-[:MENTIONS]->(Entity)`, facts as
  relationships carrying `chunk_id` and a verbatim `evidence` quote.
- Links: `(Document)-[:ABOUT]->(domain node)`, `(Entity)-[:REFERS_TO]->(domain node)`, recomputed on
  every `kg link`. Entities are matched by the plan's `name_column` inside the neighbourhood (2 hops) of
  the node their documents are ABOUT, so "legs" in a chair review links to the chair's legs; an entity
  named for several products gets one REFERS_TO per product. To follow a fact to the right one, go from
  its `chunk_id` to the document's ABOUT node:

  ```cypher
  MATCH (part:Entity)-[f:HAS_DEFECT]->(defect:Entity)
  MATCH (:Chunk {chunk_id: f.chunk_id})-[:PART_OF]->(:Document)-[:ABOUT]->(product)
  MATCH (part)-[:REFERS_TO]->(node)-[*0..2]-(product)
  RETURN defect.name, part.name, labels(node)[0], product
  ```

## Code map

```
src/kgbuilder/
  cli.py            composition root + Typer commands (the only place adapters are built)
  config.py         settings from the environment / .env
  core/             shared kernel: text normalisation, Cypher identifier escaping, error types
  llm/              LLMClient/Embedder protocols, Gemini adapter (retry), disk-cache decorator,
                    refine.py = the propose -> validate -> critique -> retry loop
  graph/            Neo4j driver factory
  tracking/         Tracker protocol + NullTracker, MLflow adapter (runs, LLM traces, usage metrics)
  structured/       staging -> profiler -> proposer (LLM) + plan (validation) -> importer
  text/             documents -> chunking -> lexical -> schema (LLM) -> extraction (LLM) -> subject_graph
  resolution/       matchers (Strategy) -> resolver (merge, undo) ; linking
  validation/       checks/ (Strategy families), validator, gold (gold file), evaluate (exact-match scoring), judge (LLM-as-a-judge sheet and scoring)
  pipeline/         Stage protocol + context/state, the concrete stages, the runner
```

| Stage (MLflow run) | Module | LLM? |
|---|---|---|
| `profile` | `structured/staging.py`, `structured/profiler.py` | no |
| `plan` | `structured/proposer.py`, `structured/plan.py` | yes, code-validated, then critic |
| `build_domain` | `structured/importer.py` | no |
| `ingest_text` | `text/documents.py`, `text/chunking.py`, `text/lexical.py` | embeddings only |
| `text_schema` | `text/schema.py` | yes, code-validated, then critic |
| `extract` | `text/extraction.py`, `text/subject_graph.py` | yes, every triple verified in code |
| `resolve` | `resolution/matchers.py`, `resolution/resolver.py` | borderline pairs only |
| `link` | `resolution/linking.py` | no |
| `validate`, `eval` | `validation/` | no |

## Experiment tracking

Every stage is one MLflow run; `kg run` is a parent run with nested stage runs. Each run logs the params
that explain its result (models, temperature, prompt version hashes, chunk sizes, thresholds), metrics
(counts, rates, rounds, duration, LLM calls and failed attempts, cache hits, embedding calls, latency, and
tokens: `prompt_tokens`, `completion_tokens` for the visible answer and `thinking_tokens` for the hidden
reasoning, which is billed as output too), the files written to `out/`, the prompt templates, and one trace
per LLM request, attached to its stage run. Every run is tagged with `git_sha` (`-dirty` when there were
uncommitted changes) and `code_version`. LLM responses are cached under `.cache/llm`, keyed by
model, temperature, prompt and output schema, so reruns are free and reproducible.

To evaluate a change (prompt, model, threshold): run, change one thing, run again, compare the two runs
in the MLflow UI on the stage metrics and on `validate` / `eval`.

## Gold file

```json
{"triples":   [{"subject": "Stockholm Chair", "predicate": "HAS_PROBLEM", "object": "wobbly legs",
                "doc_id": "product_reviews/stockholm_chair_reviews.md"}],
 "er_pairs":  [{"a": "Table", "b": "Tables", "same": true}],
 "questions": [{"question": "Who supplies part X?", "cypher": "MATCH ... RETURN s.name", "expected": ["..."]}]}
```

Every section is optional. Give `doc_id` and label those documents exhaustively: precision is computed
only over facts from labelled documents. A triple may carry `evidence`, the verbatim sentence it rests
on. Question Cypher runs in a read-only transaction. The committed reference set for `data/` is
`tests/gold/text_gold.json` (all 10 review files, labelled by Claude, not by hand; see its `_comment`).

## LLM-as-a-judge

Exact matching undercounts (`wobbly legs` versus `legs wobble`), so `kg eval` also supports a second,
meaning-based score. The judge is Claude in the Claude Code session, never the model that built the graph.

1. `kg eval gold.json` writes `out/judge_sheet.json`: every in-scope fact with its exact-match result, and
   every gold triple with whether it was found. Only unsettled facts and unfound gold need a judge.
2. The judge writes `out/judge_verdicts.json`: per unsettled fact `SUPPORTED` (with the review sentence),
   `UNSUPPORTED` (with a reason code: `not_in_text`, `wrong_relation`, `wrong_entity`, `contradicted`)
   or `AMBIGUOUS`; per unfound gold triple the sheet fact that states it, or `null`. Format:
   `validation/judge.py`.
3. `kg eval gold.json --verdicts out/judge_verdicts.json` logs `precision_validated`,
   `recall_validated`, `f1_validated`, `ambiguous_rate`, `vague_rate`, `unsupported_<reason>` and
   `gold_corrections` next to the exact-match metrics, with `judge_model` and the file hashes as params.
   A verdict file that does not cover exactly the graph's sheet is refused, never scored silently.

## Development

```
uv run pytest                    # all tests; those marked neo4j skip when Neo4j is down
uv run pytest -m "not neo4j"     # fast tests, no database, no network
uv run ruff check . ; uv run ruff format .
```

Note: the `neo4j` tests wipe the database they connect to. Working rules for contributors (and for
Claude) are in `CLAUDE.md`; the audit and the refactoring history are in `REFACTOR_PLAN.md`.
