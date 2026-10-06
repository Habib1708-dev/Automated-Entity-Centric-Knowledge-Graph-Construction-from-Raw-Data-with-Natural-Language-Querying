"""The anchor graph's navigation contract (R90): W1-W5 and the composed walk in both arms, the witness of
every thing-to-thing hop, and the target gold placed on a snapshot's nodes.

The snapshot is invented (a desk lamp and a kettle, each with a part called "Switch") and holds one wrong
claim attachment, so the leak the anchor arm prevents is visible: arm B walks from the lamp into the
kettle's chunk through a claim, arm A cannot. No Neo4j, no LLM.
"""

import pytest

from kgbuilder.anchor import AnchorGraph, Arm, Context, TargetPlacer, record_nodes
from kgbuilder.audit.inputs import Record, Relation
from kgbuilder.audit.snapshot import (
    AboutLink,
    GraphSnapshot,
    SnapshotAttachment,
    SnapshotClaim,
    SnapshotMention,
)
from kgbuilder.resolution.identity_graph import Assignment
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.validation.gold import MentionRef
from kgbuilder.validation.qa_gold import RecordEvidence
from kgbuilder.validation.target_gold import QuestionTargets, Target, TargetGold

LAMP, KETTLE = "notes/lamp.md", "notes/kettle.md"
L1, K1, S1, S2 = "Product:L1", "Product:K1", "Part:S1", "Part:S2"
STICKING, HUM, SHADE, LID = "concept:sticking", "concept:hum", "concept:shade", "concept:lid"


def mention(mid: str, doc: str, name: str, chunk: int) -> SnapshotMention:
    return SnapshotMention(
        id=mid, name=name, type="Thing", doc_id=doc, chunks=[f"{doc}#{chunk}"], derived=False
    )


def refers(mid: str, said: str, canonical: str, name: str, kind: str) -> Assignment:
    return Assignment(
        mention=mid, said=said, kind=kind, canonical=canonical, name=name, type="Thing", reason="test"
    )


def claim(cid: str, subject: str, obj: str, chunk: str) -> SnapshotClaim:
    return SnapshotClaim(
        id=cid, predicate="HAS", subject=subject, object=obj, chunk_id=chunk, evidence="x",
        subject_name="a", object_name="b", derived=False,
    )  # fmt: skip


def attached(cid: str, thing: str) -> SnapshotAttachment:
    return SnapshotAttachment(
        observation=cid, thing=thing, kind="record:Product", name="x", how="document", evidence=""
    )


SNAPSHOT = GraphSnapshot(
    source="test",
    records=[
        Record(id=L1, label="Product", key="L1", name="Desk Lamp", properties={}),
        Record(id=K1, label="Product", key="K1", name="Kettle K-2", properties={}),
        Record(id=S1, label="Part", key="S1", name="Switch", properties={}),
        Record(id=S2, label="Part", key="S2", name="Switch", properties={}),
    ],
    relations=[
        Relation(source=S1, type="PART_OF", target=L1),
        Relation(source=S2, type="PART_OF", target=K1),
    ],
    chunks=[
        Chunk(chunk_id=f"{LAMP}#0", doc_id=LAMP, index=0, text="The Desk Lamp switch sticks."),
        Chunk(chunk_id=f"{LAMP}#1", doc_id=LAMP, index=1, text="The shade is bright."),
        Chunk(chunk_id=f"{KETTLE}#0", doc_id=KETTLE, index=0, text="The kettle switch works. It hums."),
    ],
    mentions=[
        mention("m1", LAMP, "switch", 0),
        mention("m2", KETTLE, "switch", 0),
        mention("m3", LAMP, "sticks", 0),
        mention("m4", KETTLE, "hums", 0),
        mention("m5", LAMP, "shade", 1),
        mention("m6", KETTLE, "kettle lid", 0),
    ],
    claims=[
        claim("o1", "m1", "m3", f"{LAMP}#0"),
        claim("o2", "m2", "m4", f"{KETTLE}#0"),  # attached to the lamp below: the planted leak
    ],
    references=[
        refers("m1", "switch", S1, "Switch", "record"),
        refers("m2", "switch", S2, "Switch", "record"),
        refers("m3", "sticks", STICKING, "sticking", "concept"),
        refers("m4", "hums", HUM, "hum", "concept"),
        refers("m5", "shade", SHADE, "shade", "concept"),
        refers("m6", "kettle lid", LID, "kettle lid", "concept"),
    ],
    documents_about=[
        AboutLink(source=LAMP, thing=L1, name="Desk Lamp", how="file"),
        AboutLink(source=KETTLE, thing=K1, name="Kettle K-2", how="file"),
    ],
    sections_about=[AboutLink(source=f"{LAMP}#1", thing=S1, name="Switch", how="heading")],
    attachments=[attached("o1", L1), attached("o2", L1)],
    extracted_mentions_edges=5,
    derived_object_collisions=0,
)
A = AnchorGraph(SNAPSHOT, Arm.ANCHOR)
B = AnchorGraph(SNAPSHOT, Arm.LAYERED)


