"""The committed R228 audit of the lighthouses and NTSB builds: C3 (identity), C4 (record links), C6 (purity).

R228 built the sheets offline from `out/r155b_<ds>` with R227's targets, had them judged blind and scored
them with `kg anchor-judged`. These tests pin the records as the thesis reports them: each verdict file
answers its sheet's code side under the review rules, every quote stands in what its item showed, the
header names the sheet it judged, and C4 and C6 recomputed from the committed files equal the committed
report (C3 needs the snapshot, so its numbers are pinned from the report). No Neo4j, no LLM, no `out/`.
"""

import pytest

from kgbuilder.anchor.judged import check_evidence, score_c4, score_c6
from kgbuilder.anchor.judged_report import JudgedReport, blind_view
from kgbuilder.anchor.sheets import C3Sheet, C4Sheet, C6Sheet, CodeSide
from kgbuilder.pipeline.inputs import digest
from kgbuilder.validation.anchor_verdicts import load_verdicts

from .qa_corpus import REPO

R228 = REPO / "tests" / "gold" / "r228"
DATASETS = ["lighthouses", "ntsb"]
SHEETS = {"c3": C3Sheet, "c4": C4Sheet, "c6": C6Sheet}
# the thresholds of the direction (config.py): link precision and purity at least 0.95
BOUND = 0.95
# (C4 links right, C6 anchor-arm record/individual pairs pure, C3 hard rule passed)
REPORTED = {"lighthouses": ((34, 36), (77, 79), False), "ntsb": ((36, 36), (58, 60), True)}


def _read(dataset: str, name: str):
    return (R228 / dataset / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("dataset", DATASETS)
@pytest.mark.parametrize("name", list(SHEETS))
def test_each_verdict_file_answers_its_sheet_under_the_review_rules(dataset, name):
    code = CodeSide.model_validate_json(_read(dataset, f"{name}_code.json"))
    verdicts = load_verdicts(R228 / dataset / f"{name}_verdicts.json", {i.id for i in code.items})
    sheet = SHEETS[name].model_validate_json(_read(dataset, f"{name}_sheet.json"))
    check_evidence(sheet, verdicts)
    assert verdicts.judge.model == "claude-opus-5-5" and verdicts.changes == []
    assert verdicts.judge.snapshot_hash == sheet.snapshot_hash
    assert verdicts.judge.sheet_hash == digest(R228 / dataset / f"{name}_sheet.json")


@pytest.mark.parametrize("dataset", DATASETS)
def test_the_committed_report_is_the_score_of_the_committed_verdicts(dataset):
    report = JudgedReport.model_validate_json(_read(dataset, "anchor_judged.json"))
    code = {c: CodeSide.model_validate_json(_read(dataset, f"{c}_code.json")) for c in ("c4", "c6")}
    files = {
        c: load_verdicts(R228 / dataset / f"{c}_verdicts.json", {i.id for i in code[c].items}) for c in code
    }
    assert score_c4(files["c4"], code["c4"], BOUND) == report.final.c4
    assert score_c6(files["c6"], code["c6"], BOUND) == report.final.c6
    assert score_c4(blind_view(files["c4"]), code["c4"], BOUND) == report.blind.c4
    links, pure, c3_passed = REPORTED[dataset]
    assert (report.final.c4.links.accepted.k, report.final.c4.links.accepted.n) == links
    anchor = report.final.c6.purity["anchor"]["record_individual"].accepted
    assert (anchor.k, anchor.n) == pure
    assert report.final.c3.hard_passed is c3_passed
    # a wrong record link is C3's wrong record merge too (R93): lighthouses fails C3 on its two C4 links
    assert report.final.c3.record_wrong_merges == report.final.c4.incorrect
