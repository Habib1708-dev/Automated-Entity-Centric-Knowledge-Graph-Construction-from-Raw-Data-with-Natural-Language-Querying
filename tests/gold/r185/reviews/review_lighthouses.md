# Review of lighthouses_qa_semantic.json (LS01, LS02)

Reviewer: Claude (Fable 5.1), 2026-10-14. Blind review: read only chunks.json (all 30 chunks),
staging/, earlier_questions.json, corpus_spec.json, the semantic file and tests/gold/qa/lighthouses_qa*.json.
No out/, mlruns, verdicts or source code opened.

All six quotes were checked by script (`text.Contains(quote)` per cited chunk): all verbatim,
including the double space in Berry Head's "clockwork mechanism  which turned the optic".

## LS01: light turned by clockwork

Checked: every chunk of all ten articles for any drive of a revolving light.
Qualifying passages re-derived: Berry Head (#0 weight falling down a 45 m shaft; #1 clockwork mechanism
needing winding), Godrevy (#2 clockwork motor driven by a descending weight), St Anthony's (#1 weights
descending a 38-foot shaft drove the rotating optic). No other article names a drive for its revolving
light: Anvil Point, Farne, St Agnes, Old Higher, Tater Du and St Mary's (first optic) only say
"revolving"/"rotating"; St Mary's later sealed-beam array is battery-powered; Whitby's lights were fixed.
Answer stands: Berry Head, Godrevy, St Anthony's. All qualifying chunks are cited.

Judgement call (St Anthony's): a reader who knows lighthouses accepts a weight descending a shaft as
clockwork, but a careful literal reader could object that the article never calls the optic drive
"clockwork" while it does call the 1920 occulting mechanism "clockwork", so the article seems to
distinguish the two. That is a reasonable disagreement, so I reworded the question in place:

> ... had its light turned by clockwork, that is by a gravity-run movement rather than by gas,
> electricity or batteries?

The gloss makes St Anthony's count by the stated criterion (weights descending a shaft = gravity-run)
and rules out St Mary's batteries and the 1921 acetylene drive at Berry Head (which counts anyway via
"had"). I chose the words deliberately: "weight", "drive/driven/drove", "mechanism", "rotating",
"optic" and "shaft" all appear in the St Anthony's sentence, so the gloss uses none of them and the
"worded differently" requirement still holds (St Anthony's passage shares no key word or word form with
the question; Godrevy shares only "clockwork"/"light", Berry Head shares "turned"/"clockwork").

Lookalikes confirmed real and named in the note: St Mary's battery-powered revolving array; Berry
Head's acetylene drive; St Anthony's "clockwork occulting mechanism" (key word, wrong function); Whitby's
occulting mechanism; the bare "revolving"/"rotating" optics elsewhere. I added the St Anthony's occulting
mechanism explicitly to the lookalike list and recorded the rewording in the note.

Novelty: no earlier question (LB01-LB50, LC, LD, LE) asks about the optic drive or clockwork
(LB42 asks which Portland light never revolved; LE07 asks about first-order optics). New fact.

Names: "St Anthony's Lighthouse" with alias "St. Anthony's Lighthouse" (the article uses both). Kept.

Doubt left standing: "gravity-run movement" is slightly technical wording; a plain "descending weight"
would read better but would share a key word with every qualifying passage, which the capability forbids.

## LS02: buildings visitors can rent

Checked: every chunk for any letting, hire, lease, accommodation or visitor use.
Qualifying passages re-derived: Old Higher (#2 "available as holiday lets"), Whitby (#2 keepers'
cottages "available to hire by holidaymakers"; same chunk also says part of the fog-signal building
"is also used as holiday accommodation"). Both cited, one quote each. No missed member: Farne's lease
is to Trinity House for a peppercorn rent (not to visitors); St Mary's is "open to visitors" with a
museum, visitor centre and cafe, nothing let; Godrevy's keepers' cottages were demolished and its
"visitors' book" is a book; Anvil Point's "residential block" and St Anthony's keeper's cottage are
only described. Answer stands: Old Higher, Whitby.

Worded differently: neither passage shares "buildings", "visitors" or "rent" (Old Higher: cottages,
holiday lets; Whitby: cottages, hire, holidaymakers). Lookalikes are real and keyword-baited: Farne
carries "rent", St Mary's carries "visitors". Both named in the note.

Question wording: natural and unambiguous ("has", present tense, both articles say "now"); a reader
cannot reasonably add or drop a member. Left unchanged.

Novelty: LB36 (disused), LB46 (Marie Stopes), LE20 (ROC lookout) touch the same articles but not this
fact. New.

Names: added alias "Whitby South Light" (the article calls the present tower "Whitby South") next to
"Whitby High lighthouse"; Old Higher's aliases (Portland Bill High Light, Portland High Light,
Portland High) match the article's usage.

Doubt left standing: none.

## Changes made

- LS01 question: added the gloss "that is by a gravity-run movement rather than by gas, electricity or
  batteries"; note extended with the reason and the occulting-mechanism lookalike.
- LS02: alias "Whitby South Light" added. Question, answer, chunks and quotes unchanged.
