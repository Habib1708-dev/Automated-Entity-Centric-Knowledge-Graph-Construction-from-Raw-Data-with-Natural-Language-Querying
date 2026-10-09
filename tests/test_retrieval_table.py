"""The technique table of plan R126-R128 (R127, validation/retrieval_table.py, `kg retrieve-table`), without
Neo4j or a model: each technique's rates and short lists at every budget, the best per measure chosen by the
pre-registered rule (counts first, then the tie count, then the order given) and paired with the runner-up,
only seeded techniques competing for start nodes, the pooled table re-scored over every dataset's questions,
the refusals (a missing report, reports of other runs or questions, a dataset named like the pooled table, no
shared budget), the markdown and metrics, and the stage reading report folders and writing both files.
R128: the committed table of the three builds at the pre-registered budgets, its choices by the rule."""

import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError, MissingInputError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.retrieval_stages import RetrieveTableStage
from kgbuilder.validation.retrieval_scores import RetrievalFingerprint, RetrievalOutcome, score_retrieval
from kgbuilder.validation.retrieval_table import RetrievalTable, build_table

from .fakes import RecordingTracker

SYSTEMS = ["a", "b", "c"]  # "a" starts from no node, like vector search
FP1 = RetrievalFingerprint(gold_hash="g1", targets_hash="t1", graph_digest="d1", embed_model="e1")
FP2 = RetrievalFingerprint(gold_hash="g2", targets_hash="t2", graph_digest="d2", embed_model="e1")


def report(system, fingerprint, rows, budgets=(1, 2)):
    """A report of `system` from rows (question id, gold chunks, ranked, seeds or None, targets)."""
    outcomes = [
        RetrievalOutcome(
            question_id=q, type="lookup", system=system, gold_chunks=gold, ranked=ranked, seeds=seeds,
            gold_targets=targets, latency_ms=1.0,
        )
        for q, gold, ranked, seeds, targets in rows
    ]  # fmt: skip
    return score_retrieval(outcomes, list(budgets), system, fingerprint)


# d1: Q1 one gold chunk and a target, Q2 two gold chunks, Q3 two targets and no evidence
def dataset_one():
    return {
        "a": report("a", FP1, [
            ("Q1", ["x1"], ["x1", "z"], None, [["n1"]]),
            ("Q2", ["x2", "x3"], ["x2", "x3"], None, []),
            ("Q3", [], ["z"], None, [["n2"], ["n3"]]),
        ]),
        "b": report("b", FP1, [
            ("Q1", ["x1"], ["z", "x1"], ["n1"], [["n1"]]),
            ("Q2", ["x2", "x3"], ["x2"], [], []),  # one chunk: short at K = 2
            ("Q3", [], [], ["n2", "n3"], [["n2"], ["n3"]]),
        ]),
        "c": report("c", FP1, [
            ("Q1", ["x1"], ["x1"], ["z", "n1"], [["n1"]]),
            ("Q2", ["x2", "x3"], ["z", "x3", "x2"], [], []),
            ("Q3", [], [], ["n3"], [["n2"], ["n3"]]),
        ]),
    }  # fmt: skip


# d2: one question, which b and c complete at K = 1 and a only at 2
def dataset_two():
    return {
        "a": report("a", FP2, [("R1", ["y1"], ["z", "y1"], None, [["m1"]])]),
        "b": report("b", FP2, [("R1", ["y1"], ["y1"], ["m1"], [["m1"]])]),
        "c": report("c", FP2, [("R1", ["y1"], ["y1"], ["m1"], [["m1"]])]),
    }


def best(table, name, measure, k):
    return next(
        b for b in next(t for t in table.tables if t.name == name).best if (b.measure, b.k) == (measure, k)
    )


def test_each_technique_has_its_rates_and_short_lists_at_every_budget():
    d1 = build_table({"d1": dataset_one()}, SYSTEMS).tables[0]
    a1, b2, c2 = d1.score("a", 1), d1.score("b", 2), d1.score("c", 2)
    assert (a1.complete.k, a1.complete.n, a1.evidence_recall.k, a1.evidence_recall.n) == (1, 2, 2, 3)
    assert (a1.short_chunks, a1.seed_recall, a1.short_seeds) == (0, None, None)  # no seeds: no seed scores
    assert (b2.complete.k, b2.short_chunks, b2.seed_recall.k, b2.seed_recall.n, b2.short_seeds) == (
        1,
        1,
        3,
        3,
        1,
    )
    assert (c2.seed_recall.k, c2.seed_found.k, c2.seed_found.n, c2.short_seeds) == (2, 1, 2, 1)


