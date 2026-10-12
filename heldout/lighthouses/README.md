# Benchmark dataset: Trinity House lighthouses (Wikidata tables + Wikipedia articles)

A new dataset of the R152-R157 benchmark (REFACTOR_PLAN R153), chosen by the user on 2026-10-12 from five
scouted candidates. Like the held-out NHTSA dataset (`heldout/nhtsa/`) it mixes tables and text, and the
pipeline is **frozen** for it: no prompt, threshold or model is changed because of what it does on this data.

## Source and licence

- **Tables**: Wikidata (`https://query.wikidata.org/sparql` for the item list, `https://www.wikidata.org/w/api.php`
  `wbgetentities` for the claims and labels). Wikidata is released under CC0: no conditions.
- **Text**: English Wikipedia (`https://en.wikipedia.org/w/api.php`, `prop=info` for page lengths and
  `prop=extracts|revisions` for the plain text of each article with its revision id and timestamp). The text
  is licensed under CC BY-SA 4.0. `ATTRIBUTION.md` names every article with its URL, revision and history
  (the author list); `LICENSE.md` states which files of this folder the share-alike licence covers.

Fetched on 2026-10-12 (the date is in every `raw/` file). Both sources are live and editable, so a new
`fetch` will not reproduce `raw/`: TextExtracts cannot fetch an old revision. `raw/` is the frozen copy.

## What was selected, and how

The rule is fixed in `build.py` and was implemented and run before any article text was read; the
article prose was first opened after `data/` was written.

- **Lighthouses** (`lighthouses.csv`, 80 rows): every Wikidata item that is an instance of lighthouse
  (`P31` = `Q39715`) operated by Trinity House (`P137` = `Q1570725`), all of them, with or without an
  article, as NHTSA's "every recall". Per property the preferred-rank values, else every normal-rank value;
  deprecated values never. Thirteen properties, the ones most items have (scouted on 50 items: inception
  50, height 49, historic county 49, light characteristic 41, down to structural engineer 7). Some items
  are real noise of the source (Europa Point in Gibraltar, the Casquets); the rule keeps them.
- **Events** (`events.ndjson`, 62 rows): every non-deprecated significant-event statement (`P793`) of those
  items, with its point-in-time qualifier (`P585`) as the date; 9 have no date (null).
- **Engineers** (`engineers.json`, 4 rows): every architect (`P84`) or structural engineer (`P631`) of a
  selected lighthouse, with birth, death, father and sibling.
- **Documents** (`articles/*.md`, 10 articles, 5,729 words): of the 74 items with an English article, those
  whose article title (after redirects) contains "Lighthouse" and belongs to no other selected item, with a
  current wikitext length of 6,000-10,000 bytes, lowest numeric Q-id first, the first ten. Why each part:
  - the title rule drops place articles ("Coquet Island", "Spurn") and pages shared by two items through
    redirects ("Lighthouses on Lundy", "Harwich High and Low Lighthouses"): such a page is not about one
    record;
  - under 6,000 bytes are stubs (Bamburgh 2,940, Hilbre Island 3,049) with little to ask about;
  - over 10,000 bytes are the long famous articles (Eddystone 43.6 kB, Bishop Rock 29.7 kB, Wolf Rock
    25 kB): they would dominate a small corpus and are the ones a model most likely remembers;
  - 13 articles fall in the window; ten of them at about 72 words per kB of wikitext give about 5,000-6,500
    words, 2.5-3x NHTSA's 1,974, enough for 50 questions without rewording, and cheap to build.

The sizes came out as planned for the lighthouses (80, target 70-80) and the words (5,729, target
5,000-6,500), and below the scout's estimate for the events (62 against 80-110) and the engineers (4
against about 10): only 20 lighthouses name an architect and 9 a structural engineer, and they share four
people. Nothing was tuned to change that.

## Files

`raw/` holds the selected records as fetched. `data/` is the pipeline input, written from `raw/` by
`build.py` and never edited by hand (a test rebuilds it and compares):

