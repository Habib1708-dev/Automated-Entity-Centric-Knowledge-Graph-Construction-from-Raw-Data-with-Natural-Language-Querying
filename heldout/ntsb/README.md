# Benchmark dataset: NTSB aviation accidents (database tables + investigators' narratives)

A new dataset of the R152-R157 benchmark (REFACTOR_PLAN R154), chosen by the user on 2026-10-12 from five
scouted candidates. Like the held-out NHTSA dataset (`heldout/nhtsa/`) it mixes tables and text, and the
pipeline is **frozen** for it: no prompt, threshold or model is changed because of what it does on this data.

## Source and licence

The US National Transportation Safety Board's aviation accident database, the MS Access export of 2008 to
present (`avall.zip` from `https://data.ntsb.gov/avdata`, 96,583,215 bytes; inside it `avall.mdb`, written
by the NTSB on 2026-10-01). No key or login. Works of the US federal government are in the public domain
(17 U.S.C. 105). One caveat: 49 U.S.C. 1154(b) bars using NTSB accident reports as evidence in a civil
action for damages; it does not restrict research use.

Fetched on 2026-10-12 (the date and the database's own date are in every `raw/` file). The NTSB refreshes
`avall.zip` every month and can revise a closed case, so a new `fetch` may differ; `raw/` is the frozen copy.

## What was selected, and how

The rule is fixed in `build.py` (`EVENT_TYPE`, `FIRST_DAY`/`LAST_DAY`, `MODELS`, `MIN_WORDS`/`MAX_WORDS`,
`EVENTS_PER_MODEL`) and was implemented and run before any narrative was read. Its thresholds were checked on
counts only.

1. Accidents (`ev_type` ACC, not incidents) dated 2021-01-01 to 2023-12-31: old enough that the final report
   and its probable cause exist (4,546 accidents).
2. Exactly one aircraft in the event: no mid-air collisions (4,494).
3. The aircraft is one of six models of six makes: Cessna 172S, Piper PA-28-181, Cirrus SR22, Beech A36,
   Robinson R44 II, Air Tractor AT-502B (airplanes and a helicopter; personal, training and agricultural
   flying; piston and turbine engines). The model must equal the selected one and the reported make start with
   the selected make, both upper-cased with spaces and hyphens removed, because the source spells them per
   record ("CIRRUS DESIGN CORP" / "SR22", "CIRRUS" / "SR-22", "ROBINSON HELICOPTER COMPANY" / "R44 II"). 199
   candidates (`raw/selection.json`). The spelling is handled only within those two columns: the series
   (`acft_series`) is not read, and a successor make is no prefix of the selected one. So records of the six
   models are not candidates when the model is split over model and series ("172" / "S", "R44" / "II",
   "PA28" / "181", "AT502" / "B") or the make is TEXTRON AVIATION INC (Cessna 172S) or RAYTHEON AIRCRAFT
   COMPANY (Beech A36): 56 single-aircraft accidents of the window (Cessna 172S 26, Robinson R44 II 17, Air
   Tractor AT-502B 6, Piper PA-28-181 5, Beech A36 2), 16 of them eligible under step 4. This was found by a
   review after the rule was frozen and run; the rule stands, so the pool depends on how each record is
   written. Reading the series too would have given 245 candidates, 116 eligible, and 3 of the 30 kept
   events replaced (counted, not used).
4. The factual narrative (`narr_accf`) has 100-250 words and the probable cause (`narr_cause`) is not empty:
   103 eligible. Under 100 words is a one-paragraph note with little to ask about; over 250 a long
   investigation that would dominate a small corpus.
5. Per model, the 5 lowest NTSB numbers (`ntsb_no`, compared as text): 30 events.

Eligible events per model, counted before freezing: Cessna 172S 22, Piper PA-28-181 19, Cirrus SR22 15,
Beech A36 11, Robinson R44 II 29, Air Tractor AT-502B 7; every model has at least 5, so the list stood.
`select_events` raises instead of taking fewer if a later fetch leaves a model short.

What the rule gives, and should be said with any result:
- **Regional and year skew.** An NTSB number starts with the investigating office and the year
  ("CEN21LA126"), so "lowest" means offices in alphabetical order, earliest year first: 27 events are from
  the Central region (CEN) and 3 from Alaska (ANC), none from the Eastern (ERA) or Western (WPR) regions
  (the eligible pool has 57 CEN, 30 ERA, 13 WPR, 3 ANC); 19 are from 2021, 8 from 2022, 3 from 2023.
  Ordering by date instead would have given 18 CEN, 9 ERA, 2 WPR, 1 ANC (counted, not used).
- **Short, limited investigations.** The word window keeps 29 "LA" (limited, often the pilot's own report)
  cases and 1 "FA" (field) case; 23 of the 30 events had no injury and 1 was fatal.

## Files

`raw/` holds the selected rows as read from the database. `data/` is the pipeline input, written from `raw/`
by `build.py` and never edited by hand (a test rebuilds it and compares):

| File | Format | Content |
|---|---|---|
| `raw/selection.json` | JSON | the 199 candidates of steps 1-3 with the counts the rule reads (narrative words, whether a cause exists): the input of steps 4-5 |
| `raw/tables.json` | JSON | the 30 events' rows of the nine tables used, only the columns the build or the rule reads |
| `raw/codes.json` | JSON | the meaning of every code of the 18 decoded columns, from the database's data dictionary and code table |
| `data/models.csv` | CSV, 6 rows | name ("Cessna 172S"), make, model, the make as the records spell it most often, category, engine type |
| `data/events.csv` | CSV, 30 rows | NTSB number, date, local time, place, nearest airport, light and weather conditions, wind, injury totals, coordinates |
| `data/aircraft.json` | JSON list, 30 records | NTSB number, registration, selected model (the key to `models.csv`), the reported make and model, year, category, regulation part, damage, operator, flight purpose, airports, and the engine as a nested object |
| `data/findings.ndjson` | one JSON object per line, 67 | the investigators' coded findings |
| `data/events_sequence.ndjson` | one JSON object per line, 72 | the sequence of occurrences, one marked as the defining event |
| `data/crew.csv` | CSV, 38 rows | each person of the crew table: role, age, sex, medical certificate, injury, seat, and six flight-hour totals pivoted in from `flight_time` |
| `data/injuries.csv` | CSV, 143 rows | person category, injury level, number of people |
| `data/documents/<make>_<model>_accidents.md` | Markdown, 6 | `# <Make> <Model> accidents investigated by the NTSB`, then per event `## <ntsb_no> (<date>, <city>, <state>)` with the factual narrative verbatim and `Probable cause: <narr_cause>` verbatim |

In all 386 structured rows (NHTSA: 59) and 6 documents of 5,513 narrative words (4,877 narrative and 636
probable cause; NHTSA: 1,974). The narratives appear only in the documents, so the text path carries them.
`ntsb_no` is the one visible key: the internal `ev_id` is not in `data/`, and the build adds `ntsb_no` to every
child table.

Joins, as the profiler finds them (asserted in `tests/test_ntsb.py`): every child table's `ntsb_no` points at
both `events.csv` and `aircraft.csv` (with one aircraft per event, both hold it once), the two point at each
other, and `aircraft.model` and `aircraft.reported_model` point at `models.csv` `model`. Documents link to
their model by file name: with no name column in the plan the guess is `name` ("Beech A36"), and all six
link; a plan that names `model` instead leaves the Beech A36 document unlinked, because "A36" is shorter than
the 4 characters linking trusts in a file name. Each section heading names its event's key, so every whole
section links to its event. The pipeline links chunks, though, and four sections are longer than the
default `chunk_max_chars` (1500, which the `ntsb` preset keeps): CEN21LA362 (Air Tractor AT-502B),
CEN23LA344 (Beech A36), CEN23LA017 (Cirrus SR22) and CEN21LA353 (Piper PA-28-181). The chunker cuts each in
two and only the first piece keeps the `## <ntsb_no>` heading, so 4 of the 34 chunks link to no event (see
"Known properties"; `tests/test_ntsb.py` pins them).

## What the build changes, and what it leaves out

- **Codes become words** through the database's data dictionary (`eADMSPUB_DataDictionary`), else its code
  table (`ct_iaids`): damage SUBS is "Substantial", far_part 091 "Part 91: General Aviation". Four codes are
  defined in neither, the injury table's newer person categories `Flig`, `Pass`, `Cabi`, `LapC`; they stay
  as they are, no meaning is guessed.
- **Text values** lose their outer spaces ("West Liberty " is "West Liberty"); an empty one is null.
  Narratives keep their inner layout; only their line endings become "\n".
- **Single-precision numbers** (flight hours, airport distance) are written as typed: 48.6, not
  48.599998474121094.
- **Left out**: owner names and addresses, the operator's address, the crew's home city and state, the NTSB
  staff user ids, and, where the NTSB marks the operator as a private individual (10 of 30), the operator's
  name (the NTSB already leaves it empty) and the aircraft's registration (`regis_no`, so `registration` is
  null): the public FAA registry turns an N-number into its owner's name and address. Both are cleared by
  `fetch` before `raw/` is saved; no narrative names a registration. The air temperature is left out too: the data dictionary gives it in
  Celsius, but the selected values read as Fahrenheit (28 in Alaska in March, 90 in Michigan in May), so its
  unit is unknown. The findings' `Cause_Factor` and `cm_inPc` flags are empty on every selected row (the
  first is read correctly in older years, "C" or "F" until 2020, and is empty on every row since 2021; the
  second is empty in every year, so it is either unused or unreadable), so `findings.ndjson` has no
  cause/factor column; `raw/` keeps both to show it.
