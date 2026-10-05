# R77 assertion gold: labelling and judging rules

Purpose: measure whether the graph keeps a claim's **truth** (affirmed / negated), **modality** (actual /
possible / conditional) and **condition** (layered-model Step 7). "The kettle leaked", "the kettle did not
leak", "the kettle may leak" and "if the lid is open, the kettle leaks" are four different facts, and a
count over claims must tell them apart.

The labellers and judges are Claude (Fable 5.1 subagents, reviewed by the lead judge in the session),
never the model that built the graph. Code scores the result (`kg assertion`,
`src/kgbuilder/validation/assertion.py`). All examples below are invented (a kettle, a delivery van, a
weather station); none comes from a dataset being judged.

Two passes, in this order, so the labels are fixed before anything stored is seen:

1. **Pass 1, gold (blind):** list the claims of every sampled sentence and label each claim's truth,
   modality and condition, from the text alone.
2. **Pass 2, matching (after the run):** for each claim, the stored observations that state it, and
   whether each of the three fields is kept.

## The sample (`<ds>_assertion_sample.json`)

Per dataset, drawn from the source text by code before any extraction (seed 77; the draw reads documents
and chunks only):

- `r68`: R68's 40 random sentences (furniture, held-out), whose claims R68's pass 1 already listed blind;
  `natural`: 40 random sentences of the generality corpus, which R68 did not sample;
- `cue`: 20 random sentences not in the first stratum that contain a negation, modal or condition word
  (`not`, `no`, `never`, a word ending in "n't", `without`, `may`, `might`, `could`, `can`, `risk`, `if`,
  `when`, `unless`, `while`, `until`, ...), so that negated, possible and conditional claims are numerous
  enough to measure. Rates over the whole text come from the random stratum only;
- `named`: the sentences the task's done criteria name.

## Pass 1: the claims and their labels (blind)

**Input:** `<ds>_assertion_blind.json`. Per sentence: `id`, `stratum`, `text` (the sampled sentence),
`chunk_text` (its whole section), `context` (what the document is about) and, for the `r68` stratum,
`claims`: the claims R68 listed. Open nothing else: not `out/`, no graph or pipeline output, no sheet.

**The claims.** For `r68` sentences keep R68's claims exactly as they are (same order, same wording,
none added or removed) and only add the three labels. For every other sentence list the claims with
R68's rules (`tests/gold/r68/judge_rules.md`, pass 1, "What a claim is", "Not a claim", "One claim per
statement"): everything the sentence states that a question could need, one claim per statement, written
close to the sentence's wording with its negation, possibility, condition and speaker kept.

**Per claim, three labels:**

- `truth`: `negated` when the sentence says the claim's statement does **not** hold or did **not** happen
  ("the lid did not crack", "no rattling at all", "never leaked", "the van failed to start" is affirmed:
  a failure happened); otherwise `affirmed`. A fault stated through a negation is negated of the positive
  statement: "the drawer of the cabinet does not close" denies "the drawer closes". Judge the main
  statement of the claim, not a word inside a name ("a no-drip spout" is affirmed).
- `modality`:
  - `conditional` when the claim is said to hold under a stated condition: an "if", "unless", "whenever"
    clause, or a "when" / "while" / "once" clause that means "whenever" ("the kettle whistles when the
    water boils", "if the lid is open, the kettle leaks"). A "when" clause that dates one past event is
    time, not a condition ("when I opened the box yesterday, the lid was cracked" is actual).
  - `possible` when the sentence says the claim might hold or happen, not that it does: uncertainty or a
    risk ("may", "might", "could", "can lead to", "increases the risk of", "possibly").
  - `actual` otherwise. A statement of ability or capacity is actual ("the kettle holds 1.7 litres",
    "the van can carry two pallets"); so is a past or present fact, an opinion, a plan or an order.
  - Both a condition and a modal word: `conditional` ("the van may stall if it is overloaded" is
    conditional on "if it is overloaded"). Truth is separate: "the station may not report rain" is
    negated and possible.
- `condition`: for a `conditional` claim, the condition's words copied verbatim from `text`, with their
  conjunction ("when the water boils"); `null` for every other claim.

**Output:** `tests/gold/r77/<ds>_assertion_gold.json`, every sentence id of the input exactly once, in
input order (an empty `claims` list when the sentence states nothing a question could need):

```json
{"labeller": {"model": "claude-fable-5-1", "date": "YYYY-MM-DD", "pass": 1,
              "input": "<ds>_assertion_blind.json"},
 "sentences": [{"id": "0123456789ab", "stratum": "cue",
                "claims": [{"claim": "the kettle may leak if the lid is open", "truth": "affirmed",
                            "modality": "conditional", "condition": "if the lid is open",
                            "note": "optional: why, for a hard case"}]}]}
```

## Pass 2: matching (with the assertion sheet, after the run)

**Input:** `<ds>_assertion_sheet.json` (`kg coverage-sheet` on the sample: each sentence with its chunk's
observations, each with `truth`, `modality` and `condition`) and the gold file. The claims and their
labels are fixed: never change them in this pass.

**Per claim:**

- `matched`: the ids of the observations of the sentence's own chunk that state this claim's content
  (subject, relation, object as R68 pass 2 reads them), **whatever their truth, modality and condition**:
  an observation storing "the lid cracked" for the claim "the lid did not crack" is matched, and its truth
  is then wrong. Empty when no observation states it; then no field is judged.
- `fields` (only when `matched` is not empty): for each of `truth`, `modality`, `condition`, `true` when
  the matched observations, read as stored (their names together with the field), keep the claim's label,
  else `false`:
  - truth: kept when the stored meaning is the claim's; a negation the names carry ("no rattling" as the
    object, truth affirmed) keeps it as well, and code counts it apart (exact agreement with the label);
  - modality: kept when the stored modality is the label, or the names carry it ("possible leak");
  - condition: for a `conditional` claim, kept when the stored condition (or the names) holds the gold
    condition's content; for any other claim, kept when the observation has no condition.
  Every matched observation must keep a field for it to be `true`.
- `reason`: one line, always; for a `false` field, what is stored and what the text says.

Output: `tests/gold/r77/<ds>_assertion_verdicts.json`, scored by `kg assertion`.
