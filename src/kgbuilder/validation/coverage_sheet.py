"""The coverage sheet: each sampled sentence with everything one graph stores about it (R68).

Role in the pipeline: `kg coverage-sheet SAMPLE` finds every sentence of a committed sample
(sentences.py) in the current graph and writes `out/coverage_sheet.json`. The judge (Claude in the
session) lists each sentence's claims and says which stored item holds each one; `kg coverage` scores
that verdict file against the sheet (coverage.py), without the graph.
Design: the graph is read by `read_chunk_things` and by the one reader every scorer uses
(`CheckContext.facts`, so an observation is seen as the same triple the judge sheet shows); assembling the
sheet from those reads is a pure function, testable without Neo4j. A sentence is found by its document
and its wording, so a sample drawn on one graph fits another graph of the same text.
Not here: drawing the sample (sentences.py) and scoring the verdicts (coverage.py).
"""

import json

from neo4j import Driver
from pydantic import BaseModel

from ..core.errors import EvaluationError
from ..text.chunking import Chunk
from ..text.schema import TextSchema
from .checks.base import StoredFact
from .judge import fact_id
from .sentences import SentenceSample

# Record fields longer than this are cut on the sheet: a long text property (a recall's Summary) is the
# source text itself, not a typed value a question could filter on, and the judge needs to see only that.
_FIELD_CHARS = 80


class SheetThing(BaseModel):
    """A thing a sentence's chunk hangs on (its document's ABOUT, or its section's) and its record."""

    name: str  # display name, as on the ABOUT link and on HAS_OBSERVATION
    fields: dict[str, str]  # the record's properties as text; the judge may cite one as `<name>.<field>`

    def field_ids(self) -> list[str]:
        return [f"{self.name}.{field}" for field in self.fields]


class SheetObservation(BaseModel):
    """An observation from a sentence's chunk, as the judge sees it: its own wording and, after
    resolution, the node each end now is (a merge shows as another name there)."""

    id: str  # the observation id, which the judge sheet uses as the fact id
    subject: str
    predicate: str
    object: str
    subject_entity: str
    object_entity: str
    subject_type: str
    object_type: str
    polarity: str
    time: str
    # the assertion (R77), which the R77 matching pass judges; defaults for sheets written before it
    truth: str = "affirmed"
    modality: str = "actual"
    condition: str = ""
    # R77 part d; absent from sheets written before it
    negation: str = ""
    hedge: str = ""
    triple_truth: str = "affirmed"
    evidence: str | None
    things: list[str]  # what it hangs on (HAS_OBSERVATION)


class SheetSentence(BaseModel):
    """One sampled sentence in the graph the sheet was written from."""

    id: str
    doc_id: str
    chunk_id: str  # the chunk it was found in, in this graph
    text: str
    chunk_text: str  # the whole chunk, so the judge can resolve "it" and "they"
    context: str  # what the chunk's document is about (its first heading)
    things: list[SheetThing]
    observations: list[SheetObservation]

    def item_ids(self) -> set[str]:
        """What a claim of this sentence may be covered by: an observation or a record field."""
        return {o.id for o in self.observations} | {f for t in self.things for f in t.field_ids()}


class CoverageSheet(BaseModel):
    """What the judge decides on, and everything `kg coverage` needs to check the verdicts."""

    seed: int
    population: int
    text_schema: TextSchema  # the graph's fact types: a missed claim names the one that could hold it
    sentences: list[SheetSentence]


