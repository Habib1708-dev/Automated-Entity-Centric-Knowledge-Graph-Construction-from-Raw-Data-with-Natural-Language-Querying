"""The mention pass: the retrieval-worthy things a chunk names or talks about that no claim names (R101).

Role in the pipeline: `kg mention-pass`, after `kg link` (derivation must not see these mentions) and before
`kg resolve` (which decides what each one refers to, as for any mention). Until R101 a mention existed only
as the end of a claim, so a thing no fact type fits ("No leaks were found on the pump", a place named only in
a title) was unreachable: 2 of 86, 11 of 65 and 15 of 62 target names had no node at all (R90).
Design: the LLM proposes, code decides (one definition for the prompt, these checks, the gold and the judge:
tests/gold/r101/rules.md). Per chunk the model lists the things not already listed, each with two separate
answers (R104): its class (named by its own name, or a kind) and its type (one of the schema's entity types,
or none). Until R104 the type alone was asked and the class followed from it, so a common noun of a type
meant for named things ("café" as a place) became a named individual. Code refuses every finding an Out rule
it can see rejects, with the rule as the reason: not whole words in the chunk or its document's name (the C1
rule), more than a few words or clause punctuation, only function words, a pronoun, a number, quantity, date
or time, the `Value` type or an unknown type, a name the chunk already lists, a name given twice. The Out
rules code cannot see (a describing word alone, a reporting verb, a title next to a name) are measured by the
judge, never by a word list of a domain. Code then files each accepted finding under a type that can hold
its class (`filed_type`): the proposed one, else the built-in fallback of the class (`Particular`, `Kind`,
text/schema.py). `pass_rows` turns the accepted findings into rows: one mention per document and normalised
name, so a mention a claim or derivation already made for the name is reused (its type wins) and only gains
the chunk's MENTIONS edge.
Not here: writing (text/subject_graph.py), the stage (pipeline/stages.py), deciding identity (resolution/).
"""

import logging
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..core.errors import LLMResponseError
from ..core.identity import document_of
from ..core.text import contains_words, norm
from ..core.values import UNIT_SYMBOLS, VALUE_TYPE, parse_quantity
from ..llm.base import LLMClient
from .chunking import Chunk
from .extraction import PRONOUNS
from .schema import KIND_TYPE, PARTICULAR_TYPE, IdentityClass, MentionClass, TextSchema
from .subject_graph import MentionRow, mention_row

log = logging.getLogger(__name__)

# The pass's accepted findings, one per line, which the audit's snapshot replays (audit/snapshot.py), and its
# refused ones with their reasons
PASS_FILE = "mentions.jsonl"
PASS_REJECTED_FILE = "mentions_rejected.jsonl"

_WORKERS = 8  # independent LLM calls, one per chunk

# A name of more words is a phrase, not an entry ("replacement of the hull plates" has five)
_MAX_WORDS = 6
# Punctuation that joins clauses or lists: a name holding it is more than one thing or a clause
_CLAUSE_PUNCTUATION = re.compile(r"[,;:!?()\[\]\"]")
_ARTICLE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
# Function words of English: a name made only of these names nothing. Language-level, not a domain's.
# fmt: off
_FUNCTION_WORDS = PRONOUNS | frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "of", "to", "in", "on", "at", "by", "for", "with", "from",
    "into", "onto", "over", "under", "about", "as", "if", "then", "than", "so", "not", "no", "yes", "all",
    "any", "some", "each", "every", "both", "either", "neither", "more", "most", "less", "much", "many",
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "do", "does", "did", "can",
    "could", "will", "would", "shall", "should", "may", "might", "must", "there", "here", "what", "which",
    "who", "whom", "whose", "when", "where", "why", "how", "very", "too", "also", "just", "only", "still",
})
# Words of the calendar and the clock: with digits they make a date or a time, which claims hold as values
_CALENDAR = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
    "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "am", "pm",
})
# Units of duration: a number with one is a time span ("40 minutes"), a value a claim holds
_DURATIONS = frozenset({
    "second", "seconds", "sec", "secs", "minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs",
    "day", "days", "week", "weeks", "month", "months", "year", "years",
})
# fmt: on

