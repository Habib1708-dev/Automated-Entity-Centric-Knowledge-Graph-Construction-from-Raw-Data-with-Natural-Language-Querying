# R83: classify each wrong QA answer by its cause (layered-model Step 8, first part)

Repo: C:/Users/Habib/Desktop/Knowledge-Graph-AI-Thesis. You only READ files and write ONE output file.
Never run `kg`, never call an LLM API, never query Neo4j (the database holds test leftovers, not the
graphs), never edit anything in the repo. Use `uv run python` (plain `python` is not installed) for any
small script that reads JSON.

## What you classify

The graph system's wrong answers in the last measured run (R77 part f, git 13ee2b6, DeepSeek answering
over a Gemini-built graph). For dataset D (furniture / heldout / generality):

- gold questions: `tests/gold/qa/D_qa.json` (question, type, expected answer, evidence chunk ids and
  quotes, the expected route, for record questions a DuckDB `sql` over the source files)
- the run's answers with full plan traces: `out/r77d_D/answers_graph.jsonl` (each line: entities / number /
  text / citations, and `plan.attempts[].plan.steps` with `steps[].items` counts and `note`s, `fallback`,
  `checks` / `verified` of read_check, `exact` for a text2cypher fallback)
- outcomes: `out/r77d_D/qa_outcomes_graph.jsonl` (`correct`), free-text verdicts with reasons:
  `tests/gold/r77/D_partd_graph_verdicts.json`
- the graph's content as files: `out/r77d_D/triples.jsonl` (every extracted claim with its evidence,
  truth / modality / condition, chunk id), `out/r77d_D/rejected.jsonl` (claims code rejected, with the
  reason), `out/r77d_D/resolve.json` (identity decisions), `out/r77d_D/plan.json` (record graph plan),
  `out/r77d_D/text_schema.json` (types and predicates), `out/r77d_D/build_report.json`,
  `out/r77d_D/eval_report.json`
- source text and records: furniture `data/` (CSV files and `data/product_reviews/*.md`); heldout
  `heldout/nhtsa/data/` (complaints, `recalls.json`, `vehicles.csv`); generality `tests/fixtures/generality/`
- earlier runs of the same questions, to see whether a question is right in other runs (planner variance):
  `out/r79_D/qa_outcomes_graph.jsonl` (R79) and `out/r77c_D/qa_outcomes_graph.jsonl` (R77 part c)
- context: `REFACTOR_PLAN.md` sections R71, R74, R77 (parts b and f), R78, R79 describe the system and
  earlier failure readings; `src/kgbuilder/query/plan.py`, `plan_cypher.py`, `plan_run.py` are the
  primitives (find_entity, filter_records, related, find_claims, read_check, retrieve_chunks, list, count,
  rank, answer_from_chunks).

## The cause list (closed; take the FIRST step of the answer's own trace that goes wrong)

Walk the answer's trace from its first step. At the first step whose output is not what the question
needs, decide who is responsible with this rule: **if a plan that asks for what the question says, with the
existing primitives, would have found the needed items in this graph, it is a query cause (Q); if the graph
does not hold what such a plan would need, it is a graph cause (G)** - and then name the G cause by the
first in the G list that fits. If every graph and plan step was right and the error is in reading or in the
final answer, it is a reading cause (R). Check S first: a failure that is not the system's fault.

S. Scoring, not the system
- `S1_right_by_meaning`: the answer is right by meaning, scored wrong (form, alias, word order). Flag only; no gold change.
- `S2_gold_doubtful`: the gold answer itself is doubtful given the source text. Flag only; no gold change.

Q. Query layer
- `Q1_names_not_linked`: find_entity found nothing or the wrong candidate for a name the graph holds.
- `Q2_wrong_primitive_or_field`: the plan asked the graph the wrong thing although the right request was
  expressible: claims searched for what is a record field, subject instead of object, the wrong label or
  relationship, listing a claim's subject instead of the thing it is about, a filter the question did not ask
  for, a missing `label` on a count, read_check dropped where a count needed it.
- `Q3_missing_primitive`: what the question asks cannot be expressed with the primitives at all (comparing
  two properties of one record, date arithmetic, a list over several labels). Name the missing operation.
- `Q4_fallback_wrong`: every plan was refused or failed, and the fallback (text2cypher or reading) was wrong.

G. Graph (R68 order: the extraction miss first, so that the model's misses are not blamed on the shape)
- `G1_extraction_miss`: the schema and shape could hold the needed fact; the model did not extract it (or
  code rejected it: say so, with the `rejected.jsonl` reason).
- `G2_no_schema_type`: no type or predicate in the text schema could hold it.
- `G3_identity`: mentions of one thing not joined, or of two things joined.
- `G4_attachment`: the claim exists but is not attached to the thing the question is about.
- `G5_assertion`: truth / modality / condition stored wrong, so a filter or count included or left it out wrongly.
- `G6_attribution`: who said it matters (a dealer, the maker, someone other than the author).
- `G7_time_or_role`: a time, date range, or role the question filters on is not stored in usable form.
- `G8_event_structure`: the answer needs several claims joined as one occurrence or ordered (THEN / CAUSES).
- `G9_concept`: the fact is there under a concept typed or split so a plan cannot reach it ("seal" vs
  "mechanical seal"; a material typed so no part claim exists).
- `G10_set_or_quantifier`: a range or set ("certain 2014-2020 ... vehicles") is stored as one name, so its
  members are not reachable.
- `G11_other_graph`.

R. Reading
- `R1_read_check_wrong`: read_check rejected a right candidate or accepted a wrong one.
- `R2_ranking_cut`: the right chunks were reachable but not among the top k given to the reader.
- `R3_reader_error`: the reader had the right chunks or items and answered wrong (including the wrong form,
  e.g. a list where a number was asked, or extra items).

## Fix candidate (closed; the one change that would most directly fix this question)

`gold_scoring`, `planner` (prompt or plan check, existing primitives), `new_primitive`, `read_check`,
`reader`, `extraction_coverage`, `open_predicates`, `identity`, `llm_attachment`, `assertion`, `speaker`,
`valid_time`, `events`, `concept_typing`, `sets_quantifiers`, `read_check_writeback` (store verified
read_check results as claims), `none`.

Added by the lead reviewer after the classification, for rows the list above could only name loosely
(each change listed in R83): `query_code` (an existing primitive's code is wrong, not the plan or the
prompt) and `document_fields` (a document's own header fields, such as a rating or an author, that no
claim or record holds).

## Output

Write a JSON file (path given in your task) with exactly this shape, one row per wrong question, nothing
else:

```json
{"dataset": "D", "run": "R77 part f (13ee2b6)", "classified_by": "<your model>", "rows": [
  {"question_id": "H44", "type": "structured_filter",
   "cause": "Q3_missing_primitive", "also": [],
   "first_wrong_step": "step 2 filter_records ... (or 'final answer')",
   "evidence": "what the trace did, what the gold needs, and what the graph or text holds: quote the stored claim (subject PREDICATE object) or the source sentence verbatim",
   "fix": "new_primitive",
   "right_in": ["r79"]}
]}
```

- `also`: other causes that would ALSO have to be fixed for the answer to be right (often empty).
- `right_in`: which of `r79`, `r77c` scored this question correct.
- Every claim in `evidence` must come from the files: no guess about what a model "probably" did. If you
  cannot tell, say so in `evidence` and pick the most specific cause the files support.
- Be brief: `evidence` is 1-3 sentences.

At the end, reply with: the counts per cause and per fix, and the 3 cases you were least sure about.