def test_the_best_is_chosen_by_counts_then_the_tie_count_then_the_order_given():
    table = build_table({"d1": dataset_one()}, SYSTEMS)
    # K = 1: a and c complete one question each; a finds two gold chunks, c one
    e1 = best(table, "d1", "evidence", 1)
    assert (e1.best, e1.runner_up) == ("a", "c")
    # K = 2: b and c tie on both counts, so the order given puts b second; a completes Q2, b does not
    e2 = best(table, "d1", "evidence", 2)
    assert (e2.best, e2.runner_up, e2.paired.only_a, e2.paired.only_b) == ("a", "b", 1, 0)
    # start nodes: only b and c compete (a has none); b finds Q1's target at 1, c does not
    s1 = best(table, "d1", "seeds", 1)
    assert (s1.best, s1.runner_up, s1.paired.only_a, s1.paired.only_b, s1.paired.p_value) == (
        "b",
        "c",
        1,
        0,
        1.0,
    )
    reordered = build_table({"d1": dataset_one()}, ["a", "c", "b"])
    assert best(reordered, "d1", "evidence", 2).runner_up == "c"


def test_the_pooled_table_rescores_every_datasets_questions_together():
    table = build_table({"d1": dataset_one(), "d2": dataset_two()}, SYSTEMS)
    assert [t.name for t in table.tables] == ["d1", "d2", "pooled"]
    pooled = table.tables[2]
    assert pooled.graph_digest == "d1+d2"
    c1 = pooled.score("c", 1)
    assert (c1.complete.k, c1.complete.n, c1.evidence_recall.k, c1.evidence_recall.n) == (2, 3, 2, 4)
    # c completes Q1 and R1 at 1, a only Q1: the pairing runs over every dataset's questions
    e1 = best(table, "pooled", "evidence", 1)
    assert (e1.best, e1.runner_up, e1.paired.questions, e1.paired.only_a, e1.paired.only_b) == (
        "c",
        "a",
        3,
        1,
        0,
    )
    assert (pooled.score("b", 1).seed_recall.k, pooled.score("b", 1).seed_recall.n) == (3, 4)


def test_reports_that_do_not_fit_one_table_are_refused():
    one, two = dataset_one(), dataset_two()
    with pytest.raises(EvaluationError, match="d2: no report of c"):
        build_table({"d1": one, "d2": {"a": two["a"], "b": two["b"]}}, SYSTEMS)
    other_graph = {**one, "c": report("c", FP2, [("Q1", ["x1"], ["x1"], [], [])])}
    with pytest.raises(EvaluationError, match="d1: c ran on"):
        build_table({"d1": other_graph}, SYSTEMS)
    fewer = {**one, "b": report("b", FP1, [("Q1", ["x1"], ["x1"], [], [["n1"]])])}
    with pytest.raises(EvaluationError, match="other questions"):
        build_table({"d1": fewer}, SYSTEMS)
    with pytest.raises(EvaluationError, match="'pooled' names the table"):
        build_table({"pooled": one}, SYSTEMS)
    rows = [("R1", ["y1"], ["y1"], ["m1"], [["m1"]])]
    apart = {"b": report("b", FP2, rows, budgets=(1,)), "c": report("c", FP2, rows, budgets=(2,))}
    with pytest.raises(EvaluationError, match="share no budget"):
        build_table({"d2": apart}, ["b", "c"])


