# Review of lighthouses_qa_sql.json (LQ01, LQ02)

Reviewer: Claude Fable 5.1, 2026-10-14. Read only `heldout/lighthouses/data`, the file under review,
`earlier_questions.json` and `tests/gold/qa/lighthouses_qa*.json`; queries run through
`kgbuilder.validation.qa_records.connect` as the checker does. No out/, mlruns, src or verdict files opened.

## LQ01: lighthouses with no event (expected 34)

Checked:
- Join key. `events.lighthouse` is a STRUCT(qid, name); every event qid is in the table (0 orphans),
  every event name equals the table name for that qid (0 mismatches), no duplicate qids in the table,
  no null qid in events. So the anti-join is the same by qid or by name: 34 both ways.
- Counts: 80 table rows, 62 events, 46 distinct lighthouses with an event (listed by hand from
  events.ndjson and by `count(DISTINCT lighthouse.qid)`), 80 - 46 = 34. The given sql returns 34.
- Alternative readings. "Event" is any row of the events file regardless of its `event` label
  (renovation, stiffening, conversion to solar power all count) and regardless of a null date (9 events
  are undated, LC05). The undated case is the one real alternative reading: a reader who thinks a null
  date means "no event" would drop Lizard, Start Point, Peninnis, Longships, Round Island, Wolf Rock,
  Bishop Rock from the 46; only Lizard, Start Point, Peninnis, Longships and Round Island have nothing
  else, giving 39. Fixed by wording, see below. Service entry / retirement in the table are not events
  of "the events file", so no second reading there.
- Note: every claim re-checked (80 / 62 / 46 / 34, the 18 trap, Bishop Rock and Lundy North with three
  events). Accurate.

Changed: question now ends "counting an undated event as an event?" to close the undated reading.
Expected, sql and note unchanged.

Novelty: LC15 asks the same anti-join as a set for Northumberland only (3 names, easy to list from 9
rows). LQ01 is a count over all 80 rows, which cannot be done by listing. Different operation and scope,
but the same underlying idea; doubt left standing (see below).

## LQ02: service entry in a later year than inception (8 names)

Checked:
- Every row by hand. Both cells non-empty in 32 rows (48 rows have an empty inception or service entry
  and are out; DuckDB loads empty cells as NULL, so CAST(NULL) drops them without error). Of the 32:
  22 equal years, 2 earlier (Winterton 1867 -> 1840, Orfordness 1793 -> 1792), 8 later: Hartland Point,
  Bull Point, Portland Bill, St Bees, Belle Tout, Europa Point (1841-08-01), Helgoland (1811-02),
  Bishop Rock. Cell lengths are only 4, 7 or 10, so `substr(..., 1, 4)` is a safe year extraction for
  every row; re-derived by padding to a date and `year()`: same eight.
- Traps in the note verified: plain string comparison gives 12 (adds Pendeen, Trevose Head, Flamborough
  Head, Souter); Godrevy ('1859-03-01' vs '1859') and Royal Sovereign (identical) are not in that 12;
  "cells differ" gives 10.
- Alternative readings. "Later year" is strictly later; equal-year rows with a fuller date on one side
  are explicitly excluded by "comparing only the year", which also covers Godrevy, whose full date is in
  the inception cell. Hartland Point's 2012 service entry (with 2012 retirement) looks like a data
  oddity but the question asks what the table records. No other reading found.
- `records`: all 8 cells match lighthouses.csv exactly (including "1841-08-01" and "1811-02").
- `expected` equals the sql's first column, in the same order.
- Names as the source gives them ("Belle Tout lighthouse", "Helgoland lighthouse" lower-case l) with
  capitalised and Heligoland aliases; earlier gold files add no bare aliases (e.g. "Hartland Point"), so
  none added here for consistency.

Changed: nothing.

Novelty: no earlier question compares two columns of the same row. LE10 uses the same mixed-precision
service_entry cells but asks for the full-date rows, a different operation.

## Doubts left standing

- LQ01 shares its anti-join idea with LC15 (Northumberland subset). The count over the whole table is a
  genuinely different task for an agent that lists rows, but a reader judging novelty strictly by
  concept may call it a variant.
- LQ02 depends on the checker treating the lower-case "lighthouse" names case-insensitively or using the
  aliases given; the aliases cover it.
