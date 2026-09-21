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
    """A fact relationship between two entities, as stored in the graph."""

    predicate: str
    subject_type: str
    object_type: str
    chunk_id: str | None
    evidence: str | None
    subject_names: list[str]  # display name first, then aliases
    object_names: list[str]


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
        """All entity-to-entity facts. Cached: several families need them and the query is the big one."""
        records, _, _ = self.driver.execute_query(
            "MATCH (s:Entity)-[r]->(o:Entity) "
            "RETURN type(r) AS predicate, s.type AS subject_type, o.type AS object_type, "
            "r.chunk_id AS chunk_id, r.evidence AS evidence, "
            "[s.name] + coalesce(s.aliases, []) AS subject_names, "
            "[o.name] + coalesce(o.aliases, []) AS object_names"
        )
        return [StoredFact(**r.data()) for r in records]


class GraphCheck(Protocol):
    """One family of related checks."""

    def run(self, ctx: CheckContext) -> CheckOutput: ...
