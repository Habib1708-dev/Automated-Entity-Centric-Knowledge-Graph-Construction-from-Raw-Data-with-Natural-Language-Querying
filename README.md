# kgbuilder

Automated knowledge graph construction from CSV files and text, into Neo4j.

The design follows the course notes in `lesson*.md` (LLM proposes a declarative plan, rules execute it),
with one change: everything that can be computed exactly is computed in code, and the LLM only decides
what is left.

## Setup

```
uv sync
docker compose up -d        # Neo4j 5 + APOC on bolt://localhost:7687, browser on http://localhost:7474
copy .env.example .env      # then set GEMINI_API_KEY
```

## Usage

```
uv run kg run data/ --goal "supply chain root cause analysis"   # whole pipeline, needs GEMINI_API_KEY
uv run kg run data/ --goal "..." --gold gold.json               # also scores recall against labelled triples
uv run kg reset                                                  # clear Neo4j before a clean rerun
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db         # inspect runs, params, metrics, artifacts
uv run pytest                                                    # end-to-end test uses a scripted LLM, needs Neo4j
```

Stages can also be run one at a time, with human review points in between:
`kg profile` -> `kg plan` (review `out/plan.json`) -> `kg build` -> `kg ingest-text` -> `kg text-schema`
(review `out/text_schema.json`) -> `kg extract` -> `kg resolve` -> `kg link` -> `kg validate`.

Inputs in the data dir: CSV and JSON (staged to CSV tables), plus md/txt/pdf (documents).

## Graph model

- Domain graph: labels and relationships from the approved plan (Product, Part, Supplier, ...).
- Lexical graph: `(Chunk)-[:PART_OF]->(Document)`, `(Chunk)-[:NEXT_CHUNK]->(Chunk)`, optional chunk embeddings.
- Subject graph: `(:Entity {type, name, aliases})`, `(Chunk)-[:MENTIONS]->(Entity)`, facts as relationships carrying
  `chunk_id` and a verbatim `evidence` quote.
- Links: `(Document)-[:ABOUT]->(domain node)`, `(Entity)-[:REFERS_TO]->(domain node)`.

## Pipeline

| Stage | Module | LLM? |
|---|---|---|
| Stage JSON, profile tables | `ingest.py`, `profiler.py` | no |
| Propose + critique plan | `schema.py`, `plan.py` | yes, validated in code each round |
| Import domain graph + reconciliation | `importer.py` | no |
| Chunk text, lexical graph | `lexical.py` | no (embeddings optional) |
| Propose entity/fact types | `textschema.py` | yes, validated in code |
| Extract triples, evidence verified in code | `extract.py` | yes |
| Entity resolution | `resolve.py` | borderline pairs only |
| Link the three graphs | `link.py` | no |
| Validate structure, provenance, consistency, accuracy | `validate.py` | no |

Every stage is one MLflow run (`tracking.py`); the whole pipeline is a parent run. LLM responses are cached
under `.cache/llm`, keyed by model, prompt and output schema.
