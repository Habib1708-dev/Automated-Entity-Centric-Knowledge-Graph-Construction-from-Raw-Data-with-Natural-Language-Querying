"""Facts derived in code from the links, instead of extracted: a named part belongs to the product its
document is about.

Role in the pipeline: second half of `kg link`, after the ABOUT links exist. For every fact type the text
schema marks `derived` (subject type S, predicate P, object type O), every S entity mentioned in a chunk of
a document ABOUT a domain node becomes `(S entity)-[:P]->(O entity named like that node)`: one fact per
mention chunk, with the chunk's sentence that names the entity as verbatim evidence.
Design: "the LLM proposes, code decides", one step further: what the document states by itself (the title
names the product, the review names the part) is never asked from the model, which used to spend more
than half of its output on it. The rule only reads the path Entity <-MENTIONS- Chunk -PART_OF-> Document
-ABOUT-> node, written by three deterministic stages. Derived facts carry `extractor = "derived"`, so a
reader can tell them from model output. All writes are MERGE: a rerun adds nothing.
Not here: matching entities to domain nodes (linking.py) and the extracted facts (text/extraction.py).
"""

import re

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.identity import entity_id
from ..core.text import norm
from ..structured.plan import ConstructionPlan
from ..text.schema import FactType, TextSchema
from .linking import read_domain_nodes

# The `extractor` property of a derived fact; facts from the model carry the model id instead.
DERIVED_EXTRACTOR = "derived"
# A sentence ends at ".", "!" or "?" followed by whitespace, or at a line break (headings, list items).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class DerivationReport(BaseModel):
    """Counts of one derivation run. Metric names in MLflow; keep them stable."""

    facts_derived: int
    entities_created: int  # object entities (the products) that no extracted fact had created before
    skipped_no_evidence: int  # mentions whose chunk has no sentence naming the entity: no quote, no fact


def pick_sentence(text: str, names: list[str]) -> str | None:
    """The first sentence of `text` containing one of `names` (compared with `norm`), returned verbatim.

    None when no sentence does: the entity reached this chunk through the document context or through an
    alias merged from another chunk, and a derived fact must not quote what its chunk does not say.
    """
    wanted = [norm(name) for name in names if norm(name)]
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if sentence and any(w in norm(sentence) for w in wanted):
            return sentence
    return None


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
    facts = created = skipped = 0
    for fact_type in schema.derived():
        known = existing_entities(driver, fact_type.object_type)
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
            target = (
                known.get(norm(product), entity_id(fact_type.object_type, product))
                if product is not None
                else None
            )
            if sentence is None or target == r["id"]:  # no quote, or a self-reference: no fact
                skipped += 1
                continue
            rows.append(
                {"s": r["id"], "o": target, "name": product, "chunk_id": r["chunk_id"], "evidence": sentence}
            )
        created += _write(driver, fact_type, rows)
        facts += len(rows)
    return DerivationReport(facts_derived=facts, entities_created=created, skipped_no_evidence=skipped)


def _write(driver: Driver, fact_type: FactType, rows: list[dict]) -> int:
    """MERGE the object entities, their mentions and the facts. Returns how many entities were new."""
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
    predicate = cypher_ident(fact_type.predicate)
    driver.execute_query(
        "UNWIND $rows AS r MATCH (s:Entity {id: r.s}), (o:Entity {id: r.o}) "
        # same MERGE key as the subject-graph writer: one fact per statement, never duplicated on rerun
        f"MERGE (s)-[f:{predicate} {{chunk_id: r.chunk_id, evidence: r.evidence}}]->(o) "
        "SET f.extractor = $extractor",
        rows=rows,
        extractor=DERIVED_EXTRACTOR,
    )
    return len(targets) - existing[0]["n"]