- **Injuries** keep every source row, the "Total" rows and the zero counts too (see "Known properties" for
  why the "Total" rows are not totals to trust).

## How the database was read

`fetch` downloads `avall.zip` once into a cache directory outside the repository (`--cache`, default
`<temp>/kgbuilder-ntsb`; the 97 MB download took about 5 minutes) and reads the 561 MB `avall.mdb` with the
pure-Python `access-parser` 0.0.6 (pinned in the command; `fetch` refuses another release, because its
text fix replaces a function inside that release's module), which is not a project dependency (the machine has no 64-bit Access driver,
no mdbtools and no Java). Every used table is read whole: about 45-60 seconds for the run, well within
memory, so no partial read was needed. access-parser decodes Jet 4 "compressed" text wrongly (it ignores the
switches between one and two bytes per character, so "O’Hare" came out as "O\x19 Hare", with one word too
many); `fetch` replaces that one decoding step with the rule mdbtools uses (`decompress_text`, tested). The
selection was computed after the fix; it is the same 30 events as before it.

## Known properties, and how gold questions should treat them

- **Formulaic narratives.** Many sections repeat the standard sentence that the pilot reported "no preaccident
  mechanical malfunctions or failures ... that would have precluded normal operation", and `narr_accf` of a
  limited investigation is often the pilot's account more than an analysis.
