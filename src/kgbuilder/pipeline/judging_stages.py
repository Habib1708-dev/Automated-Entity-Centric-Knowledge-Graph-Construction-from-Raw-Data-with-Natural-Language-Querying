"""The anchor-graph evaluation's judging stages (R93): `kg anchor-sheets` and `kg anchor-judged`.

Role in the pipeline: after `kg anchor-eval` (anchor_stages.py); no graph, no model. Judging itself happens in
between, by Claude in the session, never in a stage.
`kg anchor-sheets` rebuilds the snapshot behind R87's fidelity gate (it refuses to write sheets when C0
fails), runs R87's code checks for their flags and split groups, and writes the blind sheets of C3, C4 and C6
with their code sides (anchor/sheet_builder.py), starting C6 from the target nodes of the two arms' reports.
One run per build: params name the build and the reports with their hashes; metrics are the sheet sizes and
the flag counts; artifacts the six files.
`kg anchor-judged` rebuilds the snapshot, refuses sheets built from another one, loads the committed verdict
files against their code sides (validation/anchor_verdicts.py: complete, reviewed, every quote in its item),
rescores R75's identity pairs on the snapshot, and scores C3, C4 and C6 on the reviewed and the blind labels
(anchor/judged_report.py). One run per build: params name every sheet and verdict file by hash, the judge
model and the two minimums; metrics are the criteria; the artifact is the report.
Design: wiring and logging only, like anchor_stages.py.
Not here: judging, the sheets' contents (anchor/sheet_builder.py), the scores (anchor/judged.py).
"""

from pathlib import Path

from ..anchor import Arm
from ..anchor.judged import check_evidence, rescore_identity
from ..anchor.judged_report import JudgedReport, blind_view, impact, score_all
from ..anchor.report import AnchorReport
from ..anchor.sheet_builder import build_sheets, snapshot_digest
from ..anchor.sheets import C3Sheet, C4Sheet, C6Sheet, CodeSide, JudgingSheets
from ..audit import build_snapshot, check_fidelity, load_logged, run_checks
from ..core.errors import EvaluationError
from ..text.schema import TextSchema
from ..validation.anchor_verdicts import load_verdicts
from ..validation.gold import load_gold
from .inputs import digest, input_file
from .stages import BaseStage

# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


def sheet_file(criterion: str) -> str:
    return f"{criterion.lower()}_sheet.json"


def code_file(criterion: str) -> str:
    return f"{criterion.lower()}_code.json"


