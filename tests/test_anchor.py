"""The anchor graph's navigation contract (R90): W1-W5 and the composed walk in both arms, the witness of
every thing-to-thing hop, the target gold placed on a snapshot's nodes, and the criteria computed from
them (C2, C5, C7, C8, C9) up to the stage's MLflow run, arm C (vector retrieval) and the pairing of the
arms question by question (R92).

The snapshot is invented (a desk lamp and a kettle, each with a part called "Switch") and holds one wrong
claim attachment, so the leak the anchor arm prevents is visible: arm B walks from the lamp into the
kettle's chunk through a claim, arm A cannot. The stage test reuses the graph audit's invented build
folder (tests/test_audit.py). The committed reports of R90's runs (tests/gold/r90, R91) must still load
and must name the gold files they were computed from. No Neo4j, no LLM.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.anchor import AnchorGraph, Arm, Context, PlacedTarget, TargetPlacer, record_nodes
from kgbuilder.anchor.compare import compare_arms, pair
from kgbuilder.anchor.criteria import (
    connectivity,
    evidence_reach,
    findability,
    rank_chunks,
    selectivity,
    size,
)
from kgbuilder.anchor.report import START_MODES, AnchorReport, evaluate
from kgbuilder.anchor.vector import Ranked, cosine_rank, vector_reach
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.audit.inputs import Record, Relation
from kgbuilder.audit.snapshot import (
    AboutLink,
    GraphSnapshot,
    SnapshotAttachment,
    SnapshotClaim,
    SnapshotMention,
)
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.anchor_stages import COMPARE_FILE, AnchorCompareStage, AnchorEvalStage, report_file
from kgbuilder.pipeline.inputs import digest
from kgbuilder.resolution.identity_graph import Assignment
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.validation.gold import MentionRef
from kgbuilder.validation.interval import Proportion
from kgbuilder.validation.qa_gold import QAGold, RecordEvidence
from kgbuilder.validation.target_gold import QuestionTargets, Target, TargetGold
from tests.fakes import RecordingTracker
from tests.test_audit import KETTLE as AUDIT_KETTLE
from tests.test_audit import _snapshot as audit_build

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


# --- the criteria (part b) ----------------------------------------------------------------------------


def placed_target(name: str, nodes: list[str], aliases: list[str] | None = None) -> PlacedTarget:
    return PlacedTarget(name=name, aliases=aliases or [], nodes=nodes, missing=[])


def qa(*questions: dict) -> QAGold:
    return QAGold.model_validate(
        {
            "dataset": "test",
            "written_by": "test",
            "date": "2026-10-06",
            "corpus": {
                "data_dir": "x",
                "chunk_max_chars": 1500,
                "chunk_min_chars": 200,
                "chunk_overlap_chars": 0,
            },
            "questions": [
                {"type": "lookup", "question": "q", "expected": {"text": "x"}, "route": "retrieval"} | q
                for q in questions
            ],
        }
    )


def chunks(*ids: str) -> list[dict]:
    return [{"chunk_id": c, "quote": "The"} for c in ids]


def test_chunks_rank_by_targets_reaching_them_then_walk_length_then_id():
    assert rank_chunks([{"a": 1, "b": 2}, {"b": 3, "c": 1}]) == ["b", "a", "c"]


def test_c2_counts_a_target_found_within_k_and_one_the_build_lacks_as_a_miss():
    placed = {
        "Q1": [placed_target("Desk Lamp", [L1]), placed_target("switch", [S2]), placed_target("toaster", [])]
    }
    found = findability(A, placed)
    assert [t.rank for t in found.targets] == [1, 2, None]
    assert found.unplaced == 1
    assert (found.hit_at[1].k, found.hit_at[5].k, found.hit_at[5].n) == (1, 2, 3)


def test_c5_ranks_within_budgets_in_both_start_modes_and_leaves_out_questions_without_a_start():
    gold = qa(
        {"id": "Q1", "chunks": chunks(f"{LAMP}#1")},
        {"id": "Q2", "chunks": chunks(f"{KETTLE}#0")},
        {"id": "Q3", "route": "exact", "sql": "SELECT 1", "expected": {"number": 1}},
    )
    placed = {"Q1": [placed_target("sticking", [STICKING])], "Q3": [placed_target("lamp", [L1])]}
    for mode in START_MODES:
        reach = evidence_reach(A, gold, placed, (1, 2), mode)
        (q1,) = reach.questions
        assert q1.ranked == [f"{LAMP}#0", f"{LAMP}#1"]  # the lamp's other chunk is 3 steps away
        assert q1.hits == {1: 0, 2: 1} and not q1.complete(1) and q1.complete(2)
        assert reach.no_start == ["Q2"]
        assert (reach.recall_at[2].k, reach.complete_at[1].k, reach.reached.k) == (1, 0, 1)


def test_c5_end_to_end_starts_from_the_best_lookup_hit_not_from_the_gold_nodes():
    gold = qa({"id": "Q1", "chunks": chunks(f"{KETTLE}#0")})
    placed = {"Q1": [placed_target("switch", [S2])]}  # the kettle's switch; W1 puts the lamp's first
    assert evidence_reach(A, gold, placed, (1,), "gold_start").questions[0].hits == {1: 1}
    e2e = evidence_reach(A, gold, placed, (1,), "end_to_end").questions[0]
    assert e2e.starts == [S1] and e2e.hits == {1: 0}


def test_c7_lists_the_nodes_a_lookup_shows_above_the_hub_line():
    found = selectivity(A, {"Q1": [placed_target("switch", [S1])]}, hub_share=0.5)
    assert found.nodes == 2 and found.corpus == 3
    assert [(h.node, h.chunks) for h in found.hubs] == [(S1, 2)]  # S2 reaches 1 of 3 chunks
    assert found.median_share == pytest.approx(0.5) and found.p90_share == pytest.approx(2 / 3)


def test_c8_reaches_the_kettle_only_through_the_unwitnessed_claim_of_arm_b():
    gold = qa({"id": "Q1", "type": "multi_hop", "chunks": chunks(f"{KETTLE}#0")})
    placed = {"Q1": [placed_target("sticking", [STICKING])]}
    records = {"Q1": [K1]}
    a, b = connectivity(A, gold, placed, records), connectivity(B, gold, placed, records)
    assert a.questions[0].unreached == [K1, f"{KETTLE}#0"] and a.connections.k == 0
    assert b.questions[0].unreached == [] and b.connections.k == 2
    assert (a.unwitnessed_hops, b.unwitnessed_hops) == (0, 2)


def test_c9_counts_the_labels_each_arm_walks_and_sums_the_logged_cost():
    usage = {"extract.cost_usd": 0.4, "resolve.cost_usd": 0.1, "extract.prompt_tokens": 100.0}
    a, b = size(SNAPSHOT, Arm.ANCHOR, usage), size(SNAPSHOT, Arm.LAYERED, {})
    # records 4 + concepts 4 + mentions 6 + chunks 3 + documents 2; arm B adds the 2 claims
    assert (a.nodes, b.nodes) == (19, 21)
    # relations 2, MENTIONS 6, REFERS_TO 6, ABOUT 3, PART_OF 3, NEXT_CHUNK 1; arm B: 2 attachments + 3 x 2
    assert (a.edges, b.edges) == (21, 29)
    assert (a.cost_usd, a.tokens, b.cost_usd) == (pytest.approx(0.5), 100.0, None)


def test_the_report_flattens_every_criterion_and_logs_no_rate_without_a_denominator():
    gold = qa({"id": "Q1", "type": "multi_hop", "chunks": chunks(f"{LAMP}#1")})
    report = evaluate(
        SNAPSHOT, Arm.ANCHOR, gold, {"Q1": [placed_target("sticking", [STICKING])]}, {},
        budgets=(5, 10), hub_share=0.2, usage={}, fidelity_passed=True,
        provenance={"quotes": Proportion.of(3, 3), "empty": Proportion.of(0, 0)},
    )  # fmt: skip
    metrics = report.metrics()
    assert metrics["c0_fidelity_passed"] == 1.0 and metrics["c1_provenance_min"] == 1.0
    assert metrics["c2_hit_at_1"] == 1.0 and metrics["c5_gold_start_recall_at_5"] == 1.0
    assert metrics["c8_connections"] == 1.0 and metrics["c8_unwitnessed_hops"] == 0.0
    assert "c9_build_cost_usd" not in metrics  # no usage logged


@pytest.mark.parametrize("arm", list(Arm))
def test_the_stage_rebuilds_the_build_and_logs_one_run_per_arm(tmp_path, arm):
    s, out, data = audit_build(tmp_path)
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t",
            build="b",
            git_sha="abc",
            runs={},
            counts=snapshot_counts(s),
            usage={"x.cost_usd": 0.2},
        ).model_dump_json(),
        encoding="utf-8",
    )
    qa_file, targets_file = tmp_path / "qa.json", tmp_path / "targets.json"
    question = {
        "id": "Q1",
        "chunks": chunks(f"{AUDIT_KETTLE}#0"),
        "question": "Is the Birch Kettle lid stiff?",
    }
    qa_file.write_text(qa(question).model_dump_json(), encoding="utf-8")
    kettle = Target(
        name="Birch Kettle", records=[RecordEvidence(file="products.csv", row={"product_id": "P-2"})]
    )
    targets_file.write_text(
        TargetGold(
            dataset="t", qa_gold=qa_file.as_posix(), written_by="test", date="2026-10-06",
            questions=[QuestionTargets(id="Q1", targets=[kettle])],
        ).model_dump_json(),
        encoding="utf-8",
    )  # fmt: skip
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "anchor", tracker=tracker)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_targets=targets_file)
    state = run_stages(ctx, state, [AnchorEvalStage(arm)])
    run = tracker.run(f"anchor_eval_{arm.value}")
    assert run.logged_params["arm"] == arm.value and run.logged_params["anchor_budgets"] == [5, 10]
    assert run.logged_metrics["c0_fidelity_passed"] == 1.0
    assert run.logged_metrics["c2_hit_at_1"] == 1.0 and run.logged_metrics["c9_build_cost_usd"] == 0.2
    assert run.logged_metrics["c5_gold_start_recall_at_5"] == 1.0
    assert (tmp_path / "anchor" / report_file(arm)).is_file()
    assert state.anchor.placed["Q1"][0].nodes == ["Product:P-2"]


# --- the committed results of R90 (R91) ---------------------------------------------------------------

RESULTS = Path(__file__).resolve().parent / "gold" / "r90"
RUNS = json.loads((RESULTS / "runs.json").read_text(encoding="utf-8"))["runs"]


@pytest.mark.parametrize("run", RUNS, ids=lambda r: f"{r['dataset']}-{r['arm']}")
def test_each_committed_report_loads_and_names_the_gold_it_was_computed_from(run):
    repo = RESULTS.parent.parent.parent
    report = AnchorReport.model_validate_json((repo / run["report"]).read_text(encoding="utf-8"))
    assert report.arm.value == run["arm"] and report.fidelity_passed
    # the same bytes as the committed gold and logged counts: the report describes these files
    assert digest(repo / "tests" / "gold" / "r89" / f"{run['dataset']}_targets.json") == run["targets_hash"]
    assert digest(repo / "tests" / "gold" / "r87" / f"{run['dataset']}_logged.json") == run["logged_hash"]
    assert run["git_dirty_files"] in ("", ".claude/settings.json")  # the code was exactly `git_sha`


def test_every_dataset_has_a_committed_report_in_both_arms():
    assert {(r["dataset"], r["arm"]) for r in RUNS} == {
        (d, a.value) for d in ("furniture", "heldout", "generality") for a in Arm
    }


# --- arm C and the pairing (R92) ----------------------------------------------------------------------


def test_cosine_rank_puts_the_nearest_chunks_first_and_breaks_ties_by_id():
    chunks = {"z": [0.0, 0.0], "b": [0.0, 2.0], "c": [1.0, 1.0], "a": [3.0, 0.0]}
    ranked = cosine_rank([1.0, 0.0], chunks, top=3)
    assert [r.chunk for r in ranked] == ["a", "c", "b"]  # b and the zero vector z both score 0
    assert ranked[1].cosine == pytest.approx(2**-0.5)


def test_vector_reach_scores_the_graph_pool_in_its_order():
    gold = qa({"id": "Q1", "chunks": chunks("x#0")}, {"id": "Q2", "chunks": chunks("y#0", "y#1")})
    rankings = {
        "Q2": [Ranked(chunk=c, cosine=0.5) for c in ("y#1", "x#0", "y#0")],
        "Q1": [Ranked(chunk="x#0", cosine=1)],
    }
    reach = vector_reach(gold, ["Q2", "Q1"], rankings, (1, 3))
    assert [q.question for q in reach.questions] == ["Q2", "Q1"]
    assert reach.questions[0].hits == {1: 1, 3: 2}
    assert (reach.recall_at[1].k, reach.recall_at[1].n, reach.complete_at[3].k) == (2, 3, 2)


def test_pair_counts_the_questions_only_one_side_got_and_refuses_different_questions():
    p = pair({"Q1": True, "Q2": False, "Q3": True}, {"Q1": False, "Q2": False, "Q3": True}, "a", "b")
    assert (p.only_a, p.only_b, p.result.a_correct, p.result.b_correct) == (["Q1"], [], 2, 1)
    assert p.result.p_value == 1.0  # one discordant question is no evidence
    with pytest.raises(EvaluationError):
        pair({"Q1": True}, {"Q2": True}, "a", "b")


def test_compare_arms_pairs_c5_in_both_modes_with_vector_and_c8_between_the_graph_arms():
    gold = qa({"id": "Q1", "type": "multi_hop", "chunks": chunks(f"{KETTLE}#0")})
    placed = {"Q1": [placed_target("sticking", [STICKING])]}
    reports = [
        evaluate(SNAPSHOT, arm, gold, placed, {}, budgets=(1,), hub_share=0.2, usage={}, fidelity_passed=True,
                 provenance={})
        for arm in (Arm.ANCHOR, Arm.LAYERED)
    ]  # fmt: skip
    rankings = {"Q1": [Ranked(chunk=f"{KETTLE}#0", cosine=0.9)]}
    comparison = compare_arms(*reports, vector_reach(gold, ["Q1"], rankings, (1,)), rankings, (1,))
    assert set(comparison.pairs) == {
        "c5_gold_start_complete_at_1",
        "c5_gold_start_vs_vector_complete_at_1",
        "c5_end_to_end_complete_at_1",
        "c5_end_to_end_vs_vector_complete_at_1",
        "c8_all_connections",
    }
    assert comparison.pairs["c5_gold_start_vs_vector_complete_at_1"].only_b == [
        "Q1"
    ]  # only the vector finds it
    assert comparison.pairs["c8_all_connections"].only_b == ["Q1"]  # only arm B's leak reaches the kettle
    assert comparison.metrics()["c8_all_connections_only_b"] == 1.0


class WordEmbedder:
    """Embeds a text as counts of a few words, so similarity is predictable; records what it embedded."""

    WORDS = ("lid", "shade", "kettle", "lamp", "stiff", "cracked")

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts += texts
        return [[float(t.lower().count(w)) for w in self.WORDS] for t in texts]


def test_the_compare_stage_embeds_chunks_and_questions_and_logs_the_pairings(tmp_path):
    s, out, data = audit_build(tmp_path)
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t", build="b", git_sha="abc", runs={}, counts=snapshot_counts(s)
        ).model_dump_json(),
        encoding="utf-8",
    )
    qa_file, targets_file = tmp_path / "qa.json", tmp_path / "targets.json"
    question = {
        "id": "Q1",
        "chunks": chunks(f"{AUDIT_KETTLE}#0"),
        "question": "Is the Birch Kettle lid stiff?",
    }
    qa_file.write_text(qa(question).model_dump_json(), encoding="utf-8")
    kettle = Target(
        name="Birch Kettle", records=[RecordEvidence(file="products.csv", row={"product_id": "P-2"})]
    )
    targets_file.write_text(
        TargetGold(dataset="t", qa_gold=qa_file.as_posix(), written_by="test", date="2026-10-06",
                   questions=[QuestionTargets(id="Q1", targets=[kettle])]).model_dump_json(),
        encoding="utf-8",
    )  # fmt: skip
    tracker, embedder = RecordingTracker(), WordEmbedder()
    folder = tmp_path / "anchor"
    ctx = PipelineContext(settings=Settings(), driver=None, out=folder, tracker=tracker, embedder=embedder)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_targets=targets_file)
    state = run_stages(ctx, state, [AnchorEvalStage(Arm.ANCHOR), AnchorEvalStage(Arm.LAYERED)])
    state.anchor_reports = (folder / report_file(Arm.ANCHOR), folder / report_file(Arm.LAYERED))
    state = run_stages(ctx, state, [AnchorCompareStage()])
    run = tracker.run("anchor_compare")
    assert (
        run.logged_params["embed_model"] == Settings().embed_model and run.logged_params["anchor_report_hash"]
    )
    assert run.logged_metrics["embedded_questions"] == 1.0 and run.logged_metrics["embedded_chunks"] == 2.0
    assert embedder.texts[-1] == "Is the Birch Kettle lid stiff?"  # the chunks first, then the questions
    assert state.anchor_comparison.rankings["Q1"][0].chunk == f"{AUDIT_KETTLE}#0"
    assert run.logged_metrics["c5_vector_complete_at_5"] == 1.0
    assert (folder / COMPARE_FILE).is_file()


def test_the_compare_stage_refuses_the_reports_in_the_wrong_order(tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    gold = qa({"id": "Q1", "chunks": chunks(f"{LAMP}#0")})
    for arm in Arm:
        report = evaluate(
            SNAPSHOT,
            arm,
            gold,
            {},
            {},
            budgets=(1,),
            hub_share=0.2,
            usage={},
            fidelity_passed=True,
            provenance={},
        )
        (folder / report_file(arm)).write_text(report.model_dump_json(), encoding="utf-8")
    ctx = PipelineContext(
        settings=Settings(), driver=None, out=tmp_path, tracker=RecordingTracker(), embedder=WordEmbedder()
    )
    state = PipelineState(
        anchor_reports=(folder / report_file(Arm.LAYERED), folder / report_file(Arm.ANCHOR))
    )
    with pytest.raises(EvaluationError):
        AnchorCompareStage().run(ctx, state, None)
