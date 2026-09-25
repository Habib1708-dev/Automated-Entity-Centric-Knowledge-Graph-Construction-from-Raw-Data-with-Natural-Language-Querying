"""Extract subject-predicate-object facts from chunks, and verify every one of them in code.

Role in the pipeline: `kg extract`. Input is the stored chunks and the approved `TextSchema`; output is
accepted triples (written by subject_graph.py) and rejected triples (kept as an artifact for analysis).
Design: the LLM proposes, code decides. A fact is stored only if its type is in the schema, its evidence
quote is a verbatim span of the chunk, and both entity names occur in the chunk or in the document's
name (the chunk's context); a number (`Value` object) and a time must be stated in the quote itself (R66).
This is the project's guard against hallucinated facts, and the rejection rate per reason is a logged
quality metric.
Not here: graph writes (subject_graph.py) and entity merging (resolution/).
"""

from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from ..core.text import norm
from ..core.values import VALUE_TYPE, parse_quantity
from ..llm.base import LLMClient
from .chunking import Chunk
from .schema import TextSchema

# "exactly as written" and "verbatim" make code verification possible at all; "never use pronouns" avoids
# entities called "it"; the explicit permission to return nothing reduces forced, low-quality facts.
# The <document> line is the chunk's context (chunking.py): a review after the first one says "this
# dresser", and without the document's name the extractor had to call the product "dresser".
# The "be exhaustive" rule (R30) answers the judge pass of 2026-09-22: with only "skip" rules the model
# kept one or two claims per sentence, dropped the third item of a list and skipped hedged complaints
# ("a bit thin", "feels flimsy"); 12 of 22 missed reference facts were of that kind.
# R34 made the rule domain-neutral: R30 wrote it in the words of furniture reviews (defects, failures,
# assembly, examples quoted from the corpus), which would steer the model on any other dataset. The four
# instructions are the same; the examples are generic hedge words, none taken from the corpus.
# The naming rule (R47): without a limit on what a name contains, the model copied whole clauses
# ("... sticks when i open it too fast"), so one kind got a name no other wording could match and entity
# resolution never compared it. The rule is grammatical (a clause of time or condition, a frequency
# word), stated with generic function words only, so it applies to any text; the hedge rule above still
# keeps degree words ("a bit") in the claim, and the evidence keeps the full sentence.
# The claim's qualifiers (R66, the observation graph): polarity lets one fact type hold good, bad and neutral
# claims, so praise and measures are no longer lost to a problems-only schema; time keeps "when" out of the
# names (the naming rule) without dropping it; a number is returned bare so that code can parse it. Each is
# checked in code where it can be: a time and a number must be words of the quote (`verify`).
PROMPT = """Extract facts from the text chunk as subject-predicate-object triples.

Allowed entity types:
{entity_types}

Allowed fact types (subject_type -[PREDICATE]-> object_type):
{fact_types}

Rules:
- Use only the entity types and fact types above. Skip anything that does not fit.
- Be exhaustive: one triple per distinct claim. A sentence that lists several claims gives one triple
  per item of the list. A mild or hedged claim ("a bit", "somewhat", "seems to", "more than expected")
  still counts. A claim about the thing the document is about as a whole goes on that thing. When one
  statement supports two of the allowed fact types, state both facts.
- `subject` and `object` are names exactly as written in the text, with only the words that name the thing:
  leave out a clause that says when or under which condition the claim holds ("when ...", "if ...",
  "after ...") and words of frequency ("sometimes", "often"); they stay in the evidence. Never use
  pronouns.
- The chunk comes from the document named in <document>. When the text refers to the product or thing
  the document is about with a pronoun or a generic word ("it", "this dresser"), use the proper name
  from the document name as the entity name.
- `evidence` must be ONE contiguous quote copied verbatim from the chunk that supports the fact.
- `polarity` is the claim's own tone toward its subject: "positive" when it speaks well of it, "negative"
  when it reports a fault, a harm or dissatisfaction, "neutral" when it only states what is so.
- `time`: when the quote says when the claim holds or after how long ("since ...", "within ..."),
  copy those words verbatim from the quote; otherwise leave it empty.
- When a fact type's object type is {value_type}, `object` is only the number and its unit, copied from
  the quote.
- Extract only what the text states. Do not infer. Return an empty list when nothing qualifies.

<document>{context}</document>
<chunk id="{chunk_id}">
{text}
</chunk>"""


