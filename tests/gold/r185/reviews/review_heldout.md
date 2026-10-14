# Review of heldout_qa_semantic.json (HS01, HS02)

Reviewer: Claude Fable 5.1, independent of the writer. Read only the scratchpad files (all 81 chunks of
chunks.json, the three staging CSVs including every Consequence and Notes cell of recalls.csv,
earlier_questions.json) and the six earlier gold files tests/gold/qa/heldout_qa*.json. No out/, mlruns,
verdicts or answers opened.

## HS01 (complaints told the behaviour is normal)

Checked
- Re-derived the set from all 25 complaint chunks. Four qualify: 10689793 ("OPERATES AS EXPECTED", dealer
  and Ford), 10690483 ("ADVISED BY FORD THIS IS NORMAL"), 10972343 ("NISSAN ENGINEERING DECLARED THE SYSTEM
  WORKED AS INTENDED"), 11176393 (dealer "SAID THAT NOTHING IS WRONG WITH IT"). No missed member: every
  other complaint either reports no verdict, admits a defect (10667633 "KNOWN ISSUE", 11205702
  "MANUFACTURER'S DEFECT"), says the failure could not be duplicated (11030208, 11209676), was not diagnosed
  (10957753, 11205702) or concerns a Honda recall confirmation (10823637, 10824361, 10825467).
- All four quotes are verbatim (checked by substring match, including the double spaces and quotation marks
  in the 10689793 quote); all four qualifying chunks are cited.
- "Worded differently": only 10690483 contains "normal"; the other three carry the idea as "expected",
  "as intended", "nothing wrong". They share with the question only "dealer"/"manufacturer", words that
  appear in most complaints and in the lookalikes, so they do not help a word search.
- Novelty: HB38 (did 10689793's dealer deny the hesitation), H37 (what Nissan engineering declared in
  10972343), HD13, HB18 ("could not duplicate" set) touch single members; no earlier question asks for this
  set.
- Entity names: complaint numbers with alias "Complaint N", the style of the earlier gold files.

Changed
- Question wording tightened from "told the owner the vehicle was behaving normally" to "told the owner
  that the behaviour they complained about was normal". Reason: with the old wording a careful reader could
  argue that "the dealer could not duplicate the failure" (11030208, 11209676) means the vehicle behaved
  normally at the dealer. The new wording requires a verdict on the reported behaviour itself, which the
  "could not duplicate" complaints do not give. Members and evidence unchanged.
- Note corrected: it claimed a search on "normal" finds one complaint; a substring search also hits
  11032133 ("NORMALY", the owner's own word about miles to empty). That complaint is now named as a
  word-lookalike that must not count. The note also now states the duplicate rule and the earlier
  questions checked.

Doubts left standing
- 11176393 "nothing is wrong with it" is a verdict that the car is fine rather than literally "normal".
  I keep it as a member: the dealer accepted the owner's description (no steering feedback) and declared it
  not a fault, which is the same verdict as "this is normal". A strict reader could still argue it belongs
  with "no problem found" style findings; the gold treats it as in.

## HS02 (recall campaigns describing a liquid leaking)

Checked
- Re-derived the set from all 30 recall summary chunks and all 30 remedy chunks (the chunks.json has 56
  recall chunks; 22V875000 and 20V218000 hold summary and remedy in one chunk). Four qualify: 20V064000
  (coolant leak from engine castings), 22V875000 (water may leak into the harness connector), 26V011000
  (block heater coolant leak), 20V373000 (water may enter the steering gear box). No other summary or
  remedy mentions water, coolant, oil, fuel leaking, moisture or any liquid moving.
- Table check: the Consequence column adds an "engine oil leak" only for 20V064000 (already in); no other
  campaign's Consequence or Notes mentions a liquid. So the table cannot add a member.
- All four quotes verbatim; all four qualifying chunks cited.
- "Worded differently": 20V373000 says "Water may enter", sharing neither "liquid" nor "leak" with the
  question; its only shared words ("recalling", "vehicles") are in every recall chunk.
- Lookalikes are real: the four fuel pump recalls name a liquid-system part with no leak; 26V332000 has
  "crack and short circuit" like 26V011000 but no liquid; complaint 11229137 (freon smell) is not a recall.
- Novelty: HB05 uses the 22V875000 water sentence only as a clue to the vehicle; HE12, HB45, HD20 ask
  the remedies of 20V064000, 26V011000, 20V373000. No earlier question asks for the set of leak campaigns.
- Entity names: bare campaign numbers, the style of every earlier gold file (no aliases there either).

Changed
- Question and members unchanged.
- Note extended: states that all summary and remedy chunks were read, names the grease remedy of
  22V875000 as not a leak, and lists the earlier questions checked for novelty.

Doubts left standing
- Whether "water may enter through the steering gear box cover" counts as "leaking into a vehicle part"
  is the intended semantic stretch of the question; a very literal reader might exclude it because no leak
  is named. The gold keeps it in, which is the point of the capability being tested.
