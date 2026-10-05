"""Concepts: which mentions of a kind-naming type share one canonical concept (R75, layered-model Step 5).

Role in the pipeline: the concept part of `kg resolve` (identity.py), after records and individuals. The
mentions of a concept type (and of the built-in `Value`) with one type and normalised name start as one
concept, across documents; entity resolution (resolver.py) then decides which concepts are the same kind.
Design: a concept is what an `:Entity` was before R75 (one per type and name), so the resolver's candidates,
blocking, passes and canonical choice are unchanged; the outcome is written by identity.py as one
`REFERS_TO` edge per mention instead of merging nodes. Two guards join the polarity guard of R66
(guards.py), built here from the graph: the sentences of the concepts' chunks (`SameSentence`) and the
claims of the schema's part-of fact types (`PartAndWhole`). A value's concept is named by its
canonical spelling ("25kg" and "25 kilograms" are one "25 kg"), and values are never compared.
Not here: the pure decision steps (resolver.py), records and individuals (records.py, identity.py).
"""

from collections.abc import Callable

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import concept_id, document_of
from ..core.text import split_sentences
from ..core.values import VALUE_TYPE, parse_quantity
from ..llm.base import Embedder, LLMClient
from ..text.schema import TextSchema
from .blocking import Blocking
from .guards import BlockLog, CompoundName, Guard, OpposedPolarity, PartAndWhole, SameSentence
from .matchers import EntityRecord
from .mentions import MentionRecord, read_mention_texts
from .resolver import (
    Adjudicator,
    AdjudicatorFor,
    Candidate,
    Decision,
    MentionRow,
    MergeGroup,
    ResolvePreview,
    decide_in_passes,
    embedding_matcher,
    mention_lines,
    nominate,
    preview,
)

# Conservative on purpose: a false merge destroys information, a missed merge only leaves a duplicate.
# Each name comes with the sentences that mention it and their document (R39): a name alone is often
# ambiguous ("rails", "switch"), and whether two names come from the same thing's documents matters. Until
# R39 the context was the first 300 characters of one arbitrary chunk, which for 20 of 78 names never
# contained the name at all. Since R75 only concepts are adjudicated here, so the question is always
# "the same kind" (R41's item-or-kind choice is now the schema's identity class); the examples of a kind
# are structural, no longer a furniture list ("a part, a defect or a symptom", generality cleanups).
ADJUDICATE_PROMPT = """Do these two names, both of type {etype}, name the same kind of thing?
{etype} names kinds, states or properties that many things share, so the names may come from different
documents: answer whether A and B are the same kind, even when they are mentioned in different documents.
Different sizes, models, materials, pieces or people are NOT the same. Answer conservatively.

A: {a}
B: {b}

Where A is mentioned ([document] sentence):
{ctx_a}

Where B is mentioned ([document] sentence):
{ctx_b}"""


class SamePair(BaseModel):
    """The LLM's response schema for one adjudication."""

    same: bool


class ConceptResolution(BaseModel):
    """The concepts before resolution, their mentions, and what resolution decided about them."""

    concepts: list[EntityRecord]  # one per type and normalised name
    members: dict[str, list[str]]  # concept id -> its mention ids
    decisions: list[Decision]
    groups: list[MergeGroup]
    passes: int
    blocked: dict[str, int] = {}  # guard name -> the pairs it kept apart


def concept_records(mentions: list[MentionRecord]) -> tuple[list[EntityRecord], dict[str, list[str]]]:
    """One concept per type and normalised name (a value: per type and canonical spelling), with its
    mentions. Its name is the spelling written in the most chunks, then the shortest, then the first in
    order, so the same graph always names it the same; every spelling is an alias. Pure."""
    groups: dict[str, list[MentionRecord]] = {}
    for m in mentions:
        groups.setdefault(concept_id(m.type, _concept_name(m)), []).append(m)
    records = []
    for cid, members in sorted(groups.items()):
        spellings: dict[str, int] = {}
        for m in members:
            spelling = _concept_name(m)
            spellings[spelling] = spellings.get(spelling, 0) + m.chunks
        name = min(spellings, key=lambda s: (-spellings[s], len(s), s))
        records.append(
            EntityRecord(
                id=cid,
                name=name,
                type=members[0].type,
                aliases=sorted({m.name for m in members} | set(spellings)),
                mentions=sum(m.chunks for m in members),
                polarities=sorted({p for m in members for p in m.polarities}),
            )
        )
    return records, {cid: sorted(m.id for m in members) for cid, members in groups.items()}


def _concept_name(mention: MentionRecord) -> str:
    """A value's canonical spelling ("25 kg"); any other name as written."""
    quantity = parse_quantity(mention.name) if mention.type == VALUE_TYPE else None
    return quantity.text if quantity else mention.name