Polarity = Literal["negative", "positive", "neutral"]


class RawTriple(BaseModel):
    """One fact as the LLM returns it. `polarity` and `time` default to "no tone" and "no time", which is
    also what a claim derived by code or extracted before R66 carries."""

    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence: str
    polarity: Polarity = Field(default="neutral", description="the claim's tone toward its subject")
    time: str = Field(default="", description="words of the evidence saying when the claim holds, or empty")


class ChunkExtraction(BaseModel):
    """The LLM's response schema for one chunk."""

    triples: list[RawTriple]


class Triple(RawTriple):
    """A fact tied to the chunk it came from."""

    chunk_id: str


class RejectionReason(StrEnum):
    """Why a triple was not stored. The values are metric name suffixes in MLflow; keep them stable."""

    OFF_SCHEMA = "off_schema"
    EVIDENCE_NOT_VERBATIM = "evidence_not_verbatim"
    EMPTY_ARGUMENT = "empty_argument"
    PRONOUN_ARGUMENT = "pronoun_argument"
    SELF_REFERENCE = "self_reference"
    ARGUMENT_NOT_IN_CHUNK = "argument_not_in_chunk"
    VALUE_NOT_A_NUMBER = "value_not_a_number"
    VALUE_NOT_IN_EVIDENCE = "value_not_in_evidence"
    TIME_NOT_IN_EVIDENCE = "time_not_in_evidence"


class Rejection(BaseModel):
    reason: RejectionReason
    detail: str


class Rejected(BaseModel):
    triple: Triple
    reason: RejectionReason
    detail: str


class ExtractionResult(BaseModel):
    triples: list[Triple]
    rejected: list[Rejected]
    accepted_per_pass: list[int] = []  # new facts each extraction pass contributed (R61)

    @property
    def accept_rate(self) -> float:
        total = len(self.triples) + len(self.rejected)
        return len(self.triples) / total if total else 1.0

    def rejections_by_reason(self) -> dict[RejectionReason, int]:
        """Count per reason, including zeros, so every run logs the same metric names."""
        return {reason: sum(r.reason == reason for r in self.rejected) for reason in RejectionReason}

    @property
    def off_schema_rate(self) -> float:
        """Share of all returned triples that the schema had no fact type for: claims the text made but
        the graph cannot hold. A high rate points at the schema, not at the extractor (R57)."""
        total = len(self.triples) + len(self.rejected)
        off = sum(r.reason == RejectionReason.OFF_SCHEMA for r in self.rejected)
        return off / total if total else 0.0

    def off_schema_signatures(self) -> dict[str, int]:
        """How often each missing fact type was asked for, most frequent first, as
        `Subject -[PREDICATE]-> Object`. A recurring signature is a schema gap; a reversed or
        differently spelled one is a near miss (R57 report, input to R59)."""
        counts: dict[str, int] = {}
        for r in self.rejected:
            if r.reason == RejectionReason.OFF_SCHEMA:
                t = r.triple
                key = f"{t.subject_type} -[{t.predicate}]-> {t.object_type}"
                counts[key] = counts.get(key, 0) + 1
        # ties ordered by name, so the same result gives the same file
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# Names that are only pronouns or determiners name nothing: "IT" became an entity in R61's second pass,
# although the prompt says "Never use pronouns". A closed word class of English, so the check is
# domain-neutral; a name that merely contains one ("this dresser") is left to the naming rules.
# fmt: off
PRONOUNS = frozenset({
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his", "she", "her", "it", "its",
    "they", "them", "their", "this", "that", "these", "those", "one", "ones", "something", "someone",
    "anything", "everything",
})
# fmt: on


