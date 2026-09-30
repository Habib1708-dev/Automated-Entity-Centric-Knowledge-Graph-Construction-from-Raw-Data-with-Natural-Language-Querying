"""Question-answer benchmark (R70): the gold file's own checks and its fit to a corpus (quotes verbatim,
records present), and the scoring of hand-made answers (sets, numbers, free text through a verdict file,
citation faithfulness, recall@k, per type with n and interval). Pure: no Neo4j, no LLM."""

import json

import pytest
from pydantic import ValidationError

from kgbuilder.core.errors import EvaluationError, InvalidGoldError
from kgbuilder.validation.judge import JudgeMeta
from kgbuilder.validation.qa import (
    AnswerVerdict,
    Citation,
    QAAnswer,
    QAVerdicts,
    citation_is_faithful,
    entities_match,
    is_correct,
    load_answers,
    load_qa_verdicts,
    score_qa,
)
from kgbuilder.validation.qa_gold import (
    ChunkEvidence,
    CorpusSpec,
    Expected,
    ExpectedEntity,
    QAGold,
    QAQuestion,
    QuestionType,
    RecordEvidence,
    Route,
    check_qa_gold,
    load_qa_gold,
)

LAMP_0 = "reviews/lamp.md#0"
LAMP_1 = "reviews/lamp.md#1"
KETTLE_0 = "reviews/kettle.md#0"
CHUNKS = {
    LAMP_0: "# Lamp reviews\n\nThe **switch** broke after a week. The shade is fine.",
    LAMP_1: "The switch does not break easily, it feels solid.",
    KETTLE_0: "# Kettle reviews\n\nThe lid rattles when the water boils.",
}
ROWS = {"parts.csv": [{"part_id": "P1", "part_name": "Switch", "supplier": "Acme Ltd"}]}


def question(
    qid: str, qtype: QuestionType, expected: Expected, route: Route = Route.RETRIEVAL, **evidence: list
) -> QAQuestion:
    """A gold question; its evidence defaults to the lamp's broken switch."""
    evidence.setdefault(
        "chunks", [ChunkEvidence(chunk_id=LAMP_0, quote="The **switch** broke after a week.")]
    )
    return QAQuestion(
        id=qid, type=qtype, question=f"question {qid}?", expected=expected, route=route, **evidence
    )


def gold(*questions: QAQuestion) -> QAGold:
    corpus = CorpusSpec(data_dir="reviews", chunk_max_chars=1500, chunk_min_chars=200, chunk_overlap_chars=0)
    return QAGold(
        dataset="test", written_by="claude", date="2026-09-30", corpus=corpus, questions=list(questions)
    )


LAMP = ExpectedEntity(name="Lamp", aliases=["Desk Lamp"])
BROKEN = question("Q1", QuestionType.LOOKUP, Expected(entities=[LAMP]))
COUNT = question("Q2", QuestionType.AGGREGATION, Expected(number=1), route=Route.EXACT)
SHADE = question(
    "Q3",
    QuestionType.LOOKUP,
    Expected(text="The shade is fine."),
    chunks=[ChunkEvidence(chunk_id=LAMP_0, quote="The shade is fine.")],
)
SUPPLIER = question(
    "Q4",
    QuestionType.STRUCTURED_FILTER,
    Expected(entities=[ExpectedEntity(name="Acme Ltd")]),
    route=Route.EXACT,
    chunks=[],
    records=[RecordEvidence(file="parts.csv", row={"part_name": "Switch", "supplier": "Acme Ltd"})],
)
GOLD = gold(BROKEN, COUNT, SHADE, SUPPLIER)


def answer(qid: str, **fields: object) -> QAAnswer:
    return QAAnswer(question_id=qid, **fields)


def right_answers() -> list[QAAnswer]:
    """A right answer to every question of GOLD (Q3's free text is right only if the judge says so)."""
    return [
        answer(
            "Q1",
            retrieved=[LAMP_0, KETTLE_0],
            entities=["desk lamp"],
            citations=[Citation(chunk_id=LAMP_0, quote="the switch broke after a week")],
        ),
        answer("Q2", number=1),
        answer("Q3", retrieved=[LAMP_0], text="Reviewers find the shade fine."),
        answer("Q4", entities=["Acme Ltd"]),
    ]


def verdicts(correct: bool = True, qid: str = "Q3") -> QAVerdicts:
    judge = JudgeMeta(model="claude-fable-5-1", date="2026-09-30")
    return QAVerdicts(
        judge=judge,
        gold="gold.json",
        answers="answers.jsonl",
        verdicts=[AnswerVerdict(question_id=qid, correct=correct, reason="says the shade is fine")],
    )


# --- the gold file -------------------------------------------------------------------------------


