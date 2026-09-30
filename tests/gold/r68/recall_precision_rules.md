# R68: precision of the held-out recall-text claims (judging rules)

Purpose: judge every claim the graph `out/r68g_heldout` extracted from the recall notice texts (415
claims, MLflow extract `9945e539`), which the held-out judge sheet leaves out because the gold covers only
the complaint documents. Same question as R67's recall-observation judgement and the `evaluation`
skill's precision side: **does the notice text state this claim, with this relation?** The judges are
Claude (Fable 5.1 subagents, reviewed by the lead judge), never the model that built the graph. Code
computes the scores from the verdicts.

All examples are invented (a kettle maker's safety notice); none comes from the dataset.

## Input

A group file with `text_schema` (the graph's entity and fact types, with descriptions) and `documents`:
per notice its `chunks` (the whole text) and its `claims`: `id`, `subject` / `subject_type`,
`predicate`, `object` / `object_type` (the claim's own wording), `polarity`, `time`, `evidence` (a quote
code has already checked is verbatim in the chunk) and `chunk_id`.

## Verdict per claim

- `SUPPORTED`: the notice states it, and the predicate means what its fact type's description says.
  Judge the claim's own wording, allowing paraphrase and abbreviation. Give `evidence`: the sentence
  that states it, copied from the chunk.
- `UNSUPPORTED` with a `reason_code`:
  - `not_in_text`: the notice does not say it (general knowledge, a guess from the component's name);
  - `wrong_relation`: both ends are right, but the text does not support this relation (a component
    typed as affected when it is the remedy part; a cause stored the wrong way round);
  - `wrong_entity`: an end is misread (the wrong component, an entity given a type it is not);
  - `contradicted`: the text says the opposite.
- `AMBIGUOUS`: the text can honestly be read both ways. Never a bin for hard cases.

Also per claim:

- `vague` (only on `SUPPORTED`): true when the claim is too unspecific to answer any question
  ("Notice 7K-0042 ADDRESSES safety risk").
- `overstated` (only on `SUPPORTED`): true when the text states the claim only as possible or
  conditional ("the lid **may** crack", "**if** the base is wet") or only for a limited set ("**certain**
  kettles **built March to May**"), and the claim states it as plain and general ("lid cracks";
  "notice covers the Meridian Kettle"). The verdict itself ignores this (a notice's "may" still states its
  defect, as in R67); code reports a strict precision that counts overstated claims as unsupported.
- `polarity_ok`: true when the claim's `polarity` fits its tone: `negative` for a defect, a failure or a
  hazard, `neutral` for a plain link or fact (which notice covers which product or part, a date, a
  number), `positive` for a strength.
- `reason`: one line.

Rules that decide the frequent cases:

- A notice covering several products (or product years) states one claim per product; each is judged
  alone.
- `COVERS_COMPONENT` / `ADDRESSES_PROBLEM`: supported when the notice's defect, consequence or remedy
  concerns that part or that problem.
- A problem named with its component ("cracked lid") and linked to that component is not double counting;
  judge the link.
- A remedy part (what the maker replaces) typed as the defective component is `wrong_relation` only when
  the notice clearly says it is not itself defective.

## Output

A JSON file, every claim of the group exactly once, in input order:

```json
{"judge": {"model": "claude-fable-5-1", "date": "YYYY-MM-DD", "group": 1},
 "verdicts": [{"id": "0123456789ab", "doc_id": "record/Recall/...", "verdict": "SUPPORTED",
               "vague": false, "overstated": true, "polarity_ok": true,
               "evidence": "The lid may crack when ...", "reason": "the notice's defect is a cracking lid"},
              {"id": "...", "doc_id": "...", "verdict": "UNSUPPORTED", "reason_code": "not_in_text",
               "polarity_ok": true, "reason": "the notice never names the thermostat"}]}
```
