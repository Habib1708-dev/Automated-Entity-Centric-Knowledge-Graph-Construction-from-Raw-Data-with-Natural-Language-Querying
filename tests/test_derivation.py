"""Derived facts: sentence picking and the containing mention as pure functions, and (with Neo4j) the rule
that a named part is PART_OF the product its document is about: one fact per mention chunk, verbatim
evidence, the product's mention of the document reused (named exactly like the node, or in full, R60) or
created once per document (R75), idempotent, scored by the gold set and accepted by the validation checks."""

import pytest

from kgbuilder.core.identity import mention_id
from kgbuilder.core.text import pick_sentence, split_sentences
from kgbuilder.resolution.derivation import DERIVED_EXTRACTOR, Candidate, containing_mention, derive_facts
from kgbuilder.resolution.linking import attach_observations, link_graphs
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.text.subject_graph import mention_row, write_mentions, write_subject_graph
from kgbuilder.validation.checks.base import CheckContext
from kgbuilder.validation.evaluate import score_triples
from kgbuilder.validation.gold import GoldTriple
from kgbuilder.validation.paths import score_paths
from kgbuilder.validation.validator import validate_graph

from .sample_plans import node

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Product", description="a product", identity="keyed", record_labels=["Product"]),
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


def triple(subject, stype, predicate, obj, otype, chunk, evidence) -> Triple:
    return Triple(
        subject=subject, subject_type=stype, predicate=predicate, object=obj, object_type=otype,
        evidence=evidence, chunk_id=chunk,
    )  # fmt: skip


def mention(driver, entity_type: str, name: str, chunk_ids: list[str]) -> str:
    """Write a mention named in the given chunks without a claim (the extractor named it, no fact held)."""
    row = mention_row(entity_type, name, chunk_ids[0])
    write_mentions(driver, [row], {(c, row.id) for c in chunk_ids})
    return row.id


def test_pick_sentence_returns_the_first_verbatim_sentence_naming_the_entity():
    text = "Looks **nice**.\nThe Drawer Rails stick badly! Avoid.\n\n- @anna"
    assert (
        pick_sentence(text, ["drawer rails"]) == "The Drawer Rails stick badly!"
    )  # verbatim, not normalised
    assert pick_sentence(text, ["handles", "rails"]) == "The Drawer Rails stick badly!"  # any alias
    assert pick_sentence(text, ["handles"]) is None
    assert pick_sentence(text, [""]) is None


def test_an_initial_or_a_title_ends_no_sentence_and_a_line_break_does():
    """Found in R75: "Dr. J. Pike" was cut into three sentences, so no sentence named the person."""
    text = "Talk by Dr. J. Pike (Soil Ecology) on peat. Then tea!\nMr.\nNext line."
    assert split_sentences(text) == [
        "Talk by Dr. J. Pike (Soil Ecology) on peat.",
        "Then tea!",
        "Mr.",
        "Next line.",
    ]
    assert pick_sentence(text, ["Dr. J. Pike"]) == "Talk by Dr. J. Pike (Soil Ecology) on peat."


def test_the_containing_mention_names_every_word_of_the_node_as_whole_words():
    full = Candidate(id="full", name="2019 Subaru Outback", chunks=3)
    short = Candidate(id="short", name="2019 OUTBACK", chunks=1)
    escaped = Candidate(id="escaped", name="escaped coolant", chunks=9)
    assert containing_mention("OUTBACK", [short, full]) == "full"  # the most mentioned wins
    assert containing_mention("Subaru Outback", [short, full]) == "full"  # every word, any order
    assert containing_mention("ESCAPE", [escaped]) is None  # a word inside another word is no match
    assert containing_mention("OUTBACK", []) is None
    assert containing_mention("", [full]) is None
    # a tie on chunks goes to the shorter name, then to the id: the same graph gives the same choice
    tied = Candidate(id="tied", name="Outback 2019", chunks=3)
    assert containing_mention("OUTBACK", [full, tied]) == "tied"
    twin = Candidate(id="a-twin", name="2019 Outback", chunks=3)
    assert containing_mention("OUTBACK", [tied, twin]) == "a-twin"


VEHICLE_PLAN = ConstructionPlan(
    nodes=[
        node("vehicles.csv", "Vehicle", "vehicle_id", ["model"]).model_copy(update={"name_column": "model"})
    ],
    relationships=[],
)
VEHICLE_SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Vehicle", description="a vehicle", identity="keyed", record_labels=["Vehicle"]),
        EntityType(name="Component", description="a part"),
    ],
    fact_types=[
        FactType(
            predicate="PART_OF",
            subject_type="Component",
            object_type="Vehicle",
            description="y",
            derived=True,
        )
    ],
)


