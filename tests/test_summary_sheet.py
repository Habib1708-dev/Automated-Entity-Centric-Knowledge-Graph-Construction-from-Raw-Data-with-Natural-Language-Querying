"""The judging sheet of the LLM node summaries (R124b, `kg summary-sheet`), on the shared hand-made graph of
test_hybrid_units.py: a units file of summaries written from the graph's evidence becomes a sheet with each
node's numbered facts as its prompt showed them, and the stage logs the file's hash and the strata; a file
whose summaries came from other evidence, that misses a node, or that holds template cards is refused before
any sheet is written. Needs Neo4j."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.hybrid import RenderedCard, evidence_hash
from kgbuilder.hybrid.summaries import facts, numbered_facts
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import card_representation, read_units
from kgbuilder.pipeline.inputs import digest
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.pipeline.summary_stages import SHEET_FILE, SummarySheetStage
from kgbuilder.validation.summary_judging import SummarySheet

from .fakes import RecordingTracker
from .test_hybrid_units import RELATED_PLAN, load


def context(driver, tmp_path):
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    (out / PLAN_FILE).write_text(RELATED_PLAN.model_dump_json(), encoding="utf-8")
    return PipelineContext(settings=Settings(), driver=driver, out=out, tracker=RecordingTracker())


def units_file(tmp_path, cards: list[RenderedCard]):
    path = tmp_path / "units.jsonl"
    path.write_text("\n".join(c.model_dump_json(exclude_none=True) for c in cards) + "\n", encoding="utf-8")
    return path


def summaries(ctx):
    evidence = read_units(ctx, PipelineState(), card_representation(ctx.settings, "template"), RELATED_PLAN)
    return evidence.evidence, [
        RenderedCard(ref=e.ref, text=f"{e.title} is a node.", evidence_hash=evidence_hash(e), truncated=False,
                     facts_used=[], fallback=False)
        for e in evidence.evidence
    ]  # fmt: skip


def test_the_sheet_shows_each_sampled_nodes_facts_as_its_prompt_did_and_its_summary(driver, tmp_path):
    load(driver)
    ctx = context(driver, tmp_path)
    evidence, cards = summaries(ctx)
    path = units_file(tmp_path, cards)
    run_stages(ctx, PipelineState(), [SummarySheetStage(path)])
    sheet = SummarySheet.model_validate_json((ctx.out / SHEET_FILE).read_text(encoding="utf-8"))
    by_ref = {e.ref: e for e in evidence}
    assert len(sheet.rows) == len(evidence) <= 50  # every stratum here is smaller than ten
    for row in sheet.rows:
        assert (
            row.facts == numbered_facts(facts(by_ref[row.ref])) and row.summary == f"{row.title} is a node."
        )
    # the press holds a denied and a conditional claim: a qualified record
    assert next(r for r in sheet.rows if r.ref == "Press:P1").stratum == "record_qualified"
    run = ctx.tracker.run("summary_sheet")
    assert run.logged_params["units_hash"] == digest(path) == sheet.source_hash
    assert run.logged_metrics["rows"] == len(evidence) and "population_concept" in run.logged_metrics


@pytest.mark.parametrize(
    "change, message",
    [
        ("other evidence", "Press:P1: the summary was written from other evidence"),
        ("missing", "Press:P1 has no summary"),
        ("template", "holds no summary cards"),
    ],
)
def test_summaries_not_written_from_the_graphs_evidence_are_refused(driver, tmp_path, change, message):
    load(driver)
    ctx = context(driver, tmp_path)
    _, cards = summaries(ctx)
    if change == "other evidence":
        cards = [
            c.model_copy(update={"evidence_hash": "0" * 16}) if c.ref == "Press:P1" else c for c in cards
        ]
    elif change == "missing":
        cards = [c for c in cards if c.ref != "Press:P1"]
    else:
        cards = [c.model_copy(update={"fallback": None, "facts_used": None}) for c in cards]
    with pytest.raises(EvaluationError, match=message):
        run_stages(ctx, PipelineState(), [SummarySheetStage(units_file(tmp_path, cards))])
    assert not (ctx.out / SHEET_FILE).exists()
