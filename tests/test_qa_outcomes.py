"""Outcomes in the QA benchmark (R205, validation/qa_gold.py and qa.py): a gold question may expect a
question back, a subject the dataset does not hold, a held subject whose answer no source states, or a
refusal, with no answer form, a note saying why, and evidence only for a held subject; such a question is
scored by code on the outcome given; an answerable question given another outcome is wrong; an answer
without an outcome reads as "answer", so old answers files score as before; the report adds outcome
accuracy, recall and precision per outcome and the false abstentions, only when outcomes are in play;
a verdict file by meaning need not judge an expected outcome. Pure: no Neo4j, no LLM."""

import pytest
from pydantic import ValidationError

from kgbuilder.validation.judge import JudgeMeta
from kgbuilder.validation.qa import AnswerVerdict, QAAnswer, QAVerdicts, is_correct, score_qa
from kgbuilder.validation.qa_gold import ChunkEvidence, Expected, QAQuestion, QuestionType, Route

from .test_qa import BROKEN, CHUNKS, LAMP, LAMP_0, SHADE, gold, question

HELD = [ChunkEvidence(chunk_id=LAMP_0, quote="The **switch** broke after a week.")]


def expecting(qid: str, outcome: str, **fields) -> QAQuestion:
    """A question expecting `outcome`, with a note and no evidence unless given."""
    fields.setdefault("chunks", [])
    return question(qid, QuestionType.LOOKUP, Expected(outcome=outcome), note="why", **fields)


SCOPE = expecting("O1", "out_of_scope")
VAGUE = expecting("O2", "clarify")
MISSING = expecting("O3", "insufficient_evidence", chunks=HELD)
WRITE = expecting("O4", "refuse")


def given(qid: str, outcome: str | None = None, **fields) -> QAAnswer:
    return QAAnswer(question_id=qid, outcome=outcome, retrieved=[LAMP_0], **fields)


# --- the gold ------------------------------------------------------------------------------------------


def test_an_expected_outcome_has_no_answer_form_and_old_gold_expects_an_answer():
    assert Expected(entities=[LAMP]).outcome == "answer" and Expected(outcome="refuse").kind == "outcome"
    with pytest.raises(ValidationError, match="gives no answer form"):
        Expected(outcome="out_of_scope", text="Nothing.")
    with pytest.raises(ValidationError, match="exactly one"):
        Expected(outcome="answer")


def test_an_expected_outcome_says_why_and_only_a_held_subject_needs_evidence():
    assert SCOPE.route == Route.RETRIEVAL and SCOPE.chunks == []  # no chunk needed, even on that route
    with pytest.raises(ValidationError, match="says why"):
        question("O5", QuestionType.LOOKUP, Expected(outcome="clarify"), chunks=[])
    with pytest.raises(ValidationError, match="holds the subject"):
        expecting("O6", "insufficient_evidence")
    with pytest.raises(ValidationError, match="has no query"):
        expecting("O7", "out_of_scope", sql="SELECT 1")


# --- scoring -------------------------------------------------------------------------------------------


def test_an_expected_outcome_is_right_only_when_that_outcome_is_given():
    assert is_correct(SCOPE.expected, given("O1", "out_of_scope"), None)
    assert not is_correct(SCOPE.expected, given("O1", "insufficient_evidence"), None)
    assert not is_correct(SCOPE.expected, given("O1"), None)  # no outcome reads as an answer
    assert is_correct(WRITE.expected, given("O4", "refuse"), None)


def test_an_answerable_question_given_another_outcome_is_wrong_whatever_its_fields():
    assert is_correct(BROKEN.expected, given("Q1", entities=["Lamp"]), None)  # before R204: no outcome
    assert is_correct(BROKEN.expected, given("Q1", "answer", entities=["Lamp"]), None)
    assert not is_correct(BROKEN.expected, given("Q1", "clarify", entities=["Lamp"]), None)


def test_the_report_scores_outcomes_per_outcome_and_counts_false_abstentions():
    questions = gold(BROKEN, SCOPE, VAGUE, MISSING, WRITE)
    answers = [
        given("Q1", "insufficient_evidence"),  # a false abstention
        given("O1", "out_of_scope"),
        given("O2", "clarify"),
        given("O3", "insufficient_evidence"),
        given("O4", "refuse"),
    ]
    report = score_qa(questions, answers, CHUNKS, k=5)
    scores = report.outcome_scores
    assert (scores.accuracy.k, scores.accuracy.n) == (4, 5)
    assert (scores.false_abstention.k, scores.false_abstention.n) == (1, 1)
    assert (scores.recall["insufficient_evidence"].k, scores.recall["insufficient_evidence"].n) == (1, 1)
    precision = scores.precision["insufficient_evidence"]
    assert (precision.k, precision.n) == (1, 2)
    assert scores.recall["answer"].n == 1 and scores.recall["answer"].k == 0
    assert report.overall.correct.k == 4  # the false abstention is the one wrong answer
    # the held subject's chunk shows it is held; it is no evidence to retrieve
    assert report.overall.recall_all_at_k.n == 1
    metrics = report.metrics()
    assert metrics["outcome_accuracy"] == 0.8 and metrics["false_abstention_n"] == 1
    assert metrics["outcome_precision_insufficient_evidence"] == 0.5
    assert {f"outcome_recall_{o}" for o in ("answer", "clarify", "out_of_scope", "refuse")} <= metrics.keys()


def test_answers_without_outcomes_on_answerable_gold_score_as_before():
    report = score_qa(gold(BROKEN), [given("Q1", entities=["Lamp"])], CHUNKS, k=5)
    assert report.outcome_scores is None and "outcome_accuracy" not in report.metrics()
    assert report.overall.correct.k == 1


def test_a_verdict_file_by_meaning_judges_only_the_answerable_questions():
    verdicts = QAVerdicts(
        judge=JudgeMeta(model="claude", date="2026-10-15", run_id="r", git_sha="s"),
        gold="g.json", answers="a.jsonl", scope="all",
        verdicts=[AnswerVerdict(question_id="Q3", correct=True, reason="says the shade is fine")],
    )  # fmt: skip
    answers = [given("Q3", "answer", text="It is fine."), given("O1", "clarify")]
    report = score_qa(gold(SHADE, SCOPE), answers, CHUNKS, k=5, verdicts=verdicts)
    assert [o.correct for o in report.outcomes] == [True, False]  # O1 scored by code, not by a verdict
