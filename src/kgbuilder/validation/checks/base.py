"""The contract every validation check family implements, and the data the families share.

Design: Strategy. `GraphCheck.run` receives a read-only `CheckContext` and returns a `CheckOutput`.
A family that does not apply (no plan, no mentions yet) returns an empty output instead of failing,
so that `kg validate` is useful after every stage, not only at the end.
The fact reader here is the "flattening reader" of the layered-model task (fixed decision 4): every claim,
whatever the identity layer says about its ends (R75), is read as one triple with its ends' canonical names
and every other name their mentions are written with, so judge and exact-match scores stay comparable with
R62-R68. Since identity no longer deletes anything, the reader applies the two rules the physical merge used
to apply to the graph: a claim whose two ends are one entity says nothing ("X relates to X"), and a claim
identical to another in predicate, entities, chunk, quote and time is the same statement written twice.
"""

from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

from neo4j import Driver
from pydantic import BaseModel

from ...graph.canonical import canonical_aliases, canonical_id, canonical_name
from ...structured.plan import ConstructionPlan
from ...text.schema import TextSchema
from ..report import CheckOutput


class Attached(BaseModel):
    """One thing a claim hangs on (HAS_OBSERVATION, R76): its display name, the route that attached it and
    the evidence the route found (resolution/attachment.py)."""

    thing: str
    how: str
    evidence: str


class StoredFact(BaseModel):
    """One observation of the graph, read as a triple: subject kind, predicate, object kind, and its source.

    Every scorer (exact match, the judge sheet, path truth) reads claims in this one shape, so the scores
    of the observation graph (R64) stay comparable with those of the edge graph before it.
    """

    predicate: str
    subject_type: str
    object_type: str
    chunk_id: str | None
    evidence: str | None
    subject_names: list[str]  # the canonical entity's display name first, then the other names (aliases)
    object_names: list[str]
    # the names the claim itself gave its ends (R44); None only in judge sheets written before R44
    subject_name: str | None = None
    object_name: str | None = None
    # display names of the things the observation is attached to (HAS_OBSERVATION), and of the things its
    # document is ABOUT; both empty for a graph or judge sheet from before R64, and for text-only data
    things: list[str] = []
    about: list[str] = []
    # each attachment with its route and evidence (R76), and the things the claim's own chunk is ABOUT (its
    # section, R67); both empty for a graph or judge sheet from before R76
    attachments: list[Attached] = []
    sections: list[str] = []
    # the claim's qualifiers (R66); the defaults are what a claim from before R66 or a derived one carries
    polarity: str = "neutral"
    time: str = ""
    value: float | None = None
    unit: str | None = None

    @property
    def own_subject(self) -> str:
        """What this fact calls its subject: its own wording, else the entity's display name."""
        return self.subject_name or self.subject_names[0]

    @property
    def own_object(self) -> str:
        """What this fact calls its object: its own wording, else the entity's display name."""
        return self.object_name or self.object_names[0]


class ClaimRow(StoredFact):
    """An observation as the graph returns it, before flattening: a fact plus its id and the canonical ids
    of its two ends."""

    id: str
    subject_id: str
    object_id: str


def flatten(rows: list[ClaimRow]) -> list[StoredFact]:
    """The claims as triples: self-references left out, and of the claims identical in predicate, canonical
    ends, chunk, quote and time only the first by its own wording and id (the R64 repeat rule; the same one
    survives every rebuild, so the id the judge's verdicts refer to is stable). Order: by observation id.
    Pure."""
    kept: dict[tuple, ClaimRow] = {}
    for row in sorted(rows, key=lambda r: (r.subject_name or "", r.object_name or "", r.id)):
        if row.subject_id == row.object_id:
            continue
        key = (row.subject_id, row.predicate, row.object_id, row.chunk_id, row.evidence, row.time)
        kept.setdefault(key, row)
    fields = set(StoredFact.model_fields)
    return [StoredFact(**row.model_dump(include=fields)) for row in sorted(kept.values(), key=lambda r: r.id)]


def _names(display: str, aliases: list[str]) -> list[str]:
    return [display, *(a for a in aliases if a != display)]


@dataclass
class CheckContext:
    """What the checks may look at. `plan`, `schema` and `expected_counts` are None when unknown."""

    driver: Driver
    plan: ConstructionPlan | None = None
    schema: TextSchema | None = None
    expected_counts: dict[str, int] | None = None  # node label -> rows in its source file

    def scalar(self, query: str, **params: object) -> int:
        """First column of the first row of a counting query; 0 when there is no row."""
        records, _, _ = self.driver.execute_query(query, **params)
        return records[0][0] if records else 0

    @cached_property
    def facts(self) -> list[StoredFact]:
        """All claims with a subject and an object mention, flattened (`flatten`). Cached: several families
        need them and the query is the big one. An observation missing either end is not a claim; the
        provenance check counts those."""
        records, _, _ = self.driver.execute_query(
            "MATCH (s:Mention)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Mention) "
            "RETURN o.id AS id, o.predicate AS predicate, s.type AS subject_type, t.type AS object_type, "
            "o.chunk_id AS chunk_id, o.evidence AS evidence, "
            "o.subject_name AS subject_name, o.object_name AS object_name, "
            # observations written before R66 have no qualifiers: read them as a neutral claim without time
            "coalesce(o.polarity, 'neutral') AS polarity, coalesce(o.time, '') AS time, "
            "o.value AS value, o.unit AS unit, "
            # each end as its canonical entity: the id decides self-references and repeats, the names are
            # what exact matching and the judge read
            f"{canonical_id('s')} AS subject_id, {canonical_name('s')} AS subject_display, "
            f"{canonical_aliases('s')} AS subject_aliases, "
            f"{canonical_id('t')} AS object_id, {canonical_name('t')} AS object_display, "
            f"{canonical_aliases('t')} AS object_aliases, "
            "[(n)-[h:HAS_OBSERVATION]->(o) WHERE h.name IS NOT NULL | h.name] AS things, "
            "[(o)-[:FROM]->(:Chunk)-[:PART_OF]->(:Document)-[a:ABOUT]->() WHERE a.name IS NOT NULL | a.name] "
            "AS about, "
            # attachments from before R76 have no route: leaving them out lets path truth score such a
            # graph by the rule of its time (validation/paths.py)
            "[(n)-[h:HAS_OBSERVATION]->(o) WHERE h.name IS NOT NULL AND h.how IS NOT NULL | "
            "{thing: h.name, how: h.how, evidence: coalesce(h.evidence, '')}] AS attachments, "
            "[(o)-[:FROM]->(:Chunk)-[a:ABOUT]->() WHERE a.name IS NOT NULL | a.name] AS sections"
        )
        rows = []
        for r in records:
            data = r.data()
            data["subject_names"] = _names(data.pop("subject_display"), data.pop("subject_aliases"))
            data["object_names"] = _names(data.pop("object_display"), data.pop("object_aliases"))
            rows.append(ClaimRow(**data))
        return flatten(rows)


class GraphCheck(Protocol):
    """One family of related checks."""

    def run(self, ctx: CheckContext) -> CheckOutput: ...
