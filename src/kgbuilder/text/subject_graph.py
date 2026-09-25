"""Write the subject graph: `(:Entity)` kinds, `(Chunk)-[:MENTIONS]->(Entity)`, an `(:Observation)` per claim.

Role in the pipeline: second half of `kg extract`; input is the verified triples from extraction.py. The
derivation in the link stage writes its observations through `write_observations` too, so the graph has
one shape for every claim, whoever made it.
Design: entities are keyed by (type, normalised name) through `core.identity.entity_id`, so the same
name in two chunks is one node: an entity is a *kind* shared by every document that names it (R41). A
claim is therefore not an edge between two kinds, because such an edge belongs to every thing that has
that kind, and a query from one product reached another product's claims (the R63 audit). It is its own
node (R64):
    (:Observation {id, predicate, chunk_id, evidence, subject_name, object_name, extractor})
      -[:SUBJECT]->(:Entity)   -[:OBJECT]->(:Entity)   -[:FROM]->(:Chunk)
The link stage later attaches it to the thing its document is about (`HAS_OBSERVATION`). The observation
keeps the names the extractor gave its two ends (`subject_name`, `object_name`): entity resolution may
rename a node to another review's wording of the same kind, the claim keeps what its own review said (R44).
All writes are MERGE on the observation id, so re-running extraction does not duplicate anything.
Not here: deciding which triples are valid (extraction.py), merging near-duplicates (resolution/) and
attaching observations to things (resolution/linking.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import entity_id, observation_id
from .extraction import Triple


class SubjectGraphCounts(BaseModel):
    entities: int
    facts: int  # observations written: one per distinct claim
    mentions: int


class ObservationRow(BaseModel):
    """One observation to write: its claim, its two entities (by id) and its source."""

    id: str
    predicate: str
    subject: str  # entity ids
    object: str
    chunk_id: str
    evidence: str
    subject_name: str  # the claim's own wording of its two ends
    object_name: str


def observation_row(
    predicate: str, subject: str, obj: str, chunk_id: str, evidence: str, names: tuple[str, str]
) -> ObservationRow:
    """The row for one claim; `subject` and `obj` are entity ids, `names` the claim's own wording."""
    subject_name, object_name = (n.strip() for n in names)
    return ObservationRow(
        id=observation_id(chunk_id, predicate, subject_name, object_name),
        predicate=predicate,
        subject=subject,
        object=obj,
        chunk_id=chunk_id,
        evidence=evidence,
        subject_name=subject_name,
        object_name=object_name,
    )


def write_observations(driver: Driver, rows: list[ObservationRow], extractor: str) -> None:
    """MERGE each observation with its SUBJECT, OBJECT and FROM edges. The entities and chunks must exist.

    `extractor` (the model id, or "derived") is stored on every observation.
    """
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE")
    if not rows:
        return
    driver.execute_query(
        "UNWIND $rows AS r MATCH (s:Entity {id: r.subject}), (t:Entity {id: r.object}), "
        "(c:Chunk {chunk_id: r.chunk_id}) "
        # the id is the whole MERGE key: the same claim stated in two chunks is two observations on purpose,
        # because each statement is separate evidence (three reviews saying "drawers stick" count as three)
        "MERGE (o:Observation {id: r.id}) "
        # ON CREATE only: a rerun must not rewrite a claim that resolution has since deduplicated around
        "ON CREATE SET o.predicate = r.predicate, o.chunk_id = r.chunk_id, o.evidence = r.evidence, "
        "o.subject_name = r.subject_name, o.object_name = r.object_name "
        "SET o.extractor = $extractor "
        "MERGE (o)-[:SUBJECT]->(s) MERGE (o)-[:OBJECT]->(t) MERGE (o)-[:FROM]->(c)",
        rows=[r.model_dump() for r in rows],
        extractor=extractor,
    )


def write_subject_graph(driver: Driver, triples: list[Triple], extractor: str) -> SubjectGraphCounts:
    """MERGE entities, mentions and observations. `extractor` (the model id) is stored on each observation."""
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
    # the type is a property, not a label: a `Product` label here would collide with the domain graph's
    driver.execute_query("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")

    entities: dict[str, dict] = {}
    mentions: set[tuple[str, str]] = set()
    observations: dict[str, ObservationRow] = {}
    for t in triples:
        ids = []
        for name, etype in ((t.subject, t.subject_type), (t.object, t.object_type)):
            eid = entity_id(etype, name)
            # first spelling seen becomes the display name; other spellings become aliases during ER
            entities.setdefault(eid, {"id": eid, "name": name.strip(), "type": etype})
            mentions.add((t.chunk_id, eid))
            ids.append(eid)
        row = observation_row(t.predicate, ids[0], ids[1], t.chunk_id, t.evidence, (t.subject, t.object))
        # one id is one claim: a triple repeated in its chunk (a second pass restating the first) adds no
        # node, and the first one's entities are kept, so an observation never gets two subjects
        observations.setdefault(row.id, row)

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
    write_observations(driver, list(observations.values()), extractor)
    return SubjectGraphCounts(entities=len(entities), facts=len(observations), mentions=len(mentions))
