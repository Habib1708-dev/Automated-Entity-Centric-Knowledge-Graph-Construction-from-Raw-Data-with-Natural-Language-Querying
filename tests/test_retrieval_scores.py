"""Retrieval scores without the reader (R117, validation/retrieval_scores.py): evidence recall and complete
retrieval within each budget, seed recall and found seeds against placed targets, questions without
evidence or targets left out of that measure, a target the build lacks counted as a miss, no seed scores for
a system without seeds, the nearest-rank latency, the flat metrics, two reports paired with McNemar's test
(and `compare_pairs` equal to `compare_outcomes`), the report file, the committed furniture baselines of
R117 part b, R121's seal of the hybrid settings and R124's seal of the summary settings. Pure: no Neo4j, no
model."""

import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline.index_stages import card_representation
from kgbuilder.pipeline.inputs import digest
from kgbuilder.validation.paired import compare_outcomes, compare_pairs, mcnemar_exact
from kgbuilder.validation.qa import QAOutcome
from kgbuilder.validation.qa_gold import QuestionType
from kgbuilder.validation.retrieval_scores import (
    RetrievalFingerprint,
    RetrievalOutcome,
    compare_retrieval,
    load_retrieval_report,
    score_retrieval,
)

FINGERPRINT = RetrievalFingerprint(gold_hash="g1", targets_hash="t1", graph_digest="d1", embed_model="e1")
LOOKUP, MULTI_HOP = QuestionType.LOOKUP, QuestionType.MULTI_HOP


def outcome(
    qid: str,
    qtype: QuestionType = LOOKUP,
    gold: tuple[str, ...] = (),
    ranked: tuple[str, ...] = (),
    seeds: tuple[str, ...] | None = None,
    targets: tuple[tuple[str, ...], ...] = (),
    latency: float = 10.0,
) -> RetrievalOutcome:
    return RetrievalOutcome(
        question_id=qid,
        type=qtype,
        system="s",
        gold_chunks=list(gold),
        ranked=list(ranked),
        seeds=None if seeds is None else list(seeds),
        gold_targets=[list(t) for t in targets],
        latency_ms=latency,
    )


def score(outcomes: list[RetrievalOutcome], fingerprint: RetrievalFingerprint = FINGERPRINT):
    return score_retrieval(outcomes, [5, 10], "s", fingerprint)


def test_evidence_counts_gold_chunks_within_each_budget_and_leaves_out_questions_without_evidence():
    report = score(
        [
            # one gold chunk at rank 2, the other at rank 6: in the top 10 only
            outcome("Q1", MULTI_HOP, gold=("a", "b"), ranked=("x", "a", "y", "z", "w", "b")),
            outcome("Q2", LOOKUP, gold=("c",), ranked=("c",)),
            outcome("Q3", LOOKUP, ranked=("c",)),  # no chunk evidence: no evidence measure
        ]
    )
    o = report.overall
    assert (o.evidence_recall[5].k, o.evidence_recall[5].n) == (2, 3) and o.evidence_recall[10].rate == 1.0
    assert (o.complete[5].k, o.complete[5].n) == (1, 2) and (o.complete[10].k, o.complete[10].n) == (2, 2)
    hop = report.by_type[MULTI_HOP]
    assert hop.questions == 1 and (hop.evidence_recall[5].k, hop.evidence_recall[5].n) == (1, 2)
    assert report.by_type[LOOKUP].complete[5].rate == 1.0 and report.overall.questions == 3


def test_seeds_find_a_target_by_any_of_its_nodes_and_a_target_the_build_lacks_is_a_miss():
    report = score(
        [
            outcome(
                "Q1",
                seeds=("Press:P1", "k-a", "k-b", "k-c", "k-d", "Part:S2"),  # the part's second node, rank 6
                targets=(("Press:P1",), ("Part:S1", "Part:S2"), ()),  # the third has no node in the build
            ),
            outcome("Q2", seeds=(), targets=(("Maker:M1",),)),  # nothing linked
            outcome("Q3", seeds=("Press:P1",)),  # no target: no seed measure
        ]
    )
    o = report.overall
    assert (o.seed_recall[5].k, o.seed_recall[5].n) == (1, 4) and o.seed_recall[10].k == 2
    assert (o.seed_found[5].k, o.seed_found[5].n) == (0, 2) and o.seed_found[10].k == 0
    assert (report.targets, report.targets_unplaced, report.seeded) == (4, 1, True)
    found = score([outcome("Q1", seeds=("Part:S2",), targets=(("Part:S1", "Part:S2"),))])
    assert found.overall.seed_found[5].rate == 1.0


