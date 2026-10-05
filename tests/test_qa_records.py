"""Record questions computed by code (R73): every structured source file is a DuckDB view named by its stem
(CSV, a wrapped JSON object, NDJSON, in any folder), a set question's answer is its query's first column,
a number question's its one value, and a stored answer that differs from the data is refused. No Neo4j,
no LLM; the files are written into a temporary corpus."""

import json

import pytest

from kgbuilder.core.errors import InvalidGoldError
from kgbuilder.validation.qa_gold import (
    CorpusSpec,
    Expected,
    ExpectedEntity,
    QAGold,
    QAQuestion,
    QuestionType,
    Route,
)
from kgbuilder.validation.qa_records import check_record_answers, computed_answer, connect, source_views


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "parts.csv").write_text(
        "part_id,name,weight_g\nP1,Hinge,40\nP2,Latch,15\nP3,Hinge cover,5\n", encoding="utf-8"
    )
    # a wrapper object around the list of records, as an API export writes it
    orders = {"count": 2, "results": [{"order": "O1", "part": "P1"}, {"order": "O2", "part": "P1"}]}
    (tmp_path / "orders.json").write_text(json.dumps(orders), encoding="utf-8")
    (tmp_path / "notes.ndjson").write_text(
        '{"part": "P2", "ok": false}\n{"part": "P3", "ok": true}\n', encoding="utf-8"
    )
    (tmp_path / "shop" / "review.md").write_text("# Hinge\n\nSturdy.", encoding="utf-8")  # not a table
    return tmp_path


def ask(qid: str, sql: str, expected: Expected) -> QAQuestion:
    return QAQuestion(
        id=qid,
        type=QuestionType.AGGREGATION,
        question=f"{qid}?",
        expected=expected,
        route=Route.EXACT,
        sql=sql,
    )


def gold(*questions: QAQuestion) -> QAGold:
    spec = CorpusSpec(data_dir="x", chunk_max_chars=1500, chunk_min_chars=200, chunk_overlap_chars=0)
    return QAGold(dataset="t", written_by="claude", date="2026-10-05", corpus=spec, questions=list(questions))


HEAVY = ask(
    "R1",
    "SELECT name FROM parts WHERE weight_g > 10",
    Expected(entities=[ExpectedEntity(name="Hinge"), ExpectedEntity(name="Latch")]),
)
ORDERED = ask(
    "R2",
    "SELECT count(DISTINCT r.order) FROM (SELECT unnest(results) AS r FROM orders) WHERE r.part = 'P1'",
    Expected(number=2),
)
NOT_OK = ask(
    "R3",
    "SELECT p.name FROM notes n JOIN parts p ON p.part_id = n.part WHERE NOT n.ok",
    Expected(entities=[ExpectedEntity(name="Latch", aliases=["the latch"])]),
)
NONE = ask("R4", "SELECT name FROM parts WHERE weight_g > 1000", Expected(entities=[]))


def test_every_structured_file_is_a_view_named_by_its_stem_in_any_folder(corpus):
    assert sorted(source_views(corpus)) == ["notes", "orders", "parts"]


def test_two_files_with_one_stem_are_refused(corpus):
    (corpus / "parts.json").write_text("[]", encoding="utf-8")
    with pytest.raises(InvalidGoldError, match="both the view 'parts'"):
        source_views(corpus)


def test_a_set_is_the_first_columns_distinct_values_and_a_number_its_one_value(corpus):
    con = connect(corpus)
    assert computed_answer(con, HEAVY) == ["Hinge", "Latch"]
    assert computed_answer(con, ORDERED) == 2.0  # through the wrapper object's list
    assert computed_answer(con, NOT_OK) == ["Latch"]  # a join of an NDJSON file with a CSV
    assert computed_answer(con, NONE) == []
    two_rows = ask("R5", "SELECT weight_g FROM parts", Expected(number=40))
    with pytest.raises(InvalidGoldError, match="must return one number"):
        computed_answer(con, two_rows)
    with pytest.raises(InvalidGoldError, match="the query failed"):
        computed_answer(con, ask("R6", "SELECT nothing FROM nowhere", Expected(number=1)))


def test_a_gold_that_stores_what_the_data_gives_passes(corpus):
    check_record_answers(gold(HEAVY, ORDERED, NOT_OK, NONE), corpus)


def test_a_stored_answer_that_differs_from_the_data_is_refused(corpus):
    stale_set = HEAVY.model_copy(
        update={"id": "R7", "expected": Expected(entities=[ExpectedEntity(name="Hinge")])}
    )
    stale_number = ORDERED.model_copy(update={"id": "R8", "expected": Expected(number=3)})
    # the gold names a record as the data does: an alias does not stand in for the record's own value
    by_alias = NOT_OK.model_copy(
        update={
            "id": "R9",
            "expected": Expected(entities=[ExpectedEntity(name="the latch", aliases=["Latch"])]),
        }
    )
    with pytest.raises(InvalidGoldError) as raised:
        check_record_answers(gold(stale_set, stale_number, by_alias, HEAVY), corpus)
    issues = "\n".join(raised.value.issues)
    assert "R7: the gold names ['Hinge'], the data gives ['Hinge', 'Latch']" in issues
    assert "R8: the gold says 3.0, the data gives 2.0" in issues
    assert "R9: the gold names ['the latch']" in issues
    assert "R1" not in issues