def concept_guards(driver: Driver, mentions: list[MentionRecord], schema: TextSchema | None) -> list[Guard]:
    """The guards of a resolution of `mentions`: polarity (R66), both named in one sentence of their chunks,
    a part and its whole by a claim of one of the schema's part-of fact types, and a compound one word
    longer than the other name (R75)."""
    texts = {t.chunk_id: t.text for t in read_mention_texts(driver, sorted(m.id for m in mentions))}
    sentences = [s for text in texts.values() for s in split_sentences(text)]
    signatures = (
        [[f.subject_type, f.predicate, f.object_type] for f in schema.fact_types if f.part_of]
        if schema
        else []
    )
    pairs: list[tuple[str, str]] = []
    if signatures:
        records, _, _ = driver.execute_query(
            # the claims' own wordings: a concept's aliases hold every wording of its mentions
            "MATCH (s:Mention)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Mention) "
            "WHERE [s.type, o.predicate, t.type] IN $signatures RETURN s.name AS part, t.name AS whole",
            signatures=signatures,
        )
        pairs = [(r["part"], r["whole"]) for r in records]
    return [OpposedPolarity(), SameSentence(sentences), PartAndWhole(pairs), CompoundName()]


def resolve_concepts(
    driver: Driver,
    mentions: list[MentionRecord],
    llm: LLMClient | None,
    model: str,
    thresholds: tuple[float, float],
    embedder: Embedder | None = None,
    blocking: Blocking | None = None,
    schema: TextSchema | None = None,
) -> ConceptResolution:
    """Decide which concepts of `mentions` are the same kind. `thresholds` is (auto merge, borderline);
    with `llm=None` borderline pairs stay apart (logged as skipped); meaning-based candidates need both an
    embedder and a `blocking`; `schema` names the part-of fact types. Reads the graph for the guards and
    the adjudication context only; writes nothing."""
    auto_merge, borderline = thresholds
    blocked: BlockLog = {}
    records, members = concept_records(mentions)
    adjudicator_for: AdjudicatorFor | None = None
    if llm is not None:

        def adjudicator_for(candidates: list[Candidate], merged: dict[str, list[str]]) -> Adjudicator:
            return _llm_adjudicator(llm, model, _context(driver, mentions, members), candidates, merged)

    embedding = embedding_matcher(records, embedder, blocking)
    guards = concept_guards(driver, mentions, schema)
    decisions, groups, passes = decide_in_passes(
        records, borderline, embedding, blocking, auto_merge, adjudicator_for, guards=guards, blocked=blocked
    )
    return ConceptResolution(
        concepts=records,
        members=members,
        decisions=decisions,
        groups=groups,
        passes=passes,
        blocked={name: len(pairs) for name, pairs in sorted(blocked.items())},
    )


def preview_concepts(
    mentions: list[MentionRecord],
    auto_merge: float,
    borderline: float,
    embedder: Embedder | None,
    blocking: Blocking | None,
    guards: list[Guard],
) -> ResolvePreview:
    """The candidate pairs a resolution of `mentions` would consider, with no LLM call and no write."""
    records, _ = concept_records(mentions)
    return preview(records, nominate(records, borderline, embedder, blocking, guards), auto_merge)


ContextReader = Callable[[list[str]], list[MentionRow]]


def _context(driver: Driver, mentions: list[MentionRecord], members: dict[str, list[str]]) -> ContextReader:
    """A reader of the chunks of some concepts, as rows ordered by concept, document and chunk position: the
    order of the entity reader before R75, so the same graph builds the same prompts."""
    concept_of = {m: cid for cid, ids in members.items() for m in ids}
    names = {m.id: m.name for m in mentions}

    def read(concepts: list[str]) -> list[MentionRow]:
        wanted = sorted(m for cid in concepts for m in members.get(cid, []))
        rows = [
            (concept_of[t.mention], document_of(t.chunk_id), _position(t.chunk_id), t)
            for t in read_mention_texts(driver, wanted)
        ]
        rows.sort(key=lambda r: r[:3])
        return [
            MentionRow(entity=cid, names=[names[t.mention]], document=t.document, text=t.text)
            for cid, _, _, t in rows
        ]

    return read


def _position(chunk_id: str) -> int:
    """The chunk's index in its document (chunk ids are `<doc_id>#<index>`); 0 for a hand-made id."""
    index = chunk_id.rsplit("#", 1)[-1]
    return int(index) if index.isdigit() else 0


def _llm_adjudicator(
    llm: LLMClient,
    model: str,
    read: ContextReader,
    candidates: list[Candidate],
    merged: dict[str, list[str]],
) -> Adjudicator:
    """An adjudicator that shows the LLM both names with the sentences that mention them (for a concept
    merged in an earlier pass, the sentences of all its members)."""
    shown = sorted({i for c in candidates for i in (c.a, c.b)})
    owner = {m: i for i in shown for m in merged.get(i, [i])}
    context = mention_lines(read(sorted(owner)), owner=owner)

    def render(concept: str) -> str:
        return "\n".join(f"- {line}" for line in context.get(concept, [])) or "- (no sentence names it)"

    def adjudicate(a: EntityRecord, b: EntityRecord) -> bool:
        prompt = ADJUDICATE_PROMPT.format(
            etype=a.type, a=a.name, b=b.name, ctx_a=render(a.id), ctx_b=render(b.id)
        )
        return llm.generate(prompt, SamePair, model=model).same

    return adjudicate
