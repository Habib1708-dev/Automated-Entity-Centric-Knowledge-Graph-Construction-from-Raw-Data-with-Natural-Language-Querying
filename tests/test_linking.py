"""Linking: the pure matching functions that decide ABOUT and REFERS_TO links, and (with Neo4j) scoped
linking end to end: a generic part name links to the part of the product its review is about, and the
claim, attached to that product (HAS_OBSERVATION), leads to the supplier of the product's part."""

import pytest

from kgbuilder.resolution.linking import (
    DomainNode,
    RecordKey,
    attach_observations,
    contain_entity,
    link_entity,
    link_graphs,
    match_chunk_records,
    match_document,
    match_entity,
)
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document, RecordRef
from kgbuilder.text.lexical import write_lexical_graph

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


# The R60 case, now in linking (R67): the plan names the vehicle by one column, the text writes it fully.
CIVIC = domain_node("v1", "Vehicle", "CIVIC")
ACCORD = domain_node("v2", "Vehicle", "ACCORD")


def test_an_entity_containing_a_nodes_whole_name_links_by_containment():
    [match] = contain_entity(["2016 Honda Civic"], [CIVIC, ACCORD])
    assert match.node.element_id == "v1" and match.score == 100.0
    # whole words only: "ESCAPE" must not match "escaped" (the R60 rule's own counter-example)
    assert contain_entity(["the car escaped"], [domain_node("v3", "Vehicle", "ESCAPE")]) == []
    # too short to trust, same bound as document matching: "Leg" is inside too many names
    assert contain_entity(["table leg"], [domain_node("a1", "Assembly", "Leg")]) == []


def test_containment_picks_the_longest_contained_name_and_ties_are_ambiguous():
    both = [domain_node("p1", "Product", "Table"), domain_node("p2", "Product", "Coffee Table")]
    [match] = contain_entity(["the jonkoping coffee table"], both)
    assert match.node.element_id == "p2"
    twins = [domain_node("a1", "Assembly", "Rails"), domain_node("a2", "Assembly", "Rails")]
    assert len(contain_entity(["drawer rails"], twins)) == 2  # a tie: link_entity treats it as ambiguous


def test_link_entity_uses_containment_only_inside_a_scope_and_after_fuzzy():
    # fuzzy fails on "2016 Honda Civic" vs "CIVIC"; containment inside the scope catches it
    result = link_entity(["2016 Honda Civic"], [[CIVIC, ACCORD]], [CIVIC, ACCORD], threshold=90)
    assert [m.node.element_id for m in result.matches] == ["v1"]
    assert result.scoped and result.by_containment
    # outside every scope containment is not trusted: no scope vouches for the document
    unscoped = link_entity(["2016 Honda Civic"], [], [CIVIC, ACCORD], threshold=90)
    assert unscoped.matches == [] and not unscoped.by_containment
    # a fuzzy hit keeps winning unchanged: no containment flag on an exact name
    exact = link_entity(["civic"], [[CIVIC, ACCORD]], [CIVIC, ACCORD], threshold=90)
    assert [m.node.element_id for m in exact.matches] == ["v1"] and not exact.by_containment


COMPLAINTS = [
    RecordKey(element_id="c1", key="11440801", name="11440801"),
    RecordKey(element_id="c2", key="11440802", name="11440802"),
    RecordKey(element_id="c3", key="P1", name="P1"),
]


def test_a_section_heading_naming_a_records_key_matches_that_record():
    text = "## Complaint 11440801: brakes failed\n\nThe brakes failed. Similar to complaint 11440802."
    assert [r.element_id for r in match_chunk_records(text, COMPLAINTS)] == ["c1"]  # body keys do not count
    # a key inside a longer token is not a match, and separators around the key do not matter
    assert match_chunk_records("# case 111440801", COMPLAINTS) == []
    assert [r.element_id for r in match_chunk_records("## (11440802)", COMPLAINTS)] == ["c2"]
    # "P1" is too short to trust: "## Part 1" would squash to the same token
    assert match_chunk_records("## P1", COMPLAINTS) == []


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
        "(o:Observation {id: 'o1', predicate: 'HAS_DEFECT', chunk_id: 'k1', "
        "evidence: 'the legs are wobbly'}), "
        "(o)-[:SUBJECT]->(legs), (o)-[:OBJECT]->(wobble), (o)-[:FROM]->(c)"
    )

    report = link_graphs(driver, LINK_PLAN)
    assert report.documents_linked == 1 and report.entities_linked_in_scope == 1
    assert attach_observations(driver) == 1

    # the root-cause question: the product the claim hangs on, then that product's part and its supplier
    records, _, _ = driver.execute_query(
        "MATCH (product)-[:HAS_OBSERVATION]->(o:Observation {predicate: 'HAS_DEFECT'}), "
        "(part:Entity)<-[:SUBJECT]-(o)-[:OBJECT]->(:Entity {name: 'wobbly'}) "
        "MATCH (part)-[:REFERS_TO]->(a:Assembly)-[:USED_IN]->(product) "
        "MATCH (a)-[:SUPPLIED_BY]->(s:Supplier) RETURN s.name AS supplier"
    )
    assert [r["supplier"] for r in records] == ["Nordic Wood"]

    # rerunning recomputes the links instead of adding to them
    link_graphs(driver, LINK_PLAN)
    records, _, _ = driver.execute_query("MATCH ()-[l:REFERS_TO|ABOUT]->() RETURN count(l) AS n")
    assert records[0]["n"] == 2


