"""How a reader finds what a mention refers to: Cypher expressions over the identity layer (R75).

Role in the pipeline: the identity stage (resolution/identity.py) writes one
`(:Mention)-[:REFERS_TO {canonical, name, kind, reason, score, evidence, by}]->(entity)` per mention, the
entity being a record of the domain graph, an `:Individual` or a `:Concept`. Every reader (the fact reader
of validation, the ER sheet, the query stage) reads a mention's canonical entity through the expressions
here, so the shape is written down once.
Design: the edge carries the canonical entity's stable id (`canonical`) and display name (`name`), so a
reader needs neither the target's label nor the plan's name property. A mention without the edge stands
for itself: before `kg resolve` has run, or after one identity decision was undone by deleting its edge
(the task file's reversibility rule: no node is ever merged).
Not here: deciding what a mention refers to (resolution/), writing the edges (resolution/identity.py).
"""

from typing import Literal

MENTION = "Mention"
CONCEPT = "Concept"
INDIVIDUAL = "Individual"

# What a canonical entity is; "mention" is how a reader sees a mention without an edge
CanonicalKind = Literal["record", "individual", "concept"]


def _edge_value(mention: str, prop: str, default: str) -> str:
    # a pattern comprehension, so the expression works inside RETURN, WHERE and WITH alike; the variable is
    # named after the mention's, so two expressions in one query never share it
    edge = f"ref_{mention}"
    return f"coalesce(head([({mention})-[{edge}:REFERS_TO]->() | {edge}.{prop}]), {default})"


def canonical_id(mention: str) -> str:
    """The stable id of the entity the mention bound to `mention` refers to, else the mention's own id."""
    return _edge_value(mention, "canonical", f"{mention}.id")


def canonical_name(mention: str) -> str:
    """The display name of the entity the mention refers to, else the mention's own name."""
    return _edge_value(mention, "name", f"{mention}.name")


def canonical_kind(mention: str) -> str:
    """ "record", "individual" or "concept", else "mention" for a mention without an edge."""
    return _edge_value(mention, "kind", "'mention'")


def canonical_aliases(mention: str) -> str:
    """Every name the mentions of the same canonical entity are written with, sorted; the mention's own
    name alone when it has no edge. These are the "aliases" of the graph before R75, when one merged node
    gathered the names of its members."""
    other = f"alias_{mention}"
    return (
        f"CASE WHEN EXISTS {{ MATCH ({mention})-[:REFERS_TO]->() }} "
        f"THEN apoc.coll.sort(apoc.coll.toSet([({mention})-[:REFERS_TO]->()<-[:REFERS_TO]-({other}:Mention) "
        f"| {other}.name])) ELSE [{mention}.name] END"
    )
