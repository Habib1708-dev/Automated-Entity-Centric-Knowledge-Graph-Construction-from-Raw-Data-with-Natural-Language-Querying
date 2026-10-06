"""The graph audit's stages (R87): `kg audit-snapshot`.

Role in the pipeline: after a finished build; no graph, no model. It rebuilds the build's graph offline
(audit/snapshot.py), checks the rebuild against the build's logged counts and judge sheet
(audit/fidelity.py), and runs the checks code decides alone (audit/checks.py, audit/reach.py).
Design: wiring and logging only, like stages.py (kept apart from it, which builds the graph). One MLflow run
per build audited: its params name the build, the dataset, the chunking and the input files with their
hashes; its metrics are the fidelity result and every code check; its artifacts the three report files.
The checks run even when the fidelity gate fails, so the report shows both, and the metric
`fidelity_passed` tells a reader whether the checks describe the build.
Not here: the judged metrics (later parts of R87), the scoring of verdicts.
"""

from pathlib import Path

from ..audit import build_snapshot, check_fidelity, compute_reach, gold_pairs, load_logged, run_checks
from ..text.schema import TextSchema
from .inputs import digest, input_file
from .stages import BaseStage

SNAPSHOT_FILE = "snapshot.json"
FIDELITY_FILE = "fidelity.json"
CHECKS_FILE = "code_checks.json"
# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


class AuditSnapshotStage(BaseStage):
    """Rebuild one build's graph, gate it on fidelity, and run the code checks."""

    name = "audit_snapshot"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
        params: dict[str, object] = {
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "logged": logged,
            "logged_hash": digest(logged),
            "build_git_sha": load_logged(logged).git_sha,
            # the chunker settings must be the build's, or every chunk id differs (the gate shows it)
            "chunk_max_chars": ctx.settings.chunk_max_chars,
            "chunk_min_chars": ctx.settings.chunk_min_chars,
            "chunk_overlap_chars": ctx.settings.chunk_overlap_chars,
        }
        if state.reach_gold is not None:
            claims, sample = (input_file(p, "reach gold") for p in state.reach_gold)
            params |= {"reach_claims": claims, "reach_claims_hash": digest(claims), "reach_sample": sample}
        return params

    def run(self, ctx, state, run):
        source = Path(state.audit_source)
        s = ctx.settings
        snapshot = build_snapshot(
            source, Path(state.data_dir), (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        )
        sheet = source / _SHEET
        fidelity = check_fidelity(
            snapshot, load_logged(Path(state.audit_logged)), sheet if sheet.exists() else None
        )
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        reach = compute_reach(snapshot, gold_pairs(*state.reach_gold)) if state.reach_gold else None
        checks = run_checks(snapshot, schema, reach)
        state.fidelity, state.audit = fidelity, checks
        run.metrics(**fidelity.metrics(), **checks.metrics())
        run.artifact(ctx.write(SNAPSHOT_FILE, snapshot.model_dump_json(indent=1)))
        run.artifact(ctx.write(FIDELITY_FILE, fidelity.model_dump_json(indent=2)))
        run.artifact(ctx.write(CHECKS_FILE, checks.model_dump_json(indent=2)))
        for file in (Path(state.audit_logged), *(state.reach_gold or ())):
            run.artifact(file)