def test_an_expected_answer_has_exactly_one_form():
    with pytest.raises(ValidationError, match="exactly one"):
        Expected(entities=[], number=1)
    with pytest.raises(ValidationError, match="exactly one"):
        Expected()
    with pytest.raises(ValidationError, match="cannot be empty"):
        Expected(text="  ")
    # an empty set is an answer ("no product is reported with ..."), not a missing one
    assert Expected(entities=[]).kind == "entities"


def test_a_question_needs_evidence_and_a_retrieval_question_needs_chunks():
    with pytest.raises(ValidationError, match="give its evidence"):
        question("Q9", QuestionType.LOOKUP, Expected(number=1), route=Route.EXACT, chunks=[])
    with pytest.raises(ValidationError, match="needs chunk evidence"):
        question("Q9", QuestionType.LOOKUP, Expected(number=1), chunks=[], records=SUPPLIER.records)


def test_a_gold_whose_quotes_are_verbatim_and_whose_records_exist_fits_its_corpus():
    check_qa_gold(GOLD, CHUNKS, ROWS)


def test_every_quote_must_be_verbatim_in_its_chunk_and_every_record_in_its_file():
    unmarked = question(
        "Q5",
        QuestionType.LOOKUP,
        Expected(number=1),
        chunks=[ChunkEvidence(chunk_id=LAMP_0, quote="The switch broke")],
    )
    lowercase = question(
        "Q6",
        QuestionType.LOOKUP,
        Expected(number=1),
        chunks=[ChunkEvidence(chunk_id=LAMP_1, quote="the switch")],
    )
    unknown_chunk = question(
        "Q7",
        QuestionType.LOOKUP,
        Expected(number=1),
        chunks=[ChunkEvidence(chunk_id="reviews/sofa.md#0", quote="x")],
    )
    wrong_cell = SUPPLIER.model_copy(
        update={"id": "Q8", "records": [RecordEvidence(file="parts.csv", row={"supplier": "Acme"})]}
    )
    no_file = SUPPLIER.model_copy(
        update={"id": "Q9", "records": [RecordEvidence(file="suppliers.csv", row={"supplier": "Acme Ltd"})]}
    )
    with pytest.raises(InvalidGoldError) as raised:
        check_qa_gold(
            gold(BROKEN, BROKEN, unmarked, lowercase, unknown_chunk, wrong_cell, no_file), CHUNKS, ROWS
        )
    issues = "\n".join(raised.value.issues)
    assert "'Q1' is used twice" in issues
    # the gold is copied from the file: dropped markdown or another case is a copying mistake
    assert "Q5: 'The switch broke' is not verbatim" in issues
    assert "Q6: 'the switch' is not verbatim" in issues
    assert "Q7: no chunk 'reviews/sofa.md#0'" in issues
    # a cell must equal the row's value; a prefix of it is not the record
    assert "Q8: no row of parts.csv" in issues
    assert "Q9: no staged file 'suppliers.csv'" in issues


def test_loading_a_malformed_gold_file_names_the_field(tmp_path):
    path = tmp_path / "gold.json"
    data = GOLD.model_dump(mode="json")
    data["questions"][0]["type"] = "trivia"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InvalidGoldError, match=r"questions\.0\.type"):
        load_qa_gold(path)
    path.write_text(GOLD.model_dump_json(), encoding="utf-8")
    assert load_qa_gold(path) == GOLD


# --- answer correctness --------------------------------------------------------------------------


def test_a_set_is_right_only_when_it_names_every_expected_entity_and_nothing_else():
    expected = [ExpectedEntity(name="Jönköping Table"), LAMP]
    # names compare with norm (case, accents) and through aliases
    assert entities_match(expected, ["jonkoping table", "DESK LAMP"])
    assert not entities_match(expected, ["Jönköping Table"])  # one missing
    assert not entities_match(expected, ["Jönköping Table", "Lamp", "Kettle"])  # one too many
    assert entities_match([], [])  # "none" answered as none
    assert not is_correct(Expected(entities=[LAMP]), answer("Q1", text="the lamp"), None)  # no set given


def test_numbers_must_be_equal():
    three = Expected(number=0.3)
    assert is_correct(three, answer("Q", number=0.1 + 0.2), None)  # float dust only
    assert not is_correct(three, answer("Q", number=0.31), None)
    assert not is_correct(three, answer("Q", text="0.3"), None)


def test_free_text_answers_are_decided_by_the_judges_verdict():
    assert score_qa(GOLD, right_answers(), CHUNKS, k=5, verdicts=verdicts()).overall.correct.k == 4
    assert (
        score_qa(GOLD, right_answers(), CHUNKS, k=5, verdicts=verdicts(correct=False)).overall.correct.k == 3
    )


