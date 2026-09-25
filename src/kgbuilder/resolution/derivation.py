"""Facts derived in code from the links, instead of extracted: a named part belongs to the product its
document is about.

Role in the pipeline: second half of `kg link`, after the ABOUT links exist. For every fact type the text
schema marks `derived` (subject type S, predicate P, object type O), every S entity mentioned in a chunk of
a document ABOUT a domain node becomes an observation "S entity P (O entity named like that node)": one per
mention chunk, with the chunk's sentence that names the entity as verbatim evidence. It is written by the
subject-graph writer's `write_observations`, so a derived claim has the shape of an extracted one (R64).
Design: "the LLM proposes, code decides", one step further: what the document states by itself (the title
names the product, the review names the part) is never asked from the model, which used to spend more
than half of its output on it. The rule only reads the path Entity <-MENTIONS- Chunk -PART_OF-> Document
-ABOUT-> node, written by three deterministic stages. The object entity is the one the text already has
for that node when its name contains the node's name ("2019 Subaru Outback" for "OUTBACK", R60), so the
thing is one node, not two. Derived observations carry `extractor = "derived"`, so a reader can tell them
from model output. All writes are MERGE: a rerun adds nothing.
Not here: matching entities to domain nodes (linking.py) and the extracted facts (text/extraction.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import entity_id
from ..core.text import norm, pick_sentence
from ..structured.plan import ConstructionPlan
from ..text.schema import FactType, TextSchema
from ..text.subject_graph import observation_row, write_observations
from .linking import read_domain_nodes

# The `extractor` property of a derived fact; facts from the model carry the model id instead.
DERIVED_EXTRACTOR = "derived"


class DerivationReport(BaseModel):
    """Counts of one derivation run. Metric names in MLflow; keep them stable."""

    facts_derived: int
    entities_created: int  # object entities (the products) that no extracted fact had created before
    skipped_no_evidence: int  # mentions whose chunk has no sentence naming the entity: no quote, no fact
    # domain nodes whose object entity was found by containment ("2019 Subaru Outback" for "OUTBACK", R60)
    targets_by_containment: int = 0


class Candidate(BaseModel):
    """An entity of the object type that the chunks of one document mention."""

    id: str
    names: list[str]  # display name first, then aliases
    mentions: int  # chunks of the ABOUT node's documents that mention it


def containing_entity(node_name: str, candidates: list[Candidate]) -> str | None:
    """The id of the entity whose name contains every word of the domain node's name, or None.

    The plan names a node by one column ("OUTBACK", the `model`), while the text names the same thing in
    full ("2019 Subaru Outback"); without this, derivation created a second entity for it (found in R54).
    Only entities mentioned in the documents ABOUT that node are candidates, so a word the node's name
    shares with another thing elsewhere cannot pull that thing in. Whole words, not substrings: "ESCAPE"
    must not match "ESCAPED". Several candidates: the most-mentioned one, as in entity resolution, then
    the shortest name and the id, so that the choice is deterministic.
    """
    wanted = set(norm(node_name).split())
    if not wanted:
        return None
    matching = [c for c in candidates if any(wanted <= set(norm(n).split()) for n in c.names)]
    if not matching:
        return None
    return min(matching, key=lambda c: (-c.mentions, len(c.names[0]), c.id)).id


def existing_entities(driver: Driver, entity_type: str) -> dict[str, str]:
    """Normalised name or alias -> entity id, for every stored entity of `entity_type`.

    The link stage runs after entity resolution, which may have absorbed the entity that carries the
    product's own name into a differently spelled canonical one ("Västerås Bookshelf" into "Västerås
    Bookshelves"). Looking the product up by `entity_id` alone would then create the absorbed entity a
    second time (found in R29); its aliases still know the name, so they are the lookup key.
    """
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity {type: $etype}) RETURN e.id AS id, [e.name] + coalesce(e.aliases, []) AS names "
        "ORDER BY id",
        etype=entity_type,
    )
    return {norm(name): r["id"] for r in records for name in r["names"] if norm(name)}


def derive_facts(driver: Driver, schema: TextSchema, plan: ConstructionPlan) -> DerivationReport:
    """Write every fact the schema marks `derived`. Idempotent; returns the counts."""
    names_by_node = {n.element_id: n.name for n in read_domain_nodes(driver, plan)}
    facts = created = skipped = contained = 0
    for fact_type in schema.derived():
        targets, by_containment = _targets(driver, fact_type.object_type, names_by_node)
        contained += by_containment
        records, _, _ = driver.execute_query(
            # ORDER BY keeps the write order, and so the report, deterministic
            "MATCH (e:Entity {type: $stype})<-[:MENTIONS]-(c:Chunk)-[:PART_OF]->(:Document)-[:ABOUT]->(n) "
            "RETURN e.id AS id, [e.name] + coalesce(e.aliases, []) AS names, c.chunk_id AS chunk_id, "
            "c.text AS text, elementId(n) AS node ORDER BY id, chunk_id",
            stype=fact_type.subject_type,
        )
        rows = []
        for r in records:
            product = names_by_node.get(r["node"])  # None: the ABOUT node has no name in the plan
            sentence = pick_sentence(r["text"], r["names"]) if product is not None else None
            target = targets.get(r["node"])
            if sentence is None or target == r["id"]:  # no quote, or a self-reference: no fact
                skipped += 1
                continue
            rows.append(
                {
                    "s": r["id"],
                    "s_name": r["names"][0],
                    "o": target,
                    "name": product,
                    "chunk_id": r["chunk_id"],
                    "evidence": sentence,
                }
            )
        created += _write(driver, fact_type, rows)
        facts += len(rows)
    return DerivationReport(
        facts_derived=facts,
        entities_created=created,
        skipped_no_evidence=skipped,
        targets_by_containment=contained,
    )


def _targets(
    driver: Driver, object_type: str, names_by_node: dict[str, str | None]
) -> tuple[dict[str, str], int]:
    """Domain node element id -> the id of the `object_type` entity its derived facts point at, and how
    many of those were found by containment.

    Order: an entity carrying the node's name or alias (R29), then the entity of its documents whose
    name contains the node's name (R60), else a new entity named like the node.
    """
    known = existing_entities(driver, object_type)
    in_documents = _candidates_by_node(driver, object_type)
    targets: dict[str, str] = {}
    contained = 0
    for node, name in names_by_node.items():
        if name is None:
            continue
        target = known.get(norm(name)) or containing_entity(name, in_documents.get(node, []))
        if target is None:
            target = entity_id(object_type, name)
        elif norm(name) not in known:
            contained += 1
        targets[node] = target
    return targets, contained


def _candidates_by_node(driver: Driver, entity_type: str) -> dict[str, list[Candidate]]:
    """Domain node element id -> the `entity_type` entities that its documents' chunks mention."""
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity {type: $etype})<-[:MENTIONS]-(c:Chunk)-[:PART_OF]->(:Document)-[:ABOUT]->(n) "
        "RETURN elementId(n) AS node, e.id AS id, [e.name] + coalesce(e.aliases, []) AS names, "
        "count(DISTINCT c) AS mentions ORDER BY node, id",
        etype=entity_type,
    )
    out: dict[str, list[Candidate]] = {}
    for r in records:
        out.setdefault(r["node"], []).append(Candidate(id=r["id"], names=r["names"], mentions=r["mentions"]))
    return out