def build_coverage_sheet(
    sample: SentenceSample,
    chunks: list[Chunk],
    facts: list[StoredFact],
    things: dict[str, list[SheetThing]],
    schema: TextSchema,
) -> CoverageSheet:
    """Place every sampled sentence in the first chunk of its document that contains it, with the
    observations from that chunk and the things it hangs on (`things`, by chunk id).

    Raises `EvaluationError` when a sentence is in no chunk of its document: the sample was drawn from
    other text, and a sheet without that sentence would silently shrink the sample.
    """
    by_doc: dict[str, list[Chunk]] = {}
    for chunk in sorted(chunks, key=lambda c: (c.doc_id, c.index)):
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    by_chunk: dict[str, list[StoredFact]] = {}
    for fact in facts:
        by_chunk.setdefault(fact.chunk_id or "", []).append(fact)

    sentences: list[SheetSentence] = []
    lost: list[str] = []
    for s in sample.sentences:
        chunk = next((c for c in by_doc.get(s.doc_id, []) if s.text in c.text), None)
        if chunk is None:
            lost.append(s.id)
            continue
        sentences.append(
            SheetSentence(
                id=s.id,
                doc_id=s.doc_id,
                chunk_id=chunk.chunk_id,
                text=s.text,
                chunk_text=chunk.text,
                context=chunk.context,
                things=things.get(chunk.chunk_id, []),
                observations=[_observation(f) for f in by_chunk.get(chunk.chunk_id, [])],
            )
        )
    if lost:
        raise EvaluationError([f"{len(lost)} sampled sentences are in no chunk of this graph: {lost[:3]}"])
    return CoverageSheet(
        seed=sample.seed, population=sample.population, text_schema=schema, sentences=sentences
    )


def _observation(fact: StoredFact) -> SheetObservation:
    return SheetObservation(
        id=fact_id(fact),
        subject=fact.own_subject,
        predicate=fact.predicate,
        object=fact.own_object,
        subject_entity=fact.subject_names[0],
        object_entity=fact.object_names[0],
        subject_type=fact.subject_type,
        object_type=fact.object_type,
        polarity=fact.polarity,
        time=fact.time,
        truth=fact.truth,
        modality=fact.modality,
        condition=fact.condition,
        negation=fact.negation,
        hedge=fact.hedge,
        triple_truth=fact.triple_truth,
        evidence=fact.evidence,
        things=fact.things,
    )


def read_chunk_things(driver: Driver, chunk_ids: list[str]) -> dict[str, list[SheetThing]]:
    """For each chunk, the things it hangs on: what its document is ABOUT and what it is ABOUT itself
    (a section tied to its record, R67), each with its record's properties. The same two links
    `attach_observations` hangs every observation on, so the sheet shows what a query can reach.
    Chunks that hang on nothing are absent from the result."""
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk) WHERE c.chunk_id IN $ids "
        # OPTIONAL: a chunk may hang on its document's thing, on its section's record, on both or on
        # nothing. Each list is collected in its own WITH (Cypher cannot join a grouping key and an
        # aggregate in one expression), then they are joined and the empty entries an unmatched
        # OPTIONAL leaves behind are dropped
        "OPTIONAL MATCH (c)-[:PART_OF]->(:Document)-[da:ABOUT]->(dn) "
        "WITH c, collect({name: da.name, node: dn}) AS from_document "
        "OPTIONAL MATCH (c)-[ca:ABOUT]->(cn) "
        "WITH c, from_document, collect({name: ca.name, node: cn}) AS from_section "
        "UNWIND from_document + from_section AS t WITH c, t WHERE t.node IS NOT NULL "
        "RETURN c.chunk_id AS chunk_id, t.name AS name, properties(t.node) AS fields "
        "ORDER BY chunk_id, name",
        ids=chunk_ids,
    )
    things: dict[str, list[SheetThing]] = {}
    for r in records:
        seen = things.setdefault(r["chunk_id"], [])
        if all(t.name != r["name"] for t in seen):  # a thing both links reach is listed once
            fields = {k: _as_text(v) for k, v in sorted(r["fields"].items())}
            seen.append(SheetThing(name=r["name"] or "", fields=fields))
    return things


def _as_text(value: object) -> str:
    """A property value as the judge reads it; lists joined, JSON spelling for flags, long text cut."""
    if isinstance(value, list):
        text = ", ".join(_as_text(v) for v in value)
    elif isinstance(value, bool | int | float):
        text = json.dumps(value)
    else:
        text = str(value)
    return text if len(text) <= _FIELD_CHARS else text[: _FIELD_CHARS - 1] + "…"
