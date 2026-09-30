# R68 coverage: judging rules

Purpose: estimate how much of what a text states the graph holds (`coverage`) and how much a query can at
least locate (`reachable`), on a fixed random sample of sentences per dataset (`<ds>_sample.json`).
The judges are Claude (Fable 5.1 subagents, reviewed by the lead judge in the session), never the model
that built the graph. Code scores the result (`kg coverage`, `src/kgbuilder/validation/coverage.py`).

Two passes, in this order, so the claims are fixed before anything stored is seen:

1. **Pass 1, blind:** list the claims of every sampled sentence from the text alone.
2. **Pass 2, matching:** for each claim, the stored items that state it, or why none does.

All examples below are invented (a kettle, a delivery van, a service note); none comes from a dataset
being judged.

## Pass 1: the claims (blind)

**Input:** `<ds>_blind.json`, the coverage sheet without its observations and record fields. Per
sentence: `id`, `text` (the sampled sentence), `chunk_text` (its whole section), `context` (what the
document is about) and `things` (the names of the things the section hangs on). Open nothing else: not
the sheet, not `out/`, no graph or pipeline output.

**What a claim is.** Everything the sentence states that a question could need:

- statements about a thing, its parts, its materials, its measurements and ratings, how it behaves, what
  is wrong with it ("the lid of the Meridian Kettle cracked");
