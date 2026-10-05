"""Attachment (R76, layered-model Step 6): which records and individuals each claim is about.

Pure tests of the four routes, the precedence per kind of thing, a claim naming two things, and the text
route that makes a document with a neutral file name about the record its sentences name most. With Neo4j:
an incident report under a neutral file name, end to end through identity and attachment (the G07 shape: the
seal's failure is a claim about the pump), the identity scope that ignores the text link, idempotence, path
truth by the new rule and the old one, and a query plan reaching a claim through an individual it hangs on.
"""

import pytest

from kgbuilder.query.plan_cypher import find_claims
from kgbuilder.resolution.attachment import (
    Attachment,
    ClaimSource,
    Particular,
    Thing,
    attach,
    attach_claims,
    choose,
    dominant_record,
    named_in_quote,
    part_of_wholes,
)
from kgbuilder.resolution.identity import IdentitySettings, resolve_identity
from kgbuilder.resolution.linking import link_graphs
from kgbuilder.resolution.mentions import read_mentions
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.text.subject_graph import write_subject_graph
from kgbuilder.validation.checks.base import CheckContext
from kgbuilder.validation.paths import score_paths

from .sample_plans import node

PUMP_A = Thing(element_id="p1", name="HP40-1183", kind="record:Pump")
PUMP_B = Thing(element_id="p2", name="HP40-2291", kind="record:Pump")
ROSA = Thing(element_id="s1", name="Rosa Delgado", kind="record:Staff")
MARIA = Thing(element_id="s2", name="Maria Lopez", kind="record:Staff")
COMPLAINT = Thing(element_id="c1", name="11440801", kind="record:Complaint")
VEHICLE = Thing(element_id="v1", name="CIVIC", kind="record:Vehicle")
WELL = Thing(element_id="i1", name="dry well", kind="individual:Place")


def particular(mention: str, thing: Thing, *names: str, doc: str = "d.md") -> Particular:
    return Particular(mention=mention, doc_id=doc, names=list(names or [thing.name]), thing=thing)


def claim(cid: str, quote: str, subject: str = "s", obj: str = "o", doc: str = "d.md", part_of: bool = False):
    return ClaimSource(
        id=cid, doc_id=doc, chunk_id=f"{doc}#0", quote=quote, subject=subject, object=obj, part_of=part_of
    )


def found(attachments: list[Attachment]) -> list[tuple[str, str, str]]:
    return [(a.thing.name, a.how, a.evidence) for a in attachments]


def test_a_name_in_the_claims_own_quote_attaches_the_claim_to_its_thing():
    # G07's sentence: the claim's subject is the seal, the thing it is about the pump the quote names
    seal_failed = claim(
        "o1", "On 29 March the mechanical seal of pump HP40-1183 failed during the night shift."
    )
    particulars = [
        particular("m1", PUMP_A, "pump HP40-1183", "HP40-1183"),  # the mention's wording first, then the key
        particular("m2", PUMP_B),
        particular("m3", ROSA, doc="other.md"),  # a thing of another document is never looked for
    ]
    assert found(named_in_quote(seal_failed, particulars)) == [
        ("HP40-1183", "key_in_sentence", "pump HP40-1183")
    ]
    # whole words only, and a name too short to trust is never looked for
    assert named_in_quote(claim("o2", "The HP40-11834 unit"), particulars) == []
    short = Thing(element_id="x", name="A1", kind="record:Pump")
    assert named_in_quote(claim("o3", "Valve A1 leaked."), [particular("m4", short)]) == []


def test_a_claim_about_a_part_hangs_on_the_whole_its_document_says_it_belongs_to():
    worn = claim("o1", "The mechanical seal was worn.", subject="seal@d")
    part = claim("o2", "the mechanical seal of pump HP40-1183", subject="seal@d", obj="pump@d", part_of=True)
    # the same wording in another document is another mention: its part-of claim says nothing about this one
    elsewhere = claim("o3", "x", subject="seal@e", obj="pump@e", doc="e.md", part_of=True)
    particular_of = {
        "pump@d": particular("pump@d", PUMP_A),
        "pump@e": particular("pump@e", PUMP_B, doc="e.md"),
    }
    assert found(part_of_wholes(worn, [part, elsewhere], particular_of)) == [
        ("HP40-1183", "part_of", "the mechanical seal of pump HP40-1183")
    ]
    # a whole that is a kind (no particular) holds nothing
    assert part_of_wholes(worn, [part], {}) == []


