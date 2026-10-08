"""Node evidence and claim sentences read from Neo4j (R118, hybrid/unit_sources.py), and `kg units` end to
end, on the shared hand-made graph (tests/graphs.py) with a few claims added: the press's claim stated once
and denied once (one claim with both counts), a conditional claim, a claim of another predicate, an
individual holding a hedged claim, and three more parts so the press is a hub. Covers: record titles, keys
and plan columns only (the name and prose columns left out), names its mentions write, relationships grouped
with the names capped in the database, the claims a node holds grouped and counted, each predicate's best
first, the claims a concept or an individual is an end of,
the claim's chunk from its FROM edge, defaults for a claim without truth fields, one sentence per claim,
and the stage's params, metrics and units file. Needs Neo4j."""

import json

from kgbuilder.config import Settings
from kgbuilder.hybrid import EvidenceCaps, read_claim_sentences, read_evidence
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import UnitsStage
from kgbuilder.pipeline.stage import PLAN_FILE

from .fakes import RecordingTracker
from .graphs import PLAN, build
from .sample_plans import rel

RELATED_PLAN = PLAN.model_copy(
    update={
        "relationships": [
            rel("parts.csv", "PART_OF", "Part", "part_id", "Press", "press_id"),
            rel("parts.csv", "MADE_BY", "Part", "part_id", "Maker", "maker_id"),
            rel("tickets.csv", "CONCERNS", "Ticket", "ticket_id", "Press", "press_id"),
        ]
    }
)

# o1 (the shared graph's claim, no truth fields: affirmed and actual) is denied by o2 in another chunk; o3 is
# the same triple under a condition; o5, another predicate, names the maker; Ada, an individual, holds a
# hedged claim o4 about the same concept
MORE = """
MATCH (press:Press {press_id: 'P1'}), (maker:Maker {maker_id: 'M1'}), (spindle:Mention {id: 'm-spindle'}),
      (wobbles:Mention {id: 'm-wobbles'}), (c1:Chunk {chunk_id: 'notes.md#0'}),
      (c2:Chunk {chunk_id: 'notes.md#1'}), (c5:Chunk {chunk_id: 'log.md#1'})
CREATE (o2:Observation {id: 'o2', predicate: 'HAS_CONDITION', truth: 'negated', triple_truth: 'negated',
                        modality: 'actual', chunk_id: 'notes.md#1'}),
       (o2)-[:SUBJECT]->(spindle), (o2)-[:OBJECT]->(wobbles), (o2)-[:FROM]->(c2),
       (press)-[:HAS_OBSERVATION]->(o2),
       (o3:Observation {id: 'o3', predicate: 'HAS_CONDITION', modality: 'conditional',
                        condition: 'when cold'}),
       (o3)-[:SUBJECT]->(spindle), (o3)-[:OBJECT]->(wobbles), (o3)-[:FROM]->(c1),
       (press)-[:HAS_OBSERVATION]->(o3),
       (norcast:Mention {id: 'm-norcast', name: 'Norcast', type: 'Maker', doc_id: 'notes.md'})
         -[:REFERS_TO {canonical: 'Maker:M1', name: 'Norcast', kind: 'record'}]->(maker),
       (o5:Observation {id: 'o5', predicate: 'MADE_BY'}),
       (o5)-[:SUBJECT]->(spindle), (o5)-[:OBJECT]->(norcast), (o5)-[:FROM]->(c1),
       (press)-[:HAS_OBSERVATION]->(o5),
       (ada:Individual {id: 'i-ada', name: 'Ada Brook', type: 'Person'}),
       (am:Mention {id: 'm-ada', name: 'Ada', type: 'Person', doc_id: 'log.md'})
         -[:REFERS_TO {canonical: 'i-ada', name: 'Ada Brook', kind: 'individual'}]->(ada),
       (o4:Observation {id: 'o4', predicate: 'REPORTS', modality: 'possible', hedge: 'may'}),
       (o4)-[:SUBJECT]->(am), (o4)-[:OBJECT]->(wobbles), (o4)-[:FROM]->(c5), (ada)-[:HAS_OBSERVATION]->(o4),
       (:Part {part_id: 'S2', name: 'Gear'})-[:PART_OF]->(press),
       (:Part {part_id: 'S3', name: 'Pin'})-[:PART_OF]->(press),
       (:Part {part_id: 'S4', name: 'Wheel'})-[:PART_OF]->(press)
"""


def load(driver) -> None:
    build(driver)
    driver.execute_query(MORE)


def test_each_node_gets_its_evidence_records_first_then_individuals_then_concepts(driver):
    load(driver)
    evidence = read_evidence(driver, RELATED_PLAN, EvidenceCaps(names=2, claims=3), {"Ticket": {"ticket_id"}})
    by_ref = {e.ref: e for e in evidence}
    assert [e.ref for e in evidence][:2] == ["Press:P1", "Press:P2"] and evidence[-1].ref == "k-wobble"
    assert [e.kind for e in evidence].index("individual") > [e.kind for e in evidence].index("record")
    press = by_ref["Press:P1"]
    # the key and the plan's other columns, never the name; columns the plan does not import stay out
    assert (press.title, press.label, press.properties) == ("Quill Press", "Press", {"press_id": "P1"})
    rels = [(n.type, n.outgoing, n.label, n.count, n.names) for n in press.relations]
    # the names sorted and cut to the cap in the database; the count keeps every part
    assert rels == [
        ("CONCERNS", False, "Ticket", 1, ["T-1"]),
        ("PART_OF", False, "Part", 4, ["Gear", "Pin"]),
    ]
    assert by_ref["Part:S1"].relations[0].names == ["Norcast"] and by_ref["Part:S1"].aliases == []
    ticket = by_ref["Ticket:T-1"]
    assert ticket.title == "T-1" and ticket.properties == {}  # titled by its key; its column excluded


