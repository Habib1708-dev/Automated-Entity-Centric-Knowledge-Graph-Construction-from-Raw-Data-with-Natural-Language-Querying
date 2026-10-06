"""The failure table of layered-model Step 8 (R83): the committed cause files classify every wrong answer
of the reference run (R77 part f, whose answers R80 froze in tests/gold/r80/) exactly once, with a cause
and a fix from the closed lists of tests/gold/r83/rules.md, and with the question's gold type.

Role: guards the table the Step 8 decision rests on, so it cannot drift from the run it describes. The
wrong answers are recomputed here by the scorer from the frozen answers, the gold and the judge's
verdicts, never copied from out/. Not here: the classification itself (Claude, by the rules file), any
graph, any LLM.
"""

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from kgbuilder.query.answers import load_system_answers, shown_texts
from kgbuilder.validation.qa import load_qa_verdicts, score_qa
from kgbuilder.validation.qa_gold import load_qa_gold

from .qa_corpus import REPO

GOLD = REPO / "tests" / "gold"
DATASETS = ["furniture", "heldout", "generality"]

Cause = Literal[
    "S1_right_by_meaning",
    "S2_gold_doubtful",
    "Q1_names_not_linked",
    "Q2_wrong_primitive_or_field",
    "Q3_missing_primitive",
    "Q4_fallback_wrong",
    "G1_extraction_miss",
    "G2_no_schema_type",
    "G3_identity",
    "G4_attachment",
    "G5_assertion",
    "G6_attribution",
    "G7_time_or_role",
    "G8_event_structure",
    "G9_concept",
    "G10_set_or_quantifier",
    "G11_other_graph",
    "R1_read_check_wrong",
    "R2_ranking_cut",
    "R3_reader_error",
]
Fix = Literal[
    "gold_scoring",
    "planner",
    "new_primitive",
    "read_check",
    "reader",
    "extraction_coverage",
    "open_predicates",
    "identity",
    "llm_attachment",
    "assertion",
    "speaker",
    "valid_time",
    "events",
    "concept_typing",
    "sets_quantifiers",
    "read_check_writeback",
    "query_code",
    "document_fields",
    "none",
]


class FailureRow(BaseModel):
    """One wrong answer: its first wrong step's cause, the other causes it also needs fixed, the evidence
    from the files, the most direct fix, and the earlier runs (R79, R77 part c) that got it right."""

    question_id: str
    type: str
    cause: Cause
    also: list[Cause]
    first_wrong_step: str
    evidence: str
    fix: Fix
    right_in: list[Literal["r79", "r77c"]]


class FailureTable(BaseModel):
    dataset: str
    run: str
    classified_by: str
    reviewed_by: str
    rules: str
    rows: list[FailureRow]


def _wrong_answers(dataset: str) -> dict[str, str]:
    """Question id -> type of every answer the reference run got wrong, scored as `kg qa-score` did."""
    gold = load_qa_gold(GOLD / "qa" / f"{dataset}_qa.json")
    answers = load_system_answers(GOLD / "r80" / dataset / "answers_graph.jsonl")
    verdicts = load_qa_verdicts(GOLD / "r77" / f"{dataset}_partd_graph_verdicts.json")
    report = score_qa(gold, answers, shown_texts(answers), k=5, verdicts=verdicts, system="graph")
    return {o.question_id: o.type for o in report.outcomes if o.correct is False}


@pytest.mark.parametrize("dataset", DATASETS)
def test_every_wrong_answer_is_classified_once_with_its_gold_type(dataset: str) -> None:
    path = GOLD / "r83" / f"{dataset}_failure_causes.json"
    table = FailureTable.model_validate_json(Path(path).read_text(encoding="utf-8"))
    ids = [r.question_id for r in table.rows]
    assert table.dataset == dataset
    assert len(ids) == len(set(ids)), "a question is classified twice"
    wrong = _wrong_answers(dataset)
    assert set(ids) == set(wrong)
    assert {r.question_id: r.type for r in table.rows} == wrong
    assert all(r.cause not in r.also for r in table.rows), "a row repeats its cause under 'also'"
    assert all(r.evidence.strip() for r in table.rows)