def test_free_text_answers_need_a_verdict_file_that_covers_exactly_them(tmp_path):
    with pytest.raises(EvaluationError, match="1 free-text answers need the judge's verdict file"):
        score_qa(GOLD, right_answers(), CHUNKS, k=5)
    with pytest.raises(EvaluationError, match="verdicts are for questions it does not cover"):
        score_qa(GOLD, right_answers(), CHUNKS, k=5, verdicts=verdicts(qid="Q1"))
    path = tmp_path / "verdicts.json"
    path.write_text(verdicts().model_dump_json(), encoding="utf-8")
    assert load_qa_verdicts(path) == verdicts()
    path.write_text(verdicts().model_dump_json().replace("says the shade is fine", ""), encoding="utf-8")
    with pytest.raises(EvaluationError, match=r"verdicts\.0\.reason"):
        load_qa_verdicts(path)


# --- citations and retrieval ---------------------------------------------------------------------


def test_a_citation_counts_only_when_its_own_retrieved_chunk_holds_the_quote():
    given = answer("Q1", retrieved=[LAMP_0, KETTLE_0])
    # the model dropped the markdown and the case: still the chunk's words
    assert citation_is_faithful(Citation(chunk_id=LAMP_0, quote="the switch broke"), given, CHUNKS)
    # a chunk the reader was never given cannot be the source of its answer
    assert not citation_is_faithful(Citation(chunk_id=LAMP_1, quote="feels solid"), given, CHUNKS)
    # the words exist in a retrieved chunk, but not in the one cited: a wrong path back to the source
    assert not citation_is_faithful(Citation(chunk_id=KETTLE_0, quote="the shade is fine"), given, CHUNKS)
    assert not citation_is_faithful(Citation(chunk_id=LAMP_0, quote="**"), given, CHUNKS)  # nothing quoted

    answers = right_answers()
    answers[0].citations.append(Citation(chunk_id=LAMP_1, quote="feels solid"))
    faithful = score_qa(GOLD, answers, CHUNKS, k=5, verdicts=verdicts()).overall.faithful
    assert (faithful.k, faithful.n) == (1, 2)


def test_a_cited_retrieved_chunk_without_text_cannot_be_checked():
    with pytest.raises(EvaluationError, match="1 cited chunks have no text"):
        score_qa(GOLD, right_answers(), {KETTLE_0: CHUNKS[KETTLE_0]}, k=5, verdicts=verdicts())


def test_recall_at_k_counts_gold_chunks_in_the_top_k_of_retrieval_questions_only():
    answers = right_answers()
    answers[0] = answers[0].model_copy(update={"retrieved": [KETTLE_0, LAMP_0]})
    at_1 = score_qa(GOLD, answers, CHUNKS, k=1, verdicts=verdicts()).overall.recall_at_k
    at_2 = score_qa(GOLD, answers, CHUNKS, k=2, verdicts=verdicts()).overall.recall_at_k
    # Q1 needs lamp#0 (2nd), Q3 needs lamp#0 (1st); the exact-route Q2 cites lamp#0 too but is not counted
    assert (at_1.k, at_1.n) == (1, 2)
    assert (at_2.k, at_2.n) == (2, 2)
    with pytest.raises(ValueError, match="k >= 1"):
        score_qa(GOLD, answers, CHUNKS, k=0, verdicts=verdicts())


def test_every_score_is_given_per_type_with_its_n_and_interval():
    report = score_qa(GOLD, right_answers(), CHUNKS, k=5, verdicts=verdicts(correct=False))
    assert set(report.by_type) == set(QuestionType)
    lookup = report.by_type[QuestionType.LOOKUP]
    assert (lookup.questions, lookup.correct.k, lookup.correct.n) == (2, 1, 2)
    assert lookup.correct.low < 0.5 < lookup.correct.high
    # a type without questions has no rate, rather than a made-up 0 or 1
    assert report.by_type[QuestionType.MULTI_HOP].correct.rate is None
    assert report.by_type[QuestionType.AGGREGATION].recall_at_k.n == 0  # its one question is exact-route


# --- the answers file ----------------------------------------------------------------------------


def test_the_answers_must_cover_every_question_once():
    answers = right_answers()
    with pytest.raises(EvaluationError) as raised:
        score_qa(GOLD, [*answers[:3], answers[0], answer("Q99")], CHUNKS, k=5, verdicts=verdicts())
    issues = "\n".join(raised.value.issues)
    assert "a question has more than one answer" in issues
    assert "1 questions have no answer (first: ['Q4'])" in issues
    assert "['Q99']" in issues


def test_the_answers_file_is_read_line_by_line_and_a_bad_line_is_named(tmp_path):
    path = tmp_path / "answers.jsonl"
    lines = [a.model_dump_json() for a in right_answers()]
    path.write_text("\n".join([lines[0], "", *lines[1:]]) + "\n", encoding="utf-8")
    assert load_answers(path) == right_answers()
    path.write_text("\n".join([lines[0], '{"question_id": "Q2", "number": "many"}']), encoding="utf-8")
    with pytest.raises(EvaluationError, match="answers line 2"):
        load_answers(path)
