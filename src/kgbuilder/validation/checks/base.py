"""The contract every validation check family implements, and the data the families share.

Design: Strategy. `GraphCheck.run` receives a read-only `CheckContext` and returns a `CheckOutput`.
A family that does not apply (no plan, no entities yet) returns an empty output instead of failing,
so that `kg validate` is useful after every stage, not only at the end.
"""

from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

from neo4j import Driver
from pydantic import BaseModel

from ...structured.plan import ConstructionPlan
from ...text.schema import TextSchema
from ..report import CheckOutput


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
    subject_names: list[str]  # display name first, then aliases
    object_names: list[str]
    # the names the claim itself gave its ends (R44); None only in judge sheets written before R44
    subject_name: str | None = None
    object_name: str | None = None
    # display names of the things the observation is attached to (HAS_OBSERVATION), and of the things its
    # document is ABOUT; both empty for a graph or judge sheet from before R64, and for text-only data
    things: list[str] = []
    about: list[str] = []
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
        """All observations with a subject and an object entity. Cached: several families need them and the
        query is the big one. An observation missing either end is not a claim; the provenance check
        counts those."""
        records, _, _ = self.driver.execute_query(
            "MATCH (s:Entity)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Entity) "
            "RETURN o.predicate AS predicate, s.type AS subject_type, t.type AS object_type, "
            "o.chunk_id AS chunk_id, o.evidence AS evidence, "
            "o.subject_name AS subject_name, o.object_name AS object_name, "
            # observations written before R66 have no qualifiers: read them as a neutral claim without time
            "coalesce(o.polarity, 'neutral') AS polarity, coalesce(o.time, '') AS time, "
            "o.value AS value, o.unit AS unit, "
            "[s.name] + coalesce(s.aliases, []) AS subject_names, "
            "[t.name] + coalesce(t.aliases, []) AS object_names, "
            "[(n)-[h:HAS_OBSERVATION]->(o) WHERE h.name IS NOT NULL | h.name] AS things, "
            "[(o)-[:FROM]->(:Chunk)-[:PART_OF]->(:Document)-[a:ABOUT]->() WHERE a.name IS NOT NULL | a.name] "
            "AS about "
            # ordered, so that every report built from the facts is the same for the same graph
            "ORDER BY o.id"
        )
        return [StoredFact(**r.data()) for r in records]


class GraphCheck(Protocol):
    """One family of related checks."""

    def run(self, ctx: CheckContext) -> CheckOutput: ...
