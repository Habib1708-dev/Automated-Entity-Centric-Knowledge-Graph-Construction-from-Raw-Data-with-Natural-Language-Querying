"""Write and clear the identity layer: one `(:Mention)-[:REFERS_TO]->(entity)` per mention, and the
`:Concept` and `:Individual` nodes the edges point at (R75).

Role in the pipeline: the last step of `kg resolve` (identity.py decides, this module writes), and
`kg resolve --undo` (`clear_identity`).
Design: Repository. Nothing is merged or deleted besides the identity layer itself, so clearing it returns
the graph to its state after `kg link`, and deleting one mention's edge undoes that one decision: the
mention then stands for itself (graph/canonical.py). Every write replaces the whole layer, so a rerun keeps
no edge the current decisions do not support.
Not here: deciding what a mention refers to (identity.py), reading the edges (graph/canonical.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..graph.canonical import CanonicalKind


class Assignment(BaseModel):
    """What one mention refers to, and why: one `REFERS_TO` edge."""

    mention: str  # mention id
    said: str  # the mention's own name
    kind: CanonicalKind
    canonical: str  # the canonical entity's stable id: a concept or individual id, or `record_ref`
    name: str  # its display name
    type: str  # the mention's type; a concept or individual node takes its founding mention's
    target: str | None = None  # a record's element id; None for concepts and individuals
    reason: str  # the rule that decided it (records.py, `_individual_reason`, `_concept_reason`)
    score: float | None = None  # the matching score, where the rule has one
    evidence: str = ""  # the name, the sentence or the other name that shows it
    by: str = "code"  # "code", or the model that adjudicated it


def clear_identity(driver: Driver) -> int:
    """Delete the identity layer: every `REFERS_TO` of a mention and every concept and individual node.
    Returns how many edges were removed. The mentions, claims and links stay as `kg link` left them."""
    records, _, _ = driver.execute_query("MATCH (:Mention)-[r:REFERS_TO]->() DELETE r RETURN count(r) AS n")
    driver.execute_query("MATCH (n) WHERE n:Concept OR n:Individual DETACH DELETE n")
    return records[0]["n"]


def write_identity(driver: Driver, assignments: list[Assignment]) -> None:
    """Replace the identity layer with `assignments`: the concept and individual nodes, one edge each."""
    clear_identity(driver)
    for label in ("Concept", "Individual"):
        driver.execute_query(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
    rows = [
        {
            "mention": a.mention,
            "canonical": a.canonical,
            "name": a.name,
            "type": a.type,
            "target": a.target,
            # every reader takes the canonical id, name and kind from the edge (graph/canonical.py)
            "props": a.model_dump(include={"canonical", "name", "kind", "reason", "score", "evidence", "by"}),
        }
        for a in assignments
    ]
    for kind, label in (("concept", "Concept"), ("individual", "Individual")):
        driver.execute_query(
            # the labels are this module's own constants, not input; MERGE: many mentions, one node
            f"UNWIND $rows AS r MATCH (m:Mention {{id: r.mention}}) MERGE (n:{label} {{id: r.canonical}}) "
            "ON CREATE SET n.name = r.name, n.type = r.type MERGE (m)-[e:REFERS_TO]->(n) SET e = r.props",
            rows=[r for r, a in zip(rows, assignments, strict=True) if a.kind == kind],
        )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (m:Mention {id: r.mention}) MATCH (n) WHERE elementId(n) = r.target "
        "MERGE (m)-[e:REFERS_TO]->(n) SET e = r.props",
        rows=[r for r, a in zip(rows, assignments, strict=True) if a.kind == "record"],
    )
