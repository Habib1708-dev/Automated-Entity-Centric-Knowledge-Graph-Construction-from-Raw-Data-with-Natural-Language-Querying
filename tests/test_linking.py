"""Linking: the pure matching functions that decide ABOUT and REFERS_TO links, and (with Neo4j) scoped
linking end to end: a generic part name links to the part of the product its review is about."""

import pytest

from kgbuilder.resolution.linking import (
    DomainNode,
    link_entity,
    link_graphs,
    match_document,
    match_entity,
    name_property,
)
from kgbuilder.structured.plan import ConstructionPlan

from .sample_plans import node, rel


def domain_node(element_id: str, label: str, name: str) -> DomainNode:
    return DomainNode(element_id=element_id, label=label, name=name)


DOMAIN = [
    domain_node("p1", "Product", "Table"),
    domain_node("p2", "Product", "Coffee Table"),
    domain_node("p3", "Product", "Stockholm Chair"),
    domain_node("a7", "Part", "Leg"),
]


def test_document_links_to_the_longest_name_contained_in_its_title():
    assert match_document("jonkoping_coffee_table_reviews", DOMAIN).element_id == "p2"
    assert match_document("Stockholm-Chair reviews (2024)", DOMAIN).element_id == "p3"


def test_document_ignores_very_short_names_and_unrelated_titles():
    assert match_document("legal_notes", DOMAIN) is None  # "Leg" is inside "legal": too short to trust
    assert match_document("warranty_terms", DOMAIN) is None


def test_entity_links_on_near_exact_names_including_aliases_and_word_order():
    assert match_entity(["Chair Stockholm"], DOMAIN, threshold=90)[0].node.element_id == "p3"
    assert match_entity(["the big one", "stockholm chair"], DOMAIN, threshold=90)[0].node.element_id == "p3"
    [exact] = match_entity(["Coffee Table"], DOMAIN, threshold=90)
    assert exact.node.element_id == "p2" and exact.score == 100


def test_entity_below_threshold_or_without_a_name_is_not_linked():
    assert match_entity(["Dining Table Deluxe"], DOMAIN, threshold=90) == []
    assert match_entity(["", "  "], DOMAIN, threshold=90) == []


def test_match_entity_returns_every_node_tied_for_the_best_score():
    legs = [domain_node("a1", "Assembly", "Legs"), domain_node("a2", "Assembly", "Legs")]
    assert [m.node.element_id for m in match_entity(["legs"], legs, threshold=90)] == ["a1", "a2"]


def test_name_property_prefers_the_plans_name_column_then_guesses():
    part = node("c.csv", "Part", "part_id", ["sub_assembly_name", "part_name"])
    # without name_column the guess picks the sub-assembly code: the bug R11 fixes
    assert name_property(part) == "sub_assembly_name"
    assert name_property(part.model_copy(update={"name_column": "part_name"})) == "part_name"
    assert name_property(node("p.csv", "Doc", "doc_id", ["Title"])) == "Title"
    assert name_property(node("p.csv", "Thing", "thing_id", ["price"])) == "thing_id"


# Two products that both have an assembly called "Legs": the case that needs scopes.
CHAIR, TABLE = domain_node("p1", "Product", "Chair"), domain_node("p2", "Product", "Table")
CHAIR_LEGS, TABLE_LEGS = domain_node("a1", "Assembly", "Legs"), domain_node("a2", "Assembly", "Legs")
FURNITURE = [CHAIR, TABLE, CHAIR_LEGS, TABLE_LEGS]


def test_a_generic_name_links_inside_the_scope_of_its_document():
    result = link_entity(["legs"], [[CHAIR, CHAIR_LEGS]], FURNITURE, threshold=90)
    assert [m.node.element_id for m in result.matches] == ["a1"] and result.scoped


def test_an_entity_mentioned_for_two_products_gets_one_link_per_product():
    result = link_entity(["legs"], [[CHAIR, CHAIR_LEGS], [TABLE, TABLE_LEGS]], FURNITURE, threshold=90)
    assert sorted(m.node.element_id for m in result.matches) == ["a1", "a2"]


