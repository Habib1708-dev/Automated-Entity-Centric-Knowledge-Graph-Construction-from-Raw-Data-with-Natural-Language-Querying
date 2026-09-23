"""Re-scoring a logged judge sheet with today's matching and gold, without the graph (R48): the logged
fact ids are kept so the run's verdicts still apply, the entity list is enough to rebuild every fact's
names, a sheet written before the entity list existed is refused, and the stage's tracking contract."""

import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.validation.er import ErSheet, SheetEntity
from kgbuilder.validation.gold import GoldPair, GoldSet, GoldTriple
from kgbuilder.validation.judge import JudgeSheet, SheetFact, SheetGold
from kgbuilder.validation.rescore import rescore

from .fakes import RecordingTracker

MERGED = ["chipped in several places", "chipping at the corners"]
ENTITIES = [
    SheetEntity(id="v", type="Component", name="veneer", aliases=["veneer"]),
    SheetEntity(id="c", type="Defect", name=MERGED[0], aliases=MERGED),
]


def fact(fid: str, doc: str, gold_index: int | None) -> SheetFact:
    return SheetFact(
        id=fid, doc_id=doc, subject="veneer", predicate="HAS_DEFECT", object=MERGED[0],
        subject_type="Component", object_type="Defect", evidence="quote", gold_index=gold_index,
    )  # fmt: skip


def gold_row(index: int, obj: str, doc: str, found: bool) -> SheetGold:
    return SheetGold(
        index=index, subject="veneer", predicate="HAS_DEFECT", object=obj, doc_id=doc, evidence="quote",
        found=found,
    )  # fmt: skip


# the sheet R41 logged, in miniature: the b.md fact matched a.md's triple through the merged alias
LOGGED = JudgeSheet(
    facts=[fact("f-a", "a.md", 0), fact("f-b", "b.md", 0)],
    gold=[gold_row(0, MERGED[1], "a.md", True), gold_row(1, "chipped", "b.md", False)],
    er=ErSheet(entities=ENTITIES, pairs=[]),
)
GOLD = GoldSet(
    triples=[
        GoldTriple(subject="veneer", predicate="HAS_DEFECT", object=MERGED[1], doc_id="a.md"),
        GoldTriple(subject="veneer", predicate="HAS_DEFECT", object="chipped", doc_id="b.md"),
    ],
    er_pairs=[GoldPair(a=MERGED[1], b=MERGED[0], same=True)],
)


def test_rescoring_applies_todays_matching_and_keeps_the_logged_ids():
    report = rescore(LOGGED, GOLD)
    sheet = report.judge_sheet
    assert [(f.id, f.gold_index) for f in sheet.facts] == [("f-a", 0), ("f-b", None)]
    assert [g.found for g in sheet.gold] == [True, False]
    assert report.triples.precision == 0.5 and report.triples.recall == 0.5
    # today's ER pairs, placed on the logged entities
    assert [(p.a_entities, p.b_entities) for p in sheet.er.pairs] == [(["c"], ["c"])]
    assert report.er.accuracy == 1.0


def test_a_sheet_without_its_entity_list_cannot_be_rescored():
    # before R33 the sheet kept only each fact's display name; matching needs every alias
    with pytest.raises(EvaluationError):
        rescore(LOGGED.model_copy(update={"er": None}), GOLD)


def test_rescore_stage_logs_its_sources_and_the_scores(driver, tmp_path):
    sheet_file = tmp_path / "judge_sheet.json"
    sheet_file.write_text(LOGGED.model_dump_json(), encoding="utf-8")
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(GOLD.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)

    run_stages(ctx, PipelineState(gold=gold_file, sheet=sheet_file), [st.RescoreStage()])
    run = tracker.run("rescore")
    assert run.logged_params["sheet_hash"] and run.logged_params["gold_hash"]
    assert run.logged_metrics["triple_precision"] == 0.5 and run.logged_metrics["er_accuracy"] == 1.0
    written = json.loads((tmp_path / "out" / st.RescoreStage.SHEET_FILE).read_text(encoding="utf-8"))
    assert [f["gold_index"] for f in written["facts"]] == [0, None]
    assert {Path(a).name for a in run.artifacts} >= {"gold.json", "judge_sheet.json"}
