"""Extract subject-predicate-object facts from chunks, and verify every one of them in code.

Role in the pipeline: `kg extract`. Input is the stored chunks and the approved `TextSchema`; output is
accepted triples (written by subject_graph.py) and rejected triples (kept as an artifact for analysis).
Design: the LLM proposes, code decides. A fact is stored only if its type is in the schema, its evidence
quote is a verbatim span of the chunk, and both entity names occur in the chunk or in the document's
name (the chunk's context); a number (`Value` object) and a time must be stated in the quote itself (R66);
a negated, possible or conditional claim carries the words that make it so (its cue: negation, hedge,
condition), and each cue must be words of the quote (R77, revised in part d); the hedge of an actual claim
is dropped, not judged (R81). This is the project's guard against hallucinated facts, and the rejection rate
per reason is a logged quality metric.
Not here: graph writes (subject_graph.py) and entity merging (resolution/).
"""

from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from ..core.text import contains_words, norm
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
# The claim's assertion (R77, layered-model Step 7): "the lid cracked", "the lid did not crack", "the lid may
# crack" and "the lid cracks when it is cold" are four facts, and a count over claims must tell them apart.
# Possible and conditional are one axis with actual; what a thing is able to do stays actual, so that "can"
# in a capacity is not read as a hedge. The condition takes the "if"/"when" clause that the naming rule keeps
# out of the names, which until R77 was lost or stored as a time (R68 cause "role"). The examples are
# invented (a kettle), as is the generic word that replaced "this dresser", the last corpus word of this
# prompt (task file, generality cleanups).
# R77 part d (the user, 2026-10-06): truth is said of the statement the claim is made of, as the gold reads
# it. R77 said it of the triple ("a fault the names already state stays affirmed"), so "we still couldn't
# get the drawers to slide right" was stored affirmed with "couldn't" in its object, and a count over truth
# missed 5 of 15 furniture and 3 of 3 held-out denials. The names now leave the denial out unless the name
# is itself the denied state; the cue fields (`negation`, `hedge`) keep the denying and hedging words, so
# the original wording survives for the query agent, and code derives whether the stored triple itself is
# denied (`triple_truth`). The cues replace R77's closed word lists, which rejected right claims ("prevents
# sagging") and could not pass an inverted condition: the model reads the meaning, code checks only that
# the words are the quote's.
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
  "after ..."), words of frequency ("sometimes", "often"), and the words that deny the fact or say it only
  may hold; they stay in the evidence and in the fields below. Never use pronouns.
- The chunk comes from the document named in <document>. When the text refers to the thing the document
  is about with a pronoun or a generic word ("it", "this kettle"), use the proper name from the document
  name as the entity name.
- `evidence` must be ONE contiguous quote copied verbatim from the chunk that supports the fact.
- `polarity` is the claim's own tone toward its subject: "positive" when it speaks well of it, "negative"
  when it reports a fault, a harm or dissatisfaction, "neutral" when it only states what is so.
- `time`: when the quote says when the claim holds or after how long ("since ...", "within ..."),
  copy those words verbatim from the quote; otherwise leave it empty. A condition is not a time.
- `truth`: "negated" when the text says the fact does not hold or did not happen, in whatever words:
  "the kettle never leaked" denies a leak, "the filter prevents scale" denies scale; otherwise "affirmed".
  Name the fact itself ("leak", not "never leaked"). Keep the denial in a name only when the name is itself
  the denied state, as a fault called "will not switch off"; the fact is still negated.
- `negation`: for a negated fact, the words of the quote that deny it, copied verbatim ("never",
  "prevents"); otherwise leave it empty.
- `modality`: "possible" when the text says the fact may hold or happen, not that it does; "conditional"
  when it holds under a condition the text states, in whatever words ("if ...", "unless ...", "when ..."
  meaning whenever, "had the lid been shut"); otherwise "actual", also for what a thing is able to do
  ("holds two litres"). A fact with both a condition and a possibility is conditional.
