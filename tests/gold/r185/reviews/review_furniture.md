# Review of furniture_qa_semantic.json (FS01, FS02)

Reviewer: Claude (Fable 5.1), 2026-10-14. Blind: read only chunks.json (70 chunks = every review of
the 10 products), staging/products.csv, earlier_questions.json (188 earlier furniture questions) and
tests/gold/qa/furniture_qa*.json. No pipeline output opened.

## FS01 - reviews that say "would not buy again" or warn others off

**Checked.** Re-read all 70 chunks for any wording that advises against buying. Qualifying reviews:
Helsingborg #1 (AVOID THIS PRODUCT), #3 (Would not recommend), #4 (DO NOT BUY; also "should come with a
warning label"), #5 (wouldn't buy it again or recommend it), Malmö Desk #2 and Västerås Bookshelf #2
(probably wouldn't buy again). Answer set confirmed: Helsingborg Dresser, Malmö Desk, Västerås Bookshelf.
No missed chunk, no extra member. All six quotes verbatim (script check), every qualifying chunk cited.
Novelty: no earlier question asks about not buying again or recommending against (F08-FN10 checked).

**Changed.**
- Note: the writer claimed "AVOID THIS PRODUCT" shares no key word with the question, but the question's
  head noun is "products". Corrected: the passage with no content-word overlap is "Would not recommend."
  (only "would"/"not"); "AVOID THIS PRODUCT" shares only the generic "product(s)".
- Note: recorded the reading of "Would not recommend." as a warning to others, and that excluding it
  leaves the set unchanged (the dresser counts through #1, #4, #5).
- Note: added missed lookalikes: 3-star "expected better quality control/hardware" (Gothenburg #1,
  Helsingborg #0), "mixed feelings overall" (Uppsala #2), "worried it might break" (Örebro #2),
  "don't trust putting anything heavy on the upper shelves" (Västerås #4).
- Aliases: "Malmo Desk", "Vasteras Bookshelf" (diacritic-free spellings a reader could give).
- Question wording kept: natural, and the fuzzy chunk (#3) does not decide any member.

**Doubt left standing.** Whether "Would not recommend." is a "warning to other people" is a judgement
call; it affects only the evidence list, never the answer set.

## FS02 - products that needed two people to assemble

**Checked.** Re-read all 70 chunks for any helper mention. Qualifying: Gothenburg #0, #6; Helsingborg #2;
Linköping #0, #2; Uppsala #0, #5. Set confirmed: Gothenburg Table, Helsingborg Dresser, Linköping Bed,
Uppsala Sofa. Verified the note's claim that no Jönköping, Malmö, Norrköping, Örebro, Stockholm or Västerås
review mentions a helper (Malmö #5 says "by myself"). All seven quotes verbatim. Novelty: FD10, FC11 and
FB48 ask about assembly *time*, FE19 about seating; none asks about a second person.

**Changed.**
- Question reworded from "took two people to assemble" to "needed two people to put together". Reason:
  the "worded differently" requirement did not strictly hold. Both roommate chunks (Helsingborg #2,
  Linköping #2) contain "took" and "assembly", i.e. a key word and a word form of the original question.
  With the new wording those two chunks share no key word at all, while "with two people" passages still
  match literally (allowed). The new wording also makes the two "needs/requires two people" reviews count
  unambiguously (the original "took two people" could be read as excluding advice).
- Note: rewritten for the new wording; added lookalikes for "put together" without a helper (Gothenburg
  #2, Stockholm #5, Västerås #2), "two kids" (Gothenburg #0), "Two of the drawers" (Helsingborg #3), "two
  months" (Linköping #4, Uppsala #4), "seating 6-8 / 3-4 people" (Gothenburg #3, Uppsala #6).
- Alias: "Linkoping Bed".

**Doubt left standing.** None on membership. "put together" is slightly more colloquial than the sources'
"assembly", which is intended (it is the semantic gap under test).

## Format
Keys match earlier retrieval gold (no `records`/`sql` on retrieval questions). JSON re-parsed and all 13
quotes re-verified after the edits.
