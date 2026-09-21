"""Evaluation scoring as pure functions, the gold file formats, and the check families plus read-only
question answering against Neo4j."""

import json

import pytest
from neo4j.exceptions import Neo4jError

from kgbuilder.validation.checks import CheckContext
from kgbuilder.validation.checks.base import StoredFact
from kgbuilder.validation.evaluate import (
    GoldPair,
    GoldQuestion,
    GoldTriple,
    Score,
    load_gold,
    run_questions,
    score_entities,
    score_er,
    score_triples,
)
from kgbuilder.validation.report import CheckOutput
from kgbuilder.validation.validator import validate_graph


def fact(
    subject: list[str], obj: list[str], chunk: str = "a.md#0", predicate: str = "HAS_PROBLEM"
) -> StoredFact:
    return StoredFact(
        predicate=predicate, subject_type="Product", object_type="Problem", chunk_id=chunk,
        evidence="quote", subject_names=subject, object_names=obj,
    )  # fmt: skip


FACTS = [
    fact(["Table", "Tables"], ["wobbles"]),  # correct, found through the alias too
    fact(["Table", "Tables"], ["bad smell"]),  # wrong: not in gold
    fact(["Chair"], ["squeaks"], chunk="b.md#0"),  # from a document nobody labelled
]
GOLD = [
    GoldTriple(subject="tables", predicate="HAS_PROBLEM", object="Wobbles", doc_id="a.md"),
    GoldTriple(subject="Table", predicate="HAS_PROBLEM", object="scratches", doc_id="a.md"),  # missed
]


def test_triple_scores_are_limited_to_labelled_documents():
    score = score_triples(FACTS, GOLD)
    assert (score.predicted, score.gold) == (2, 2)  # the b.md fact is out of scope
    assert score.precision == 0.5 and score.recall == 0.5 and score.f1 == 0.5


def test_without_doc_ids_every_fact_counts_towards_precision():
    gold = [g.model_copy(update={"doc_id": None}) for g in GOLD]
    assert score_triples(FACTS, gold).predicted == 3


def test_entity_scores_ignore_how_entities_are_connected():
    score = score_entities(FACTS, GOLD)
    # predicted in a.md: {table, tables}, {wobbles}, {bad smell}
    # gold names: tables, wobbles, table, scratches
    assert score.predicted == 3 and score.precision == pytest.approx(2 / 3)
    assert score.gold == 4 and score.recall == 0.75


def test_empty_sides_do_not_divide_by_zero():
    assert Score.of(0, 0, 0, 0).f1 == 1.0
    assert score_triples([], GOLD).recall == 0.0 and score_triples([], GOLD).precision == 1.0


def test_er_accuracy_counts_correct_merges_and_correct_separations():
    names = [["Table", "Tables"], ["Table Lamp"]]
    pairs = [
        GoldPair(a="table", b="Tables", same=True),  # merged: right
        GoldPair(a="Table", b="Table Lamp", same=False),  # kept apart: right
        GoldPair(a="Table Lamp", b="Lamp", same=True),  # not merged: wrong
    ]
    assert score_er(names, pairs) == pytest.approx(2 / 3)


def test_gold_file_may_be_a_bare_triple_list_or_sections(tmp_path):
    triple = {"subject": "a", "predicate": "P", "object": "b"}
    (tmp_path / "list.json").write_text(json.dumps([triple]), encoding="utf-8")
    (tmp_path / "full.json").write_text(
        json.dumps({"triples": [triple], "er_pairs": [{"a": "x", "b": "y", "same": False}]}), encoding="utf-8"
    )
    assert len(load_gold(tmp_path / "list.json").triples) == 1
    assert len(load_gold(tmp_path / "full.json").er_pairs) == 1


@pytest.mark.neo4j
def test_questions_are_answered_read_only(driver):
    driver.execute_query("CREATE (:Supplier {name: 'Nordic Wood'}), (:Supplier {name: 'Lux Metal'})")
    ask = GoldQuestion(
        question="which suppliers?",
        cypher="MATCH (s:Supplier) RETURN s.name",
        expected=["lux metal", "Nordic Wood"],
    )
    wrong = ask.model_copy(update={"expected": ["Nordic Wood"]})
    assert [r.correct for r in run_questions(driver, [ask, wrong])] == [True, False]

    destructive = ask.model_copy(update={"cypher": "MATCH (n) DETACH DELETE n RETURN 1"})
    with pytest.raises(Neo4jError):
        run_questions(driver, [destructive])
    assert driver.execute_query("MATCH (n) RETURN count(n) AS c")[0][0]["c"] == 2


@pytest.mark.neo4j
def test_checks_report_a_damaged_graph_and_custom_families_plug_in(driver):
    driver.execute_query(
        "CREATE (d:Document {doc_id: 'a.md'}), (c:Chunk {chunk_id: 'a.md#0', text: 'The table wobbles'}), "
        "(c)-[:PART_OF]->(d), (:Chunk {chunk_id: 'lost#0', text: 'x'}), "
        "(t:Entity {id: '1', name: 'table', type: 'Product'}), "
        "(w:Entity {id: '2', name: 'wobble', type: 'Problem'}), "
        "(c)-[:MENTIONS]->(t), (t)-[:HAS_PROBLEM {chunk_id: 'a.md#0', evidence: 'never said'}]->(w)"
    )

    class AlwaysFails:
        def run(self, ctx: CheckContext) -> CheckOutput:
            out = CheckOutput(metrics={"custom": 1.0})
            out.add("custom: plugged in", False, "by design", "structure")
            return out

    report = validate_graph(driver, plan=None, schema=None)
    failed = {c.name for c in report.checks if not c.passed}
    assert failed == {
        "lexical: every chunk belongs to a document",
        "provenance: every entity is mentioned in a chunk",
        "provenance: evidence quotes exist in their chunk",
    }
    assert report.metrics["evidence_verified_rate"] == 0.0 and not report.passed

    custom = validate_graph(driver, None, None, checks=[AlwaysFails()])
    assert [c.name for c in custom.checks] == ["custom: plugged in"] and custom.metrics == {"custom": 1.0}