def test_w1_puts_exact_names_first_then_shared_words():
    assert [f.node for f in A.find(["switch"])] == [S1, S2]  # one mention each: the node id breaks the tie
    assert A.find(["Kettle k-2"])[0].node == K1
    assert A.find(["L1"])[0].node == L1  # a record's key is one of its names
    partial = A.find(["lamp shade"])
    assert [f.node for f in partial] == [SHADE, L1]  # {shade} shares 1/2 of the words, {desk, lamp} 1/3
    assert not any(f.exact for f in partial)
    assert A.find(["toaster"]) == []


def test_w1_finds_a_node_by_the_name_of_a_mention_that_refers_to_it():
    assert A.find(["sticks"])[0].node == STICKING  # its canonical name is "sticking"


def test_w2_reaches_mentions_and_about_links_and_arm_b_adds_the_chunks_of_claims():
    assert A.chunks_of(L1) == {f"{LAMP}#0", f"{LAMP}#1"}  # the document ABOUT it
    assert A.chunks_of(S1) == {f"{LAMP}#0", f"{LAMP}#1"}  # its mention and the section ABOUT it
    assert A.chunks_of(HUM) == {f"{KETTLE}#0"}
    assert B.chunks_of(L1) == {f"{LAMP}#0", f"{LAMP}#1", f"{KETTLE}#0"}  # the wrongly attached claim
    assert A.chunks_of("unknown") == set()


def test_w3_prefers_the_chunk_own_about_link_to_its_document():
    assert A.about(f"{LAMP}#1") == {S1}
    assert A.about(f"{LAMP}#0") == {L1}


def test_w4_follows_record_relations_in_arm_a_and_claim_joins_in_arm_b():
    assert A.related(S1) == {L1: 1}
    assert K1 not in A.related(L1)
    assert B.related(L1)[S2] == 1  # through claim o2
    assert B.related(L1)[K1] == 2


def test_w5_gives_the_document_and_the_neighbours():
    assert A.context(f"{LAMP}#0") == Context(document=LAMP, previous=None, next=f"{LAMP}#1")
    assert A.context(f"{KETTLE}#0") == Context(document=KETTLE, previous=None, next=None)


def test_the_composed_walk_counts_steps_and_stays_on_its_side_in_arm_a():
    # sticking -W2-> lamp#0 -W3-> the lamp -W2-> lamp#1
    assert A.walk([STICKING]) == {f"{LAMP}#0": 1, f"{LAMP}#1": 3}
    assert f"{KETTLE}#0" in B.walk([STICKING])  # arm B leaks into the kettle through the lamp's claims
    assert A.reached_nodes([STICKING]) == {STICKING, L1, S1}


