"""Linking: the pure matching functions that decide ABOUT links, and (with Neo4j) scoped linking end to end
together with the identity and attach stages (R75, R76): a generic part name in a review refers to the part
of the product the review is about, and the claim, attached to that product (HAS_OBSERVATION), leads to the
supplier of the product's part. Mention-to-record matching itself is tested in test_records.py, the
attachment routes in test_attachment.py."""

import pytest

from kgbuilder.resolution.attachment import attach_claims
from kgbuilder.resolution.identity import IdentitySettings, resolve_identity
from kgbuilder.resolution.linking import (
    DomainNode,
    RecordKey,
    link_graphs,
    match_chunk_records,
    match_document,
)
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document, RecordRef
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import EntityType, TextSchema

from .sample_plans import node, rel

SETTINGS = IdentitySettings(auto_merge=92, borderline=80, link_threshold=90)


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


# the review's parts are records of the plan's assemblies; its defects are kinds
LINK_SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Component", description="d", identity="keyed", record_labels=["Assembly"]),
        EntityType(name="Defect", description="d"),
    ],
    fact_types=[],
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
        "<-[:PART_OF]-(c:Chunk {chunk_id: 'chair_reviews.md#0', text: 'the legs are wobbly'}), "
        "(c)-[:MENTIONS]->(legs:Mention {id: 'm1', name: 'legs', type: 'Component', "
        "doc_id: 'chair_reviews.md'}), "
        "(c)-[:MENTIONS]->(wobble:Mention {id: 'm2', name: 'wobbly', type: 'Defect', "
        "doc_id: 'chair_reviews.md'}), "
        "(o:Observation {id: 'o1', predicate: 'HAS_DEFECT', chunk_id: 'chair_reviews.md#0', "
        "evidence: 'the legs are wobbly'}), "
        "(o)-[:SUBJECT]->(legs), (o)-[:OBJECT]->(wobble), (o)-[:FROM]->(c)"
    )

    report = link_graphs(driver, LINK_PLAN)
    assert report.documents_linked == 1
    identity = resolve_identity(driver, LINK_SCHEMA, LINK_PLAN, None, "m", SETTINGS)
    [legs] = [a for a in identity.assignments if a.said == "legs"]
    assert (legs.kind, legs.reason, legs.name) == ("record", "name", "Legs")
    # the claim hangs on the chair (its document) and on the chair's legs (named in its quote): two kinds
    attached = attach_claims(driver, LINK_PLAN, LINK_SCHEMA)
    assert (attached.observations_attached, attached.by_route) == (
        1,
        {"key_in_sentence": 1, "part_of": 0, "section": 0, "document": 1},
    )

    # the root-cause question: the product the claim hangs on, then that product's part and its supplier
    records, _, _ = driver.execute_query(
        "MATCH (product)-[:HAS_OBSERVATION]->(o:Observation {predicate: 'HAS_DEFECT'}), "
        "(part:Mention)<-[:SUBJECT]-(o)-[:OBJECT]->(:Mention {name: 'wobbly'}) "
        "MATCH (part)-[:REFERS_TO]->(a:Assembly)-[:USED_IN]->(product) "
        "MATCH (a)-[:SUPPLIED_BY]->(s:Supplier) RETURN s.name AS supplier"
    )
    assert [r["supplier"] for r in records] == ["Nordic Wood"]

    # rerunning recomputes the links instead of adding to them
    link_graphs(driver, LINK_PLAN)
    resolve_identity(driver, LINK_SCHEMA, LINK_PLAN, None, "m", SETTINGS)
    records, _, _ = driver.execute_query("MATCH ()-[l:REFERS_TO|ABOUT]->() RETURN count(l) AS n")
    assert records[0]["n"] == 3  # the document's ABOUT and one identity edge per mention


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
    assert attach_claims(driver, plan, None).observations_attached == 1
    records, _, _ = driver.execute_query(
        "MATCH (r:Recall)-[:HAS_OBSERVATION]->(:Observation {id: 'o1'}) RETURN r.recall_id AS id"
    )
    assert [r["id"] for r in records] == ["16V074000"]


@pytest.mark.neo4j
def test_a_complaint_section_reaches_its_record_and_the_claim_hangs_on_both_things(driver):
    # the held-out shape: one document per vehicle, one "## Complaint <key>" section per complaint
    driver.execute_query(
        "CREATE (v:Vehicle {vehicle_id: 'CIVIC', model: 'CIVIC'}), "
        "(:Complaint {complaint_id: '11440801'}), "
        "(d:Document {doc_id: 'civic.md', title: '2016_honda_civic_complaints'}), "
        "(c:Chunk {chunk_id: 'civic.md#0', doc_id: 'civic.md', index: 0, "
        "text: '## Complaint 11440801: brakes\\n\\nMy 2016 Honda Civic lost its brakes.'}), "
        "(c)-[:PART_OF]->(d), "
        # the full vehicle name, which no spelling rule accepts against 'CIVIC' (entities_linked was 0 on
        # held-out from R58 to R60). Since R75 the key rule finds it, as on held-out, where a vehicle's key
        # is its model; containment, which R60 added for it, no longer links at all (R95a)
        "(e:Mention {id: 'm1', name: '2016 Honda Civic', type: 'Vehicle', doc_id: 'civic.md'}), "
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
    schema = TextSchema(
        entity_types=[
            EntityType(name="Vehicle", description="d", identity="keyed", record_labels=["Vehicle"])
        ],
        fact_types=[],
    )
    [civic] = resolve_identity(driver, schema, plan, None, "m", SETTINGS).assignments
    assert (civic.kind, civic.reason, civic.name) == ("record", "key", "CIVIC")

    # one claim, two things (R67 decision, kept by R76's precedence per kind): the vehicle, which the
    # quote names (so the most specific route of its kind), AND the complaint of the section
    assert attach_claims(driver, plan, schema).observations_attached == 1
    records, _, _ = driver.execute_query(
        "MATCH (n)-[h:HAS_OBSERVATION]->(:Observation {id: 'o1'}) "
        "RETURN labels(n)[0] AS thing, h.name AS name, h.how AS how ORDER BY thing"
    )
    assert [(r["thing"], r["name"], r["how"]) for r in records] == [
        ("Complaint", "11440801", "section"),
        ("Vehicle", "CIVIC", "key_in_sentence"),
    ]

    # rerunning recomputes chunk links too, instead of adding to them
    link_graphs(driver, plan)
    records, _, _ = driver.execute_query("MATCH (:Chunk)-[l:ABOUT]->() RETURN count(l) AS n")
    assert records[0]["n"] == 1