class AnchorSheetsStage(BaseStage):
    """Write the blind judging sheets of C3, C4 and C6 for one build, with their code sides (R93)."""

    name = "anchor_sheets"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
        reports = state.need("anchor_reports", "pass the anchor and layered reports of kg anchor-eval")
        anchor, layered = (input_file(r, "anchor-eval report") for r in reports)
        s = ctx.settings
        return {
            "dataset": state.need("anchor_dataset", "pass the dataset's name"),
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "logged": logged,
            "logged_hash": digest(logged),
            "anchor_report": anchor,
            "anchor_report_hash": digest(anchor),
            "layered_report": layered,
            "layered_report_hash": digest(layered),
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        source, s = Path(state.audit_source), ctx.settings
        snapshot = build_snapshot(
            source, Path(state.data_dir), (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        )
        sheet = source / _SHEET
        fidelity = check_fidelity(
            snapshot, load_logged(Path(state.audit_logged)), sheet if sheet.exists() else None
        )
        if not fidelity.passed:  # a sheet of another graph would have the judge score the wrong thing
            raise EvaluationError(["the snapshot is not the build's graph (C0 failed): no sheets written"])
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        reports = [
            AnchorReport.model_validate_json(Path(r).read_text(encoding="utf-8"))
            for r in state.anchor_reports
        ]
        if [r.arm for r in reports] != [Arm.ANCHOR, Arm.LAYERED]:
            raise EvaluationError(
                [f"pass the anchor report, then the layered one (got {[r.arm for r in reports]})"]
            )
        # C6 starts from every target node R90 placed, in either arm (the placements do not depend on the arm)
        starts = {n for r in reports for ts in r.placed.values() for t in ts for n in t.nodes}
        sheets = build_sheets(snapshot, run_checks(snapshot, schema), starts, state.anchor_dataset)
        state.anchor_sheets = sheets
        for criterion, sheet_model in (("C3", sheets.c3), ("C4", sheets.c4), ("C6", sheets.c6)):
            run.artifact(ctx.write(sheet_file(criterion), sheet_model.model_dump_json(indent=1)))
            run.artifact(ctx.write(code_file(criterion), sheets.code[criterion].model_dump_json(indent=1)))
        run.metrics(**sheet_counts(sheets))


def sheet_counts(sheets: JudgingSheets) -> dict[str, float]:
    """The size of every sheet, and how many items each code flag raised."""
    code = sheets.code
    out = {
        "c3_merge_items": float(len(sheets.c3.merges)),
        "c3_split_items": float(len(sheets.c3.splits)),
        "c4_link_items": float(sum(i.kind == "link" for i in code["C4"].items)),
        "c4_unlinked_items": float(sum(i.kind == "unlinked" for i in code["C4"].items)),
        "c6_pairs": float(len(sheets.c6.pairs)),
        "c6_pairs_only_layered": float(sum(i.arms == [Arm.LAYERED.value] for i in code["C6"].items)),
    }
    for criterion, side in code.items():
        for flag in sorted({f for i in side.items for f in i.flags}):
            out[f"{criterion.lower()}_flag_{flag}"] = float(sum(flag in i.flags for i in side.items))
    return out


JUDGED_FILE = "anchor_judged.json"
CRITERIA = ("C3", "C4", "C6")
_SHEET_MODELS = {"C3": C3Sheet, "C4": C4Sheet, "C6": C6Sheet}


def verdict_file(criterion: str) -> str:
    return f"{criterion.lower()}_verdicts.json"


class AnchorJudgedStage(BaseStage):
    """Score the judged criteria C3, C4 and C6 of one build from its committed sheets and verdicts (R93)."""

    name = "anchor_judged"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
        folder = input_file(
            state.need("anchor_judged_dir", "pass the folder of sheets and verdicts"), "folder"
        )
        gold = input_file(state.need("identity_gold", "pass R75's identity gold"), "identity gold")
        report = input_file(state.need("anchor_placements", "pass the anchor-eval report"), "anchor report")
        files = {
            name: input_file(folder / name, name)
            for c in CRITERIA
            for name in (sheet_file(c), verdict_file(c))
        }
        s = ctx.settings
        return {
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "logged": logged,
            "logged_hash": digest(logged),
            "judged_dir": folder,
            "identity_gold": gold,
            "identity_gold_hash": digest(gold),
            "anchor_report": report,
            "anchor_report_hash": digest(report),
            **{f"{name.removesuffix('.json')}_hash": digest(path) for name, path in files.items()},
            "anchor_min_link_precision": s.anchor_min_link_precision,
            "anchor_min_purity": s.anchor_min_purity,
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        source, folder, s = Path(state.audit_source), Path(state.anchor_judged_dir), ctx.settings
        snapshot = build_snapshot(
            source, Path(state.data_dir), (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        )
        sheets = {
            c: _SHEET_MODELS[c].model_validate_json((folder / sheet_file(c)).read_text(encoding="utf-8"))
            for c in CRITERIA
        }
        stale = [c for c, sheet in sheets.items() if sheet.snapshot_hash != snapshot_digest(snapshot)]
        if stale:  # verdicts on another graph would be scored as if they were about this one
            raise EvaluationError([f"the {c} sheet was built from another snapshot" for c in stale])
        code = {
            c: CodeSide.model_validate_json((folder / code_file(c)).read_text(encoding="utf-8"))
            for c in CRITERIA
        }
        files = {c: load_verdicts(folder / verdict_file(c), {i.id for i in code[c].items}) for c in CRITERIA}
        for c in CRITERIA:
            check_evidence(sheets[c], files[c])
        pairs = rescore_identity(snapshot, load_gold(Path(state.identity_gold)).identity_pairs)
        thresholds = {"min_link_precision": s.anchor_min_link_precision, "min_purity": s.anchor_min_purity}
        placed = AnchorReport.model_validate_json(
            Path(state.anchor_placements).read_text(encoding="utf-8")
        ).placed
        report = JudgedReport(
            dataset=sheets["C3"].dataset,
            judge_model=files["C3"].judge.model,
            final=score_all(files, code, pairs, **thresholds),
            blind=score_all({c: blind_view(f) for c, f in files.items()}, code, pairs, **thresholds),
            impact=impact(files, code, placed),
        )
        state.anchor_judged = report
        run.params(judge_model=report.judge_model)
        run.metrics(**report.metrics())
        run.artifact(ctx.write(JUDGED_FILE, report.model_dump_json(indent=1)))
        for c in CRITERIA:
            run.artifact(folder / verdict_file(c))
