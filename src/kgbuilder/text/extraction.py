"""Extract subject-predicate-object facts from chunks, and verify every one of them in code.

Role in the pipeline: `kg extract`. Input is the stored chunks and the approved `TextSchema`; output is
accepted triples (written by subject_graph.py) and rejected triples (kept as an artifact for analysis).
Design: the LLM proposes, code decides. A fact is stored only if its type is in the schema, its evidence
quote is a verbatim span of the chunk, and both entity names occur in the chunk or in the document's
name (the chunk's context). This is the project's guard against hallucinated facts, and the rejection
rate per reason is a logged quality metric.
Not here: graph writes (subject_graph.py) and entity merging (resolution/).
"""

from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum

from pydantic import BaseModel

from ..core.text import norm
from ..llm.base import LLMClient
from .chunking import Chunk
from .schema import TextSchema

# "exactly as written" and "verbatim" make code verification possible at all; "never use pronouns" avoids
# entities called "it"; the explicit permission to return nothing reduces forced, low-quality facts.
# The <document> line is the chunk's context (chunking.py): a review after the first one says "this
# dresser", and without the document's name the extractor had to call the product "dresser".
PROMPT = """Extract facts from the text chunk as subject-predicate-object triples.

Allowed entity types:
{entity_types}

Allowed fact types (subject_type -[PREDICATE]-> object_type):
{fact_types}

Rules:
- Use only the entity types and fact types above. Skip anything that does not fit.
- `subject` and `object` are the entity names exactly as written in the text. Never use pronouns.
- The chunk comes from the document named in <document>. When the text refers to the product or thing
  the document is about with a pronoun or a generic word ("it", "this dresser"), use the proper name
  from the document name as the entity name.
- `evidence` must be ONE contiguous quote copied verbatim from the chunk that supports the fact.
- Extract only what the text states. Do not infer. Return an empty list when nothing qualifies.

<document>{context}</document>
<chunk id="{chunk_id}">
{text}
</chunk>"""


class RawTriple(BaseModel):
    """One fact as the LLM returns it."""

    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence: str


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
    SELF_REFERENCE = "self_reference"
    ARGUMENT_NOT_IN_CHUNK = "argument_not_in_chunk"


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

    @property
    def accept_rate(self) -> float:
        total = len(self.triples) + len(self.rejected)
        return len(self.triples) / total if total else 1.0

    def rejections_by_reason(self) -> dict[RejectionReason, int]:
        """Count per reason, including zeros, so every run logs the same metric names."""
        return {reason: sum(r.reason == reason for r in self.rejected) for reason in RejectionReason}


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
    if not schema.allows(triple.subject_type, triple.predicate, triple.object_type):
        fact_type = f"{triple.subject_type} -[{triple.predicate}]-> {triple.object_type}"
        return Rejection(
            reason=RejectionReason.OFF_SCHEMA, detail=f"fact type {fact_type} is not in the schema"
        )
    if not evidence or evidence not in text:
        return Rejection(
            reason=RejectionReason.EVIDENCE_NOT_VERBATIM,
            detail="evidence is not a verbatim span of the chunk",
        )
    if not subject or not obj:
        return Rejection(reason=RejectionReason.EMPTY_ARGUMENT, detail="empty subject or object")
    if subject == obj:
        return Rejection(reason=RejectionReason.SELF_REFERENCE, detail="subject equals object")
    for role, name, normalised in (("subject", triple.subject, subject), ("object", triple.object, obj)):
        if normalised not in names_source:
            return Rejection(
                reason=RejectionReason.ARGUMENT_NOT_IN_CHUNK,
                detail=f"{role} '{name}' does not appear in the chunk or the document name",
            )
    return None


def build_prompt(chunk: Chunk, schema: TextSchema) -> str:
    """The extraction prompt for one chunk, with the schema rendered as two bullet lists."""
    return PROMPT.format(
        entity_types="\n".join(f"- {e.name}: {e.description}" for e in schema.entity_types),
        fact_types="\n".join(
            f"- {f.subject_type} -[{f.predicate}]-> {f.object_type}: {f.description}"
            for f in schema.fact_types
        ),
        context=chunk.context,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
    )


def extract_chunk(
    chunk: Chunk, schema: TextSchema, llm: LLMClient, model: str, temperature: float = 0.0
) -> ExtractionResult:
    """Extract one chunk, verify every triple, and drop exact repeats within the chunk."""
    reply = llm.generate(build_prompt(chunk, schema), ChunkExtraction, model=model, temperature=temperature)
    accepted: list[Triple] = []
    rejected: list[Rejected] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in reply.triples:
        triple = Triple(**raw.model_dump(), chunk_id=chunk.chunk_id)
        rejection = verify(raw, chunk.text, schema, chunk.context)
        if rejection:
            rejected.append(Rejected(triple=triple, reason=rejection.reason, detail=rejection.detail))
            continue
        # models sometimes emit the same fact twice with different casing
        key = (norm(triple.subject), triple.predicate, norm(triple.object), norm(triple.evidence))
        if key not in seen:
            seen.add(key)
            accepted.append(triple)
    return ExtractionResult(triples=accepted, rejected=rejected)


def extract_all(
    chunks: list[Chunk],
    schema: TextSchema,
    llm: LLMClient,
    model: str,
    temperature: float = 0.0,
    workers: int = 8,
) -> ExtractionResult:
    """Extract every chunk in parallel. The calls are I/O bound, so threads are enough.

    `pool.map` keeps chunk order, so the output (and the artifacts written from it) is deterministic.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: extract_chunk(c, schema, llm, model, temperature), chunks))
    return ExtractionResult(
        triples=[t for r in results for t in r.triples],
        rejected=[x for r in results for x in r.rejected],
    )
