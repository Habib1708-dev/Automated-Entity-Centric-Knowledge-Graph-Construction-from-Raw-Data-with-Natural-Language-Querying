"""Derived facts: sentence picking as a pure function, and (with Neo4j) the rule that a named part is
PART_OF the product its document is about: one fact per mention chunk, verbatim evidence, product entity
created once, idempotent, scored by the gold set and accepted by the validation checks."""

import pytest

from kgbuilder.resolution.derivation import DERIVED_EXTRACTOR, derive_facts, pick_sentence
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.validation.checks.base import CheckContext
from kgbuilder.validation.evaluate import score_triples
from kgbuilder.validation.gold import GoldTriple
from kgbuilder.validation.validator import validate_graph

from .sample_plans import node

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Product", description="a product"),
        EntityType(name="Component", description="a part"),
        EntityType(name="FailureMode", description="a failure"),
    ],
    fact_types=[
        FactType(
            predicate="EXHIBITS_FAILURE", subject_type="Component", object_type="FailureMode", description="x"
        ),
        FactType(
            predicate="PART_OF",
            subject_type="Component",
            object_type="Product",
            description="y",
            derived=True,
        ),
    ],
)
PLAN = ConstructionPlan(
    nodes=[node("products.csv", "Product", "product_id", ["product_name"])], relationships=[]
)


def test_pick_sentence_returns_the_first_verbatim_sentence_naming_the_entity():
    text = "Looks **nice**.\nThe Drawer Rails stick badly! Avoid.\n\n- @anna"
    assert (
        pick_sentence(text, ["drawer rails"]) == "The Drawer Rails stick badly!"
    )  # verbatim, not normalised
    assert pick_sentence(text, ["handles", "rails"]) == "The Drawer Rails stick badly!"  # any alias
    assert pick_sentence(text, ["handles"]) is None
    assert pick_sentence(text, [""]) is None


@pytest.mark.neo4j
def test_a_named_part_is_part_of_the_product_its_document_is_about(driver):
    driver.execute_query(
        "CREATE (p:Product {product_id: 'P1', product_name: 'Helsingborg Dresser'}), "
        "(d:Document {doc_id: 'h.md', title: 'helsingborg_dresser_reviews'})-[:ABOUT]->(p), "
        "(c1:Chunk {chunk_id: 'h.md#0', text: 'Looks nice. The drawer rails stick badly! Avoid.'})"
        "-[:PART_OF]->(d), "
        "(c2:Chunk {chunk_id: 'h.md#1', text: 'The rails are rough.'})-[:PART_OF]->(d), "
        "(rails:Entity {id: 'e1', name: 'drawer rails', type: 'Component', "
        "aliases: ['drawer rails', 'rails']}), "
        "(c1)-[:MENTIONS]->(rails), (c2)-[:MENTIONS]->(rails), "
        "(stick:Entity {id: 'e2', name: 'stick', type: 'FailureMode', aliases: ['stick']}), "
        "(c1)-[:MENTIONS]->(stick), "
        "(rails)-[:EXHIBITS_FAILURE {chunk_id: 'h.md#0', evidence: 'The drawer rails stick badly!'}]"
        "->(stick), "
        # mentioned in c2 by a name c2's text does not contain (it came through the document context)
        "(handles:Entity {id: 'e3', name: 'handles', type: 'Component', aliases: ['handles']}), "
        "(c2)-[:MENTIONS]->(handles), "
        # a document that is ABOUT nothing: its parts belong to no known product
        "(o:Document {doc_id: 'o.md', title: 'notes'}), "
        "(c3:Chunk {chunk_id: 'o.md#0', text: 'legs wobble'})-[:PART_OF]->(o), "
        "(legs:Entity {id: 'e4', name: 'legs', type: 'Component', aliases: ['legs']}), "
        "(c3)-[:MENTIONS]->(legs)"
    )

    report = derive_facts(driver, SCHEMA, PLAN)
    assert report.model_dump() == {"facts_derived": 2, "entities_created": 1, "skipped_no_evidence": 1}

    records, _, _ = driver.execute_query(
        "MATCH (s:Entity)-[f:PART_OF]->(o:Entity) "
        "RETURN s.name AS s, o.name AS o, o.type AS t, f.chunk_id AS c, f.evidence AS e, "
        "f.extractor AS x ORDER BY c"
    )
    assert [r.data() for r in records] == [
        {"s": "drawer rails", "o": "Helsingborg Dresser", "t": "Product", "c": "h.md#0",
         "e": "The drawer rails stick badly!", "x": DERIVED_EXTRACTOR},
        {"s": "drawer rails", "o": "Helsingborg Dresser", "t": "Product", "c": "h.md#1",
         "e": "The rails are rough.", "x": DERIVED_EXTRACTOR},
    ]  # fmt: skip
    mentions = driver.execute_query(
        "MATCH (:Chunk)-[:MENTIONS]->(o:Entity {name: 'Helsingborg Dresser'}) RETURN count(*) AS n"
    )[0][0]["n"]
    assert mentions == 2

    # a rerun (kg link is recomputed after every resolve) adds nothing
    again = derive_facts(driver, SCHEMA, PLAN)
    assert again.facts_derived == 2 and again.entities_created == 0
    assert driver.execute_query("MATCH ()-[f:PART_OF]->(:Entity) RETURN count(f) AS n")[0][0]["n"] == 2

    # the gold set's PART_OF triples are scored against derived facts exactly like extracted ones
    facts = CheckContext(driver=driver).facts
    gold = [
        GoldTriple(subject="drawer rails", predicate="PART_OF", object="Helsingborg Dresser", doc_id="h.md")
    ]
    assert score_triples(facts, gold).recall == 1.0

    # derived facts conform to the schema (the derived type is part of it) and have verbatim provenance
    checks = validate_graph(driver, plan=None, schema=SCHEMA)
    assert [c.name for c in checks.checks if not c.passed] == []
    assert checks.metrics["evidence_verified_rate"] == 1.0