@pytest.mark.neo4j
def test_the_vehicle_the_text_names_in_full_is_the_target_not_a_second_mention(driver):
    driver.execute_query(
        "CREATE (v:Vehicle {vehicle_id: 'V1', model: 'OUTBACK'}), "
        "(w:Vehicle {vehicle_id: 'V2', model: 'ESCAPE'}), "
        "(d:Document {doc_id: 's.md', title: 'subaru_outback_complaints'})-[:ABOUT]->(v), "
        "(:Chunk {chunk_id: 's.md#0', text: 'The 2019 Subaru Outback battery died.'})-[:PART_OF]->(d), "
        "(e:Document {doc_id: 'f.md', title: 'ford_escape_complaints'})-[:ABOUT]->(w), "
        "(:Chunk {chunk_id: 'f.md#0', text: 'My old Outback never did this.'})-[:PART_OF]->(e)"
    )
    battery = mention(driver, "Component", "battery", ["s.md#0"])
    car = mention(driver, "Vehicle", "2019 Subaru Outback", ["s.md#0"])
    # a vehicle mention containing OUTBACK, but in a document about another vehicle: not a candidate
    mention(driver, "Vehicle", "old Outback", ["f.md#0"])

    report = derive_facts(driver, VEHICLE_SCHEMA, VEHICLE_PLAN)
    assert report.facts_derived == 1 and report.mentions_created == 0 and report.targets_by_containment == 1
    records, _, _ = driver.execute_query(
        "MATCH (:Mention {id: $s})<-[:SUBJECT]-(:Observation {predicate: 'PART_OF'})-[:OBJECT]->(o:Mention) "
        "RETURN o.id AS id",
        s=battery,
    )
    assert [r["id"] for r in records] == [car]
    # a rerun picks the same mention and adds nothing
    again = derive_facts(driver, VEHICLE_SCHEMA, VEHICLE_PLAN)
    assert again.facts_derived == 1 and again.mentions_created == 0
    assert driver.execute_query("MATCH (o:Observation) RETURN count(o) AS n")[0][0]["n"] == 1


@pytest.mark.neo4j
def test_the_product_named_exactly_like_its_node_is_reused_once_per_document(driver):
    """R75: a mention belongs to one document, so the product of each review file is its own mention; the
    identity stage later finds that both are the one record."""
    driver.execute_query(
        "CREATE (p:Product {product_id: 'P1', product_name: 'Västerås Bookshelf'}), "
        "(d:Document {doc_id: 'v.md', title: 'vasteras_bookshelf_reviews'})-[:ABOUT]->(p), "
        "(e:Document {doc_id: 'w.md', title: 'vasteras_bookshelf_notes'})-[:ABOUT]->(p), "
        "(:Chunk {chunk_id: 'v.md#0', text: 'The Västerås Bookshelf shelves sag.'})-[:PART_OF]->(d), "
        "(:Chunk {chunk_id: 'w.md#0', text: 'The shelves bow.'})-[:PART_OF]->(e)"
    )
    mention(driver, "Component", "shelves", ["v.md#0"])
    mention(driver, "Component", "shelves", ["w.md#0"])
    named = mention(driver, "Product", "Västerås Bookshelf", ["v.md#0"])
    report = derive_facts(driver, SCHEMA, PLAN)
    assert report.facts_derived == 2 and report.mentions_created == 1  # only w.md had no product mention
    records, _, _ = driver.execute_query(
        "MATCH (:Observation {predicate: 'PART_OF'})-[:OBJECT]->(o:Mention) "
        "RETURN o.id AS id, o.doc_id AS doc ORDER BY doc"
    )
    assert [(r["id"], r["doc"]) for r in records] == [
        (named, "v.md"),
        (mention_id("Product", "Västerås Bookshelf", "w.md"), "w.md"),
    ]