def verify(triple: RawTriple, chunk_text: str, schema: TextSchema, context: str = "") -> Rejection | None:
    """Return why the triple must be rejected, or None when it is grounded and schema-conformant.

    An entity name must occur in the chunk or in `context` (the document's name, which the prompt shows);
    the evidence quote must occur in the chunk itself. All comparisons use `norm`, so case, accents,
    markdown markers and whitespace do not cause rejections.
    """
    text = norm(chunk_text)
    # the document name is a legitimate source of a name, never of a quote: a quote proves a claim
    # was made in this chunk, and the document name makes no claims
    names_source = text + " " + norm(context)
    subject, obj, evidence = norm(triple.subject), norm(triple.object), norm(triple.evidence)
    if not schema.allows_extraction(triple.subject_type, triple.predicate, triple.object_type):
        fact_type = f"{triple.subject_type} -[{triple.predicate}]-> {triple.object_type}"
        why = (
            "is derived by code, not extracted"
            if schema.allows(triple.subject_type, triple.predicate, triple.object_type)
            else "is not in the schema"
        )
        return Rejection(reason=RejectionReason.OFF_SCHEMA, detail=f"fact type {fact_type} {why}")
    if not evidence or evidence not in text:
        return Rejection(
            reason=RejectionReason.EVIDENCE_NOT_VERBATIM,
            detail="evidence is not a verbatim span of the chunk",
        )
    if not subject or not obj:
        return Rejection(reason=RejectionReason.EMPTY_ARGUMENT, detail="empty subject or object")
    for role, name, normalised in (("subject", triple.subject, subject), ("object", triple.object, obj)):
        if set(normalised.split()) <= PRONOUNS:
            return Rejection(
                reason=RejectionReason.PRONOUN_ARGUMENT, detail=f"{role} '{name}' is a pronoun, not a name"
            )
    if subject == obj:
        return Rejection(reason=RejectionReason.SELF_REFERENCE, detail="subject equals object")
    for role, name, normalised in (("subject", triple.subject, subject), ("object", triple.object, obj)):
        if normalised not in names_source:
            return Rejection(
                reason=RejectionReason.ARGUMENT_NOT_IN_CHUNK,
                detail=f"{role} '{name}' does not appear in the chunk or the document name",
            )
    return _verify_qualifiers(triple, evidence)


def _verify_qualifiers(triple: RawTriple, evidence: str) -> Rejection | None:
    """A number and a time must be words of the quote (normalised `evidence`), not of the chunk around it:
    the quote is what states the claim, and a number from the next sentence would be another claim's."""
    if triple.object_type == VALUE_TYPE:
        if parse_quantity(triple.object) is None:
            return Rejection(
                reason=RejectionReason.VALUE_NOT_A_NUMBER,
                detail=f"value '{triple.object}' does not start with a number",
            )
        if norm(triple.object) not in evidence:
            return Rejection(
                reason=RejectionReason.VALUE_NOT_IN_EVIDENCE,
                detail=f"value '{triple.object}' is not in the evidence",
            )
    if triple.time and norm(triple.time) not in evidence:
        return Rejection(
            reason=RejectionReason.TIME_NOT_IN_EVIDENCE, detail=f"time '{triple.time}' is not in the evidence"
        )
    return None


# The second pass ("gleaning", R61). R57 measured that facts are lost silently inside the model: across five
# runs code rejected none for its type, so a missed claim is one the model never returned. Asking again,
# with what it already found in view, targets exactly that. The facts shown are the accepted ones, so a
# claim that failed verification may come back in a form that passes. Same rules and the same `verify`:
# the second pass may only add, never loosen. "Return an empty list" keeps it from padding.
# R61's measurement: pass 2 found real misses but also reached past the text (a part placed in another by
# general knowledge, a location read as "part of", a non-physical thing typed as a physical one). The
# closing rules (R62) name those three kinds of over-reach in generic words; none is a corpus phrase.
GLEAN_SUFFIX = """

These triples were already extracted from this chunk:
<already_extracted>
{found}
</already_extracted>
Return only the claims of the chunk that this list misses, under the same rules and with the same
fact types. Do not repeat a triple of the list. Return an empty list when it misses nothing.
A second look tends to reach past the text, so each new triple must be stated by the chunk itself:
- not what is generally known about the things, and not what a sentence only suggests;
- not a place where something is, or a comparison with something else, read as a relation;
- an entity only of a type whose description covers it."""


