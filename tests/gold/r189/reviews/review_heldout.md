# Review of heldout_qa_sql.json (HQ01, HQ02)

Reviewer: Claude Fable 5.1, independent and adversarial. Read only `heldout/nhtsa/data/*`, the file under review,
`scratchpad/r185/heldout/earlier_questions.json` (188 questions) and `tests/gold/qa/heldout_qa*.json`.
Queries run through `kgbuilder.validation.qa_records.connect` exactly as the checker does.

## HQ01 (count of recalls whose filer name differs from the vehicles-list manufacturer)

Checked
- Gold sql returns 7. My own join (same keys, plus a `lower()` variant) also returns 7 and the same seven campaign
  numbers; all 29 recalls join a vehicles.csv row (29 joined rows, 5 distinct vehicles), so no recall is lost by the join.
- By hand over the rows: the four non-Toyota vehicles have a single filer string equal to their vehicles-list
  manufacturer (Civic 5, Escape 9, Outback 3, Rogue 5 = 22 matches); all 7 RAV4 recalls are filed under three other
  names (TEMA x5, Southeast Toyota Distributors x1, Gulf States Toyota x1). 29 - 22 = 7.
- `records`: vehicles.csv cells and the seven recalls.csv (NHTSACampaignNumber, Manufacturer) pairs match the source
  exactly; model_year written as a string "2019", as earlier gold does.
- Note: arithmetic and the list of campaigns are right; "the two distributors" is accurate for the two non-TEMA filers.

Alternative readings
- The original wording "the company that filed the recall [is] different from the manufacturer" invites an identity
  reading: Toyota Motor Engineering & Manufacturing is a Toyota subsidiary, so a careful reader could reasonably call it
  the same company as Toyota Motor Corporation and answer 2 (the two distributors). Both readings were defensible,
  so the question was ambiguous.
- Using the complaint rows' manufacturer instead of vehicles.csv gives the same 7 (complaints also say
  "Toyota Motor Corporation"); no difference between the two sources.

Changed
- Reworded to make it a name comparison: "For how many recall campaigns does the filing company's name in the recall
  record (its Manufacturer field) differ, as a name, from the manufacturer name recorded for the affected vehicle in
  the vehicles list?" Expected (7), sql and records unchanged.
- Note extended: says TEMA is a subsidiary (why the trap exists), that the question now asks about names, and that
  case folding does not change the count.

Novelty: HN01 (RAV4 recalls not filed by TEMA, answer 2), HB11 (number of distinct filers) and H56 (filer with most
recalls) are the nearest; none joins the vehicles list or asks for this count. Novel.

Doubts left: none on the answer. A solver that does not see vehicles.csv as a table could still get 7 from the
complaint rows, which is fine; the question names the vehicles list as the reference.

## HQ02 (complaints whose incident year has no recall for the vehicle)

Checked
- Gold sql returns {10809523, 10816192, 11209676, 10667633, 11123359}. My own anti-join on make + model + model year
  (not model alone) returns the same five; each model occurs with exactly one make and model year, so the gold's
  model-only match is safe.
- Year extraction: all 29 ReportReceivedDate and all 25 dateOfIncident / dateComplaintFiled strings are exactly
  10 characters with "/" at positions 3 and 6 and four digits at 7-10, so `substr(..., 7, 4)` is the year for every row
  whether the row is day-first (recalls, e.g. 18/12/2023) or month-first (complaints, e.g. 12/20/2018).
- Recall years per model from my query: CIVIC 2016/2023/2026, ESCAPE 2015/2016/2017/2020/2022/2025/2026,
  OUTBACK 2019/2020/2021, RAV4 2019/2020, ROGUE 2016/2017/2021/2022/2023, exactly as the note says. By hand: the two
  Civic incidents in Dec 2015, the RAV4 incident of 12/20/2018, the Escape incident of 12/20/2014 and the Outback
  incident of 08/28/2018 are the only incidents in a year with no recall for that model; every Rogue incident is 2017.
- Filed-date reading gives 4 (11209676 drops out, filed 05/24/2019), as the note's trap says.
- `records`: the five complaints.csv rows match the source (odiNumber as a string, dates verbatim, productModel);
  the extra dateComplaintFiled on 11209676 documents the trap. Aliases "Complaint N" follow HE05's style.

Alternative readings
- "Incident that happened in a calendar year" points only to dateOfIncident; the filed-date reading is a mistake, not
  a second meaning, and the note records it.
- "NHTSA received no recall campaign for the vehicle" could be read against real-world recalls outside the data;
  added "none of the recall campaigns in the data" to close that.
- "The vehicle concerned" could mean the model only or make+model+year; both give the same five (checked).

Changed
- Question reworded to "...a calendar year in which NHTSA received none of the recall campaigns in the data for the
  vehicle the complaint concerns?" Expected, sql and records unchanged.
- Note extended with the two reviewer checks (date-string shape for every row; full-key match gives the same five).

Novelty: HB21 (incidents in 2018), H67 (vehicles with both a 2019 complaint and a 2019 recall), HN03 (recalls received
in the model year), HE04 (recalls received before the vehicle's earliest complaint) are the nearest; none is an
anti-join of complaint incident years against recall years. Novel.

Doubts left: none on the answer. The question depends on the solver using the received date of the recall, which is
the only date field in the recall records (the "recall began" dates live in the remedy text and are not asked about).

## Final check
Re-parsed the edited file and ran both `sql` fields: HQ01 -> 7 == expected; HQ02 -> the same five odiNumbers ==
expected.
