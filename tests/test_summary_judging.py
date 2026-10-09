"""The groundedness instrument of the LLM node summaries (R124b, validation/summary_judging.py and the `kg
summary-judged` stage), without Neo4j: the seeded stratified sample, the verdict file refused when it does
not answer its sheet (another sheet, a node missing, twice or unknown, a quote the summary lacks, a fact the
node lacks, a polarity flip without its fact), the counts per fault and stratum, and the stage's params,
metrics and report, and R124b's committed sheet and verdicts counted as reported. The sheet stage over a graph
is tested in test_summary_sheet.py (Neo4j)."""

import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.graph.connection import open_driver
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.inputs import digest
from kgbuilder.pipeline.summary_stages import REPORT_FILE, SummaryJudgedStage
from kgbuilder.validation.summary_judging import (
    PER_STRATUM,
    SAMPLE_SEED,
    Candidate,
    Groundedness,
    SummarySheet,
    draw_sheet,
    load_verdicts,
    score,
)

from .fakes import RecordingTracker

FACTS = [
    "F1. Press -MADE_BY-> Maker (1): Norcast",
    "F2. Press (Press) has condition hums (Condition) [denied]",
]


def candidate(ref: str, kind: str = "record", qualified: bool = False) -> Candidate:
    return Candidate(
        ref=ref, kind=kind, qualified=qualified, title=f"Press {ref}", label="Press", facts=FACTS,
        summary=f"Press {ref} is made by Norcast. It hums.", fallback=False,
    )  # fmt: skip


# 12 qualified records, 3 plain records, no individual, 25 concepts
POOL = (
    [candidate(f"r{i}", qualified=True) for i in range(12)]
    + [candidate(f"p{i}") for i in range(3)]
    + [candidate(f"c{i}", kind="concept") for i in range(25)]
)


@pytest.fixture
def sheet_file(tmp_path):
    path = tmp_path / "summary_sheet.json"
    path.write_text(draw_sheet(POOL, "units.jsonl", "h1").model_dump_json(), encoding="utf-8")
    return path


def verdict_file(tmp_path, sheet_file, verdicts: list[dict], sheet_hash: str | None = None):
    path = tmp_path / "verdicts.json"
    judge = {"model": "claude-opus-5-5", "date": "2026-10-09", "sheet_hash": sheet_hash or digest(sheet_file)}
    path.write_text(json.dumps({"judge": judge, "verdicts": verdicts}), encoding="utf-8")
    return path


def grounded(ref: str) -> dict:
    return {"ref": ref, "reason": "every statement is a fact"}


FLIP = {
    "fault": "polarity_flip", "quote": "It hums.", "fact": "F2", "reason": "F2 is denied; the text states it",
}  # fmt: skip


def all_refs(sheet_file) -> list[str]:
    return [row["ref"] for row in json.loads(sheet_file.read_text(encoding="utf-8"))["rows"]]


def test_the_sample_takes_ten_per_stratum_all_of_a_smaller_one_and_is_the_same_every_time():
    sheet = draw_sheet(POOL, "units.jsonl", "h1")
    assert (sheet.seed, sheet.per_stratum) == (SAMPLE_SEED, PER_STRATUM) == (124, 10)
    assert sheet.population == {
        "record_qualified": 12, "record_plain": 3, "individual_qualified": 0, "individual_plain": 0,
        "concept": 25,
    }  # fmt: skip
    strata = [row.stratum for row in sheet.rows]
    assert strata == ["record_qualified"] * 10 + ["record_plain"] * 3 + ["concept"] * 10
    assert draw_sheet(list(POOL), "units.jsonl", "h1") == sheet