@pytest.mark.neo4j
def test_a_named_part_is_part_of_the_product_its_document_is_about(driver):
    driver.execute_query(
        "CREATE (p:Product {product_id: 'P1', product_name: 'Helsingborg Dresser'}), "
        "(d:Document {doc_id: 'h.md', title: 'helsingborg_dresser_reviews'})-[:ABOUT]->(p), "
        "(:Chunk {chunk_id: 'h.md#0', text: 'Looks nice. The drawer rails stick badly! Avoid.'})"
        "-[:PART_OF]->(d), "
        "(:Chunk {chunk_id: 'h.md#1', text: 'The drawer rails are rough.'})-[:PART_OF]->(d), "
        # a document that is ABOUT nothing: its parts belong to no known product
        "(o:Document {doc_id: 'o.md', title: 'notes'}), "
        "(:Chunk {chunk_id: 'o.md#0', text: 'legs wobble'})-[:PART_OF]->(o)"
    )
    stick = triple(
        "drawer rails", "Component", "EXHIBITS_FAILURE", "stick", "FailureMode", "h.md#0",
        "The drawer rails stick badly!",
    )  # fmt: skip
    write_subject_graph(driver, [stick], extractor="test")
    mention(driver, "Component", "drawer rails", ["h.md#1"])
    # named in h.md#1 by a name its text does not contain (it came through the document context)
    mention(driver, "Component", "handles", ["h.md#1"])
    mention(driver, "Component", "legs", ["o.md#0"])

    report = derive_facts(driver, SCHEMA, PLAN)
    assert report.model_dump() == {
        "facts_derived": 2,
        "mentions_created": 1,
        "skipped_no_evidence": 1,
        "targets_by_containment": 0,
    }
    records, _, _ = driver.execute_query(
        "MATCH (s:Mention)<-[:SUBJECT]-(f:Observation {predicate: 'PART_OF'})-[:OBJECT]->(o:Mention), "
        "(f)-[:FROM]->(:Chunk {chunk_id: f.chunk_id}) "
        "RETURN s.name AS s, o.name AS o, o.type AS t, f.chunk_id AS c, f.evidence AS e, "
        "f.extractor AS x ORDER BY c"
    )
    assert [r.data() for r in records] == [
        {"s": "drawer rails", "o": "Helsingborg Dresser", "t": "Product", "c": "h.md#0",
         "e": "The drawer rails stick badly!", "x": DERIVED_EXTRACTOR},
        {"s": "drawer rails", "o": "Helsingborg Dresser", "t": "Product", "c": "h.md#1",
         "e": "The drawer rails are rough.", "x": DERIVED_EXTRACTOR},
    ]  # fmt: skip
    mentions = driver.execute_query(
        "MATCH (:Chunk)-[:MENTIONS]->(o:Mention {name: 'Helsingborg Dresser'}) RETURN count(*) AS n"
    )[0][0]["n"]
    assert mentions == 2

    # a rerun (kg link recomputes) adds nothing
    again = derive_facts(driver, SCHEMA, PLAN)
    assert again.facts_derived == 2 and again.mentions_created == 0
    count = "MATCH (o:Observation {predicate: 'PART_OF'}) RETURN count(o) AS n"
    assert driver.execute_query(count)[0][0]["n"] == 2

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


@pytest.mark.neo4j
def test_a_shared_part_no_longer_carries_one_products_defect_to_another(driver):
    """The R62 audit's leak, end to end (R64): both reviews name the drawer rails, only the dresser's says
    they stick. In the edge graph the bed reached "stick" through the shared rails node; with observations
    the bed has the rails and nothing the dresser said about them."""
    dresser, bed = "Helsingborg Dresser", "Linköping Bed"
    driver.execute_query(
        "CREATE (:Product {product_id: 'P1', product_name: $dresser}), "
        "(:Product {product_id: 'P2', product_name: $bed})",
        dresser=dresser,
        bed=bed,
    )
    documents = [
        Document(doc_id="h.md", title="helsingborg_dresser_reviews", text="x"),
        Document(doc_id="l.md", title="linköping_bed_reviews", text="x"),
    ]
    chunks = [
        Chunk(chunk_id="h.md#0", doc_id="h.md", index=0, text="The drawer rails stick badly."),
        Chunk(chunk_id="l.md#0", doc_id="l.md", index=0, text="The drawer rails glide well."),
    ]
    write_lexical_graph(driver, documents, chunks)
    stick = triple(
        "drawer rails", "Component", "EXHIBITS_FAILURE", "stick", "FailureMode", "h.md#0",
        "The drawer rails stick badly.",
    )  # fmt: skip
    write_subject_graph(driver, [stick], extractor="test")
    mention(driver, "Component", "drawer rails", ["l.md#0"])  # the bed review names the rails, no claim

    link_graphs(driver, PLAN)
    derive_facts(driver, SCHEMA, PLAN)
    assert attach_observations(driver) == 3  # the failure and one derived PART_OF per product
    assert attach_observations(driver) == 3  # recomputed, not added to

    def claims(product: str) -> list[str]:
        records, _, _ = driver.execute_query(
            "MATCH (:Product {product_name: $p})-[:HAS_OBSERVATION]->(o:Observation)-[:OBJECT]->(t:Mention) "
            "RETURN o.predicate + ' ' + t.name AS claim ORDER BY claim",
            p=product,
        )
        return [r["claim"] for r in records]

    assert claims(dresser) == ["EXHIBITS_FAILURE stick", f"PART_OF {dresser}"]
    assert claims(bed) == [f"PART_OF {bed}"]
    report = score_paths(CheckContext(driver=driver).facts, SCHEMA)
    assert (report.paths_total, report.paths_true) == (1, 1)
