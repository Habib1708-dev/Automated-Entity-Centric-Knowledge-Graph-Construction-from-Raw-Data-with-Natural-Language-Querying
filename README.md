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
uv run kg rescore SHEET gold.json --verdicts V.json              # re-score an earlier eval run's logged sheet (no graph)
uv run kg coverage-sample tests/gold/r68/x_sample.json           # fixed random sample of sentences (coverage, below)
uv run kg coverage-sample S.json --build B --data D             # ... from a finished build's corpus, no graph (R101)
uv run kg extract --from-build B                                # an earlier build's claims, re-verified, no LLM (R102)
uv run kg coverage-sheet tests/gold/r68/x_sample.json            # what the graph stores about each; writes out/coverage_sheet.json
uv run kg coverage SHEET VERDICTS                                 # score the judge's coverage verdicts (no graph)
uv run kg assertion SHEET GOLD VERDICTS                           # truth, modality, condition kept (R77; no graph)
uv run kg audit-snapshot BUILD --data D --logged L --out O        # graph audit: offline snapshot, fidelity, code checks (R87; no graph)
uv run kg audit-relink BUILD --data D --logged L --out O  # replay the record matching under the current rules (R94; no graph)
uv run kg audit-relink BUILD --data D --logged L --out O --choose  # ... with the LLM choosing among near misses (R95b; paid)
uv run kg audit-relink BUILD --data D --logged L --out O --join  # ... and the individuals decided again (R98; LLM + embedder)
uv run kg audit-relink BUILD --data D --logged L --out O --join --faithful  # gate: the build's own inputs give its resolve.json
uv run kg anchor-eval BUILD --data D --logged L --targets T --arm anchor|layered --out O  # anchor-graph criteria C0-C2, C5, C7-C9 (R90; no graph)
uv run kg anchor-compare BUILD --data D --targets T --anchor-report A --layered-report L --out O  # arm C (vector) + McNemar pairing (R92; embeds, cents)
uv run kg anchor-sheets BUILD --dataset N --data D --logged L --anchor-report A --layered-report L --out O  # blind judging sheets C3, C4, C6 (R93; no graph)
uv run kg anchor-judged BUILD --judged J --identity-gold G --data D --logged L --anchor-report A --out O  # score the judge's verdicts on C3, C4, C6 (R93)
uv run kg mention-eval BUILD --dataset N --data D --logged L --gold-dir tests/gold/r101 --out O [--verdicts V]  # mention recall and pass precision (R102; no graph)
uv run kg ask "Which parts crack?"                                # answer one question from the graph, with citations
uv run kg qa tests/gold/qa/furniture_qa.json                      # every gold question: graph, vector-only, records plus vector
uv run kg qa GOLD --system graph --plans tests/gold/r80/furniture   # replay frozen plans on a changed graph (R80)
uv run kg qa-score GOLD out/answers_graph.jsonl --verdicts V.json # score with the judge's verdicts on free text (no graph)
uv run kg qa-compare A/qa_outcomes_graph.jsonl B/qa_outcomes_vector.jsonl  # paired McNemar test of two systems (no graph)
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
uv run kg --preset heldout run --goal "..."   # quality settings on the held-out NHTSA data (asks first)
uv run kg --preset quality_deepseek run ...    # quality with DeepSeek-V4.1-Flash generating (asks first)
uv run kg --preset heldout_deepseek run ...    # the same on the held-out data (asks first)
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

**The held-out dataset.** `heldout/nhtsa/` holds a second, mixed-format dataset (CSV, nested JSON,
NDJSON, Markdown; real NHTSA recalls and owner complaints) used only to test generalisation, with the
pipeline frozen; see its README.

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

### DeepSeek

`LLM_PROVIDER=deepseek` (the presets `quality_deepseek` and `heldout_deepseek`) generates with DeepSeek's
OpenAI-compatible API (`DEEPSEEK_API_KEY` in `.env`; model id `deepseek-flash` is DeepSeek-V4.1-Flash).
DeepSeek has no embedding model, so vectors still come from Gemini (`GEMINI_API_KEY`). It also has no
schema-constrained decoding, only a JSON mode: the adapter sends the pydantic schema as JSON Schema in a
system message, and a reply that does not validate (or comes back empty, which the API may do in JSON mode)
is retried. Thinking levels map to DeepSeek's efforts: `low` → low, `medium` and `high` → high, `minimal`
switches thinking off. `cost_usd` uses the peak price, so it is an upper bound (off-peak costs half).

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
- The proposal stages are not deterministic: a rerun can propose other labels and relation names. An
  evaluation run therefore pins both reviewed proposals before building: `copy tests\gold\domain_plan.json
  out\plan.json` and `copy tests\gold\text_schema.json out\text_schema.json`, then `kg build`,
  `kg ingest-text`, `kg extract`, `kg link`, `kg resolve`, `kg attach`, `kg eval tests/gold/text_gold.json`
  (since R75 `kg link` comes before `kg resolve`; since R76 `kg attach` follows it).

