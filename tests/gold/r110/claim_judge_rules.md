# R110 claim judging rules

Purpose: judge whether every claim a build stores is correct, as the graph stores it. Claim precision was
last judged in R66 / R68; the extraction has changed since (truth, modality and condition in R77, R81, R82),
so today's claims are unmeasured. Code scores the verdicts (`kg claim-eval --verdicts`,
`src/kgbuilder/validation/claim_eval.py`).

The judges are Claude (Opus 5.5 subagents, one batch each, reviewed by the lead judge in the session), never
the model that built the graph. Every example below is invented (an observatory and its staff); none comes
from a dataset being judged.

## Input

One batch file per judge: the question, the schema's `relations` (predicate -> subject type -> object type:
description) and `types` (entity type -> description), the `chunks` the batch's claims come from (each with
its `context`, what the document is about, and its `text`), and the `claims`. Open nothing else: no other
batch, no `out/` folder, no graph, no earlier verdict.

Each claim shows what the graph stores:

- `subject` (`subject_type`) `predicate` `object` (`object_type`): the claim's own wording of its two ends;
- `evidence`: the quote the extractor gave;
- `truth`: `affirmed` (the statement holds or happened) or `negated` (the text says it does not hold or did
  not happen), with `negation`, the words that deny it;
- `modality`: `actual`, `possible` (the text says it may hold: "may", "might", "could", "a risk of") or
  `conditional` (it holds under a stated condition), with `hedge`, the words that make it possible, and
  `condition`, the condition's words;
- `polarity`: the claim's tone toward its subject, `positive`, `negative` or `neutral`;
- `time`: time words from the evidence, empty when none was stored;
- `origin`: `extracted` (the model wrote it from the text) or `derived` (code wrote it by a rule, below).

## The question

**Read in its chunk, does the text state this claim as the graph stores it?** That is: these two things,
this relation as the schema describes it, and the stored truth, modality, condition, tone, time and end
types.

Read the claim as a sentence: "subject PREDICATE object", the predicate meaning what its description in
`relations` says, then apply the fields. "shutter motor SHOWS sticking, truth negated" reads "the shutter
motor does not stick".

- **The whole chunk counts**, not only the quote. A pronoun or "this one" may be resolved from the chunk and
  its `context` ("this telescope" in a document about the North Dome telescope is the North Dome telescope).
  A claim whose quote is wrong but whose content another sentence of the chunk states is judged on the
  chunk; mention the quote in the reason.
