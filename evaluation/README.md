# Evaluation of kgbuilder: criteria, metrics, and how to reproduce them

> **How to read this folder.** `README.md` (this file) defines *what* is measured and *how*; it changes
> only when the criteria change. Every file named `results_<date>.md` is a **snapshot**: its numbers
> belong to the git commit, the MLflow runs and the gold file version named in its header, and they do
> not describe the system after that date. When a newer snapshot exists, the older one is history, kept
> so the thesis can show how the numbers moved. Nothing in this folder is regenerated automatically:
> if the code, the prompts, the gold or the judge changed after a snapshot's date, that snapshot is stale.

## 1. What is evaluated

The pipeline turns CSV tables and review texts into one Neo4j graph. Three parts of it are scored:

| Part | Question | How |
|---|---|---|
| domain graph (from the CSVs) | are all rows there, with the right labels and relationships? | reconciliation against `tests/gold/domain_expectations.json` (deterministic, no LLM) |
| text graph (from the reviews) | did the extractor find the facts a careful reader finds, and only those? | the gold set `tests/gold/text_gold.json` + the LLM judge (this document) |
| the whole graph | does it answer the goal's questions? | five gold questions with Cypher and expected answers, plus entity-resolution pairs |

The goal of the data set is *supply chain root cause analysis*: from a defect a reviewer reports, reach
the part and the supplier behind it.

## 2. The reference set ("gold")

`tests/gold/text_gold.json`, 96 triples, 12 entity-resolution pairs, 5 questions.

- **Labelled by Claude (Fable 5.1), not by a human.** Nobody had time to hand-label; the thesis must say
  so wherever the numbers appear. Two mitigations were applied: the labels were written from the review
  text *before any extraction output was opened*, and every later change to the gold is recorded as a
  "gold correction" with its reason.
- **Whole documents, all 10 review files** (70 reviews, about 5 500 words). A subset would give too few
  facts for any rate to mean something.
- **Predicates come from a pinned reference schema** (`tests/gold/text_schema.json`), because the LLM's
  schema proposal is not deterministic between runs (see the snapshot of 2026-09-22).
- **Every triple carries a verbatim `evidence` sentence**; a test (`tests/test_validation.py`) fails if a
  quote is not found in its document, a review file has no labels, or a claim is repeated.
- Labelling rules (also in the file's `_comment`): only facts of the schema's types (physical defects,
  operational failures, defects that impeded assembly, and one `PART_OF` per component a review names);
  praise, taste, instructions and assembly time are not facts; the subject is the named component when
  the review names one, else the product's full name; one triple per distinct claim per document.

Known skew: the Helsingborg Dresser file holds 28 of the 96 facts, the Stockholm Chair file 1. Every
rate must be read next to its `n`.

## 3. The two scoring methods

### 3.1 Exact match (`kg eval gold.json`)

Deterministic string matching in `validation/evaluate.py`: an extracted fact counts as correct when its
predicate equals a gold predicate and the gold names are among the entity's name and aliases after
`norm` (lower case, accents and whitespace removed). Precision is computed over facts from labelled
documents only. Strength: free, reproducible, a regression floor. Weakness: "legs wobble" and "wobbly
legs" do not match, nor does "table" against "Gothenburg Table", so it undercounts by a wide margin.

### 3.2 LLM-as-a-judge (`kg eval gold.json --verdicts out/judge_verdicts.json`)

Scheme: **gold for recall, the review text for precision**, each with the exact-match shortcut.

- The judge is **Claude in the Claude Code session** (model named in the verdict file), never the model
  that built the graph (Gemini). A model grading its own phrasing is lenient; the thesis needs an
  independent reader. The judge is not called from the pipeline: no API key, no cost, no code path.
- Code writes `out/judge_sheet.json`: every in-scope fact with a stable id and its exact-match result,
  every gold triple with found/unfound. The judge decides only what string matching could not settle.
- **Precision side**, per unsettled fact: does *this review's text* state this fact, with this relation?

  | Verdict | Meaning |
  |---|---|
  | `SUPPORTED` | the text states it; carries the sentence. A `PART_OF` between a named part and the document's product is supported by the document itself |
  | `UNSUPPORTED` | with a reason code: `not_in_text` (true or not, the review does not say it), `wrong_relation`, `wrong_entity`, `contradicted` |
  | `AMBIGUOUS` | the text can honestly be read both ways; leaves the denominator and is counted |

  A `SUPPORTED` fact too unspecific to answer any goal question is flagged `vague`.
- **Recall side**, per unfound gold triple: the judge names the extracted fact that states the same thing
  by meaning, or `null`.
- Every verdict carries a one-line reason. The verdict file is validated by pydantic models
  (`validation/judge.py`): a `SUPPORTED` without evidence or an `UNSUPPORTED` without reason code is
  rejected, and a verdict file that does not cover exactly the graph's sheet (missing, duplicate,
  unknown or stale ids) is refused with `EvaluationError`, never scored silently.
