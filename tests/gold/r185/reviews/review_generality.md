# Review of generality_qa_semantic.json (GS01, GS02)

Reviewer: Claude Fable 5.1, adversarial pass, 2026-10-14. Read only the scratchpad folder
(chunks.json, staging/, corpus_spec.json, earlier_questions.json) and tests/gold/qa/generality_qa*.json.
All 29 chunks of chunks.json were read in full; both staging tables too (neither holds a fact for these questions).

## GS01: How many documents report that the Harbour Street resurfacing was pushed back to a later year? (3)

Checked
- Re-derived from all 29 chunks. The resurfacing is mentioned in exactly six chunks (bakery, budget 2025,
  budget 2026, council notices, flood meeting, letter). Qualifying: council notices ("postponed to 2026"),
  budget 2026 ("carried over from 2025", "had slipped by a year"), the letter ("moved to next year's budget").
  Not qualifying: budget 2025 (the postponed thing is the vote on the motion; the resurfacing is a 2025 line),
  flood meeting (a call to reorder two projects, not a report that one moved), bakery (a hope it starts before
  winter). A term scan for postpon/slipped/carried over/moved/delay/later over every chunk found no other hit
  about the resurfacing ("moved" in the Mill Lane item is the railing; "later" in the travel memo is late claims).
  Answer 3 confirmed; one chunk per document, so passages and documents agree.
- All three quotes are verbatim (checked by string containment, not by eye).
- Wording: the council notices passage shares no word of the predicate ("pushed back", "later year") with the
  question; the other two share only "year". The project name is unavoidably shared with every mention,
  qualifying or not, which is the point: the mentions are easy to find, the work is judging each one.
- Lookalikes are real, three of them, all named in the note.
- Novelty: GE05 (confirm3) counts documents that name Harbour Street (6, exact route); G-series asks which
  Pike wrote about the delay; GB-series asks the 2026 amount and whether the motion was voted on. None asks
  how many documents report the postponement.

Changed
- Note only. The writer claimed the three passages use "none of the question's words"; two of them contain
  "year" and all contain the project name. Reworded to say exactly which words are shared and why that is fine.
  Question, answer and evidence unchanged.

Doubts left standing
- A reader who pairs the 2025 and 2026 budgets could say "the 2025 budget shows it was planned for 2025", but
  that document reports nothing about a move on its own; the question says "documents report", singular per
  document. I consider 3 safe.

## GS02: Which pieces of equipment do the documents report as having got stuck or clogged? (corer; intake screen of KV12-0457)

Checked
- Re-derived from all 29 chunks. Only two things are reported stuck or blocked: the corer (field log "jammed at
  5 m", minutes "the first one jammed in December", one event, one entity) and the Harbour Station intake
  screen ("half blocked with leaves"). I looked for every other candidate: culvert too narrow (undersized, and
  not equipment), corer lent out (unavailable), KV12-0457 tripping on low suction pressure in April and the
  June alarm (symptom, no cause reported), seal drip/failure, pump "ran dry", flume pump vibration, power cut,
  flooded site AB-20, footbridge closed, east railing that moves (the opposite of stuck), library roof leak.
  None qualifies. A term scan for jam/block/clog/stuck/seiz/obstruct confirms only the three cited chunks.
- All three quotes verbatim. Every qualifying chunk is cited (the April shift notes and June notes are
  lookalikes, correctly left out).
- Wording: the question says "stuck or clogged"; the passages say "jammed" and "half blocked with leaves". The
  field log passage shares no key word with the question at all. Lookalikes are real and named in the note.
- Novelty: earlier questions use the jammed corer and the cleaned screen as hops (who freed it, which core,
  install year, trips after cleaning), none asks for the set of stuck/clogged equipment.

Changed
- Aliases: added "peat corer" and "the Aldmoor team's corer" for the corer; added "intake screen of the Harbour
  Station pump", "pump KV12-0457", "Kestrel V-12", "the Kestrel V-12 pump", "the Harbour Station pump", "the
  pump at Harbour Station" for the screen, since a reader is likely to name the pump rather than its screen
  (the May report itself frames the trips as the pump's problem).
- Note: added the footbridge/railing as a non-member (closed and moving are not stuck, and neither is
  equipment), named the pump's model and station, and recorded that the field log's corer and the minutes'
  "first" corer are one entity, so listing both is a surplus member.
- Question, answer set and evidence unchanged.

Doubts left standing
- An answer that lists both "intake screen" and "KV12-0457" as two items would be one entity named twice; the
  judge by meaning should see one, exact matching may see a surplus. Same for "corer" plus "first corer".
- "Equipment" is slightly loose for a pump's screen; the aliases and the note make the pump-level answer
  acceptable, so the looseness does not change the set.
