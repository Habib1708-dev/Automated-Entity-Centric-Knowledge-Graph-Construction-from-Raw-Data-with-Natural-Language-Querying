"""Entity-resolution scoring (validation/er.py): exact lookup, pairs with a missing name counted apart,
the judge's name-to-entity verdicts and their coverage guard, and the eval stage logging both ER metrics
against Neo4j."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.validation.er import PairVerdict, SheetEntity, build_er_sheet, score_er, score_er_verdicts
from kgbuilder.validation.gold import GoldPair
from kgbuilder.validation.judge import JudgeMeta, Verdicts

from .fakes import RecordingTracker

ENTITIES = [
    SheetEntity(id="t", type="Product", name="Table", aliases=["Tables"]),
    SheetEntity(id="l", type="Product", name="Table Lamp"),
    SheetEntity(id="h", type="Component", name="the pre-drilled holes"),
]
PAIRS = [
    GoldPair(a="table", b="Tables", same=True),  # 0: merged, right
    GoldPair(a="Table", b="Table Lamp", same=False),  # 1: kept apart, right
    GoldPair(a="Table Lamp", b="Lamp", same=True),  # 2: "Lamp" is no entity: not extracted by name
    GoldPair(a="predrilled holes", b="pre-drilled holes", same=True),  # 3: neither name exact
]


def test_exact_er_scores_only_pairs_whose_names_both_exist():
    sheet = build_er_sheet(ENTITIES, PAIRS)
    assert [p.index for p in sheet.to_judge()] == [2, 3]
    score = score_er(sheet)
    # before R33 pair 2 counted as "not merged", a resolution error; now it is set apart
    assert (score.accuracy, score.scored, score.correct, score.not_extracted) == (1.0, 2, 2, 2)


def test_no_scorable_pair_gives_no_accuracy_instead_of_zero():
    score = score_er(build_er_sheet([], PAIRS))
    assert score.accuracy is None and score.not_extracted == 4


def test_judge_places_names_and_code_decides_merged():
    sheet = build_er_sheet(ENTITIES, PAIRS)
    score = score_er_verdicts(
        sheet,
        [
            PairVerdict(
                pair_index=2, a_entity="l", b_entity=None, reason="no lamp other than the table lamp"
            ),
            PairVerdict(pair_index=3, a_entity="h", b_entity="h", reason="both spellings are entity h"),
        ],
    )
    # pairs 0, 1 exact and right; pair 3 placed on one entity and `same`: right; pair 2 still missing
    assert (score.accuracy, score.scored, score.not_extracted) == (1.0, 3, 1)

    split = score_er_verdicts(
        sheet,
        [
            PairVerdict(
                pair_index=2, a_entity="l", b_entity="t", reason="the judge reads 'Lamp' as the table"
            ),
            PairVerdict(pair_index=3, a_entity="h", b_entity=None, reason="-"),
        ],
    )
    assert (split.correct, split.scored) == (2, 3)  # pair 2 on two entities although `same`: wrong


def test_er_verdicts_that_do_not_fit_the_sheet_are_refused():
    sheet = build_er_sheet(ENTITIES, PAIRS)
    ok = [PairVerdict(pair_index=i, a_entity=None, b_entity=None, reason="-") for i in (2, 3)]
    with pytest.raises(EvaluationError, match="1 ER pairs have no verdict"):
        score_er_verdicts(sheet, ok[:1])
    with pytest.raises(EvaluationError, match="already placed"):
        score_er_verdicts(sheet, ok + [PairVerdict(pair_index=0, a_entity=None, b_entity=None, reason="-")])
    with pytest.raises(EvaluationError, match="more than one verdict"):
        score_er_verdicts(sheet, ok + ok[:1])
    with pytest.raises(EvaluationError, match="not on the sheet"):
        score_er_verdicts(
            sheet, [ok[0], PairVerdict(pair_index=3, a_entity="gone", b_entity=None, reason="-")]
        )


@pytest.mark.neo4j
def test_eval_stage_logs_exact_and_validated_er_accuracy(driver, tmp_path):
    driver.execute_query(
        "CREATE (:Entity {id: 'd', name: 'drawer', type: 'Component', aliases: ['drawers']}), "
        "(:Entity {id: 'r', name: 'the metal rails', type: 'Component'})"
    )
    gold_file = tmp_path / "gold.json"
    pairs = [
        {"a": "drawers", "b": "drawer", "same": True},
        {"a": "drawer rails", "b": "metal rails", "same": True},
    ]
    gold_file.write_text(json.dumps({"er_pairs": pairs}), encoding="utf-8")
    out = tmp_path / "out"
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker)

    report = run_stages(ctx, PipelineState(gold=gold_file), [st.EvalStage()]).evaluation
    assert report.er.accuracy == 1.0 and report.er.not_extracted == 1 and report.er_valid is None
    sheet = json.loads((out / "judge_sheet.json").read_text(encoding="utf-8"))
    assert [p["index"] for p in sheet["er"]["pairs"] if not (p["a_entities"] and p["b_entities"])] == [1]

    verdict_file = tmp_path / "judge_verdicts.json"
    verdict_file.write_text(
        Verdicts(
            judge=JudgeMeta(model="claude-test", date="2026-09-23"),
            gold="gold.json",
            er=[
                PairVerdict(pair_index=1, a_entity="r", b_entity="r", reason="the rails are the drawer rails")
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    run_stages(ctx, PipelineState(gold=gold_file, verdicts=verdict_file), [st.EvalStage()])
    metrics = tracker.runs[-1].logged_metrics
    assert metrics["er_accuracy"] == 1.0 and metrics["er_not_extracted"] == 1
    assert metrics["er_accuracy_valid"] == 1.0 and metrics["er_not_extracted_valid"] == 0
    assert metrics["er_pairs_scored_valid"] == 2