def test_every_hop_says_what_witnesses_it():
    assert all(e.witnessed and e.how == "relation" for e in A.thing_edges())
    unwitnessed = {frozenset((e.a, e.b)) for e in B.thing_edges() if not e.witnessed}
    # the kettle's chunk concerns the kettle's switch and the hum, never the lamp
    assert unwitnessed == {frozenset((L1, S2)), frozenset((L1, HUM))}


# --- placing the target gold --------------------------------------------------------------------------

PLAN = ConstructionPlan.model_validate(
    {
        "nodes": [
            {"source_file": "products.csv", "label": "Product", "unique_column": "id", "properties": ["name"],
             "description": "x", "name_column": "name"},
            {"source_file": "parts.csv", "label": "Part", "unique_column": "id", "properties": ["name"],
             "description": "x", "name_column": "name"},
        ],
        "relationships": [],
    }
)  # fmt: skip
ROWS = {
    "products.csv": [{"id": "L1", "name": "Desk Lamp"}, {"id": "K1", "name": "Kettle K-2"}],
    "parts.csv": [
        {"id": "S1", "name": "Switch"},
        {"id": "S2", "name": "Switch"},
        {"id": "S9", "name": "Plug"},
    ],
    "fits.csv": [{"part": "S1", "product": "L1"}],
}


def test_a_record_ref_becomes_every_record_its_rows_fed():
    known = {r.id for r in SNAPSHOT.records}
    assert record_nodes(RecordEvidence(file="parts.csv", row={"name": "Switch"}), PLAN, ROWS, known) == [
        S1,
        S2,
    ]
    assert (
        record_nodes(RecordEvidence(file="fits.csv", row={"part": "S1"}), PLAN, ROWS, known) == []
    )  # no rule
    assert (
        record_nodes(RecordEvidence(file="parts.csv", row={"id": "S9"}), PLAN, ROWS, known) == []
    )  # not kept


def test_targets_are_placed_on_records_and_on_what_their_mentions_refer_to():
    gold = TargetGold(
        dataset="test",
        qa_gold="qa.json",
        written_by="test",
        date="2026-10-06",
        questions=[
            QuestionTargets(
                id="Q1",
                targets=[
                    Target(name="lamp", records=[RecordEvidence(file="products.csv", row={"id": "L1"})]),
                    Target(
                        name="switch",
                        mentions=[
                            MentionRef(doc_id=LAMP, names=["Switch"]),
                            MentionRef(doc_id=LAMP, names=["toggle"]),
                        ],
                    ),
                ],
            ),
            QuestionTargets(id="Q2", note="no start"),
        ],
    )
    placed = TargetPlacer(SNAPSHOT, PLAN, ROWS).place(gold)
    lamp, switch = placed["Q1"]
    assert lamp.nodes == [L1] and lamp.missing == []
    assert switch.nodes == [S1]  # the lamp's switch, not the kettle's
    assert switch.missing == [f"mention {LAMP} ['toggle']"]
    assert placed["Q2"] == []
    assert not switch.loose


def test_a_mention_ref_falls_back_to_longer_names_only_when_no_name_is_equal():
    placer = TargetPlacer(SNAPSHOT, PLAN, ROWS)
    assert placer.mention_nodes(MentionRef(doc_id=KETTLE, names=["lid"])) == ([LID], True)
    assert placer.mention_nodes(MentionRef(doc_id=KETTLE, names=["switch"])) == ([S2], False)
    assert placer.mention_nodes(MentionRef(doc_id=KETTLE, names=["kettle lids"])) == ([], False)


@pytest.mark.parametrize("arm", list(Arm))
def test_both_arms_read_the_same_records_and_chunks(arm):
    graph = AnchorGraph(SNAPSHOT, arm)
    assert graph.chunk_ids == [f"{KETTLE}#0", f"{LAMP}#0", f"{LAMP}#1"]
    assert graph.kind[L1] == "record" and graph.kind[HUM] == "concept"