def test_a_nodes_claims_are_grouped_by_triple_with_their_counts_each_predicates_best_first(driver):
    load(driver)
    evidence = read_evidence(driver, RELATED_PLAN, EvidenceCaps(names=5, claims=3), {})
    press = {e.ref: e for e in evidence}["Press:P1"]
    # o1 (no truth fields: affirmed) and o2 (denied) are one claim, the best supported, read from o1's chunk
    first = press.claims[0]
    assert (first.id, first.stated, first.denied, first.chunk_id) == ("o1", 1, 1, "notes.md#0")
    assert first.sentence == "Spindle (Component) has condition wobbles (Condition)"
    # then the best of the next predicate (o5), before the condition's own claim (o3): by support and
    # sentence alone o3 would come second
    assert [c.id for c in press.claims] == ["o1", "o5", "o3"] and press.claims_total == 3
    assert (press.claims[2].modality, press.claims[2].condition) == ("conditional", "when cold")
    capped = {e.ref: e for e in read_evidence(driver, RELATED_PLAN, EvidenceCaps(names=5, claims=1), {})}
    assert [c.id for c in capped["Press:P1"].claims] == ["o1"] and capped["Press:P1"].claims_total == 3


def test_individuals_and_concepts_get_their_names_held_claims_and_the_claims_they_are_an_end_of(driver):
    load(driver)
    by_ref = {e.ref: e for e in read_evidence(driver, RELATED_PLAN, EvidenceCaps(names=5, claims=3), {})}
    ada, wobble = by_ref["i-ada"], by_ref["k-wobble"]
    assert (ada.kind, ada.label, ada.title, ada.aliases) == ("individual", "Person", "Ada Brook", ["Ada"])
    assert [(c.sentence, c.modality, c.hedge) for c in ada.claims] == [
        ("Ada Brook (Person) reports wobbles (Condition)", "possible", "may")
    ]
    assert [(n.type, n.outgoing, n.label, n.names) for n in ada.relations] == [
        ("REPORTS", True, "Condition", ["wobbles"])
    ]
    # a concept holds no claim; it is the object of the spindle's claims (three observations, one other end)
    # and of Ada's
    assert (wobble.kind, wobble.aliases, wobble.claims, wobble.claims_total) == (
        "concept",
        ["wobbling"],
        [],
        0,
    )
    assert [(n.type, n.outgoing, n.label, n.count, n.names) for n in wobble.relations] == [
        ("HAS_CONDITION", False, "Component", 1, ["Spindle"]),
        ("REPORTS", False, "Person", 1, ["Ada Brook"]),
    ]


def test_every_claim_gets_one_sentence_from_its_canonical_ends(driver):
    load(driver)
    sentences = read_claim_sentences(driver)
    assert [s.id for s in sentences] == ["o1", "o2", "o3", "o4", "o5"]
    assert sentences[0].text == "Spindle (Component) has condition wobbles (Condition)"
    assert sentences[4].text == "Spindle (Component) made by Norcast (Maker)"
    assert sentences[1].chunk_id == "notes.md#1" and sentences[3].chunk_id == "log.md#1"


def test_kg_units_writes_every_card_and_claim_sentence_and_logs_what_it_rendered(driver, tmp_path):
    load(driver)
    out = tmp_path / "build"
    out.mkdir()
    (out / PLAN_FILE).write_text(RELATED_PLAN.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    settings = Settings(index_card_names=2, index_card_claims=1, index_card_max_chars=1500)
    ctx = PipelineContext(settings=settings, driver=driver, out=out, tracker=tracker)
    run_stages(ctx, PipelineState(), [UnitsStage("template")])
    run = tracker.run("units")
    assert run.logged_params["cards"] == "template" and len(run.logged_params["graph_digest"]) == 12
    assert (run.logged_params["index_card_names"], run.logged_params["index_card_claims"]) == (2, 1)
    m = run.logged_metrics
    # 8 records (2 presses, 4 parts, a maker, a ticket), Ada, the concept; 5 claim sentences; the press's
    # two other claims left out by the claims cap of 1
    assert (m["cards"], m["cards_record"], m["cards_individual"], m["cards_concept"]) == (10, 8, 1, 1)
    assert (m["claims"], m["claims_skipped"], m["cards_truncated"]) == (5, 2, 0)
    lines = [
        json.loads(line) for line in (out / UnitsStage.UNITS_FILE).read_text(encoding="utf-8").splitlines()
    ]
    assert [line["unit"] for line in lines] == ["card"] * 10 + ["claim"] * 5
    press = next(line for line in lines if line.get("ref") == "Press:P1")
    assert press["text"].splitlines()[2:] == [
        "CONCERNS <- Ticket (1): T-1",
        "PART_OF <- Part (4): Gear, Pin (+2 more)",
        "Claims (1 of 3):",
        "- Spindle (Component) has condition wobbles (Condition) [stated 1, denied 1]",
    ]