Stages can also be run one at a time, with human review points in between:

```
kg profile data/  ->  kg plan data/ --goal "..."   (review out/plan.json)
                  ->  kg build data/
                  ->  kg ingest-text data/
                  ->  kg text-schema --goal "..."   (review out/text_schema.json)
                  ->  kg extract  ->  kg link  ->  kg mention-pass  ->  kg resolve [--undo]  ->  kg attach
                  ->  kg validate [--gold gold.json]
```

`kg mention-pass` (R101) asks the LLM, chunk by chunk, for the things the text names or talks about that no
claim names (tests/gold/r101/rules.md), refuses in code every finding a visible rule rejects, and writes the
rest as mentions; it runs after `kg link` (derivation must not see them) and before `kg resolve`.
`kg resolve` decides what every mention refers to (a record, an individual or a concept) and runs after
`kg link`, because records are matched inside the scope of the things the documents are ABOUT;
`kg resolve --undo` removes that identity layer again. `kg attach` then decides which records and
individuals each claim is about (no LLM); it reads the identity edges, so it runs after every `kg resolve`
and every `kg link`. `kg resolve --preview` lists the concept pairs a
resolve run would consider (spelling or meaning, score, joined on spelling alone or asked to the LLM)
without an LLM call or a write. Which pairs close in meaning are
asked is a blocking rule (`er_embedding_blocking`): `threshold` (an absolute score, chosen per dataset) or
`mutual_nearest` (each name among the other's `er_neighbours` nearest; no scale to choose).

`text-schema` and `extract` work on the chunks stored by `ingest-text`, never on re-chunked files, so
chunk ids in the graph and in the provenance of facts always agree.

Inputs in the data dir: CSV and tabular JSON/NDJSON (staged to CSV), plus md/txt/pdf (documents).
JSON files that are not tabular are reported as skipped, not silently ignored.

## Graph model

- Domain graph: labels and relationships from the approved plan (Product, Part, Supplier, ...).
- Lexical graph: `(Chunk)-[:PART_OF]->(Document)`, `(Chunk)-[:NEXT_CHUNK]->(Chunk)`, optional embeddings.
- Subject graph: a `(:Mention {type, name, doc_id})` is one name of one type in one document (since R75);
  `(Chunk)-[:MENTIONS]->(Mention)`. Each claim is its own node (since R64), pointing at the mentions of its
  two ends, so a claim keeps what its own document said:

  ```
  (thing)-[:HAS_OBSERVATION]->(:Observation {id, predicate, chunk_id, evidence, subject_name, object_name, extractor,
                                             polarity, time, value, unit})
  (:Observation)-[:SUBJECT]->(:Mention)   (:Observation)-[:OBJECT]->(:Mention)   (:Observation)-[:FROM]->(:Chunk)
  ```

  The id is built from the chunk, the claim's own wording and its time (when it has one), so it survives
  every identity decision. Three reviews of one claim are three observations.
  Since R66 every claim has a `polarity` (`positive`, `negative` or `neutral`), so one fact type holds
  praise, faults and plain statements, and a `time` copied from its sentence ("after just two months of
  use") when the sentence gives one. A claim about a number ends in the built-in type `Value`: the
  observation carries `value: 25.0, unit: 'kg'` (known units are normalised, others kept as written), and
  every wording of one number refers to one concept named in a canonical spelling ("25 kg"). Code rejects a
  number or a time that is not in the quote.
- Identity (since R75): every mention `REFERS_TO {canonical, name, kind, reason, score, evidence, by}` one
  canonical entity, decided by `kg resolve` from the text schema's identity class of its type:
  `keyed` types refer to a record of their plan labels (by its key, by name inside the scope of the
  document's thing, or by a key attribute in the same sentence: "Maria Lopez (Finance Office)" is the
  Maria Lopez whose team is Finance Office; records still tied are not linked, the mention is logged as
  ambiguous); `individual` types, and keyed mentions no record fits, refer to an `(:Individual)`, and one
  name in two documents stays two things unless the text gives evidence: name variants ("Dr. J. Pike",
  "Jon Pike", "Jonathan Pike") only nominate a pair, which is joined when a record's key attribute stands in
  the sentence or an LLM answers "the same" with a quote from each side that code finds in that side's own
  text; `concept` types refer to a `(:Concept)` per type and name, and entity resolution joins concepts
  that are the same kind. Entity resolution never joins two numbers, nor two kinds that claims use with
  opposite polarity ("resistant to scratches" and "scratches easily"), nor two names one sentence uses as
  two things, nor a part and its whole (a claim of a fact type the schema marks `part_of`, or a name that is
  the other plus one word at its end: "transmission" and "transmission box"). Nothing is merged: undoing a decision deletes its edge, and the mention then stands for
  itself. The fact reader of validation flattens every claim into one triple with its ends' canonical
  names and every other name their mentions are written with, leaving out self-references and exact
  repeats (same entities, predicate, chunk, quote and time).
- Derived facts: a fact type the text schema marks `"derived": true` (in the reference schema:
  `PART_OF` from a Component or Assembly to a Product) is never asked from the extractor. `kg link` writes
  it from `Mention <-[:MENTIONS]- Chunk -[:PART_OF]-> Document -[:ABOUT]-> product`, one observation per
  mention chunk, with the chunk's sentence naming the part as `evidence` and `extractor: "derived"`; its
  object is the document's mention of the product.
- Links: `(Document)-[:ABOUT]->(domain node)` and `(Chunk)-[:ABOUT]->(record)` for a section whose heading
  names the record's key, recomputed on every `kg link`.
- Attachment (since R76): `(record or :Individual)-[:HAS_OBSERVATION {name, how, evidence}]->(Observation)`,
  recomputed on every `kg attach`. Code decides what a claim is about, never the model, and every edge names
  its route: `key_in_sentence` (a name of a record or individual that a mention of the claim's document
  refers to stands in the claim's quote: "the mechanical seal of pump HP40-1183 failed"), `part_of` (the
  claim's subject is a part of the thing, by a claim of the same document of a fact type marked `part_of`),
  `section` (the chunk is ABOUT the record) and `document` (the document is ABOUT it). A document `kg link`
  leaves about nothing is ABOUT the record its sentences name clearly most often (`ABOUT {how: 'text'}`).
  The most specific route wins per kind of thing (a record's label, an individual's type), so a quote
  naming another staff member replaces the document's, while a complaint's section record and the
  document's vehicle both keep the claim. Concepts never hold claims. Every question starts at the thing
  and walks its observations:

  ```cypher
  MATCH (product)-[:HAS_OBSERVATION]->(o:Observation {predicate: 'HAS_DEFECT'})
  MATCH (part:Mention)<-[:SUBJECT]-(o)-[:OBJECT]->(defect:Mention)
  MATCH (part)-[:REFERS_TO]->(node)-[*0..2]-(product)
  RETURN defect.name, part.name, labels(node)[0], product
  ```

## Code map

```
src/kgbuilder/
  cli.py            composition root + Typer commands (the only place adapters are built)
  config.py         settings from the environment / .env
  core/             shared kernel: text normalisation, ids, Cypher identifier escaping, error types
  llm/              LLMClient/Embedder protocols, Gemini adapter (retry), disk-cache decorator,
                    refine.py = the propose -> validate -> critique -> retry loop
  graph/            Neo4j driver factory ; canonical (how readers find what a mention refers to)
  tracking/         Tracker protocol + NullTracker, MLflow adapter (runs, LLM traces, usage metrics)
  structured/       staging -> profiler -> proposer (LLM) + plan (validation) -> importer
  text/             documents -> chunking -> lexical -> schema (LLM) -> extraction (LLM) -> subject_graph
                    -> mention_pass (LLM, R101: the things no claim names, checked in code)
  resolution/       linking (ABOUT) -> derivation ; identity: mentions -> records ->
                    individuals (variants, identity_evidence: what the adjudicator is shown) / concepts (matchers, blocking, guards: Strategies -> resolver
                    decisions) -> identity_graph (the edges) ; attachment (HAS_OBSERVATION, after identity)
  validation/       checks/ (Strategy families), validator, gold (gold file), evaluate (exact-match scoring), judge (LLM-as-a-judge sheet and scoring)
                    sentences -> coverage_sheet -> coverage (coverage estimate), interval (Wilson intervals)
                    -> assertion (truth, modality and condition against the assertion gold)
                    mention_gold (R101: the things sampled sentences name, for the mention pass)
                    -> mention_eval (R102: recall of that gold, judged precision of the pass's mentions)
                    qa_gold (question-answer gold file), qa_records (record answers computed by DuckDB)
                    -> qa (answer scoring, outcome rows) -> paired (McNemar comparison of two systems)
                    target_gold (anchor-graph targets: names, aliases, the records and mentions they reach)
  audit/            graph-correctness audit (R87): inputs -> snapshot (a build rebuilt offline) -> fidelity
                    (against its logged counts) ; scope -> checks (provenance, flags) ; reach (traversal) ;
                    relink (the record matching replayed under the current rules, R94) -> reidentify (the
                    individuals' joining replayed on it, faithful to the build or measured, R98)
  anchor/           anchor-graph evaluation (R90): navigation (W1-W5 in two arms over the audit snapshot) ;
                    targets (target gold on a build's nodes) -> criteria (C2, C5, C7, C8, C9) -> report
                    -> vector (arm C, C5 by cosine) -> compare (McNemar, question by question)
                    sheets <- sheet_builder (blind sheets of C3, C4, C6, R93) -> judged -> judged_report
  query/            names -> traversal / graph_store -> reader ; ranking ; systems (graph system, records plus
                    vector RAG, vector-only baseline)
                    planner -> plan (primitives, check) -> plan_cypher -> plan_run, read_check ; graph_schema
                    exact (text2cypher, the plans' logged fallback) with cypher_check
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
| `link` | `resolution/linking.py`, `resolution/derivation.py` | no |
| `resolve` | `resolution/identity.py`, `resolution/records.py`, `resolution/concepts.py`, `resolution/resolver.py` | borderline concept pairs only |
| `validate`, `eval` | `validation/` | no |
| `coverage_sample`, `coverage_sheet`, `coverage` | `validation/sentences.py`, `validation/coverage_sheet.py`, `validation/coverage.py` | no |
| `assertion` | `validation/assertion.py` | no |
| `audit_snapshot` | `audit/` | no |
| `ask`, `qa_graph`, `qa_vector` | `query/`, `validation/qa.py` | yes, the reader; every citation checked in code |
| `qa_score` | `validation/qa.py` | no |
| `qa_compare` | `validation/paired.py` | no |

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
 "questions": [{"question": "Who supplies part X?", "cypher": "MATCH ... RETURN s.name", "expected": ["..."]}],
 "identity_pairs": [{"a": {"doc_id": "minutes.md", "names": ["Jon Pike"]},
                     "b": {"doc_id": "award.md", "names": ["Jonathan Pike"]}, "same": true,
                     "evidence": [{"doc_id": "minutes.md", "quote": "..."}, {"doc_id": "award.md", "quote": "..."}]}]}
```

`identity_pairs` (R75) are mention pairs: a name in one document and a name in another (or the same) one,
the same individual, record or kind or not. `kg eval` logs `identity_precision`, `identity_recall` and
`identity_apart_rate` (the gold's different pairs kept apart) with their counts; a name the graph never
extracted is counted apart (`identity_not_extracted`). The current gold is `tests/gold/r75/`.

Every section is optional. Give `doc_id` and label those documents exhaustively: precision is computed
only over facts from labelled documents. A triple may carry `evidence`, the verbatim sentence it rests
on. Question Cypher runs in a read-only transaction. The committed reference set for `data/` is
`tests/gold/text_gold.json` (all 10 review files, labelled by Claude, not by hand; see its `_comment`).

## Evaluation results

The criteria and metric definitions live in `docs/evaluation/README.md`; measured numbers are dated
snapshots next to it (`docs/evaluation/results_<date>.md`), each pinned to a commit and an MLflow run.
A snapshot describes the system on its date only. `docs/` is git-ignored (moved there on 2026-09-22),
so these files exist only in the local checkout, like the skills.

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

## Coverage estimate

How much of what the text states can a question reach? No gold set is needed: a fixed sample of sentences
is judged claim by claim (R68).

1. `kg coverage-sample FILE` splits the graph's chunks into sentences and draws a seeded random sample
   (default 40 sentences, seed 68). The sample is committed; a sentence is identified by its document and
   wording, so the same sample can be measured on another graph of the same text.
2. `kg coverage-sheet FILE` finds each sentence in the current graph and writes `out/coverage_sheet.json`:
   its chunk, the things the chunk hangs on (with their record fields) and the observations from it.
3. The judge (Claude in the session) first lists each sentence's claims from the text alone, then says for
   each which observations or record fields state it, or the first of ten ordered causes why none does
   (`extraction` first, then `no_schema_type`, `identity`, `attachment`, `assertion`, `attribution`, `role`,
   `event_structure`, `concept`, `other`). Format: `validation/coverage.py`.
4. `kg coverage SHEET VERDICTS` logs `coverage` (stated by something stored) and `reachable` (stored, or
   about a thing its chunk hangs on) with their Wilson intervals, coverage by polarity and over the claims
   the schema had a place for, and `missed_<cause>` per cause. It needs no graph.

**A claim's assertion (R77).** Every observation says whether the text states or denies it (`truth`:
affirmed / negated), whether it holds, may hold or holds under a condition (`modality`: actual / possible /
conditional) and, for a conditional one, the condition's words. Extraction checks each against the quote
(a negation, a modal word, a condition word); a query plan finds only the claims that hold unless the
question's own words ask for others. `kg assertion SHEET GOLD VERDICTS` scores the judge's matching of the
assertion gold (`tests/gold/r77/`, rules in `assertion_rules.md`) against a coverage sheet of its sample:
per field the share of matched claims that keep their label, by the judge (`<field>_kept`) and exactly
(`<field>_exact`), overall and per gold value.

## Question answering

The graph as an index into the text (layered-model Steps 2-4, R71-R74). Three systems answer the same
question with the same reader model and the same number of chunks (`QA_TOP_K`), so they differ only in
how they choose:

- `graph` (R74): the model writes a query plan of fixed primitives (`find_entity`, `filter_records`,
  `related`, `find_claims`, `read_check`, `retrieve_chunks`, ending in `list`, `count`, `sum`, `rank` or
  `answer_from_chunks`); code checks it against the graph's schema (names, value types, an optional tone
  or time filter only with the question's own words), compiles each step to parameterised, read-only
  Cypher and runs it. Counts and lists are computed by code; `read_check` reads each candidate's text
  and keeps it only with a quote code finds in the chunk. A refused or failing plan gets one retry with
  the reasons, then text2cypher (logged), then reading the chunks the graph's retrieval route reaches
  (names linked by spelling and meaning, three fixed traversal patterns, ranked by similarity);
- `records_vector` (R73, R74): the same plans over the record layer alone (the plan's labels: no claim
  primitives, no documents), with vector search as its source of text. It separates what the records
  give from what the extracted claims give;
- `vector`: the chunks nearest the question in the `chunk_embeddings` index, nothing from the graph.

1. `kg qa GOLD` asks every question of a gold file (`tests/gold/qa/`, format `validation/qa_gold.py`)
   and writes `out/answers_<system>.jsonl`, with the chunks each reader saw and, for the graph, how they
   were found. Each system is its own MLflow run, with its own cost; it logs what code can score at once:
   sets and numbers, recall@k, citation faithfulness, per question type with intervals.
   `kg qa GOLD --plans DIR` (R80) replays the plans and text2cypher queries of an earlier run's
   `DIR/answers_<system>.jsonl` instead of asking the planner, so a changed graph is measured by its answers
   alone; a replayed query the graph refuses goes to reading. The reference plans (R77 part f) are in
   `tests/gold/r80/<dataset>/`.
2. The judge (Claude in the session) decides the free-text answers in a verdict file (`validation/qa.py`).
3. `kg qa-score GOLD ANSWERS --verdicts V` logs the final scores and writes one outcome row per question
   (`qa_outcomes_<system>.jsonl`); it needs no graph.
4. `kg qa-compare A B` compares two outcome files question by question: the questions only one system
   answered right and the exact McNemar p-value, overall and per type. Two systems (or two steps) differ
   beyond one sample's variation only when p < 0.05; overlapping intervals of the totals are no verdict.

## Development

```
uv run pytest                    # all tests; those marked neo4j skip when Neo4j is down
uv run pytest -m "not neo4j"     # fast tests, no database, no network
uv run ruff check . ; uv run ruff format .
```

Note: the `neo4j` tests wipe the database they connect to. Working rules for contributors (and for
Claude) are in `CLAUDE.md`; the audit and the refactoring history are in `REFACTOR_PLAN.md`.