def _write(driver: Driver, fact_type: FactType, rows: list[dict]) -> int:
    """MERGE the object entities, their mentions and the observations. Returns how many entities were new."""
    if not rows:
        return 0
    targets = sorted({r["o"] for r in rows})
    existing, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.id IN $ids RETURN count(e) AS n", ids=targets
    )
    driver.execute_query(
        "UNWIND $rows AS r MERGE (o:Entity {id: r.o}) "
        # ON CREATE only, like the subject-graph writer: a name curated by entity resolution stays
        "ON CREATE SET o.name = r.name, o.type = $otype, o.aliases = [r.name]",
        rows=rows,
        otype=fact_type.object_type,
    )
    # the chunk mentions the product too: its document is about it, which is what the fact rests on;
    # without the mention the provenance check would report the product entity as sourceless
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.chunk_id}), (o:Entity {id: r.o}) "
        "MERGE (c)-[:MENTIONS]->(o)",
        rows=rows,
    )
    # the claim's wording is the two entities' display names as they are now, after resolution: the same
    # names the judge sheet showed for a derived fact before R64, so the observation id is its fact id
    names, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.id IN $ids RETURN e.id AS id, e.name AS name", ids=targets
    )
    target_names = {r["id"]: r["name"] for r in names}
    observations = [
        observation_row(
            fact_type.predicate,
            r["s"],
            r["o"],
            r["chunk_id"],
            r["evidence"],
            (r["s_name"], target_names[r["o"]]),
        )
        for r in rows
    ]
    write_observations(driver, observations, DERIVED_EXTRACTOR)
    return len(targets) - existing[0]["n"]
