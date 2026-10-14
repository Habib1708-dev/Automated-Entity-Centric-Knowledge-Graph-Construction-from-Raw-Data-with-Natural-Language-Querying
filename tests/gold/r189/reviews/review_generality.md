# Adversarial review: generality_qa_sql.json (GQ01, GQ02)

Reviewer: Claude Fable 5.1, 2026-10-14. Blind to out/, mlruns, verdicts and roadmap.
Sources read: `tests/fixtures/generality/institute/staff.csv`, `water/pumps.csv`, the institute and water
text documents (grep for the names and serials involved), `earlier_questions.json`, and every
`tests/gold/qa/generality_qa*.json`. Queries run through `kgbuilder.validation.qa_records.connect`.

## GQ01: distinct names in the staff records (answer 4)

Checked
- Re-derived with `SELECT name, count(*) FROM staff GROUP BY name`: Aiko Tanaka 1, Jonathan Pike 1,
  Maria Lopez 2, Samuel Osei 1, so 4 distinct strings over 5 rows. The gold `sql` and a second form
  (`count(*)` over `SELECT DISTINCT name`) both give 4.
- `records`: five rows, `staff_id` and `name` cells match `staff.csv` exactly.
- Note: S-104 is Soil Ecology / Postdoctoral researcher and S-219 is Finance Office / Grants accountant;
  both correct against the CSV.
- Alternative reading. The text documents show the two Maria Lopez rows are two different people (one
  receives the Hensley award for Aldmoor peat cores and travels to Utrecht; the other presents the
  grants budget and signs the travel-claims memo as grants accountant). A reader who takes "names" as
  "named staff" or "people" answers 5, and the agent has been drilled by G01, G02, G22, GB28, GB29,
  GC12 and GD14 to treat them as two people. The original wording left that reading open.
- Novelty: GB10 asks the same operation (count distinct) on the `team` column; GB15 counts distinct
  attendees in text. Same operator, different column, and a duplicate name across two people is a
  trap neither has. Acceptable.

Changed
- Question reworded to fix the counting rule: "How many different names appear in the institute's
  staff records, counting each name once no matter how many staff ids carry it?" It states that
  distinct name strings are wanted without saying that a duplicate exists.
- Note extended to say why the rule is stated (the text shows two people) and that counting rows,
  staff ids or people gives 5.

Doubts left standing
- The clause "counting each name once ..." hints that a shared name may exist. Accepted: an
  unambiguous question beats a trap that two careful readers can resolve differently.
- With five rows the "awkward to list" claim is weak; that is a property of the fixture (5 staff,
  3 pumps), not of the question.

## GQ02: total rated flow by install year (answer 60)

Checked
- The original boundary wording "2016 or later" was unambiguous (inclusive), and `install_year >= 2016`
  returns 60 (HP40-2291 42 + KV12-0457 18). `records` and note were correct.
- Novelty problem: the filter set {HP40-2291, KV12-0457} is exactly the answer set of GN05
  ("Which pumps were installed after 2015?"), and GC10 ("stations with a pump installed after 2017")
  and G40 (North Station total 84) sit nearby. Summing a set an earlier question already listed is
  only one step beyond it.
- The text documents give no install years (only serials, duties, trips and seal work), so the CSV is
  the sole source for the filter; no competing reading from the text.

Changed
- Boundary flipped to "installed in 2016 or earlier", `sql` to `install_year <= 2016`. The filter set
  is now {HP40-1183 (2012, 42), KV12-0457 (2016, 18)}, which no earlier question lists or sums; the
  total stays 60. Verified: `<= 2016` gives 60, the misread `< 2016` gives 42, all three give 102,
  North Station gives 84.
- `records` replaced accordingly (HP40-1183 row cells checked against `pumps.csv`: 2012, 42).
- Note rewritten for the new boundary and the three wrong totals (42, 102, 84).

Doubts left standing
- The trap value 42 is also the rated flow of either Hydra P-40 alone, so a wrong answer of 42 does
  not reveal which mistake was made (boundary misread or a single-pump lookup). It is still wrong
  under exact match, so scoring is unaffected.
- "2016 or earlier" and GB19's "before 2017" select the same year range on a different table and
  column; the overlap is in phrasing of a boundary, not in the rows or the operation.

## Final check
Both `sql` strings re-run on the edited file: GQ01 -> 4, GQ02 -> 60, equal to `expected`.
