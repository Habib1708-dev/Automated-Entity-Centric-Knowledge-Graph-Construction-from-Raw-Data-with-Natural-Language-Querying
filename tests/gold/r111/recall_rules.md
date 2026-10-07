# R111 claim recall: matching rules

Purpose: estimate how much of what a careful reader finds in a text a build stores as claims. The reader's
claims are R77's gold (`tests/gold/r77/<ds>_assertion_gold.json`): every claim of each sampled sentence,
written from the text alone before any output. The judge says, for each of them, which stored claims of the
sentence's chunk state it, or why none does. Code scores the verdicts (`kg claim-recall --verdicts`,
`src/kgbuilder/validation/claim_recall.py`) and compares the truth, modality and condition of the matched
claims with the reader's labels itself.

The judges are Claude (Opus 5.5 subagents, one batch each, reviewed by the lead judge in the session), never
the model that built the graph. Every example below is invented (a kettle, a delivery van, a weather
station); none comes from a dataset being judged.

## Input

One batch file per judge: the question, the schema's `relations` (each fact type's predicate with its
subject type, object type and description) and `types`, and the `sentences`. Each sentence has:

- `id`, `stratum`, `text` (the sampled sentence) and `chunk` (its section: `context`, what the document is
  about, and `text`; `null` when the section stores no claim at all);
- `gold`: the reader's claims, each with `claim` (a few words, close to the sentence), `truth`, `modality`
  and `condition`;
- `stored`: every claim the build stores for that section, each with its `id`, `subject` (`subject_type`),
  `predicate`, `object` (`object_type`), `evidence` (the quote), `truth` and `negation`, `modality` and
  `hedge`, `condition`, `polarity`, `time` and `origin` (`extracted` by the model, `derived` by code).

Open nothing else: no other batch, no `out/` folder, no graph, no source file, no earlier verdict.

**Never change a gold claim**: copy each `claim` exactly into its verdict, never add or drop one. If a gold
claim looks wrong, keep it, judge it as written, and say so in your final report; only the lead judge may
record a gold correction.

## Matched

`matched` lists the ids of the stored claims of the sentence's section that state the gold claim's content:

- subject, relation and object agree by meaning, read with the relation's description: paraphrase is fine
  ("rattles" and "rattling noise" are the same), and a stored claim may name the thing the section is about
  where the gold claim says "it";
- **whatever their truth, modality and condition**: a stored "the lid cracked" matches the gold claim "the
  lid did not crack", and a stored "the van stalls" matches "the van may stall if overloaded". Code compares
  those fields with the gold's labels; do not judge them here;
- a stored claim from another sentence of the same section counts when it states the same claim;
- list every stored claim that states it (a claim stored twice, or once extracted and once derived).

A stored claim that holds only part of the gold claim does not match it: one that drops the object ("the
kettle has a problem" for "the lid cracked"), merges it into another thing, or keeps only one half of a
pair ("resists limescale" for "resists limescale and dents" is a match for that half only when the gold
lists the halves as two claims). The miss's cause says what is missing.

## Missed

When nothing stored states the claim, give the **first** cause of this list that fits (R68's order, so a miss
the model could have made is never blamed on the graph's shape), with `schema_type` and a one-line `reason`:

| # | `cause` | Fits when | Invented example |
|---|---|---|---|
| 1 | `extraction` | a plain, stated claim by the writer that one fact type could hold faithfully, with nothing stored | "the lid cracked", a quality fact type exists, nothing stored |
| 2 | `no_schema_type` | no fact type of `relations` can hold it | a remedy, a phone number or an overall verdict, and no fact type for them |
| 3 | `identity` | stored, but with its subject or object another thing (a different named thing or kind, not a synonym) | "filter" stored as the "water filter" of another appliance |
| 4 | `attachment` | stored on a thing it is not about, or what it is about is nothing the section names | "my old kettle leaked" stored on the new kettle |
| 5 | `assertion` | negated, possible or conditional, and nothing stored | "the lid may crack": nothing stored |
| 6 | `attribution` | someone other than the writer says it, stored as the writer's own claim or not at all | "the service desk said the noise is normal" |
| 7 | `role` | it needs a participant beyond subject and object (a place, an agent, an instrument, a time window) and the stored claim drops it, or nothing is stored | "the van was repaired at the depot on Friday" stored as "van repaired" |
| 8 | `event_structure` | it links occurrences: order, repetition, a count of occurrences | "the third repair in two months" |
| 9 | `concept` | what it is about is typed as a kind that cannot carry it | the handle typed as a material, so no piece claim can hang on it |
| 10 | `other` | none of the above; say what | |

- **The writer** is the document's own source: the person who wrote it, the person whose account a report
  relays ("the caller stated that the lid cracked" is the report's own claim), or the organisation that
  issued a notice. Anyone else the text quotes is a third party.
- `schema_type`: a predicate of `relations` that could hold the claim; `null` only for `no_schema_type`.
- `extraction` applies only when the shape could hold the claim faithfully. A negated claim with nothing
  stored is `assertion`, a third party's claim with nothing stored is `attribution`, a claim needing a place
  or time window that no fact type carries is `role` (or `no_schema_type` when no fact type fits at all).
- A section with no stored claims (`chunk` null, `stored` empty) still gets a cause for each gold claim.

## Output

One JSON file per batch, every gold claim of the batch exactly once, in the batch's order; `key` is
`<sentence id>#<index of the claim in its sentence's gold list, from 0>`:

```json
{"batch": "<batch name>", "judge": "claude-opus-5-5",
 "verdicts": [
   {"key": "0123456789ab#0", "claim": "the lid cracked after a week", "matched": ["3f2a9c1b7d0e"],
    "reason": "the stored 'lid SHOWS cracked' states it"},
   {"key": "0123456789ab#1", "claim": "the service desk said the noise is normal", "matched": [],
    "cause": "attribution", "schema_type": "SHOWS",
    "reason": "stored as the writer's 'noise SHOWS normal'; the text gives it as the desk's statement"}
 ]}
```

`reason`: one line, always. The lead judge reviews every miss and a seeded 10 % of the matches; each change is
recorded with the outcome before and the reason, so the blind answer can be recovered.
