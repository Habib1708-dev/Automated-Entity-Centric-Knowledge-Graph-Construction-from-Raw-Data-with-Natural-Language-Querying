"""Write the subject graph: `(:Entity)` nodes, `(Chunk)-[:MENTIONS]->(Entity)` and facts between entities.

Role in the pipeline: second half of `kg extract`; input is the verified triples from extraction.py.
Design: entities are keyed by (type, normalised name) through `core.identity.entity_id`, so the same
name in two chunks is one node, and the derivation in the link stage finds the same entities.
Every fact relationship carries `chunk_id` and `evidence`, which is what makes the graph auditable.
All writes are MERGE, so re-running extraction does not duplicate anything.
Not here: deciding which triples are valid (extraction.py) and merging near-duplicates (resolution/).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.identity import entity_id
from .extraction import Triple


class SubjectGraphCounts(BaseModel):
    entities: int
    facts: int
    mentions: int


def write_subject_graph(driver: Driver, triples: list[Triple], extractor: str) -> SubjectGraphCounts:
    """MERGE entities, mentions and facts. `extractor` (the model id) is stored on every fact."""
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
    # the type is a property, not a label: a `Product` label here would collide with the domain graph's
    driver.execute_query("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")

    entities: dict[str, dict] = {}
    mentions: set[tuple[str, str]] = set()
    facts_by_predicate: dict[str, list[dict]] = {}
    for t in triples:
        ids = []
        for name, etype in ((t.subject, t.subject_type), (t.object, t.object_type)):
            eid = entity_id(etype, name)
            # first spelling seen becomes the display name; other spellings become aliases during ER
            entities.setdefault(eid, {"id": eid, "name": name.strip(), "type": etype})
            mentions.add((t.chunk_id, eid))
            ids.append(eid)
        facts_by_predicate.setdefault(t.predicate, []).append(
            {"s": ids[0], "o": ids[1], "chunk_id": t.chunk_id, "evidence": t.evidence}
        )

    driver.execute_query(
        "UNWIND $rows AS r MERGE (e:Entity {id: r.id}) "
        # ON CREATE only: a rerun must not overwrite a name or aliases that entity resolution curated
        "ON CREATE SET e.name = r.name, e.type = r.type, e.aliases = [r.name]",
        rows=list(entities.values()),
    )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.c}), (e:Entity {id: r.e}) MERGE (c)-[:MENTIONS]->(e)",
        rows=[{"c": c, "e": e} for c, e in sorted(mentions)],
    )
    # a relationship type cannot be a parameter, so there is one query per predicate
    for predicate, rows in facts_by_predicate.items():
        driver.execute_query(
            "UNWIND $rows AS r MATCH (s:Entity {id: r.s}), (o:Entity {id: r.o}) "
            # chunk_id + evidence are part of the MERGE key: the same fact stated in two chunks is kept
            # twice on purpose, because each statement is separate evidence
            f"MERGE (s)-[f:{cypher_ident(predicate)} {{chunk_id: r.chunk_id, evidence: r.evidence}}]->(o) "
            "SET f.extractor = $extractor",
            rows=rows,
            extractor=extractor,
        )
    return SubjectGraphCounts(entities=len(entities), facts=len(triples), mentions=len(mentions))