- **Paraphrase is fine.** The ends are the claim's own wording; a reader who would accept "shutter jams" for
  "the shutter keeps jamming" accepts the claim. A name that adds what the text does not say ("cracked
  mirror" when the text says the mirror is scratched) is not a paraphrase.
- **Opinions are statements.** "The dome is gorgeous" states "dome SHOWS gorgeous", positive.
- **The writer** is the document's own source: the person who wrote it, the person whose account a report
  relays ("the caller stated that the dome leaked" is the report's claim), or the organisation that issued a
  notice. A statement the text attributes to someone else ("the vendor said the noise is normal"), stored as
  a plain fact, is not stated by the text unless the writer endorses it: INCORRECT, `not_in_text`.
- **Lists.** "The fault affects the Kestrel, Osprey and Merlin mounts" states one claim per mount.
- **Numbers.** A claim with a number holds when the text gives that number, with that unit, for that thing
  and that relation ("the mirror weighs 4 kg"); a number the text gives for something else, or that means
  something else (a distance read as a duration), is wrong.
- **Derived claims** come from a rule: a thing the text names in a document whose whole document is about
  X stands in the relation to X (for example, a piece named in a document about one telescope "is a piece
  of" that telescope). Judge them like any claim, from the chunk and its `context`: VALID when the text,
  read with what the document is about, supports the relation for this thing ("the focuser is stiff" in a
  document about the Ridge telescope: focuser is a piece of the Ridge telescope). INCORRECT when the thing
  belongs to something else in the text (the writer's old telescope, a telescope compared with it), is no
  piece or member of X at all (a tool, a room, a person), or the relation's description does not fit.

## Labels

| Label | When |
|---|---|
| `VALID` | the text states the claim as stored |
| `VALID_ALTERNATIVE` | right under a reading the text equally allows (an ambiguous referent resolved one way) |
| `INCORRECT` | the text does not state it as stored; name **every** fault below |
| `AMBIGUOUS` | the text honestly supports both answers; not a bin for hard cases |
| `UNJUDGEABLE` | the chunk does not show what a decision needs (a sentence cut off at the chunk's edge) |

## Faults (INCORRECT only; at least one, every one that applies)

Content faults (the stored triple says what the text does not):

| Fault | When | Invented example |
|---|---|---|
| `not_in_text` | true or not, the text does not say it: general knowledge, an inference beyond the text, a third party's claim stored as fact, a derived claim the text does not support | "dome HAS_PIECE shutter" from "the dome was closed" |
| `wrong_entity` | an end is not the thing the text means: the wrong referent, a fragment of a name, another thing's piece, a claim lifted from a piece onto the whole thing | "telescope SHOWS wobbly" when the text says the tripod wobbles |
| `wrong_relation` | both ends are right, the relation between them is not the text's: another relation, the wrong direction, a number that means something else | "seal CAUSES leak" when the text says the leak damaged the seal |
| `truth` | a denied statement stored affirmed, or an affirmed one stored negated. A negation carried by a name ("no condensation", affirmed) reads the same as the denial and is no fault | "shutter SHOWS sticking", affirmed, from "the shutter never sticks" |

Field faults (the triple is the text's, a stored field around it is not):

| Fault | When | Invented example |
|---|---|---|
| `modality` | possible or conditional stored as actual, or the reverse; a statement of ability ("the dome can rotate fully") is actual | "mirror SHOWS fogging", actual, from "the mirror may fog in winter" |
| `condition` | the stored condition is not the text's condition for this claim: wrong words, a condition the text does not give, or none stored for a claim stored conditional | condition "at night" for "the motor stalls when the hatch is open" |
| `polarity` | the tone is clearly wrong: a fault or complaint stored positive, praise stored negative. Neutral for a plain fact, number, date or relation, or positive / negative where the text has that tone, is no fault | "dome SHOWS leaking", positive |
| `time` | a stored time that is not the claim's time in the text (an empty time is no fault) | time "in 2019" when the text dates the purchase, not the fault, to 2019 |
| `type` | an end's type clearly contradicts its description in `types` (a person stored as a place, a whole thing stored as a piece); judge a type only when its description clearly excludes the thing | "Edit Varga" typed as a place |

A wrong truth is both a content fault and the reason the claim is wrong; name `truth`, and also
`modality` or `condition` only when those are wrong as well.

## Output

One JSON file per batch, every claim of the batch exactly once, in the batch's order:

```json
{"batch": "<batch name>", "judge": "claude-opus-5-5",
 "verdicts": [
   {"id": "<claim id>", "label": "VALID", "reason": "the text says the shutter never sticks",
    "evidence": "The shutter never sticks."},
   {"id": "<claim id>", "label": "INCORRECT", "faults": ["truth"],
    "reason": "the text denies the sticking; stored affirmed", "evidence": "The shutter never sticks."}
 ]}
```

- `reason`: one line, always; for INCORRECT, what is stored and what the text says.
- `evidence`: copied **verbatim** from the claim's chunk `text` (one contiguous span, the sentence that
  states, denies or bears on the claim), never from `context` and never paraphrased; code rejects a quote
  that is not in the chunk. Only `UNJUDGEABLE` may leave it empty.
- `faults`: only on INCORRECT.

The lead judge reviews every INCORRECT, AMBIGUOUS and UNJUDGEABLE verdict and a seeded 10 % of the VALID
ones; each change is recorded with the label before and the reason, so the blind label can be recovered.