def build_glean_prompt(chunk: Chunk, schema: TextSchema, found: list[Triple]) -> str:
    """The prompt for a later pass: the first-pass prompt plus the facts already accepted for this chunk."""
    listed = "\n".join(f"- {t.subject} -[{t.predicate}]-> {t.object}" for t in found) or "(none)"
    return build_prompt(chunk, schema) + GLEAN_SUFFIX.format(found=listed)


def build_prompt(chunk: Chunk, schema: TextSchema) -> str:
    """The extraction prompt for one chunk, with the schema rendered as two bullet lists."""
    return PROMPT.format(
        entity_types="\n".join(f"- {e.name}: {e.description}" for e in schema.entity_types),
        fact_types="\n".join(
            f"- {f.subject_type} -[{f.predicate}]-> {f.object_type}: {f.description}"
            for f in schema.extractable()  # derived fact types are code's job, the model never sees them
        ),
        value_type=VALUE_TYPE,
        context=chunk.context,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
    )


def extract_chunk(
    chunk: Chunk,
    schema: TextSchema,
    llm: LLMClient,
    model: str,
    temperature: float = 0.0,
    passes: int = 1,
) -> ExtractionResult:
    """Extract one chunk in `passes` calls, verify every triple, and drop repeats within the chunk.

    Pass 1 uses the extraction prompt; each later pass shows the facts accepted so far and asks only for
    what they miss (R61). `accepted_per_pass` counts the new facts each pass contributed.
    """
    result = ExtractionResult(triples=[], rejected=[])
    seen: set[tuple[str, ...]] = set()
    for n in range(passes):
        prompt = build_prompt(chunk, schema) if n == 0 else build_glean_prompt(chunk, schema, result.triples)
        reply = llm.generate(prompt, ChunkExtraction, model=model, temperature=temperature)
        before = len(result.triples)
        _accept(reply.triples, chunk, schema, seen, result)
        result.accepted_per_pass.append(len(result.triples) - before)
    return result


def _accept(
    raws: list[RawTriple], chunk: Chunk, schema: TextSchema, seen: set, result: ExtractionResult
) -> None:
    """Verify each triple into `result`; a repeat of an accepted one (across passes too) is dropped."""
    for raw in raws:
        triple = Triple(**raw.model_dump(), chunk_id=chunk.chunk_id)
        rejection = verify(raw, chunk.text, schema, chunk.context)
        if rejection:
            result.rejected.append(Rejected(triple=triple, reason=rejection.reason, detail=rejection.detail))
            continue
        # models sometimes emit the same fact twice with different casing, and a later pass may repeat one;
        # the time is part of the claim (the observation id includes it), the polarity is not
        key = (
            norm(triple.subject),
            triple.predicate,
            norm(triple.object),
            norm(triple.evidence),
            norm(triple.time),
        )
        if key not in seen:
            seen.add(key)
            result.triples.append(triple)


def extract_all(
    chunks: list[Chunk],
    schema: TextSchema,
    llm: LLMClient,
    model: str,
    temperature: float = 0.0,
    workers: int = 8,
    passes: int = 1,
) -> ExtractionResult:
    """Extract every chunk in parallel. The calls are I/O bound, so threads are enough.

    `pool.map` keeps chunk order, so the output (and the artifacts written from it) is deterministic.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: extract_chunk(c, schema, llm, model, temperature, passes), chunks))
    return ExtractionResult(
        triples=[t for r in results for t in r.triples],
        rejected=[x for r in results for x in r.rejected],
        accepted_per_pass=[sum(r.accepted_per_pass[n] for r in results) for n in range(passes)],
    )