- Bias control: the judge never changes the gold in the same step; a supported fact the gold lacks is
  counted as a `gold_correction` and listed, so a reader sees how often the labeller missed something and
  whether the gold drifted toward the output.

## 4. Metrics (names as logged in MLflow, experiment `kgbuilder`, run name `eval`)

| Metric | Definition | Answers |
|---|---|---|
| `triple_precision`, `triple_recall`, `triple_f1` | exact match, triple level | reproducible floor |
| `entity_precision`, `entity_recall`, `entity_f1` | exact match, entity level (right things found, regardless of relation) | entity coverage |
| `precision_validated` | (exact matches + `SUPPORTED`) / (exact + `SUPPORTED` + `UNSUPPORTED`) | is what was extracted true |
| `recall_validated` | (gold found by string + by the judge) / gold | was everything found |
| `f1_validated` | harmonic mean of the two | the headline number |
| `ambiguous_rate` | `AMBIGUOUS` / facts judged | how far to trust the precision number |
| `vague_rate` | vague / all supported | how useful the facts are |
| `unsupported_<reason>` | one count per reason code, always logged | what kind of errors the extractor makes |
| `gold_corrections` | `SUPPORTED` facts the gold lacks | how often the labeller missed a true fact |
| `judged_facts` | facts the judge had to decide | the `n` behind the validated rates |
| `er_accuracy` | share of gold pairs the graph gets right (`same` pairs share an entity, others do not) | does entity merging work |
| `question_accuracy` | share of gold questions whose Cypher returns exactly the expected set | does the chain answer the goal |
| `evidence_verified_rate` | facts whose quote exists verbatim in their chunk (validate stage) | a free precision floor without any judge |
| `cost_usd`, tokens, `llm_latency_s` | from `prices.yaml` and the LLM traces (stage runs) | the price of the accuracy |

Params logged with every eval run: `gold`, `gold_hash`, `verdicts`, `judge_model`,
`judge_verdicts_hash`. Two eval runs with different hashes are not comparable.

## 5. How to reproduce a snapshot

```
uv run kg reset
copy tests\gold\domain_plan.json out\plan.json
copy tests\gold\text_schema.json out\text_schema.json
uv run kg --preset quality build
uv run kg --preset quality ingest-text
uv run kg --preset quality extract          # paid; cached when the prompts are unchanged
uv run kg --preset quality resolve
uv run kg --preset quality link
uv run kg --preset quality eval tests/gold/text_gold.json               # writes out/judge_sheet.json
# the judge fills out/judge_verdicts.json from the sheet and the review files
uv run kg --preset quality eval tests/gold/text_gold.json --verdicts out/judge_verdicts.json
```

Always pass the preset of the graph being scored; without it, `kg eval` logs to the experiment of the
preset named in `.env`. A `quality` run needs the user's agreement (`presets.yaml`, `ask_permission`).

## 6. Limits of this evaluation

- One labeller and one judge, from the same model family, and no human agreement number. The
  "label before looking" order and the listed gold corrections reduce but do not remove this bias.
- 96 gold facts and 10 documents: one fact is about one point of recall. Differences of a few points
  between two runs are noise; a claim that variant B beats A needs both judged with the same gold and
  ideally more than one run per side.
- The judge pass is a session, not a program: reproducible in procedure, not bit for bit.
- Exact-match numbers depend on naming; they are a floor, never the headline.