def test_a_system_without_seeds_has_no_seed_scores_rather_than_zeros():
    report = score([outcome("Q1", gold=("a",), ranked=("a",), targets=(("Press:P1",),))])
    metrics = report.metrics()
    assert not report.seeded and report.overall.seed_recall == {} and report.overall.seed_found == {}
    assert "seed_recall_at_5" not in metrics and metrics["evidence_recall_at_5"] == 1.0


def test_latency_is_the_nearest_rank_percentile_of_the_questions():
    report = score([outcome(f"Q{i}", latency=ms) for i, ms in enumerate((40.0, 10.0, 30.0, 20.0))])
    assert (report.latency_p50_ms, report.latency_p95_ms) == (20.0, 40.0)
    assert score([]).latency_p50_ms is None


def test_the_metrics_give_each_rate_with_its_interval_overall_and_its_n_per_type():
    metrics = score(
        [outcome("Q1", MULTI_HOP, gold=("a",), ranked=("a",), seeds=("n1",), targets=(("n1",),))]
    ).metrics()
    assert metrics["complete_at_10"] == 1.0 and metrics["complete_at_10_n"] == 1
    assert metrics["evidence_recall_at_5_low"] < 1.0 and metrics["evidence_recall_at_5_high"] == 1.0
    assert metrics["seed_found_at_5_multi_hop"] == 1.0 and metrics["seed_found_at_5_n_multi_hop"] == 1
    assert metrics["questions_lookup"] == 0 and metrics["complete_at_5_lookup"] is None  # logged as nothing
    assert metrics["latency_p95_ms"] == 10.0 and metrics["targets"] == 1


def test_a_question_ranked_twice_and_a_budget_below_one_are_refused():
    with pytest.raises(EvaluationError, match="more than once"):
        score([outcome("Q1"), outcome("Q1")])
    with pytest.raises(ValueError, match="at least 1"):
        score_retrieval([outcome("Q1")], [0, 5], "s", FINGERPRINT)


def test_two_systems_are_paired_on_complete_retrieval_and_on_found_seeds():
    a = score(
        [
            outcome("Q1", gold=("a",), ranked=("a",), seeds=("n1",), targets=(("n1",),)),
            outcome("Q2", gold=("b",), ranked=("b",), seeds=(), targets=(("n2",),)),
            outcome("Q3", gold=("c",), ranked=(), seeds=()),
            outcome("Q4", gold=("d",), ranked=("d",), seeds=()),
        ]
    )
    b = score(
        [
            outcome("Q1", gold=("a",), ranked=(), seeds=(), targets=(("n1",),)),
            outcome("Q2", gold=("b",), ranked=("b",), seeds=("n2",), targets=(("n2",),)),
            outcome("Q3", gold=("c",), ranked=("c",), seeds=()),
            outcome("Q4", gold=("d",), ranked=(), seeds=()),
        ]
    )
    c = compare_retrieval(a, b, 5, "r1/a", "r1/b")
    assert (c.complete.overall.only_a, c.complete.overall.only_b, c.complete.overall.questions) == (2, 1, 4)
    assert c.complete.overall.p_value == mcnemar_exact(2, 1) and c.complete.a == "r1/a"
    assert (c.seed_found.overall.only_a, c.seed_found.overall.only_b) == (1, 1)
    assert c.seed_found.overall.questions == 2  # Q3 and Q4 have no target
    assert c.metrics()["complete_at_5_only_a"] == 2 and c.metrics()["seed_found_at_5_questions"] == 2
    # vector search has no seeds: only completeness is paired
    unseeded = score([outcome(o.question_id, gold=tuple(o.gold_chunks)) for o in b.outcomes])
    assert compare_retrieval(a, unseeded, 5).seed_found is None


def test_reports_of_other_graphs_questions_or_budgets_are_never_paired():
    a = score([outcome("Q1", gold=("a",))])
    other_graph = FINGERPRINT.model_copy(update={"graph_digest": "d2"})
    with pytest.raises(EvaluationError, match="differ in what they ran on"):
        compare_retrieval(a, score([outcome("Q1", gold=("a",))], other_graph), 5)
    with pytest.raises(EvaluationError, match="other questions"):
        compare_retrieval(a, score([outcome("Q2", gold=("a",))]), 5)
    with pytest.raises(EvaluationError, match="budget 3"):
        compare_retrieval(a, a, 3)