# The pass. Intent, rule by rule: "an index of the text" is the job (R101: index entries, not phrases, so
# the graph never becomes a noisy copy of the text); In and Out are the definition's (rules.md), each Out rule
# a kind of noise the pass must not add; "written as a verb" because a fault is as often "sticks" as "a stick"
# and questions start from it ("leaking", "crashed" had no node, R90); "known only by its role" because a
# question asks about "the dealer" or "customer service" as about a named one; "also when absent" because a
# denied thing is still what a question asks about ("No condensation was found"), the sentence keeps the
# denial; "not listed yet" because the claims' mentions are already in the graph.
# R104, from R102's judged errors: the class is asked as its own answer and explained apart from the types,
# because the type alone decided it and a common noun of a type meant for named things became a named thing
# ("café", "vote", "street"; 20 of 33 errors); the title rule, because the role next to an already listed
# name was the only new word left to list ("Councillor" before a listed councillor; 6 of 33); "work done to
# a thing" and "ordinary use" settle the clash of "an action done to a thing" (In) with everyday acts (Out)
# that let "starting" and "driving" in; the writer "referred to only as such" and forms of address, because
# a complaint's reporter and a letter's "Sir" were listed as people; seasons, because "winter" was a named
# thing; "even as the subject or object", because a generic word was listed when the sentence spoke of it.
# "none" lets a thing no schema type fits be listed instead of skipped, code giving it its class's fallback
# type. The examples come from an invented observatory and ferry line, never from evaluated data.
MENTION_PROMPT = """List the things this text names or talks about that are not listed yet: the entries an
index of the text would have, so that a reader looking for one of them finds this text.

In:
- named things: people, places, organisations, events, works, awards and identifiers;
- kinds the text says something about: an object or a piece of one; a state, condition or fault, a property
  included ("the brightness fades": brightness); an incident or event; a work done to a thing that makes,
  fits, repairs, cleans, tests, replaces or withdraws it (an installation, a recalibration, an inspection);
  a person or organisation known only by its role ("technician", "ferry operator");
- a fault, state or incident of a thing, or a work done to it, written as a verb, as the text writes it
  ("the shutter sticks": sticks; "the hull was repainted": repainted);
- each of these also when the text says it is absent or did not happen ("No condensation was found on the
  mirror": condensation, mirror).
Out:
- a describing word alone; a light or reporting verb such as "is", "has", "showed" or "explained"; a clause
  or evaluation that names no thing; a quantity, date, time or season; a pronoun;
- words that would fit any text, even as the subject or object of a sentence ("thing", "issue",
  "experience", "number", "time", "people");
- everyday acts of people, the ordinary use of a thing included, that are not a fault, an incident or a work
  done to the thing (contacting, filing, buying, reading, boarding a ferry, looking through a telescope);
- the text or document itself; its writer or reader referred to only as such ("the writer", "the person
  reporting", "the undersigned"); a form of address ("Madam", "Dear neighbours"), though a name in it counts;
- a title, role or common noun written next to a thing's name or in apposition with it: it names the same
  thing, so the name is the only entry, also when the name is already listed ("dome technician Edit Varga"
  and "Edit Varga, the dome technician": Edit Varga; "ferry T-4471": T-4471).

Copy each name verbatim from the text, as whole words and without "a", "an" or "the": the shortest span that
names the thing, keeping a describing word only when it tells the thing apart ("shutter motor", not "old
shutter motor in the dome"). List each thing once; when the text gives it two names ("Night Vision Unit
(NVU)"), list the first.

Give every thing two separate answers:
- mention_class, how the text refers to it: "particular" when the text calls one individual thing by its own
  name, a proper name or an identifier ("North Dome", "Lakeside Ferries", "Varga Prize", "T-4471"); "kind"
  for everything else, also for a common noun that means one thing here ("the dome" is a kind even where it
  is North Dome, and so are "the ticket office" and "the morning crossing");
- type, what it is: the type below whose description fits it, whatever its mention_class; "{none}" when no
  type fits.

Types:
{types}

Already listed (do not list these again): {known}

Text ({document}):
{text}"""

# The type answer for a thing no schema type fits; lower case, so no schema type (PascalCase) can be named so
NO_TYPE = "none"


class FoundThing(BaseModel):
    """One thing the model lists, with its two separate answers (R104); code checks every field."""

    name: str = Field(description="the thing's name, copied verbatim from the text")
    mention_class: MentionClass = Field(
        description="particular when the text calls it by its own name or an identifier, else kind"
    )
    type: str = Field(description=f'one of the types listed, or "{NO_TYPE}" when none fits')


class FoundThings(BaseModel):
    """The LLM's response schema for one chunk."""

    things: list[FoundThing] = Field(default=[], description="the things not listed yet; empty when none")


RejectionReason = Literal[
    "value_type",  # the built-in `Value`: numbers are claims' values, never pass mentions
    "unknown_type",  # neither a schema type nor NO_TYPE
    "not_in_text",  # not whole words of the chunk or its document's name (the C1 rule)
    "too_long",  # more than _MAX_WORDS words: a phrase, not an entry
    "clause",  # clause or list punctuation: more than one thing, or a clause
    "function_words",  # only function words: names nothing
    "pronoun",
    "quantity_or_date",  # a number, a quantity of a known unit, a date or a time
    "already_listed",  # the chunk already mentions this name
    "duplicate",  # the model gave this name twice for one chunk
]