- **The probable cause overlaps the findings.** `Probable cause:` restates in words what `findings.ndjson`
  codes ("loss of directional control" against "Directional control-Not attained/maintained"), so such a fact
  can be answered from either path; questions that need the text ask what only the narrative says (the
  sequence of actions, what the pilot saw or said, the weather in words).
- **Spelling differences are real.** Table values are upper case ("GRIMES FLD", "LUNKEN FLIGHT TRAINING
  CENTER LLC") where the narratives use full or trade names; the reported make varies within one model
  ("ROBINSON HELICOPTER COMPANY" and "ROBINSON HELICOPTER"). They test entity resolution and are not fixed.
- **Four chunks belong to no event.** At the default chunk size the probable causes of CEN21LA362,
  CEN23LA344 and CEN23LA017, and the end of CEN21LA353's narrative with its probable cause, are chunks
  without the `## <ntsb_no>` heading (see Joins above). Linking puts them on no event, and their only context
  is the document's title, so the extractor cannot tell from the chunk which accident they are about. A gold
  question on these four events' probable causes (or on why CEN21LA353's landing gear failed) allows for
  that; much of a probable cause is also coded in `findings.ndjson`. The layout is not changed to avoid it: a separate
  section per probable cause would fix three of the four, and the fourth only with a larger chunk size, which
  is a change to the frozen pipeline.
- **"Total" injury rows are not totals.** 54 of the 143 rows of `injuries.csv` have the level "Total": 44
  without a count, 10 with one, and 5 of those 10 disagree with the level rows of the same event and person
  category (CEN21LA280 `Flig` Total 0, but `Flig` None 1). The level rows are consistent: their sums equal
  the injury totals of `events.csv` for all 30 events. A gold question about how many people were injured
  counts the level rows (or reads `events.csv`), never the "Total" rows.
- **Domain close to NHTSA.** Again US transportation-safety records with narratives; the user accepted this
  on 2026-10-12. The text differs (investigators' prose, not owners' complaints) and there are seven tables.

## Commands

```
uv run --with access-parser==0.0.6 python heldout/ntsb/build.py fetch [--cache DIR]  # network once, then the cached database
uv run python heldout/ntsb/build.py build                                           # offline: raw/ -> data/
uv run kg --preset ntsb run --goal "..."                                            # a comprehensive run: Claude Code asks first
```

The cost of one run is not measured yet. Scaled from the held-out preset (about $0.15 per run) by document
words (5,513 against 1,974, about 2.8x), a first guess is about $0.40-0.50, more if the seven tables make the
plan larger; the first run's `cost_usd` replaces it, and then goes into the preset's description.
