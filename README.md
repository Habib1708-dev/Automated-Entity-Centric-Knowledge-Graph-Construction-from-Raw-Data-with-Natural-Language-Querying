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
uv run kg eval gold.json                                          # precision/recall/F1, ER accuracy, questions
uv run kg reset                                                   # clear Neo4j before a clean rerun
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db          # inspect runs, params, metrics, traces
```

Models: both roles default to `gemini-3.8-flash` to keep development runs cheap (a full run on `data/` is
about $1). For a run whose results you report, use the stronger model for the plan and text schema, for
that run only (the model is logged as a param, so MLflow keeps the runs apart):

```
$env:SCHEMA_MODEL="gemini-3.1-pro-preview"; uv run kg run data/ --goal "..."   # PowerShell
SCHEMA_MODEL=gemini-3.1-pro-preview uv run kg run data/ --goal "..."           # bash
```

In PowerShell the variable stays set for the rest of that terminal session; `Remove-Item Env:SCHEMA_MODEL`
switches back.

### Free smoke runs with a local model (Ollama)

To check that the pipeline *works* without paying for API calls, run it on a local model with
[Ollama](https://ollama.com). Small local models are much weaker than Gemini, so the resulting graph says
nothing about the quality of the method: use this for plumbing checks only, never for reported results.

```
ollama pull qwen2.5:7b-instruct ; ollama pull nomic-embed-text      # once
$env:LLM_PROVIDER="ollama"; $env:SCHEMA_MODEL="qwen2.5:7b-instruct"; $env:EXTRACT_MODEL="qwen2.5:7b-instruct"
$env:EMBED_MODEL="nomic-embed-text"; $env:MLFLOW_EXPERIMENT="kgbuilder-smoke"
uv run kg reset ; uv run kg run data/ --goal "supply chain root cause analysis"
```

- `qwen2.5:7b-instruct` follows the JSON schemas reliably; `qwen3.5:4b` did not (it thinks at length and
  then breaks the JSON). The adapter always sends `think: false` and a 16k-token context window
  (`OLLAMA_NUM_CTX`), because Ollama silently cuts longer prompts.
- The separate MLflow experiment keeps smoke runs out of the real results; the LLM cache never mixes
  providers because its key contains the model name.
- A 7B model usually cannot design a valid construction plan: `kg run` then stops at the plan stage
  (the code gate working as intended). Continue with the reviewed plan and run the stages one by one:

  ```
  copy tests\gold\domain_plan.json out\plan.json
  uv run kg build data/ ; uv run kg ingest-text data/ ; uv run kg text-schema --goal "..."
  uv run kg extract ; uv run kg resolve ; uv run kg link ; uv run kg validate
  ```

  On an 8 GB laptop GPU this takes about 25 minutes (Ollama answers one request at a time), and about
  80 % of the extracted facts are rejected by the evidence checks, against 0 % with Gemini.

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
  validation/       checks/ (Strategy families), validator, evaluate (gold-set scoring)
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
only over facts from labelled documents. Question Cypher runs in a read-only transaction.

## Development

```
uv run pytest                    # all tests; those marked neo4j skip when Neo4j is down
uv run pytest -m "not neo4j"     # fast tests, no database, no network
uv run ruff check . ; uv run ruff format .
```

Note: the `neo4j` tests wipe the database they connect to. Working rules for contributors (and for
Claude) are in `CLAUDE.md`; the audit and the refactoring history are in `REFACTOR_PLAN.md`.
