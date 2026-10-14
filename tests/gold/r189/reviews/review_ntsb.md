# Adversarial review of ntsb_qa_sql.json (NQ01, NQ02)

Reviewer: Claude Fable 5.1, 2026-10-14. Read only heldout/ntsb/data, the file under review,
r185/ntsb/earlier_questions.json and tests/gold/qa/ntsb_qa*.json. Queries run through
`kgbuilder.validation.qa_records.connect` on `heldout/ntsb/data` (DuckDB).

Schema facts used: events 30 rows, 30 distinct ntsb_no; findings 67 rows over the same 30 ntsb_no
(no accident missing from either side); crew 38 rows, every ntsb_no in events. The four injury
columns of events are BIGINT with no NULLs; finding_code is VARCHAR.

## NQ01: accidents with no 'Personnel issues' finding

Checked:
- Re-derived three ways: (a) the file's anti-join; (b) GROUP BY ntsb_no over findings keeping groups
  with no finding_code starting '02'; (c) `SELECT ntsb_no FROM events EXCEPT SELECT ntsb_no FROM
  findings WHERE finding ILIKE '%personnel%'`. All three give the same 10: CEN21LA221, CEN21LA232,
  CEN21LA284, CEN21LA353, CEN21LA382, CEN21LA414, CEN22LA059, CEN22LA312, CEN23LA026, CEN23LA223.
  Also hand-walked the 67 rows: the 20 accidents with a '02' row are the complement.
- Category unambiguous: the first dash-separated segment of `finding` takes exactly four values
  (Aircraft 28 rows / code 01, Environmental issues 15 / 03, Not determined 1 / 05, Personnel issues
  23 / 02); text prefix and code prefix agree row by row. "Personnel issues" is therefore a fixed
  category, not an interpretation. The maintenance-personnel row of CEN22LA194 (code 0206203541)
  is a Personnel issues row, as the note says.
- Every accident has at least one finding row, so "no finding in the category" cannot be confused
  with "absent from the findings table" (checked: events EXCEPT findings is empty).
- Note numbers verified: 23 Personnel issues rows over 20 accidents, 67 finding rows, the 9
  "only Aircraft" accidents and CEN21LA414 "only Not determined" match the category lists.
- `expected` equals the sql's first column; accident numbers are as in the source.
- Novelty: NB13 counts accidents with one specific finding; NE07 asks which accident has the most
  findings. An anti-join on a category is new in meaning.

Changed: nothing.

Doubts left: a reader might wonder whether CEN21LA414 ("Not determined") belongs in the answer; the
question asks for accidents with no Personnel issues finding, which it is, and the note explains it.
A narrative-based reading (probable cause blames the pilot) is excluded by "in the findings table".

## NQ02: accidents where events' people on board exceed the crew table's rows

Checked:
- Re-derived with a LEFT JOIN on a grouped crew subquery (coalesce 0 for missing accidents, though
  none is missing): 9 accidents, the same nine as the records; no accident has fewer on board than
  crew rows; totals 56 on board vs 38 crew rows; 7 accidents have more than one crew row. The file's
  sql returns 9.
- "People on board" = sum of the four injury columns: no NULL or empty cell in any of the 30 rows,
  so the sum is defined everywhere. The injuries table's per-accident level-row sums agree with the
  events sums for all 30 (no row differs), so no alternative on-board count exists in the tables.
- Alternative readings tried: counting only non-Passenger crew rows gives 12 (different answer);
  using max(crew_no) instead of count(*) gives 9 (CEN22LA194 is numbered 1 and 3, so max is 3 for 2
  rows, but 2 on board is still not more). Joining crew to events and summing would multiply the
  multi-row accidents, as the note says.
- `records`: the nine events.csv rows' cells (as text) match the source exactly; the values
  listed in the note (2, 4, 3, 2, 3, 2, 3, 6, 2) match.
- Novelty: NE08 counts accidents with more than one crew row (7); NC04 asks the uninjured count of
  one accident; NB47 asks passengers in one narrative. A per-accident cross-table comparison is new.

Changed:
1. Question wording: "the number of people the crew table lists" -> "the number of people (rows of
   any category) the crew table lists". Why: the crew table lists Passenger rows for four accidents;
   a reader taking "crew" literally would count only pilots and get 12 instead of 9. The sql and
   expected are unchanged.
2. Note: added the pilot-only variant (12), the CEN22LA194 crew_no 1/3 numbering trap, and the
   statement that no injury cell is empty.

Doubts left: none on the answer. The question is wordy, but each clause removes a real alternative
reading found above.
