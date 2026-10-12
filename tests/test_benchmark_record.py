"""The benchmark record of R157 (`tests/gold/r157/outcomes.json`, committed in R158): every exact-match
outcome is what `validation/qa.py` gives for the recorded answer against its gold question and the judge's
free-text verdict, every outcome by meaning is the judge's verdict, each dataset holds its 50 questions
once, and a cause is recorded exactly for the answers wrong by either score. The thesis figures (R159) read
this record, so it must agree with the gold and the verdicts it was made from. No Neo4j, no LLM.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from kgbuilder.validation.qa import AnswerVerdict, QAAnswer, QAVerdicts, is_correct
from kgbuilder.validation.qa_gold import load_qa_gold

REPO = Path(__file__).resolve().parent.parent
R157 = REPO / "tests" / "gold" / "r157"
RECORD = json.loads((R157 / "outcomes.json").read_text(encoding="utf-8"))["questions"]
DATASETS = ["ntsb", "lighthouses", "furniture", "heldout", "generality"]


def gold_questions(dataset: str) -> dict:
    """The dataset's benchmark questions by id."""
    return {
        q.id: q for q in load_qa_gold(REPO / "tests" / "gold" / "qa" / f"{dataset}_qa_bench.json").questions
    }


def verdicts(dataset: str, kind: str) -> dict[str, AnswerVerdict]:
    """The judge's verdicts of one kind ("verdicts": free text only; "meaning": every answer) by id."""
    path = R157 / f"{dataset}_bench_agent_{kind}.json"
    return {
        v.question_id: v for v in QAVerdicts.model_validate_json(path.read_text(encoding="utf-8")).verdicts
    }


def test_every_dataset_holds_its_fifty_benchmark_questions_once():
    assert Counter(r["dataset"] for r in RECORD) == {d: 50 for d in DATASETS}
    for dataset in DATASETS:
        assert {r["question_id"] for r in RECORD if r["dataset"] == dataset} == set(gold_questions(dataset))


@pytest.mark.parametrize("dataset", DATASETS)
def test_every_outcome_is_what_the_gold_and_the_verdicts_give(dataset):
    gold = gold_questions(dataset)
    free_text, meaning = verdicts(dataset, "verdicts"), verdicts(dataset, "meaning")
    for row in (r for r in RECORD if r["dataset"] == dataset):
        question = gold[row["question_id"]]
        answer = QAAnswer(
            question_id=row["question_id"], entities=row["entities"], number=row["number"], text=row["text"]
        )
        assert row["type"] == question.type
        assert row["exact"] == is_correct(question.expected, answer, free_text.get(row["question_id"]))
        assert row["meaning"] == meaning[row["question_id"]].correct


def test_a_cause_is_recorded_for_every_wrong_answer_and_no_right_one():
    for row in RECORD:
        assert bool(row["causes"]) == (not row["exact"] or not row["meaning"]), row["question_id"]
    # the counts R157 reports
    assert (sum(r["exact"] for r in RECORD), sum(r["meaning"] for r in RECORD)) == (239, 243)