| File | Format | Content |
|---|---|---|
| `raw/wikidata.json` | JSON | the 80 items and 4 engineers with the claims the build reads (references and unused qualifiers dropped), and the label of every item they point at |
| `raw/documents.json` | JSON | every item's article title, its page after redirects, byte length and revision: the input of the document rule |
| `raw/articles/<slug>.json` | JSON | one kept article: title, page id, revision id, timestamp, plain-text extract |
| `data/lighthouses.csv` | CSV | qid, name, enwiki_article and the 13 properties; several values in one cell are joined by `; `, each distinct value once |
| `data/events.ndjson` | one JSON object per line, the lighthouse as a nested object | event, date |
| `data/engineers.json` | JSON list | qid, name, birth, death, father, sibling (one value as itself, several as a list) |
| `data/articles/<slug>.md` | Markdown | the article: its title as `#`, section markers as `##`, prose verbatim, empty sections dropped |

Joins: events point at lighthouses by `lighthouse.qid` and `lighthouse.name`. `architect` and
`structural_engineer` name engineers, but the profiler finds only the second as a key (three architect
cells name two people), and that key carries one person: every filled `structural_engineer` cell is
"James Walker", so through a found key the table reaches 1 of the 4 engineers. The other three are
reached only if the plan relates `architect` to them. In `engineers.json`, `father` is null for all four
people and `sibling` is set for one; both columns stay, because the column list was fixed before the
data was seen. Each document's file name is its lighthouse's `enwiki_article` title, but linking matches
file names against the plan's name column, and when the plan sets none the guess is `name` (the first
column containing "name" or "title"). With `name`, 9 of the 10 documents link: "Old Higher Lighthouse"
is the item named "Portland Bill High Light", so its document stays unlinked unless the plan chooses
`enwiki_article` as the name column. This is a known property of the benchmark, not something to fix for
it.

## Risks, and how the gold limits them

- **Memorisation.** Every LLM has read Wikipedia, so some answers could come from memory instead of the
  graph. The byte window keeps the famous long articles out. The gold favours what memory cannot supply:
  counts and filters over this fixed selection ("how many of these lighthouses ..."), values as they stand
  in this snapshot, and questions that need a table and a document together. The scout proposed a cheap
  closed-book control (the frozen model, no tools) to report next to the scores.
- **The scout read two articles.** While scouting, before the rule was written, the scout opened the full
  text of two articles: Bull Point Lighthouse (not selected: 11,814 bytes) and Old Higher Lighthouse (one of
  the ten). The rule's thresholds come from byte counts and article counts, not from what either says.
- **Tables and text repeat or contradict each other** (an inception year of the current tower against a
  history of older towers). Gold questions say which source they read, or avoid the conflicting facts.
- **Encyclopedic text** is cleaner than NHTSA's owner narratives: an easier text style, to be said so in the
  thesis.
- **Multi-valued cells are per-light values without their light.** Wikidata tells a lighthouse's several
  ranges, focal heights or characteristics apart by a qualifier (the colour of the light, `P462`), which
  `raw/` drops: St Anthony's `range_nmi` "16; 14" is 16 for white and 14 for red, but the CSV cannot say
  which. Today 15 `range_nmi`, 5 `focal_height_m`, 7 `light_characteristic` and 1 `height_m` cells hold
  several values. Gold questions do not ask for "the" value of such a cell; they ask about the whole set
  or avoid it.
- **The measurement columns are text.** Those multi-valued cells make the profiler type `height_m` (one
  cell, Maryport's "4.7; 6"), `focal_height_m` and `range_nmi` as text (VARCHAR), so a question that
  compares or sorts them ("taller than", "the longest range") tests text parsing as much as reasoning.
  Gold questions avoid numeric comparison on these columns, or are written knowing they are text.
- **One idea, two event labels.** The events say "automatization" (38 rows) and "automation" (2 rows,
  Lizard and Longships): two Wikidata items for one idea. A question like "how many lighthouses were
  automated" names the exact label, or its gold accepts both readings. The build does not merge them:
  that would shape the data.

## Commands

```
uv run python heldout/lighthouses/build.py fetch   # network: re-download raw/ (sources change: not reproducible)
uv run python heldout/lighthouses/build.py build   # offline: raw/ -> data/ and ATTRIBUTION.md
uv run kg --preset lighthouses run --goal "..."    # a comprehensive run: Claude Code asks first
```

The cost of one run is not measured yet, so the preset's description gives none. Scaled from the
held-out preset (about $0.15 per run) by document words (5,729 against 1,974, about 2.9x), a first guess
is about $0.45; the first run's `cost_usd` replaces it, and then goes into the preset's description.