def test_the_most_specific_route_wins_per_kind_of_thing():
    def a(thing: Thing, how: str) -> Attachment:
        return Attachment(observation="o1", thing=thing, how=how, evidence="e")

    # a quote naming one staff member replaces the staff member the whole document is about
    assert found(choose([a(MARIA, "document"), a(ROSA, "key_in_sentence")])) == [
        ("Rosa Delgado", "key_in_sentence", "e")
    ]
    # a section's complaint and the document's vehicle are two kinds: the claim keeps both (R67's decision)
    assert found(choose([a(VEHICLE, "document"), a(COMPLAINT, "section")])) == [
        ("11440801", "section", "e"),
        ("CIVIC", "document", "e"),
    ]
    # one thing found by two routes is attached once, by the more specific route
    assert found(choose([a(PUMP_A, "document"), a(PUMP_A, "part_of")])) == [("HP40-1183", "part_of", "e")]
    # an individual is a kind of its own: it never displaces a record of the same type's name
    assert len(choose([a(PUMP_A, "document"), a(WELL, "key_in_sentence")])) == 2


def test_a_claim_naming_two_things_hangs_on_both():
    both = claim("o1", "Flow dropped until HP40-2291 took over from HP40-1183.")
    attachments = attach([both], [particular("m1", PUMP_A), particular("m2", PUMP_B)], {}, {"d.md": [ROSA]})
    assert sorted(found(attachments)) == [
        ("HP40-1183", "key_in_sentence", "HP40-1183"),
        ("HP40-2291", "key_in_sentence", "HP40-2291"),
        # the document's staff member is another kind: the quote does not displace it
        ("Rosa Delgado", "document", "d.md"),
    ]


def test_section_and_document_links_attach_what_the_quote_does_not_name():
    quiet = claim("o1", "The brakes failed.")
    attachments = attach([quiet], [], {"d.md#0": [COMPLAINT]}, {"d.md": [VEHICLE]})
    assert found(attachments) == [("11440801", "section", "d.md#0"), ("CIVIC", "document", "d.md")]


def test_a_document_is_about_the_record_its_sentences_clearly_name_most():
    sentences = [
        "On 29 March the mechanical seal of pump HP40-1183 failed.",
        "Flow dropped until HP40-2291 was switched to duty.",
        "The seal of HP40-1183 was replaced on 31 March.",
        "The dry well was pumped out.",
    ]
    particulars = [particular("m1", PUMP_A), particular("m2", PUMP_B), particular("m3", WELL)]
    thing, evidence = dominant_record(sentences, particulars)
    assert (thing.name, evidence) == ("HP40-1183", "named in 2 of 4 sentences; the next record in 1")
    # a near tie means the document is about several things; one sentence is a passing mention
    assert dominant_record(sentences + ["HP40-2291 ran all night."], particulars) is None
    assert dominant_record(sentences[:1], particulars) is None
    # individuals are no candidates: only records the mentions link to
    assert dominant_record(["The dry well.", "The dry well again."], [particular("m3", WELL)]) is None


# --- end to end, with Neo4j ---------------------------------------------------------------------------

PLAN = ConstructionPlan(nodes=[node("pumps.csv", "Pump", "serial_number", ["model"])], relationships=[])
SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Pump", description="d", identity="keyed", record_labels=["Pump"]),
        EntityType(name="Equipment", description="d"),
        EntityType(name="Place", description="d", identity="individual"),
        EntityType(name="State", description="d"),
    ],
    fact_types=[
        FactType(
            predicate="COMPONENT_OF", subject_type="Equipment", object_type="Pump", description="d",
            part_of=True,
        ),
        FactType(predicate="HAS_STATE", subject_type="Equipment", object_type="State", description="d"),
        FactType(predicate="HAS_STATE", subject_type="Pump", object_type="State", description="d"),
        FactType(predicate="HAS_STATE", subject_type="Place", object_type="State", description="d"),
    ],
)  # fmt: skip
DOC = "water/notes_0329.md"  # a neutral file name: no pump's name is in it
SENTENCES = [
    "On 29 March the mechanical seal of pump HP40-1183 failed.",
    "The mechanical seal was worn.",
    "The dry well flooded.",
    "Water from the dry well reached the motor.",
    "Flow dropped until HP40-2291 was switched to duty.",
    "The seal of HP40-1183 was replaced on 31 March.",
]
CLAIMS = [
    # (subject, type, predicate, object, type, sentence index)
    ("mechanical seal", "Equipment", "COMPONENT_OF", "HP40-1183", "Pump", 0),
    ("mechanical seal", "Equipment", "HAS_STATE", "failed", "State", 0),
    ("mechanical seal", "Equipment", "HAS_STATE", "worn", "State", 1),
    ("dry well", "Place", "HAS_STATE", "flooded", "State", 2),
    ("motor", "Equipment", "HAS_STATE", "wet", "State", 3),
    ("HP40-2291", "Pump", "HAS_STATE", "on duty", "State", 4),
    ("seal", "Equipment", "HAS_STATE", "replaced", "State", 5),
]


