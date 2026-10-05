"""Paired comparison of two systems (R73): the exact McNemar p-value against hand-computed values, the
discordant counts overall and per type from hand-made outcome rows, the refusals (other questions, an
unjudged answer), and `kg qa-compare` end to end without a graph or a model."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.graph.connection import open_driver
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.qa_stages import QACompareStage
from kgbuilder.validation.paired import compare_outcomes, mcnemar_exact
from kgbuilder.validation.qa import QAOutcome
from kgbuilder.validation.qa_gold import QuestionType, Route

from .fakes import RecordingTracker

LOOKUP, COUNT = QuestionType.LOOKUP, QuestionType.AGGREGATION


def outcomes(system: str, rows: list[tuple[str, QuestionType, bool | None]]) -> list[QAOutcome]:
    return [
        QAOutcome(
            question_id=qid, type=qtype, system=system, correct=correct, route=Route.RETRIEVAL,
            system_route=None, cited_chunks=[],
        )
        for qid, qtype, correct in rows
    ]  # fmt: skip


def test_the_exact_mcnemar_p_value_doubles_the_binomial_tail_of_the_smaller_count():
    assert mcnemar_exact(0, 0) == 1.0  # no discordant question: no evidence either way
    assert mcnemar_exact(0, 6) == pytest.approx(2 / 64)  # 6 of 6 one way: the smallest p with n = 6
    assert mcnemar_exact(1, 5) == pytest.approx(2 * 7 / 64)  # (C(6,0) + C(6,1)) / 2^6, doubled
    assert mcnemar_exact(5, 1) == mcnemar_exact(1, 5)  # two-sided: the direction does not matter
    assert mcnemar_exact(3, 3) == 1.0  # the doubled tail is capped at 1
    # R71's held-out totals (29 against 22 of 38) need 7 more right ones in one system: even with no
    # question lost, 7 of 7 discordant gives p = 2 / 128 < 0.05, while 9 against 2 gives p = 0.065
    assert mcnemar_exact(7, 0) == pytest.approx(2 / 128)
    assert mcnemar_exact(9, 2) == pytest.approx(2 * (1 + 11 + 55) / 2048)
    with pytest.raises(ValueError, match="cannot be negative"):
        mcnemar_exact(-1, 2)


def test_only_the_questions_one_system_got_right_are_counted_overall_and_per_type():
    a = outcomes(
        "graph", [("Q1", LOOKUP, True), ("Q2", LOOKUP, True), ("Q3", COUNT, False), ("Q4", COUNT, True)]
    )
    b = outcomes(
        "vector", [("Q4", COUNT, True), ("Q3", COUNT, True), ("Q2", LOOKUP, False), ("Q1", LOOKUP, True)]
    )
    report = compare_outcomes(a, b, "graph", "vector")
    overall = report.overall
    # Q1 and Q4 are right in both: they say nothing about which system is better
    assert (overall.questions, overall.a_correct, overall.b_correct) == (4, 3, 3)
    assert (overall.only_a, overall.only_b, overall.p_value) == (1, 1, 1.0)
    lookup, count = report.by_type[LOOKUP], report.by_type[COUNT]
    assert (lookup.only_a, lookup.only_b, count.only_a, count.only_b) == (1, 0, 0, 1)
    assert report.by_type[QuestionType.MULTI_HOP].questions == 0  # every type present, for stable metrics
    assert not overall.differs
    metrics = report.metrics()
    assert (metrics["only_a"], metrics["only_b_aggregation"], metrics["p_value_multi_hop"]) == (1, 1, 1.0)


def test_outcomes_of_other_questions_or_unjudged_answers_are_refused():
    a = outcomes("graph", [("Q1", LOOKUP, True), ("Q2", LOOKUP, None)])
    b = outcomes("vector", [("Q1", COUNT, True), ("Q3", LOOKUP, False), ("Q3", LOOKUP, False)])
    with pytest.raises(EvaluationError) as raised:
        compare_outcomes(a, b)
    issues = "\n".join(raised.value.issues)
    assert "outcomes a have 1 unjudged answers (first: ['Q2'])" in issues
    assert "outcomes b hold a question more than once" in issues
    assert "2 questions are in one outcome file only (first: ['Q2', 'Q3'])" in issues
    assert "1 questions have different types (first: ['Q1'])" in issues


def test_qa_compare_compares_two_outcome_files_and_reads_no_graph(tmp_path):
    rows = [(f"Q{i}", LOOKUP, True) for i in range(7)]
    for name, correct in (("a", True), ("b", False)):
        (tmp_path / name).mkdir()
        lines = [o.model_dump_json() for o in outcomes(name, [(q, t, correct) for q, t, _ in rows])]
        (tmp_path / name / "qa_outcomes_graph.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # a driver to a port nothing listens on: any query would fail, so passing proves no graph is read
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)
    files = (tmp_path / "a" / "qa_outcomes_graph.jsonl", tmp_path / "b" / "qa_outcomes_graph.jsonl")
    try:
        report = run_stages(ctx, PipelineState(outcomes=files), [QACompareStage()]).paired
    finally:
        driver.close()
    # the two sides share a system name; the folder tells them apart
    assert (report.a, report.b) == ("a/qa_outcomes_graph.jsonl", "b/qa_outcomes_graph.jsonl")
    assert (report.overall.only_a, report.overall.only_b) == (7, 0) and report.overall.differs
    run = tracker.run("qa_compare")
    assert {"a_hash", "b_hash"} <= set(run.logged_params)
    assert run.logged_metrics["p_value"] == pytest.approx(2 / 128)
    assert (tmp_path / "out" / "qa_compare.json").exists()