def test_the_markdown_and_the_metrics_give_counts_and_the_pairings():
    table = build_table({"d1": dataset_one(), "d2": dataset_two()}, SYSTEMS)
    text = table.markdown()
    assert "## d1 (graph d1)" in text and "## pooled (graph d1+d2)" in text
    assert "| a | 1/2 (2/3) | 2/2 (3/3) |" in text
    assert "| b | 0/2 (1/3) | 1/2 (2/3), 1 short |" in text
    assert "| b | 2/3 (1/2) | 3/3 (2/2), 1 short |" in text  # b's start nodes on d1
    assert "| 1 | a over c: 0 / 0, p 1.000 | b over c: 1 / 0, p 1.000 |" in text
    assert "| a | -" not in text.split("**Start nodes:**")[1].split("**Best")[0]  # no seed row for a
    metrics = table.metrics()
    assert metrics["d1_evidence_at_2_only_best"] == 1 and metrics["pooled_evidence_at_1_only_best"] == 1
    assert metrics["d1_seeds_at_1_p"] == 1.0


def test_the_stage_reads_each_datasets_report_folder_and_writes_the_table(tmp_path):
    folders = {}
    for name, reports in (("d1", dataset_one()), ("d2", dataset_two())):
        folders[name] = tmp_path / name
        folders[name].mkdir()
        for system, r in reports.items():
            (folders[name] / f"retrieval_{system}.json").write_text(r.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "table", tracker=tracker)
    state = run_stages(ctx, PipelineState(), [RetrieveTableStage(folders, SYSTEMS)])
    run = tracker.run("retrieve_table")
    assert run.logged_params["systems"] == SYSTEMS and run.logged_params["d1_reports"] == str(folders["d1"])
    assert len(run.logged_params["d1_reports_hash"]) == 12 and "pooled_evidence_at_1_p" in run.logged_metrics
    written = RetrievalTable.model_validate(
        json.loads((tmp_path / "table" / "retrieve_table.json").read_text(encoding="utf-8"))
    )
    assert written == state.retrieval_table
    assert (tmp_path / "table" / "retrieve_table.md").read_text(encoding="utf-8") == written.markdown()
    # a folder without reports: the first missing one is named before anything is read
    with pytest.raises(MissingInputError, match=r"d3.retrieval_a\.json"):
        RetrieveTableStage({"d1": folders["d1"], "d3": tmp_path / "d3"}, SYSTEMS).params(ctx, PipelineState())


R128 = Path(__file__).resolve().parents[1] / "tests" / "gold" / "r128"
R128_SYSTEMS = [
    "chunk_dense", "chunk_lexical", "claim_dense", "claim_lexical", "card_dense_template",
    "card_lexical_template", "card_dense_summary", "card_lexical_summary", "graph_retrieval",
]  # fmt: skip


def test_r128_the_committed_table_holds_the_three_builds_and_the_choices_of_the_pre_registered_rule():
    table = RetrievalTable.model_validate_json((R128 / "retrieve_table.json").read_text(encoding="utf-8"))
    assert (table.systems, table.budgets) == (R128_SYSTEMS, [5, 10, 15, 20, 50])
    assert [(t.name, t.graph_digest) for t in table.tables] == [
        ("furniture", "392a170ecc10"), ("heldout", "bc7c5a1efe3d"), ("generality", "f79f9411ea81"),
        ("pooled", "392a170ecc10+bc7c5a1efe3d+f79f9411ea81"),
    ]  # fmt: skip
    pooled = table.tables[3]
    chunk, card = pooled.score("chunk_lexical", 5), pooled.score("card_lexical_template", 5)
    # n: 160 gold chunks over 99 questions, 213 targets over 141 questions; the headline counts at K = 5
    assert (chunk.evidence_recall.n, chunk.complete.n, card.seed_recall.n, card.seed_found.n) == (
        160,
        99,
        213,
        141,
    )
    assert (chunk.complete.k, card.seed_recall.k) == (86, 164)
    for t in table.tables:  # the rule applied again to the recorded counts gives every recorded choice
        for b in t.best:

            def counts(s: str, t=t, b=b) -> tuple[int, int]:
                sc = t.score(s, b.k)
                if b.measure == "evidence":
                    return sc.complete.k, sc.evidence_recall.k
                return sc.seed_recall.k, sc.seed_found.k

            competing = [s for s in table.systems if b.measure == "evidence" or t.score(s, b.k).seed_recall]
            ranked = sorted(competing, key=lambda s: (-counts(s)[0], -counts(s)[1], table.systems.index(s)))
            assert (b.best, b.runner_up) == (ranked[0], ranked[1])
    assert (R128 / "retrieve_table.md").read_text(encoding="utf-8") == table.markdown()