- `hedge`: the words of the quote that say the fact only may hold, copied verbatim ("may", "could", "a
  risk of"); required for a possible fact, allowed for a conditional one, otherwise empty.
- `condition`: for a conditional fact, the words of the condition copied verbatim from the quote ("when
  the water boils"); otherwise leave it empty.
- When a fact type's object type is {value_type}, `object` is only the number and its unit, copied from
  the quote.
- Extract only what the text states. Do not infer. Return an empty list when nothing qualifies.

<document>{context}</document>
<chunk id="{chunk_id}">
{text}
</chunk>"""


Polarity = Literal["negative", "positive", "neutral"]
Truth = Literal["affirmed", "negated"]
Modality = Literal["actual", "possible", "conditional"]


class RawTriple(BaseModel):
    """One fact as the LLM returns it. `polarity` and `time` default to "no tone" and "no time", and the
    assertion (R77) to an affirmed, actual claim without a condition or cue: also what a claim derived by
    code or extracted before R77 carries. `truth` is said of the statement (R77 part d), its cue words in
    `negation` and `hedge`."""

    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence: str
    polarity: Polarity = Field(default="neutral", description="the claim's tone toward its subject")
    time: str = Field(default="", description="words of the evidence saying when the claim holds, or empty")
    truth: Truth = Field(default="affirmed", description="whether the text states or denies the fact")
    negation: str = Field(default="", description="words of the evidence that deny the fact, or empty")
    modality: Modality = Field(
        default="actual", description="whether the fact holds, may hold, or holds under a condition"
    )
    hedge: str = Field(
        default="", description="words of the evidence that say the fact only may hold, or empty"
    )
    condition: str = Field(default="", description="words of the evidence stating the condition, or empty")


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
    # the assertion (R77; cues since part d): a negated, possible or conditional claim needs its cue as
    # words of the quote, and a cue goes only with a claim of its kind (since R81 the hedge of an actual
    # claim is dropped instead: `cue_without_assertion` is a negation on an affirmed claim)
    NEGATION_NOT_IN_EVIDENCE = "negation_not_in_evidence"
    MODALITY_NOT_IN_EVIDENCE = "modality_not_in_evidence"
    CONDITION_NOT_IN_EVIDENCE = "condition_not_in_evidence"
    CONDITION_NOT_CONDITIONAL = "condition_not_conditional"
    CUE_WITHOUT_ASSERTION = "cue_without_assertion"


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
    hedges_dropped: int = 0  # actual claims whose hedge was dropped, not rejected for it (R81)

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


def triple_truth(truth: Truth, negation: str, names: tuple[str, str]) -> Truth:
    """Whether the triple as stored is denied: only when the statement is negated and its denying words are
    in neither name (R77 part d). A name that is itself the denied state ("will not switch off") makes a
    triple that holds although its statement is negated, so a count of such states still counts it.
    Known limit: a cue word that a name also holds for another reason ("no" in "No-Spill Kettle") reads as
    a denial carried by that name."""
    if truth == "affirmed":
        return "affirmed"
    return "affirmed" if any(contains_words(name, negation) for name in names) else "negated"


def _grounded(cue: str, evidence: str) -> bool:
    """A cue is given and is whole words of the quote."""
    return bool(cue.strip()) and contains_words(evidence, cue)


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
    return _verify_assertion(triple, evidence)


def _verify_assertion(triple: RawTriple, evidence: str) -> Rejection | None:
    """A negated, possible or conditional claim needs its cue (`negation`, `hedge`, `condition`) as whole
    words of the quote (normalised `evidence`); a hedge given with a conditional claim must be too (R77
    part d). Which words deny, hedge or state a condition is the model's reading of the meaning; code checks
    that the reading rests on the quote. A negation or a condition on a claim not of its kind is rejected,
    not dropped: code cannot tell which of the two is wrong. A hedge on an actual claim is not judged here:
    `_accept` drops it (R81)."""
    if triple.truth == "negated" and not _grounded(triple.negation, evidence):
        return Rejection(
            reason=RejectionReason.NEGATION_NOT_IN_EVIDENCE,
            detail=f"negated, but its negation '{triple.negation}' is not words of the quote",
        )
    hedged = triple.modality == "possible" or (triple.modality == "conditional" and triple.hedge.strip())
    if hedged and not _grounded(triple.hedge, evidence):
        return Rejection(
            reason=RejectionReason.MODALITY_NOT_IN_EVIDENCE,
            detail=f"{triple.modality}, but its hedge '{triple.hedge}' is not words of the quote",
        )
    if triple.modality == "conditional" and not _grounded(triple.condition, evidence):
        return Rejection(
            reason=RejectionReason.CONDITION_NOT_IN_EVIDENCE,
            detail=f"conditional, but '{triple.condition}' is not a condition stated in the quote",
        )
    if triple.modality != "conditional" and triple.condition.strip():
        return Rejection(
            reason=RejectionReason.CONDITION_NOT_CONDITIONAL,
            detail=f"condition '{triple.condition}' on a claim that is {triple.modality}",
        )
    if triple.truth == "affirmed" and triple.negation.strip():
        return Rejection(
            reason=RejectionReason.CUE_WITHOUT_ASSERTION,
            detail=f"negation '{triple.negation}' on an affirmed claim",
        )
    return None


def _without_stray_hedge(raw: RawTriple) -> RawTriple:
    """The claim with the hedge of an actual claim dropped (R81). Unlike a stray negation or condition, it
    changes nothing a reader or a count relies on: in R77 part f all 9 such hedges were degree or
    approximation words on claims that were right ("about 2 hours", "a bit short"), and rejecting the whole
    claim lost them. The risk, accepted by the user: a claim that should have been possible stays actual."""
    if raw.modality == "actual" and raw.hedge.strip():
        return raw.model_copy(update={"hedge": ""})
    return raw


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
    """Verify each triple into `result`; a repeat of an accepted one (across passes too) is dropped, and so
    is the hedge of an actual claim (counted in `hedges_dropped`)."""
    for given in raws:
        raw = _without_stray_hedge(given)
        if raw is not given:
            result.hedges_dropped += 1
        triple = Triple(**raw.model_dump(), chunk_id=chunk.chunk_id)
        rejection = verify(raw, chunk.text, schema, chunk.context)
        if rejection:
            result.rejected.append(Rejected(triple=triple, reason=rejection.reason, detail=rejection.detail))
            continue
        # models sometimes emit the same fact twice with different casing, and a later pass may repeat one;
        # the time and the assertion are part of the claim (the observation id includes them), the polarity
        # is not
        key = (
            norm(triple.subject),
            triple.predicate,
            norm(triple.object),
            norm(triple.evidence),
            norm(triple.time),
            triple.truth,
            triple.modality,
            norm(triple.condition),
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
        hedges_dropped=sum(r.hedges_dropped for r in results),
    )