- opinions about the thing or one of its aspects ("the handle feels cheap", "love this kettle");
- events: a repair, a failure, an accident, an injury, a recall, a notification ("the repair
  shop replaced the thermostat");
- causes and consequences ("the loose seal makes it leak"), remedies ("the maker will replace the base
  free of charge");
- who said what ("the service desk said the noise is normal": a claim by the service desk);
- identifiers, dates, numbers and contact details the text gives about the thing or a record ("the
  batch number is 7K-0042", "the support line is 555-0100", "about 20 minutes").

**Not a claim:** a title or heading that only names the document or section ("# Meridian Kettle
Reviews"), a byline or signature ("- @kettle_fan (Leeds)"), a clerk's or editor's mark, and the writer's
own circumstances (who they are, where they live, their household, what else they own). A heading that
states a value about the thing or record does carry that claim: "## Rating: 4/5" is a rating, "## Note 17
(filed 03/02/2021)" states a filing date (the name part is still a title). A statement that identifies
the thing itself ("I own a 2021 Meridian Kettle Pro") gives its model and year, which is a claim.

**One claim per statement.** Split a sentence into the statements a question could ask about separately:
"the base is heavy and the lid rattles" is two claims; "resists limescale and dents" is two. Do not split
one statement into word-level pieces, and do not merge two statements into one.

**Read the sentence with its section.** Claims come from `text` only; `chunk_text` is there to resolve
"it", "they", "this" and to read a fragment cut from a longer sentence. A fragment contributes what it
itself says, read with its neighbours ("Brightline Appliances Ltd." cut from "Brightline Appliances Ltd.
is withdrawing ..." is the claim "the withdrawal is issued by Brightline Appliances Ltd.").

**Per claim:**

- `claim`: the claim in a few words, close to the sentence's own wording, with its negation,
  possibility ("may", "can"), condition ("when ...") and speaker kept ("the service desk said ...").
- `polarity`: the claim's tone toward what it is about: `positive`, `negative` or `neutral` (a plain
  fact, a number, a date). "No rattling at all" about a kettle is positive; "the lid may crack" is
  negative.
- `about`: exactly one name from the sentence's `things`, or `null` when the claim is about something
  none of them is (another product the writer mentions, a third party, the writer themself).
  - A claim about a part, a material or the behaviour of a thing is about that thing.
  - When a section hangs on a record and on the thing that record concerns (a note and the appliance it
    is about), a claim about the thing, its parts or its behaviour is about the thing; a claim about the
    report itself (when it was filed, what the writer was told, what the writer did) is about the record.
  - In a document written for a record (a notice text hanging only on its notice record), what the text
    reports - its defect, consequence, remedy, dates, maker, contacts - is about that record.

**Output:** `tests/gold/r68/<ds>_claims.json`, every sentence id of the input exactly once, in input
order (an empty `claims` list when the sentence states nothing a question could need):

```json
{"judge": {"model": "claude-fable-5-1", "date": "YYYY-MM-DD", "pass": 1,
           "input": "<ds>_blind.json (sheet without observations and record fields)"},
 "sentences": [{"id": "0123456789ab",
                "claims": [{"claim": "the lid cracked after a week", "polarity": "negative",
                            "about": "Meridian Kettle"}]}]}
```

## Pass 2: matching (with the full sheet)

**Input:** the full sheet `<ds>_sheet.json` and the pass-1 file `<ds>_claims.json`. **Never change a
claim** (its text, polarity or `about`), never add or drop one. If a claim looks wrong, keep it and say
so in the final report; the lead judge decides and lists every change as a gold correction.

Per sentence the sheet shows `observations` (the claims the graph stored from the sentence's chunk: own
wording `subject` / `predicate` / `object`, the resolved nodes `subject_entity` / `object_entity`,
`polarity`, `time`, `evidence`, and `things`, the nodes it hangs on) and `things` with their record
`fields`. `text_schema.fact_types` is the graph's schema.

**Covered.** `covered_by` lists observation ids and/or record fields `<thing>.<field>` that together
state the claim, where:

- subject, relation and object match the claim by meaning (paraphrase is fine; "rattles" and "rattling
  noise" are the same);
- negation, possibility and speaker are intact: "may crack" stored as "cracks" does not cover, nor does
  "the service desk said it is normal" stored as the writer's own "it is normal";
- the observation hangs on the thing the claim is about (`about` is among its `things`);
- the observation comes from this sentence or states the same claim from another sentence of the chunk;
- a record field counts only when its value states the claim (a flag, a number, a date, a category),
  never a long text field (those are cut with "…").
A polarity label that differs from the claim's tone does not by itself make a claim uncovered (polarity
is scored separately); a lost negation does (that is `assertion`).

**The writer** is the document's own source: the person who wrote a review, the submitter whose account
a report relays ("the submitter stated that the lid cracked" is the report's own claim, not a third
party's), or the organisation that issued a notice. Anyone else the text quotes (a service desk, a
maker's hotline, a friend) is a third party, and their claim is attributed (`attribution` when stored as
the writer's own).

**Missed.** Otherwise give the **first** cause of this list that fits, and only one:

| # | `cause` | Fits when | Invented example |
|---|---|---|---|
| 1 | `extraction` | the current shape could hold the claim faithfully and nothing holds it: a plain, stated claim by the writer, about one of the chunk's things, with a schema fact type, needing no role and no event | "the lid cracked" with a `HAS_CONDITION` type, and nothing stored |
| 2 | `no_schema_type` | no fact type of the schema can hold it | the schema has no type for a remedy or a phone number |
| 3 | `identity` | stored, but a merge made its subject or object a different thing (`subject_entity` / `object_entity` is another individual or another kind, not a synonym) | "filter" merged into the "water filter" of another appliance |
| 4 | `attachment` | stored on a thing it is not about, or what it is about is none of the chunk's things (`about` is null) | "my old kettle leaked" hangs on the new kettle |
| 5 | `assertion` | negated, possible or conditional, and stored as stated, or nothing stored | "may overheat" stored as "overheats"; "no rattling" stored as "rattling" |
| 6 | `attribution` | someone other than the writer says it, and it is stored as the writer's claim, or nothing stored | "the service desk said the noise is normal" |
| 7 | `role` | it needs a participant beyond subject and object: a condition, an agent, a place, an instrument | "the lid sticks when the kettle is hot" stored without "when hot" |
| 8 | `event_structure` | it links occurrences: order, repetition, a count of occurrences, one event after another | "third repair in two months, and the leak came back" |
| 9 | `concept` | what it is about is typed as a kind that cannot carry it | the handle typed as a material, so no part claim hangs on it |
| 10 | `other` | none of the above; say what | |

`extraction` comes first so that a miss the model could have made is never blamed on the graph's shape;
but it applies only when the shape could hold the claim faithfully. So a negated claim with nothing
stored is `assertion`, a claim by a third party with nothing stored is `attribution`, and a claim about
something other than the chunk's things is `attachment`.

Per missed claim:

- `schema_type`: the schema predicate that could hold it (a `predicate` of `text_schema.fact_types`);
  `null` only for `no_schema_type`;
- `near`: observation ids or record fields that hold it in part (for example the stored claim without
  its negation, for `assertion`), else `[]`;
- `reason`: one sentence on why this cause and not an earlier one.

Every claim has a `reason`, covered or not ("obs 3f2a... states the lid cracked, on the kettle").

**Output:** `tests/gold/r68/<ds>_verdicts.json` in the `CoverageVerdicts` format of
`src/kgbuilder/validation/coverage.py`, the pass-1 claims carried over unchanged:

```json
{"judge": {"model": "claude-fable-5-1", "date": "YYYY-MM-DD", "run_id": "<coverage_sheet MLflow run>",
           "git_sha": "<sha>"},
 "sheet": "tests/gold/r68/<ds>_sheet.json",
 "sentences": [{"id": "0123456789ab",
                "claims": [{"claim": "the lid cracked after a week", "polarity": "negative",
                            "about": "Meridian Kettle", "covered_by": ["3f2a9c1b7d0e"], "reason": "..."},
                           {"claim": "the service desk said the noise is normal", "polarity": "neutral",
                            "about": "Meridian Kettle", "covered_by": [], "cause": "attribution",
                            "schema_type": "HAS_CONDITION", "near": [], "reason": "..."}]}]}
```

`kg coverage` refuses a file that does not fit its sheet: a sentence missing or judged twice, an id not
stored for that sentence's chunk, an `about` that is not one of its things, a `schema_type` that is not
in the schema, or a claim with both or neither of `covered_by` and `cause`.