class PassFinding(BaseModel):
    """An accepted finding: what `mentions.jsonl` holds, one line each, so the audit replays the pass."""

    chunk_id: str
    name: str
    type: str  # the type it is stored with (`filed_type`)
    # the model's two answers (R104); None in a pass file written before R104, which asked for a type only
    mention_class: MentionClass | None = None
    proposed_type: str | None = None


def read_findings(path: Path) -> list[PassFinding]:
    """The findings of a pass file (`mentions.jsonl`), in order. A malformed line raises pydantic's
    ValidationError: the file is not a pass file."""
    return [PassFinding.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


class PassRejection(BaseModel):
    """A refused finding with the rule that refused it (`mentions_rejected.jsonl`)."""

    chunk_id: str
    name: str
    mention_class: MentionClass
    proposed_type: str
    reason: RejectionReason


# Which classes a type of each identity class can store (R104). A keyed type holds both: its records are named
# things ("Meridian Kettle") and also the pieces and kinds a text names by a common noun ("the lid", linked to
# the kettle's lid record). An individual type is one named thing each; a concept type is a kind.
_HOLDS: dict[IdentityClass, frozenset[MentionClass]] = {
    "keyed": frozenset({"particular", "kind"}),
    "individual": frozenset({"particular"}),
    "concept": frozenset({"kind"}),
}
_FALLBACK_OF: dict[MentionClass, str] = {"particular": PARTICULAR_TYPE, "kind": KIND_TYPE}


def filed_type(thing: FoundThing, schema: TextSchema) -> str:
    """The type a verified finding is stored with: the proposed one when it can hold the stated class, else
    the built-in fallback type of the class. So the class, not the type, decides whether resolve treats the
    mention as a named thing: "café" stated a kind and typed as a place becomes a `Kind`."""
    if thing.type != NO_TYPE and thing.mention_class in _HOLDS[schema.identity_of(thing.type)]:
        return thing.type
    return _FALLBACK_OF[thing.mention_class]


def verify_found(
    thing: FoundThing, chunk: Chunk, known: set[str], schema: TextSchema
) -> tuple[str, RejectionReason | None]:
    """The finding's name as it will be stored (a leading article dropped, as the definition writes names)
    and why it is refused, or None when every code-visible rule lets it through. `known` holds the
    normalised names the chunk already lists."""
    name = _ARTICLE.sub("", thing.name.strip())
    if thing.type == VALUE_TYPE:
        return name, "value_type"
    # the fallback types are code's to give (`filed_type`), so a proposed one is unknown like any other name
    if thing.type not in {e.name for e in schema.entity_types} | {NO_TYPE}:
        return name, "unknown_type"
    words = _WORD.findall(norm(name))
    if not words or not (contains_words(chunk.text, name) or contains_words(chunk.context, name)):
        return name, "not_in_text"
    # a time ("16:40") holds a colon: the value rules come before the punctuation rule
    if _quantity_or_date(name, words):
        return name, "quantity_or_date"
    if set(words) <= PRONOUNS:
        return name, "pronoun"
    if set(words) <= _FUNCTION_WORDS:
        return name, "function_words"
    if len(words) > _MAX_WORDS:
        return name, "too_long"
    if _CLAUSE_PUNCTUATION.search(name):
        return name, "clause"
    if norm(name) in known:
        return name, "already_listed"
    return name, None


def _quantity_or_date(name: str, words: list[str]) -> bool:
    """A bare number, a number with a measuring unit or a duration ("3.2 kg", "40 minutes"), or a date or
    time: calendar words and digits only ("14 April", "16:40", "Monday"). A number with any other word
    ("2016 Civic", "240 cores") may name a thing or count things: code cannot tell, the judge does."""
    quantity = parse_quantity(name)
    if quantity is not None and (
        not quantity.unit or quantity.unit in UNIT_SYMBOLS or norm(quantity.unit) in _DURATIONS
    ):
        return True
    return all(w.isdigit() or w in _CALENDAR for w in words)


def type_lines(schema: TextSchema) -> str:
    """The schema's entity types as the prompt lists them (name: description), `Value` never among them."""
    return "\n".join(f"- {e.name}: {e.description}" for e in schema.entity_types if e.name != VALUE_TYPE)


def mention_prompt(chunk: Chunk, known_names: list[str], schema: TextSchema) -> str:
    return MENTION_PROMPT.format(
        none=NO_TYPE,
        types=type_lines(schema),
        known="; ".join(known_names) or "(none)",
        document=chunk.context or chunk.doc_id,
        text=chunk.text,
    )


class PassOutcome(BaseModel):
    """What the model found and what code made of it, over all chunks."""

    found: int  # things the model listed
    accepted: list[PassFinding]
    rejected: list[PassRejection]
    failed: int  # chunks whose call failed: logged, nothing added for them


def find_mentions(
    chunks: list[Chunk], known: dict[str, list[str]], schema: TextSchema, llm: LLMClient, model: str
) -> PassOutcome:
    """Ask the LLM about every chunk (in parallel) and verify every finding, in chunk order. `known` maps a
    chunk id to the names it already mentions. A failed call adds nothing for its chunk; nothing raises."""
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        replies = list(pool.map(lambda c: _ask(llm, model, c, known.get(c.chunk_id, []), schema), chunks))
    accepted: list[PassFinding] = []
    rejected: list[PassRejection] = []
    for chunk, reply in zip(chunks, replies, strict=True):
        if reply is not None:
            kept, refused = _verify_reply(
                reply, chunk, {norm(n) for n in known.get(chunk.chunk_id, [])}, schema
            )
            accepted += kept
            rejected += refused
    return PassOutcome(
        found=len(accepted) + len(rejected),
        accepted=accepted,
        rejected=rejected,
        failed=sum(r is None for r in replies),
    )


def _verify_reply(
    reply: FoundThings, chunk: Chunk, seen: set[str], schema: TextSchema
) -> tuple[list[PassFinding], list[PassRejection]]:
    """One chunk's findings, accepted (filed under their stored type) or refused with the rule's reason, in
    the order the model gave them. `seen` holds the normalised names the chunk already lists."""
    accepted: list[PassFinding] = []
    rejected: list[PassRejection] = []
    given: set[str] = set()
    for thing in reply.things:
        name, reason = verify_found(thing, chunk, seen, schema)
        if reason is None and norm(name) in given:
            reason = "duplicate"
        if reason is None:
            given.add(norm(name))
            accepted.append(
                PassFinding(
                    chunk_id=chunk.chunk_id,
                    name=name,
                    type=filed_type(thing, schema),
                    mention_class=thing.mention_class,
                    proposed_type=thing.type,
                )
            )
        else:
            rejected.append(
                PassRejection(
                    chunk_id=chunk.chunk_id,
                    name=name,
                    mention_class=thing.mention_class,
                    proposed_type=thing.type,
                    reason=reason,
                )
            )
    return accepted, rejected


def _ask(
    llm: LLMClient, model: str, chunk: Chunk, known: list[str], schema: TextSchema
) -> FoundThings | None:
    """One call; None when the provider kept failing or the reply did not fit the schema."""
    try:
        return llm.generate(mention_prompt(chunk, known, schema), FoundThings, model=model)
    except LLMResponseError as e:  # one failed chunk adds nothing, never a failed stage
        log.warning("mention pass for %s failed: %s", chunk.chunk_id, e)
        return None


class PassRows(BaseModel):
    """The rows the pass writes: new mentions, new MENTIONS pairs, and how the findings were placed."""

    mentions: list[MentionRow]
    mentioned_in: list[tuple[str, str]]  # (chunk id, mention id), new ones only
    reused: int  # findings that gave an existing mention a chunk it was not mentioned in
    # the class stated by the finding that made each new mention (R104); none for a pre-R104 pass file
    classes: dict[str, MentionClass] = {}


def pass_rows(
    findings: Iterable[PassFinding], existing: Iterable[MentionRow], existing_pairs: set[tuple[str, str]]
) -> PassRows:
    """One mention per document and normalised name: a finding whose name a mention of its document
    already has (a claim's or a derived one; the first by id when several) reuses it, so that mention's type
    wins; otherwise the first finding of the name makes a new mention of its type, and its stated class is
    the mention's. Pure, so the stage and the audit's snapshot (audit/snapshot.py) give the same rows."""
    by_name: dict[tuple[str, str], str] = {}
    for m in sorted(existing, key=lambda m: m.id):
        by_name.setdefault((m.doc_id, norm(m.name)), m.id)
    before = set(by_name.values())
    new: dict[str, MentionRow] = {}
    classes: dict[str, MentionClass] = {}
    pairs: list[tuple[str, str]] = []
    reused = 0
    for f in findings:
        key = (document_of(f.chunk_id), norm(f.name))
        if key not in by_name:
            row = mention_row(f.type, f.name, f.chunk_id)
            new[row.id] = row
            by_name[key] = row.id
            if f.mention_class is not None:
                classes[row.id] = f.mention_class
        pair = (f.chunk_id, by_name[key])
        if pair in existing_pairs or pair in pairs:
            continue
        pairs.append(pair)
        reused += by_name[key] in before
    return PassRows(mentions=list(new.values()), mentioned_in=pairs, reused=reused, classes=classes)
