# Tech stack and execution plan

## Tech stack

| Layer | Choice | Role |
|---|---|---|
| Language / packaging | Python 3.11+, uv, hatchling | project, deps, `kg` CLI |
| CLI | Typer | `kg profile / plan / build / extract / eval` |
| Config / schemas | pydantic v2, pydantic-settings | plan, entity and fact-type models; validated LLM output |
| Structured data profiling | DuckDB | types, unique columns, FK candidates |
| LLM | Gemini via `google-genai` (structured output) | schema proposal, critic, entity and fact-type proposal, triple extraction, borderline entity resolution |
| Embeddings | Gemini embeddings (or OpenAI, swappable) | chunk vectors, entity-resolution candidates |
| Graph DB | Neo4j 5 + APOC (docker compose) | domain, lexical and subject graphs |
| Driver | `neo4j` Python driver | all writes are parameterised Cypher |
| Entity resolution | rapidfuzz (Jaro-Winkler / token ratio) + embeddings + LLM for borderline pairs only | merge duplicates |
| Orchestration | Plain Python pipeline stages with a human-approval file (`out/plan.json`). Optional later: Google ADK for a conversational front end, as in the lessons | |
| Experiment tracking | **MLflow** (local `mlruns/` or `mlflow server` with SQLite) | see below |
| LLM cache | on-disk cache under `.cache/llm` keyed by model + prompt + schema | reproducibility and cost |
| Testing | pytest (importer tests need Neo4j up) | |
| Test data | `data/` (downloaded from neo4j-contrib/agentic-kg) | see below |

### Where MLflow fits
- **One MLflow run per pipeline execution** (`kg plan`, `kg build`, `kg extract`, `kg eval`).
- **Params:** model name, temperature, prompt version/hash, chunk size and overlap, ER thresholds, dataset path.
- **Metrics:** critic rounds to convergence, plan validation errors, nodes and relationships created vs. expected (reconciliation), number of triples extracted, % of triples with a verified evidence span, ER merges, precision/recall on the gold set, token usage, latency, cost.
- **Artifacts:** `plan.json`, critic feedback per round, extracted triples (JSONL), ER merge log, evaluation report.
- **MLflow Tracing** (`mlflow.gemini.autolog()` or manual spans) for each LLM call, to inspect prompts and responses.
- Use it to compare prompt or model variants (an experiment per stage).

### Test dataset (downloaded to `data/`)
Furniture supply-chain sample from neo4j-contrib/agentic-kg:
- Structured (CSV): `products.csv` (10), `assemblies.csv` (64), `components.csv` (88), `suppliers.csv` (20), `part_supplier_mapping.csv` (176).
- Unstructured: `product_reviews/*.md` (10 review files, one per product).
- Entities are chained as product → assembly → sub-assembly/part → supplier.
- Caveat: reviews are the only unstructured source, so text-extraction quality is measured on this small corpus.

## Execution plan

Status at the start: profiler, plan proposal and critique, and domain importer exist (README "done"). Each step below ends in something runnable and testable.

### Step 1: Foundations and observability (about 1 to 2 days)
- Add `mlflow` to `pyproject.toml`, and add a `tracking.py` helper (run context manager, param/metric/artifact helpers, LLM call tracing).
- Wire it into the existing `profile`, `plan` and `build` commands.
- Point `.env.example` and the CLI at `data/`.
- **Deliverable:** `kg profile data/` and `kg plan data/ ...` produce logged MLflow runs, visible in the MLflow UI.

### Step 2: Structured path end to end (about 2 to 3 days)
- Run the plan stage against Gemini on `data/`, fix the prompt and validation issues that show up, and review `out/plan.json`.
- Run `kg build data/`. Add reconciliation checks: row counts vs. node counts, relationship counts, no orphan nodes.
- Write a gold expectation file for the domain graph (expected labels, relationship types, counts).
- **Deliverable:** a correct domain graph in Neo4j, with tests and reconciliation metrics logged in MLflow.

### Step 3: Text ingestion, chunking and lexical graph (about 2 days)
- Markdown loader and a review-aware chunker (split by review or section, with overlap).
- Create `Document` and `Chunk` nodes with embeddings. Add `NEXT_CHUNK` and `FROM_DOCUMENT` relationships, plus a link from each document to its `Product` in the domain graph.
- Create a vector index.
- **Deliverable:** `kg ingest data/product_reviews` builds the lexical graph. A test checks chunk counts and product links.

### Step 4: Entity and fact-type proposal (about 2 days)
- Entity-type proposer (NER schema, using the domain graph's node descriptions as context to avoid ambiguities like "Assembly"), and a fact-type proposer (subject, predicate, object).
- Write the result to a reviewable `out/text_schema.json` (the human approval step, as in step 2). Add a critic pass with code validation.
- **Deliverable:** `kg propose-text-schema` writes an approved schema file. Proposal quality is tracked across prompt versions in MLflow.

### Step 5: Triple extraction with evidence (about 3 days)
- Per-chunk extraction constrained to the approved schema, with a structured output.
- Every triple must carry a quote that is verified as an exact span of the chunk, and anything unverified is rejected in code.
- Write subject-graph nodes and relationships with `MENTIONED_IN` links back to chunks.
- Link extracted entities to domain nodes where they exist (e.g. a Product, or a Part).
- **Deliverable:** `kg extract` fills the subject graph. Logged metrics are triples per chunk and the evidence-verified rate.

### Step 6: Entity resolution (about 2 days)
- Candidates from rapidfuzz plus embedding similarity, auto-merge above a high threshold, and LLM adjudication only for borderline pairs.
- Log every merge decision for audit, and make the merge reversible.
- **Deliverable:** `kg resolve`. Duplicate counts before and after are logged in MLflow.

### Step 7: Evaluation harness (about 2 to 3 days)
- Build a small gold set by hand-labelling triples for 2 to 3 review files.
- Compute precision/recall for entities and triples, evidence validity, and ER accuracy.
- Run end-to-end questions against the graph (e.g. "which suppliers of the parts in product X have poor reviews?") and check the answers.
- **Deliverable:** `kg eval` produces a report artifact in MLflow. It is the regression suite used to compare prompt and model variants.

### Step 8: Packaging and (optional) agent front end (about 2 days)
- One-command pipeline `kg run data/` that chains the stages, with approval pauses.
- Optional ADK conversational wrapper (user intent, then file suggestion) that calls these stages as tools.
- Docs, and a reproducible demo script for the thesis.
- **Deliverable:** clean-clone reproduction from `uv sync` to a full KG plus evaluation report.

### Dependencies
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Step 3 can start in parallel with step 2 once the domain graph exists. Step 7's gold-set labelling can start as early as step 5.

### Risks
- **Small text corpus:** only 10 review files, so metrics will be noisy. Mitigation: a hand-labelled gold set, and a note in the thesis about the limits.
- **LLM nondeterminism:** mitigate with the cache, fixed temperature, and logging every prompt version to MLflow.
- **Schema ambiguity:** mitigate by passing node descriptions from the domain graph into the text-schema prompts.
