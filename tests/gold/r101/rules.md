# What a mention is (R101): one definition for the prompt, the code checks, the gold and the judge

A mention is an **index entry**: a thing a question could plausibly start from or ask about, written in a
sentence. The graph keeps it so that a reader can find the sentence from the thing. It is not a phrase of
the sentence, and the graph must not become a copy of the text: whatever is not a thing stays in the
sentence, where retrieval by text still finds it.

Every example below is invented (an observatory and a ferry line) and comes from no dataset under
evaluation.

## In

1. **Named particulars:** a person, place, organisation, event, work, award or identifier, as the text names
   it: "Edit Varga", "North Dome", "Lakeside Ferries", "spring open night", "Varga Prize", "T-4471".
2. **Kinds the text says something about:**
   - an object or a piece of one: "mirror", "shutter motor";
   - a state, condition or fault, including a property the text says something about: "condensation",
     "hairline crack", "misalignment", "brightness" (in "the brightness fades");
   - an incident or event: "power cut", "collision";
   - a work done to a thing, one that makes, fits, repairs, cleans, tests, replaces or withdraws it:
     "installation", "recalibration", "replacement of the hull plates", "inspection";
   - a person or organisation known only by its role: "technician", "ferry operator", "ticket office".
3. **A fault, state or incident of a thing, or a work done to a thing, written as a verb counts too**,
   as the verb form the text writes: "the shutter sticks" gives "sticks", "the crossing was cancelled" gives
   "cancelled", "the hull was repainted" gives "repainted". Other verbs stay Out (Out 2 and 8).
4. **Each of these also when the text says it is absent or did not happen:** in "No condensation was found
   on the mirror", both "condensation" and "mirror" are mentions. Whether a thing happened is the
   sentence's business, not the mention's.

## Out

1. Descriptive words alone: adjectives, manner, degree ("smooth", "quickly", "very").
2. Light or reporting verbs ("is", "has", "showed", "explained", "said", "arrived", "made").
3. Whole clauses and evaluations that name no thing ("it works as expected", "worth the money").
4. Quantities, dates and times, seasons included ("3.2 kg", "40 minutes", "14 April", "2025", "autumn"):
   claims hold values.
5. The document or source itself ("this page", "the letter"); its writer or reader referred to only as such
   ("the writer", "the person reporting", "the undersigned"); a form of address ("Madam", "Dear neighbours"),
   though a name in it is an entry ("Dear Lakeside Ferries": Lakeside Ferries). A writer called by what they
   are ("as a ferry pilot, I ...") is a role (In 2).
6. Pronouns ("it", "she", "they", "this").
7. Generic words that would fit any text, even as the subject or object of a sentence ("thing", "issue",
   "experience", "number", "time", "people", "stuff").
8. Everyday acts of people, the ordinary use of a thing included, that are not a fault, an incident or a work
   done to a thing: contacting, writing, filing, sending, presenting, buying, recommending, reading,
   deciding, boarding a ferry, looking through a telescope ("she contacted the office", "the form was
   filed").
9. A title, role or common noun written next to a thing's name or in apposition with it: it names the same
   thing (one entry per thing), so the name is the only entry, even when the name is already listed
   ("dome technician Edit Varga" and "Edit Varga, the dome technician": Edit Varga; "ferry T-4471": T-4471).

## How a mention is written

- The name is copied **verbatim** from the sentence, as whole words, without a leading article ("a", "an",
  "the"): the shortest span that names the thing ("hairline crack", not "small hairline crack near the
  edge"). A describing word stays when it tells the thing apart ("shutter motor", "spring open night").
- One entry per thing per sentence, even when the sentence names it twice. When a sentence gives two names of
  one thing ("Night Vision Unit (NVU)"), the entry is the first.
- Its class: **particular** (one named thing: In 1) or **kind** (In 2 and 3). The class is how the text
  refers to the thing, not what the thing is: the text calls a particular by its own name, a proper name or
  an identifier; a common noun is a kind even where it means one particular thing ("the dome" for North
  Dome, "the morning crossing"). A type of the schema does not decide the class: one type holds "North
  Dome" and "the dome" alike.

## For the judge of a mention the pass added (R102 precision)

- **VALID:** the name is In, verbatim, and its class is right.
- **INCORRECT:** the name is Out, is not the thing's name (a fragment, a clause), or its class is wrong.
- **AMBIGUOUS:** a reader could take it either way (a describing word that may or may not name a state).
- The class shown is the one the pass stated (R104); for a pass that stated none (R102), the one its type
  gave.

## Revised in R104 (2026-10-07): clarifications, the gold unchanged

Rules that read two ways are settled the way the gold written under R101 already reads them, so no gold
entry changes: a title or role next to a name (Out 9); a work done to a thing against its ordinary use (In 2,
In 3, Out 8); the writer, the reader and forms of address (Out 5); seasons (Out 4); generic words as subjects
or objects (Out 7); and the class as the way the text refers to the thing. The evidence, quoted from the
gold, is in REFACTOR_PLAN.md (R104), kept out of this file so that its examples stay invented.
