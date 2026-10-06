"""The anchor-graph evaluation's stage (R90 part b): `kg anchor-eval`.

Role in the pipeline: after a finished build; no graph, no model. It rebuilds the build's graph offline and
gates it on fidelity (R87: C0) with the provenance checks (C1), places the target gold (R89) on the
snapshot, and computes the criteria code decides alone (anchor/criteria.py: C2, C5, C7, C8, C9) in one arm.
Design: wiring and logging only, like audit_stages.py. One MLflow run per build and arm: its params name the
build, the dataset, the target and QA gold with their hashes, the arm, the budgets and the hub line; its
metrics are every criterion; its artifact the full report, with every per-question outcome, so arms can be
paired later. The criteria are computed even when the fidelity gate fails; `c0_fidelity_passed` says
whether they describe the build.
Not here: the judged criteria (C3, C4, C6), vector retrieval (arm C), comparing arms.
"""

from pathlib import Path

from ..anchor import Arm, TargetPlacer, read_staged, record_nodes
from ..anchor.report import evaluate
from ..audit import build_snapshot, check_fidelity, load_logged, run_checks
from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from ..validation.qa_gold import load_qa_gold
from ..validation.target_gold import load_target_gold
from .inputs import digest, input_file
from .stages import BaseStage

# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


def report_file(arm: Arm) -> str:
    return f"anchor_{arm.value}.json"


class AnchorEvalStage(BaseStage):
    """Rebuild one build's graph, gate it, and compute the anchor criteria in one arm."""

    def __init__(self, arm: Arm) -> None:
        self.arm = arm
        self.name = f"anchor_eval_{arm.value}"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
        targets = input_file(state.need("anchor_targets", "pass the target gold"), "target gold")
        qa = input_file(Path(load_target_gold(targets).qa_gold), "QA gold")
        s = ctx.settings
        return {
            "arm": self.arm.value,
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "logged": logged,
            "logged_hash": digest(logged),
            "build_git_sha": load_logged(logged).git_sha,
            "targets": targets,
            "targets_hash": digest(targets),
            "qa_gold": qa,
            "qa_gold_hash": digest(qa),
            "anchor_budgets": s.anchor_budgets,
            "anchor_hub_share": s.anchor_hub_share,
            # the chunker settings must be the build's, or every chunk id differs (the gate shows it)
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        source, s = Path(state.audit_source), ctx.settings
        snapshot = build_snapshot(
            source, Path(state.data_dir), (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        )
        logged = load_logged(Path(state.audit_logged))
        sheet = source / _SHEET
        fidelity = check_fidelity(snapshot, logged, sheet if sheet.exists() else None)
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        checks = run_checks(snapshot, schema)
        targets = load_target_gold(Path(state.anchor_targets))
        qa = load_qa_gold(Path(targets.qa_gold))
        plan = ConstructionPlan.model_validate_json((source / "plan.json").read_text(encoding="utf-8"))
        files = {r.file for q in targets.questions for t in q.targets for r in t.records}
        files |= {r.file for q in qa.questions for r in q.records}
        rows = read_staged(source / "staging", files)
        known = {r.id for r in snapshot.records}
        report = evaluate(
            snapshot,
            self.arm,
            qa,
            TargetPlacer(snapshot, plan, rows).place(targets),
            {
                q.id: sorted({n for r in q.records for n in record_nodes(r, plan, rows, known)})
                for q in qa.questions
            },
            budgets=s.anchor_budgets,
            hub_share=s.anchor_hub_share,
            usage=logged.usage,
            fidelity_passed=fidelity.passed,
            provenance=checks.provenance,
        )
        state.fidelity, state.anchor = fidelity, report
        run.metrics(**report.metrics())
        run.artifact(ctx.write(report_file(self.arm), report.model_dump_json(indent=1)))
        for file in (Path(state.audit_logged), Path(state.anchor_targets)):
            run.artifact(file)