def write_incident(driver) -> None:
    driver.execute_query(
        "CREATE (:Pump {serial_number: 'HP40-1183', model: 'Hydra P-40'}), "
        "(:Pump {serial_number: 'HP40-2291', model: 'Hydra P-40'})"
    )
    text = " ".join(SENTENCES)
    write_lexical_graph(
        driver,
        [Document(doc_id=DOC, title="notes_0329", text=text)],
        [Chunk(chunk_id=f"{DOC}#0", doc_id=DOC, index=0, text=text)],
    )
    triples = [
        Triple(
            subject=s, subject_type=st, predicate=p, object=o, object_type=ot, evidence=SENTENCES[i],
            chunk_id=f"{DOC}#0",
        )
        for s, st, p, o, ot, i in CLAIMS
    ]  # fmt: skip
    write_subject_graph(driver, triples, extractor="test")


def attachments_of(driver) -> list[tuple[str, str, str]]:
    records, _, _ = driver.execute_query(
        "MATCH (n)-[h:HAS_OBSERVATION]->(o:Observation)-[:OBJECT]->(t:Mention) "
        "RETURN t.name AS claim, h.name AS thing, h.how AS how ORDER BY claim, thing"
    )
    return [(r["claim"], r["thing"], r["how"]) for r in records]


@pytest.mark.neo4j
def test_a_report_under_a_neutral_file_name_is_attached_by_its_text(driver):
    write_incident(driver)
    assert link_graphs(driver, PLAN).documents_linked == 0  # the file name names no pump
    resolve_identity(
        driver, SCHEMA, PLAN, None, "m", IdentitySettings(auto_merge=92, borderline=80, link_threshold=90)
    )

    report = attach_claims(driver, PLAN, SCHEMA)
    assert (report.documents_about_by_text, report.documents_about_nothing) == (1, 0)
    records, _, _ = driver.execute_query(
        "MATCH (:Document {doc_id: $doc})-[l:ABOUT]->(p:Pump) RETURN p.serial_number AS key, l.how AS how, "
        "l.evidence AS evidence",
        doc=DOC,
    )
    assert [r.data() for r in records] == [
        {"key": "HP40-1183", "how": "text", "evidence": "named in 2 of 6 sentences; the next record in 1"}
    ]
    assert attachments_of(driver) == [
        ("HP40-1183", "HP40-1183", "key_in_sentence"),  # the part-of claim itself names the pump
        ("failed", "HP40-1183", "key_in_sentence"),  # G07: the seal's failure is a claim about the pump
        ("flooded", "HP40-1183", "document"),  # the pump of the document, and the place the quote names
        ("flooded", "dry well", "key_in_sentence"),
        ("on duty", "HP40-2291", "key_in_sentence"),  # the other pump, named: it replaces the document's
        ("replaced", "HP40-1183", "key_in_sentence"),
        ("wet", "HP40-1183", "document"),
        ("wet", "dry well", "key_in_sentence"),  # named in the quote, though no end of the claim
        ("worn", "HP40-1183", "part_of"),  # no name in the quote: the seal is a part of the pump
    ]
    assert report.by_route == {"key_in_sentence": 6, "part_of": 1, "section": 0, "document": 2}
    assert (report.observations_total, report.observations_attached) == (7, 7)

    # the identity stage's scope ignores the link it rests on, so a rerun of resolve decides the same
    assert all(m.anchors == [] for m in read_mentions(driver))
    # recomputed, not added to
    assert attach_claims(driver, PLAN, SCHEMA) == report
    count = "MATCH ()-[l:ABOUT|HAS_OBSERVATION]->() RETURN count(l) AS n"
    assert driver.execute_query(count)[0][0]["n"] == 1 + 9

    # path truth: every route's evidence holds; the observation graph's rule counts only the document's pump.
    # The part-of claim (subject type to whole type) is a claim here, since the schema derives nothing.
    paths = score_paths(CheckContext(driver=driver).facts, SCHEMA)
    assert (paths.paths_total, paths.paths_true, paths.paths_true_about) == (9, 9, 6)

    # a query plan about the place reaches the claim that hangs on it without naming it at either end
    [well], _, _ = driver.execute_query("MATCH (i:Individual {name: 'dry well'}) RETURN i.id AS id")
    cypher, params = find_claims(None, [well["id"]], None, None, None, None, None, 10)
    records, _, _ = driver.execute_query(
        cypher.replace("RETURN DISTINCT o.id AS id", "RETURN DISTINCT o.predicate AS p, o.object_name AS o"),
        **params,
    )
    assert sorted(r["o"] for r in records) == ["flooded", "wet"]
