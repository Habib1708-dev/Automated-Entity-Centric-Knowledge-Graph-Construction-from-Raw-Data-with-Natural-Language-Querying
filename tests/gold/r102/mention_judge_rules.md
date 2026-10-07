# Mentions: rules for the judge

A knowledge graph keeps, for every chunk of text, an index of the things the chunk names (its mentions), so
that a reader can find the chunk from the thing. You judge two kinds of items. Decide from the text the item
shows alone. Every example below is invented and comes from no dataset you will judge.

## The definition of a mention

A mention is an **index entry**: a thing a question could plausibly start from or ask about, written in a
sentence. It is not a phrase of the sentence: whatever is not a thing stays in the sentence.

### In

1. **Named particulars:** a person, place, organisation, event, work, award or identifier, as the text names
   it: "Edit Varga", "North Dome", "Lakeside Ferries", "spring open night", "Varga Prize", "T-4471".
2. **Kinds the text says something about:**
   - an object or a piece of one: "mirror", "shutter motor";
   - a state, condition or fault, including a property the text says something about: "condensation",
     "hairline crack", "misalignment", "brightness" (in "the brightness fades");
   - an incident or event: "power cut", "collision";
   - an action done to a thing or by it: "recalibration", "replacement of the hull plates", "inspection";
   - a person or organisation known only by its role: "technician", "ferry operator", "ticket office".
3. **A fault, state or incident of a thing, or an action done to a thing, written as a verb counts too**,
   as the verb form the text writes: "the shutter sticks" gives "sticks", "the crossing was cancelled" gives
   "cancelled", "the hull was repainted" gives "repainted". Other verbs stay Out (Out 2 and 8).
4. **Each of these also when the text says it is absent or did not happen:** in "No condensation was found
   on the mirror", both "condensation" and "mirror" are mentions.

### Out

1. Descriptive words alone: adjectives, manner, degree ("smooth", "quickly", "very").
2. Light or reporting verbs ("is", "has", "showed", "explained", "said", "arrived", "made").
3. Whole clauses and evaluations that name no thing ("it works as expected", "worth the money").
4. Quantities, dates and times ("3.2 kg", "40 minutes", "14 April", "2025").
5. The document or source itself, and its writer when named only by that role ("this page", "the letter",
   "the writer").
6. Pronouns ("it", "she", "they", "this").
7. Generic words that would fit any text ("thing", "issue", "time", "people", "stuff").
8. Everyday acts of people that are not a fault, an incident or a work done to a thing: contacting, writing,
   filing, sending, presenting, buying, recommending, reading, deciding ("she contacted the office").

### How a mention is written

- The name is copied **verbatim** from the text, as whole words, without a leading article: the shortest
  span that names the thing ("hairline crack", not "small hairline crack near the edge"). A describing word
  stays when it tells the thing apart ("shutter motor", "spring open night").
- Its class: **particular** (one named thing: In 1) or **kind** (In 2 and 3).

## Item kind 1: a recall item (it has "sentence", "name" and "candidates")

A reader wrote down `name` as a mention of `text` (the sentence). The graph's index of the sentence's chunk
has no entry with exactly that name, but it has the near names in `candidates` (one of them holds the other
as whole words). The chunk's full text is given so you can see where each candidate stands.

**Question: does one of the candidates, as an entry of this chunk, name the same thing as `name` does in the
sentence?**

| Label | When |
|---|---|
| VALID | yes: a reader looking for the thing `name` names would rightly be led to this sentence by one of the candidates ("shutter motor" in the sentence, candidate "motor" where the chunk means that same motor) |
| INCORRECT | no candidate names that thing: each names something else, a piece or a whole of it, or another thing of a similar name ("dome" for "North Dome" when the chunk's "dome" is another dome; "shutter" for "shutter motor") |
| AMBIGUOUS | the text honestly supports both |
| UNJUDGEABLE | the item does not show what a decision needs |

A broader or narrower name counts as the same thing only when, in this chunk, it clearly refers to the same
thing (the chunk calls "the shutter motor" just "the motor" a line later: VALID); a piece is never its whole
and a whole is never its piece.

## Item kind 2: a precision item (it has "type", "mention_class" and "text")

An automatic pass added the mention `name` to the chunk `text`, with the class `mention_class`.

**Question: is this mention, read in its chunk, an entry the definition admits, written as a name and with
the right class?**

| Label | When |
|---|---|
| VALID | the name is In, verbatim in the chunk, the shortest span that names the thing, and its class is right |
| INCORRECT | the name is Out, is not the thing's name (a fragment, a clause, a span much longer than the name), or its class is wrong |
| AMBIGUOUS | a reader could take it either way (a describing word that may or may not name a state) |
| UNJUDGEABLE | the item does not show what a decision needs |

## Every verdict

- "id": the item's id, copied exactly;
- "label": one of the labels above;
- "reason": one line;
- "evidence": a quote copied character for character from the item's sentence (recall item) or chunk text
  (precision item) that shows the reason; only UNJUDGEABLE may leave it empty.
