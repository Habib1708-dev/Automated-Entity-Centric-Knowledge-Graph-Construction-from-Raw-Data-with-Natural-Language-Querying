"""LLM-as-a-judge scoring as pure functions: the sheet (exact-match shortcut, stable fact ids), the verdict
file's own consistency rules, the validated metrics, the stale/incomplete verdict guard, and the eval
stage's tracking contract (params, metrics, artifacts) against Neo4j."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.validation.checks.base import StoredFact
from kgbuilder.validation.gold import GoldTriple
from kgbuilder.validation.judge import (
    FactVerdict,
    GoldVerdict,
    JudgeMeta,
    UnsupportedReason,
    Verdict,
    Verdicts,
    build_sheet,
    fact_id,
    score_verdicts,
)

from .fakes import RecordingTracker


def fact(subject: str, obj: str, predicate: str = "HAS_DEFECT", chunk: str = "a.md#0") -> StoredFact:
    return StoredFact(
        predicate=predicate, subject_type="Product", object_type="Defect", chunk_id=chunk,
        evidence="quote", subject_names=[subject], object_names=[obj],
    )  # fmt: skip


def gold(subject: str, obj: str, predicate: str = "HAS_DEFECT", doc: str = "a.md") -> GoldTriple:
    return GoldTriple(subject=subject, predicate=predicate, object=obj, doc_id=doc, evidence="quote")


FACTS = [
    fact("Table", "wobbly legs"),  # exact match with gold 0
    fact("Table", "legs wobble"),  # paraphrase of gold 0: the judge must settle it
    fact("Table", "dent"),  # true but the labeller missed it: a gold correction
    fact("Table", "smells", predicate="EXHIBITS_FAILURE"),  # wrong relation
    fact("Table", "hard to say"),  # ambiguous
    fact("Chair", "squeaks", chunk="b.md#0"),  # out of scope: b.md is not labelled
]
GOLD = [gold("Table", "wobbly legs"), gold("Table", "scratches")]


def supported(fid: str, **extra) -> FactVerdict:
    return FactVerdict(id=fid, verdict=Verdict.SUPPORTED, reason="said", evidence="the text", **extra)


def verdicts(facts: list[FactVerdict], recall: list[GoldVerdict]) -> Verdicts:
    return Verdicts(
        judge=JudgeMeta(model="claude-test", date="2026-09-22"), gold="g.json", facts=facts, recall=recall
    )


def test_sheet_settles_exact_matches_and_lists_the_rest_for_the_judge():
    sheet = build_sheet(FACTS, GOLD)
    assert len(sheet.facts) == 5  # the b.md fact is out of scope
    assert [f.gold_index for f in sheet.facts] == [0, None, None, None, None]
    assert [f.id for f in sheet.to_judge()] == [fact_id(f) for f in FACTS[1:5]]
    assert [g.found for g in sheet.gold] == [True, False]
    assert [g.index for g in sheet.gold_to_find()] == [1]


def test_fact_ids_are_stable_for_the_same_graph_and_change_with_it():
    assert fact_id(fact("Table", "dent")) == fact_id(fact("table", "Dent"))  # normalised names, same fact
    assert fact_id(fact("Table", "dent")) != fact_id(fact("Table", "dent", chunk="a.md#1"))


def test_verdicts_carry_what_makes_them_checkable():
    with pytest.raises(ValidationError, match="evidence"):
        FactVerdict(id="x", verdict=Verdict.SUPPORTED, reason="said")
    with pytest.raises(ValidationError, match="reason_code"):
        FactVerdict(id="x", verdict=Verdict.UNSUPPORTED, reason="not said")
    with pytest.raises(ValidationError, match="only for UNSUPPORTED"):
        FactVerdict(id="x", verdict=Verdict.AMBIGUOUS, reason="?", reason_code=UnsupportedReason.NOT_IN_TEXT)
    with pytest.raises(ValidationError, match="only for SUPPORTED"):
        FactVerdict(id="x", verdict=Verdict.AMBIGUOUS, reason="?", vague=True)


def test_validated_scores_combine_exact_matches_with_verdicts():
    sheet = build_sheet(FACTS, GOLD)
    paraphrase, correction, wrong, unclear = [f.id for f in sheet.to_judge()]
    report = score_verdicts(
        sheet,
        verdicts(
            [
                supported(paraphrase, gold_index=0),
                supported(correction, vague=True),
                FactVerdict(
                    id=wrong,
                    verdict=Verdict.UNSUPPORTED,
                    reason="text says it smells, no failure",
                    reason_code=UnsupportedReason.WRONG_RELATION,
                ),  # fmt: skip
                FactVerdict(id=unclear, verdict=Verdict.AMBIGUOUS, reason="could be read both ways"),
            ],
            [GoldVerdict(gold_index=1, matched_fact=None, reason="nothing about scratches")],
        ),
    )
    # precision: 1 exact + 2 supported over 4 judgeable (the ambiguous one leaves the denominator)
    assert report.precision_validated == 0.75
    # recall: gold 0 by exact match, gold 1 not found by the judge either
    assert report.recall_validated == 0.5
    assert report.f1_validated == pytest.approx(0.6)
    assert (report.ambiguous_rate, report.vague_rate) == (0.25, 1 / 3)
    assert report.gold_corrections == 1
    assert report.unsupported_by_reason == {
        UnsupportedReason.NOT_IN_TEXT: 0,
        UnsupportedReason.WRONG_RELATION: 1,
        UnsupportedReason.WRONG_ENTITY: 0,
        UnsupportedReason.CONTRADICTED: 0,
    }
    metrics = report.metrics()
    assert metrics["unsupported_wrong_relation"] == 1 and metrics["unsupported_not_in_text"] == 0
    assert metrics["judged_facts"] == 4


def test_recall_counts_gold_the_judge_matched_to_a_fact():
    sheet = build_sheet(FACTS, GOLD)
    to_judge = [f.id for f in sheet.to_judge()]
    facts = [supported(to_judge[0]), supported(to_judge[1])] + [
        FactVerdict(id=i, verdict=Verdict.UNSUPPORTED, reason="no", reason_code=UnsupportedReason.NOT_IN_TEXT)
        for i in to_judge[2:]
    ]
    matched = verdicts(facts, [GoldVerdict(gold_index=1, matched_fact=to_judge[1], reason="dent = scratch")])
    assert score_verdicts(sheet, matched).recall_validated == 1.0


def test_verdicts_that_do_not_fit_the_sheet_are_refused():
    sheet = build_sheet(FACTS, GOLD)
    ids = [f.id for f in sheet.to_judge()]
    complete = [supported(i) for i in ids]
    unfound = [GoldVerdict(gold_index=1, matched_fact=None, reason="-")]

    with pytest.raises(EvaluationError, match="1 facts have no verdict"):
        score_verdicts(sheet, verdicts(complete[1:], unfound))
    with pytest.raises(EvaluationError, match="not on the sheet"):
        score_verdicts(sheet, verdicts(complete + [supported("stale0000000")], unfound))
    with pytest.raises(EvaluationError, match="more than one verdict"):
        score_verdicts(sheet, verdicts(complete + [supported(ids[0])], unfound))
    with pytest.raises(EvaluationError, match="unfound gold triples have no verdict"):
        score_verdicts(sheet, verdicts(complete, []))
    with pytest.raises(EvaluationError, match="already found"):
        score_verdicts(
            sheet, verdicts(complete, unfound + [GoldVerdict(gold_index=0, matched_fact=None, reason="-")])
        )
    with pytest.raises(EvaluationError, match="matched_fact ids not on the sheet"):
        score_verdicts(
            sheet, verdicts(complete, [GoldVerdict(gold_index=1, matched_fact="nope00000000", reason="-")])
        )


@pytest.mark.neo4j
def test_eval_stage_logs_the_judge_sheet_then_the_validated_metrics(driver, tmp_path):
    driver.execute_query(
        "CREATE (d:Document {doc_id: 'a.md'}), (c:Chunk {chunk_id: 'a.md#0', text: 'x'})-[:PART_OF]->(d), "
        "(t:Entity {id: '1', name: 'Table', type: 'Product'}), "
        "(w:Entity {id: '2', name: 'legs wobble', type: 'Defect'}), "
        "(c)-[:MENTIONS]->(t), (t)-[:HAS_DEFECT {chunk_id: 'a.md#0', evidence: 'legs wobble'}]->(w)"
    )
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(json.dumps([GOLD[0].model_dump()]), encoding="utf-8")
    out = tmp_path / "out"
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker)

    # first pass: exact match misses the paraphrase, the sheet says one fact needs a judge
    report = run_stages(ctx, PipelineState(gold=gold_file), [st.EvalStage()]).evaluation
    assert report.triples.recall == 0.0 and report.judge is None
    sheet = json.loads((out / "judge_sheet.json").read_text(encoding="utf-8"))
    assert [f["gold_index"] for f in sheet["facts"]] == [None]
    assert "judge_sheet" not in json.loads((out / "eval_report.json").read_text(encoding="utf-8"))
    first = tracker.run("eval")
    assert first.logged_params["gold_hash"] and "judge_model" not in first.logged_params
    assert {Path(a).name for a in first.artifacts} == {"gold.json", "judge_sheet.json", "eval_report.json"}

    # second pass: the judge's verdicts turn the miss into a validated hit
    verdict_file = tmp_path / "judge_verdicts.json"
    verdict_file.write_text(
        verdicts(
            [supported(sheet["facts"][0]["id"], gold_index=0)],
            [GoldVerdict(gold_index=0, matched_fact=sheet["facts"][0]["id"], reason="paraphrase")],
        ).model_dump_json(),
        encoding="utf-8",
    )
    report = run_stages(
        ctx, PipelineState(gold=gold_file, verdicts=verdict_file), [st.EvalStage()]
    ).evaluation
    second = tracker.runs[-1]
    assert (
        second.logged_params["judge_model"] == "claude-test" and second.logged_params["judge_verdicts_hash"]
    )
    assert second.logged_metrics["triple_recall"] == 0.0
    assert second.logged_metrics["recall_validated"] == 1.0 and second.logged_metrics["f1_validated"] == 1.0
    assert "judge_verdicts.json" in {Path(a).name for a in second.artifacts}