def test_compare_pairs_gives_compare_outcomes_own_report():
    def qa_outcome(qid: str, qtype: QuestionType, correct: bool) -> QAOutcome:
        return QAOutcome(
            question_id=qid, type=qtype, system="s", correct=correct, route="retrieval", system_route=None,
            cited_chunks=[],
        )  # fmt: skip

    a = [qa_outcome("Q1", LOOKUP, True), qa_outcome("Q2", MULTI_HOP, False), qa_outcome("Q3", LOOKUP, True)]
    b = [qa_outcome("Q1", LOOKUP, False), qa_outcome("Q2", MULTI_HOP, True), qa_outcome("Q3", LOOKUP, True)]
    pairs = [(LOOKUP, True, False), (MULTI_HOP, False, True), (LOOKUP, True, True)]
    assert compare_outcomes(a, b, "x", "y") == compare_pairs(pairs, "x", "y")


def test_a_report_file_loads_back_and_a_foreign_file_is_refused(tmp_path):
    report = score([outcome("Q1", gold=("a",), ranked=("a",), seeds=("n1",), targets=(("n1",),))])
    path = tmp_path / "retrieval_s.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    assert load_retrieval_report(path) == report
    path.write_text('{"system": "s"}', encoding="utf-8")
    with pytest.raises(EvaluationError):
        load_retrieval_report(path)


ROOT = Path(__file__).resolve().parents[1]
R117 = ROOT / "tests" / "gold" / "r117"


def test_r117_baselines_load_pair_again_to_the_committed_comparison_and_seed_by_stable_ids():
    """The furniture baselines of R117 part b, the reference R121 pairs against: both reports ran on one
    gold, targets, graph and embedding model, the graph route's seeds are record refs and canonical ids
    (never element ids, which hold a colon after a number, `4:...`), and pairing them again gives the
    committed comparison."""
    vector = load_retrieval_report(R117 / "retrieval_vector.json")
    graph = load_retrieval_report(R117 / "retrieval_graph_retrieval.json")
    assert vector.fingerprint == graph.fingerprint and vector.fingerprint.graph_digest == "392a170ecc10"
    assert not vector.seeded and graph.seeded and graph.targets_unplaced == 0
    assert all(not seed[:1].isdigit() or ":" not in seed for o in graph.outcomes for seed in o.seeds)
    committed = json.loads((R117 / "retrieve_compare.json").read_text(encoding="utf-8"))
    names = ("r117b_retrieval/retrieval_vector.json", "r117b_retrieval/retrieval_graph_retrieval.json")
    again = [compare_retrieval(vector, graph, k, *names).model_dump(mode="json") for k in (5, 10)]
    assert again == committed
    complete = graph.overall.complete[5]
    assert (vector.overall.complete[5].k, complete.k, complete.n) == (20, 24, 33)


R121 = ROOT / "tests" / "gold" / "r121"
# R121's grid as the roadmap pre-registered it (section "Plan R116-R125"), written here rather than read from
# the seal, so an edited grid in tuning.json fails the test below
CHUNKS = ["chunk_dense", "chunk_lexical"]
CLAIMS = ["claim_dense", "claim_lexical"]
CARDS = ["card_dense", "card_lexical"]
R121_GRID = {
    "M1": CHUNKS,
    "M2": CHUNKS + CLAIMS,
    "M3": CHUNKS + CARDS,
    "M4": CHUNKS + CLAIMS + CARDS,
    "M5": CHUNKS + CLAIMS + CARDS + ["graph_route"],
}