def test_a_checked_verdict_file_is_counted_per_fault_and_stratum(tmp_path, sheet_file):
    refs = all_refs(sheet_file)
    flipped = {**grounded(refs[0]), "findings": [FLIP]}
    unsupported = {
        **grounded(refs[-1]),
        "findings": [{"fault": "unsupported", "quote": "made by Norcast", "reason": "said of another press"}],
    }
    path = verdict_file(tmp_path, sheet_file, [flipped, unsupported, *map(grounded, refs[1:-1])])
    sheet = draw_sheet(POOL, "units.jsonl", "h1")
    report = score(sheet, load_verdicts(path, sheet, digest(sheet_file)))
    assert (report.grounded.k, report.grounded.n) == (21, 23)
    assert {f: (p.k, p.n) for f, p in report.by_fault.items()} == {
        "unsupported": (1, 23), "polarity_flip": (1, 23), "identity_confusion": (0, 23),
    }  # fmt: skip
    assert {s: (p.k, p.n) for s, p in report.by_stratum.items()} == {
        "record_qualified": (9, 10), "record_plain": (3, 3), "concept": (9, 10),
    }  # fmt: skip
    metrics = report.metrics()
    assert metrics["grounded_n"] == 23 and metrics["findings_polarity_flip"] == 1
    assert {"fault_unsupported_low", "grounded_concept_high"} <= set(metrics)


@pytest.mark.parametrize(
    "change, message",
    [
        ("other sheet", "judge sheet other"),
        ("missing", "is not judged"),
        ("twice", "is judged 2 times"),
        ("unknown", "zz is not on the sheet"),
        ("quote", "the quote 'It sings.' is not in the summary"),
        ("fact", "F9 is not one of its facts"),
        ("flip without fact", "names the fact"),
    ],
)
def test_a_verdict_file_that_does_not_answer_its_sheet_is_refused(tmp_path, sheet_file, change, message):
    refs = all_refs(sheet_file)
    verdicts = [grounded(r) for r in refs]
    if change == "missing":
        verdicts.pop()
    elif change == "twice":
        verdicts.append(grounded(refs[0]))
    elif change == "unknown":
        verdicts.append(grounded("zz"))
    elif change == "quote":
        verdicts[0]["findings"] = [{**FLIP, "quote": "It sings."}]
    elif change == "fact":
        verdicts[0]["findings"] = [{**FLIP, "fact": "F9"}]
    elif change == "flip without fact":
        verdicts[0]["findings"] = [{**FLIP, "fact": ""}]
    path = verdict_file(tmp_path, sheet_file, verdicts, "other" if change == "other sheet" else None)
    sheet = draw_sheet(POOL, "units.jsonl", "h1")
    with pytest.raises(EvaluationError, match=message):
        load_verdicts(path, sheet, digest(sheet_file))


def test_the_judged_stage_logs_the_files_the_judge_and_the_counts(tmp_path, sheet_file):
    path = verdict_file(tmp_path, sheet_file, [grounded(r) for r in all_refs(sheet_file)])
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")  # never used: no graph is read
    ctx = PipelineContext(
        settings=Settings(), driver=driver, out=tmp_path / "out", tracker=RecordingTracker()
    )
    run_stages(ctx, PipelineState(), [SummaryJudgedStage(sheet_file, path)])
    driver.close()
    run = ctx.tracker.run("summary_judged")
    assert run.logged_params["judge_model"] == "claude-opus-5-5"
    assert run.logged_params["sheet_hash"] == digest(sheet_file) and run.logged_metrics["grounded"] == 1.0
    report = Groundedness.model_validate_json((tmp_path / "out" / REPORT_FILE).read_text(encoding="utf-8"))
    assert (report.grounded.k, report.grounded.n) == (23, 23)


def test_r124b_the_committed_verdicts_answer_their_sheet_and_count_42_grounded_of_44():
    """R124b's judged sample of the sealed furniture summaries: the committed verdict file answers the
    committed sheet exactly, and code counts what the roadmap reports."""
    gold = Path(__file__).parent / "gold" / "r124"
    sheet = SummarySheet.model_validate_json((gold / "summary_sheet.json").read_text(encoding="utf-8"))
    verdicts = load_verdicts(gold / "summary_verdicts.json", sheet, digest(gold / "summary_sheet.json"))
    report = score(sheet, verdicts)
    assert (report.grounded.k, report.grounded.n, report.judge_model) == (42, 44, "claude-opus-5-5")
    assert report.findings == {"unsupported": 0, "polarity_flip": 2, "identity_confusion": 0}
    assert sheet.population == {
        "record_qualified": 4, "record_plain": 178, "individual_qualified": 15, "individual_plain": 92,
        "concept": 448,
    }  # fmt: skip