def test_without_a_scope_a_generic_name_is_ambiguous_and_a_unique_one_links():
    ambiguous = link_entity(["legs"], [], FURNITURE, threshold=90)
    assert ambiguous.matches == [] and ambiguous.ambiguous
    unique = link_entity(["table"], [], FURNITURE, threshold=90)
    assert [m.node.element_id for m in unique.matches] == ["p2"] and not unique.scoped


def test_a_name_outside_the_scope_falls_back_to_a_unique_domain_match():
    # a chair review that mentions the table: not in the chair's scope, but unique in the domain
    result = link_entity(["table"], [[CHAIR, CHAIR_LEGS]], FURNITURE, threshold=90)
    assert [m.node.element_id for m in result.matches] == ["p2"] and not result.scoped


LINK_PLAN = ConstructionPlan(
    nodes=[
        node("products.csv", "Product", "product_id", ["product_name"]),
        node("assemblies.csv", "Assembly", "assembly_id", ["assembly_code", "component_name"]).model_copy(
            update={"name_column": "component_name"}
        ),
        node("suppliers.csv", "Supplier", "supplier_id", ["name"]),
    ],
    relationships=[
        rel("assemblies.csv", "USED_IN", "Assembly", "assembly_id", "Product", "product_id"),
        rel("assemblies.csv", "SUPPLIED_BY", "Assembly", "assembly_id", "Supplier", "supplier_id"),
    ],
)


@pytest.mark.neo4j
def test_a_defect_in_a_review_can_be_traced_to_the_supplier_of_that_products_part(driver):
    driver.execute_query(
        # domain: two products, each with its own "Legs" assembly from a different supplier. The
        # assembly_code comes first on purpose: the old name guess would have picked it.
        "CREATE (chair:Product {product_id: 'P1', product_name: 'Chair'}), "
        "(table:Product {product_id: 'P2', product_name: 'Table'}), "
        "(chair)<-[:USED_IN]-"
        "(:Assembly {assembly_id: 'A1', assembly_code: 'chair_asm', component_name: 'Legs'})"
        "-[:SUPPLIED_BY]->(:Supplier {supplier_id: 'S1', name: 'Nordic Wood'}), "
        "(table)<-[:USED_IN]-"
        "(:Assembly {assembly_id: 'A2', assembly_code: 'table_asm', component_name: 'Legs'})"
        "-[:SUPPLIED_BY]->(:Supplier {supplier_id: 'S2', name: 'Shanghai Metal'}), "
        # text: a chair review whose chunk says the legs wobble
        "(:Document {doc_id: 'chair_reviews.md', title: 'chair_reviews'})"
        "<-[:PART_OF]-(c:Chunk {chunk_id: 'k1'}), "
        "(c)-[:MENTIONS]->(legs:Entity {id: 'e1', name: 'legs', type: 'Component', aliases: ['legs']}), "
        "(c)-[:MENTIONS]->(wobble:Entity {id: 'e2', name: 'wobbly', type: 'Defect', aliases: ['wobbly']}), "
        "(legs)-[:HAS_DEFECT {chunk_id: 'k1', evidence: 'the legs are wobbly'}]->(wobble)"
    )

    report = link_graphs(driver, LINK_PLAN)
    assert report.documents_linked == 1 and report.entities_linked_in_scope == 1

    # the root-cause question: follow the fact's chunk to its product, then the part of that product
    records, _, _ = driver.execute_query(
        "MATCH (part:Entity)-[f:HAS_DEFECT]->(:Entity {name: 'wobbly'}) "
        "MATCH (:Chunk {chunk_id: f.chunk_id})-[:PART_OF]->(:Document)-[:ABOUT]->(product) "
        "MATCH (part)-[:REFERS_TO]->(a:Assembly)-[:USED_IN]->(product) "
        "MATCH (a)-[:SUPPLIED_BY]->(s:Supplier) RETURN s.name AS supplier"
    )
    assert [r["supplier"] for r in records] == ["Nordic Wood"]

    # rerunning recomputes the links instead of adding to them
    link_graphs(driver, LINK_PLAN)
    records, _, _ = driver.execute_query("MATCH ()-[l:REFERS_TO|ABOUT]->() RETURN count(l) AS n")
    assert records[0]["n"] == 2