def test_r121_the_config_defaults_are_the_cell_the_pre_registered_criterion_chose():
    """The seal of R121: the grid recorded is the pre-registered one (five mixes, rrf_k 10 and 60, depth 20),
    the criterion (Complete@5, then Seed Recall@5, then the smaller mix, then rrf_k 60) applied to the
    recorded cells picks the recorded choice, the `config.py` defaults are that cell, and its committed
    report ran on R117's gold, targets, graph and embedding model, scores what the seal records and pairs
    with R117's vector report as recorded. Held-out and generality runs take these defaults, so a changed
    default, grid, gold or targets file fails here."""
    tuning = json.loads((R121 / "tuning.json").read_text(encoding="utf-8"))
    assert tuning["grid"] == {"mixes": R121_GRID, "rrf_k": [10, 60], "depth": 20}
    cells = tuning["cells"]
    assert sorted((c["mix"], c["rrf_k"]) for c in cells) == [(m, k) for m in R121_GRID for k in (10, 60)]
    assert all(c["retrievers"] == R121_GRID[c["mix"]] and c["depth"] == 20 for c in cells)

    def found(cell: dict, measure: str) -> int:
        return int(cell[measure]["5"].split("/")[0])

    mixes = list(R121_GRID)
    chosen = min(
        cells,
        key=lambda c: (
            -found(c, "complete"),
            -found(c, "seed_recall"),
            mixes.index(c["mix"]),
            c["rrf_k"] != 60,
        ),
    )
    assert chosen["cell"] == tuning["choice"]["cell"]
    fields = Settings.model_fields
    assert (
        fields["hybrid_retrievers"].default == chosen["retrievers"] == tuning["choice"]["hybrid_retrievers"]
    )
    assert fields["hybrid_rrf_k"].default == chosen["rrf_k"] == tuning["choice"]["hybrid_rrf_k"]
    assert fields["hybrid_depth"].default == 20
    report = load_retrieval_report(R121 / "retrieval_hybrid.json")
    vector = load_retrieval_report(R117 / "retrieval_vector.json")
    assert report.fingerprint == vector.fingerprint
    assert report.fingerprint.gold_hash == tuning["gold_hash"] == digest(ROOT / tuning["gold"])
    assert report.fingerprint.targets_hash == tuning["targets_hash"] == digest(ROOT / tuning["targets"])
    complete = report.overall.complete[5]
    assert f"{complete.k}/{complete.n}" == chosen["complete"]["5"]
    paired = compare_retrieval(report, vector, 5, "a", "b").complete.overall
    assert paired.model_dump() == tuning["paired"]["m2_k10 vs vector"]["complete_5"]


R124 = ROOT / "tests" / "gold" / "r124"
# R124's grid as R123's roadmap entry pre-registered it, before any summary was written
R124_GRID = {"max_chars": [250, 600], "prompt": ["p1", "p2"]}


def test_r124_the_summary_defaults_are_the_cell_the_pre_registered_criterion_chose():
    """The seal of R124a: the grid recorded is the pre-registered one (caps 250 and 600, prompts p1 and p2),
    each cell once with the version the code computes for it, the criterion (Seed Recall@5 of card_dense
    alone, then @1, then @10, then the smaller cap, then p1) applied to the recorded cells picks the recorded
    choice, the `config.py` defaults are that cell, and its committed report ran on R117's gold, targets,
    graph and embedding model, scores what the seal records and pairs with A's card_dense report as recorded.
    The held-out and generality summaries take these defaults, so a changed default, grid or prompt fails
    here."""
    tuning = json.loads((R124 / "tuning.json").read_text(encoding="utf-8"))
    assert tuning["grid"] == R124_GRID
    cells = tuning["cells"]
    assert sorted((c["prompt"], c["max_chars"]) for c in cells) == [
        (p, m) for p in R124_GRID["prompt"] for m in R124_GRID["max_chars"]
    ]
    for c in cells:
        s = Settings(index_summary_prompt=c["prompt"], index_summary_max_chars=c["max_chars"])
        assert card_representation(s, "summary").version == c["representation_version"]

    def found(cell: dict, k: str) -> int:
        return int(cell["card_dense"]["seed_recall"][k].split("/")[0])

    chosen = min(
        cells, key=lambda c: (-found(c, "5"), -found(c, "1"), -found(c, "10"), c["max_chars"], c["prompt"])
    )
    assert chosen["cell"] == tuning["choice"]["cell"]
    fields = Settings.model_fields
    choice = tuning["choice"]
    assert fields["index_summary_prompt"].default == chosen["prompt"] == choice["index_summary_prompt"]
    assert (
        fields["index_summary_max_chars"].default == chosen["max_chars"] == choice["index_summary_max_chars"]
    )
    assert (fields["index_summary_model"].default, fields["index_summary_thinking"].default) == (
        tuning["summary_model"], tuning["summary_thinking"],
    )  # fmt: skip
    assert card_representation(Settings(), "summary").version == tuning["choice"]["representation_version"]
    summary = load_retrieval_report(R124 / "retrieval_card_dense_summary.json")
    template = load_retrieval_report(R124 / "retrieval_card_dense_template.json")
    vector = load_retrieval_report(R117 / "retrieval_vector.json")
    assert summary.fingerprint == template.fingerprint == vector.fingerprint
    assert summary.fingerprint.gold_hash == tuning["gold_hash"] == digest(ROOT / tuning["gold"])
    assert summary.fingerprint.targets_hash == tuning["targets_hash"] == digest(ROOT / tuning["targets"])
    recall = summary.overall.seed_recall[5]
    assert f"{recall.k}/{recall.n}" == chosen["card_dense"]["seed_recall"]["5"]
    paired = compare_retrieval(template, summary, 5, "a", "b").seed_found.overall
    assert paired.model_dump() == tuning["paired"]["summary p1_600 vs template (card_dense)"]["seed_found_5"]