@pytest.mark.neo4j
def test_a_record_document_links_to_its_record_by_key_never_by_title(driver):
    driver.execute_query(
        "CREATE (:Recall {recall_id: '16V074000', campaign: 'Piston Rings'}), "
        "(:Recall {recall_id: '16V075000', campaign: 'Brake Cables'}), "
        # a product with the record's display name: a title match would link the document here too
        "(:Product {product_id: 'P1', product_name: 'Piston Rings'}), "
        # an integer key: the document carries the key as text, so linking must compare toString(key)
        "(:Case {case_id: 123, case_name: 'Case 123'})"
    )
    docs = [
        Document(
            doc_id="record/Recall/16V074000",
            title="Piston Rings",
            text="# Piston Rings\n\n## summary\n\nThe engine may stall.",
            record=RecordRef(label="Recall", key_property="recall_id", key="16V074000"),
        ),
        Document(
            doc_id="record/Case/123",
            title="Case 123",
            text="# Case 123\n\nnotes",
            record=RecordRef(label="Case", key_property="case_id", key="123"),
        ),
    ]
    chunks = [
        Chunk(
            chunk_id="record/Recall/16V074000#0",
            doc_id="record/Recall/16V074000",
            index=0,
            text="The engine may stall.",
            context="Piston Rings",
        )
    ]
    write_lexical_graph(driver, docs, chunks)
    driver.execute_query(
        "MATCH (c:Chunk {chunk_id: 'record/Recall/16V074000#0'}) "
        "CREATE (:Observation {id: 'o1', predicate: 'MAY_STALL', chunk_id: c.chunk_id, "
        "evidence: 'The engine may stall.'})-[:FROM]->(c)"
    )

    plan = ConstructionPlan(
        nodes=[
            node("recalls.csv", "Recall", "recall_id", ["campaign"]),
            node("products.csv", "Product", "product_id", ["product_name"]),
            node("cases.csv", "Case", "case_id", ["case_name"]),
        ],
        relationships=[],
    )
    report = link_graphs(driver, plan)
    assert report.record_documents_linked == 2 and report.documents_linked == 2

    records, _, _ = driver.execute_query(
        "MATCH (:Document {doc_id: 'record/Recall/16V074000'})-[l:ABOUT]->(n) "
        "RETURN labels(n) AS labels, n.recall_id AS key, l.name AS name"
    )
    assert [(r["labels"], r["key"], r["name"]) for r in records] == [
        (["Recall"], "16V074000", "Piston Rings")
    ]

    # the claim extracted from the record's own text hangs on the record as its thing (R67 part 1)
    assert attach_observations(driver) == 1
    records, _, _ = driver.execute_query(
        "MATCH (r:Recall)-[:HAS_OBSERVATION]->(:Observation {id: 'o1'}) RETURN r.recall_id AS id"
    )
    assert [r["id"] for r in records] == ["16V074000"]


@pytest.mark.neo4j
def test_a_complaint_section_reaches_its_record_and_the_claim_hangs_on_both_things(driver):
    # the held-out shape: one document per vehicle, one "## Complaint <key>" section per complaint
    driver.execute_query(
        "CREATE (v:Vehicle {vehicle_id: 'V1', model: 'CIVIC'}), "
        "(:Complaint {complaint_id: '11440801'}), "
        "(d:Document {doc_id: 'civic.md', title: '2016_honda_civic_complaints'}), "
        "(c:Chunk {chunk_id: 'civic.md#0', doc_id: 'civic.md', index: 0, "
        "text: '## Complaint 11440801: brakes\\n\\nMy 2016 Honda Civic lost its brakes.'}), "
        "(c)-[:PART_OF]->(d), "
        # the full vehicle name, which no fuzzy threshold accepts against 'CIVIC' (entities_linked was 0
        # on held-out since R58): the containment rule must catch it inside the document's scope
        "(e:Entity {id: 'e1', name: '2016 Honda Civic', type: 'Vehicle', aliases: []}), "
        "(c)-[:MENTIONS]->(e), "
        "(o:Observation {id: 'o1', predicate: 'LOST', chunk_id: 'civic.md#0', "
        "evidence: 'My 2016 Honda Civic lost its brakes.'})-[:FROM]->(c), (o)-[:SUBJECT]->(e)"
    )
    plan = ConstructionPlan(
        nodes=[
            node("vehicles.csv", "Vehicle", "vehicle_id", ["model"]).model_copy(
                update={"name_column": "model"}
            ),
            node("complaints.csv", "Complaint", "complaint_id"),
        ],
        relationships=[],
    )

    report = link_graphs(driver, plan)
    assert report.chunks_linked == 1  # the section found its Complaint record by key
    assert report.entities_linked_by_containment == 1 and report.entities_linked == 1

    # one claim, two things (R67 decision): the vehicle of the document AND the complaint of the section
    assert attach_observations(driver) == 1
    records, _, _ = driver.execute_query(
        "MATCH (n)-[h:HAS_OBSERVATION]->(:Observation {id: 'o1'}) "
        "RETURN labels(n)[0] AS thing, h.name AS name ORDER BY thing"
    )
    assert [(r["thing"], r["name"]) for r in records] == [
        ("Complaint", "11440801"),
        ("Vehicle", "CIVIC"),
    ]

    # rerunning recomputes chunk links too, instead of adding to them
    link_graphs(driver, plan)
    records, _, _ = driver.execute_query("MATCH (:Chunk)-[l:ABOUT]->() RETURN count(l) AS n")
    assert records[0]["n"] == 1
