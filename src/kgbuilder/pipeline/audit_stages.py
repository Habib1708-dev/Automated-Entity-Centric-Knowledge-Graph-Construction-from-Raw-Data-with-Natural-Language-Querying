"""The graph audit's stages: `kg audit-snapshot` (R87) and `kg audit-relink` (R94).

Role in the pipeline: after a finished build; no graph, no model. It rebuilds the build's graph offline
(audit/snapshot.py), checks the rebuild against the build's logged counts and judge sheet
(audit/fidelity.py), and runs the checks code decides alone (audit/checks.py, audit/reach.py).
Design: wiring and logging only, like stages.py (kept apart from it, which builds the graph). One MLflow run
per build audited: its params name the build, the dataset, the chunking and the input files with their
hashes; its metrics are the fidelity result and every code check; its artifacts the three report files.
The checks run even when the fidelity gate fails, so the report shows both, and the metric
`fidelity_passed` tells a reader whether the checks describe the build.
`kg audit-relink` replays a build's record matching under the current rules (audit/relink.py), refuses a
replay that differs from the build in anything but the change being measured, and writes the build folder it
gives (the build's files with resolve.json replayed) and its logged counts, for `kg anchor-eval` and the
judging stages to measure. One run per build: metrics are the changes (in all and per cause), the record
links before and after and, with `--choose`, the chooser's outcomes; artifacts the changes and the counts.
With `--choose` it is an LLM run (R95b): the mentions with near misses are offered to the resolve model.
Not here: the judged metrics, the scoring of verdicts.
"""

import json
import shutil
from pathlib import Path
from typing import get_args

from ..audit import build_snapshot, check_fidelity, compute_reach, gold_pairs, load_logged, run_checks
from ..audit.relink import Relink, RelinkCause, RelinkReport, relink, relinked_counts
from ..config import Settings
from ..core.errors import EvaluationError, LLMUnavailableError
from ..llm.base import prompt_version
from ..llm.thinking import with_thinking
from ..resolution.record_choice import CHOICE_PROMPT, ChoiceAction
from ..structured.plan import ConstructionPlan
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


RELINK_FILE = "relink.json"
RELINKED_LOGGED = "logged.json"
RELINKED_BUILD = "build"
# the files of a build folder the snapshot reads, besides resolve.json (rewritten) and staging/ (copied)
_BUILD_FILES = ("plan.json", "text_schema.json", "profile.json", "triples.jsonl")


class AuditRelinkStage(BaseStage):
    """Replay a build's record matching under the current rules and write the build folder it gives (R94)."""

    name = "audit_relink"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
        return {
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "logged": logged,
            "logged_hash": digest(logged),
            "build_git_sha": load_logged(logged).git_sha,
            # the build's own thresholds (logged by its resolve run): the replay must decide as it did
            "domain_link_threshold": ctx.settings.domain_link_threshold,
            "er_borderline": ctx.settings.er_borderline,  # a near miss's spelling score (R95b)
            "choose": int(state.relink_choose),
            **(_choice_params(ctx.settings) if state.relink_choose else {}),
            "chunk_max_chars": ctx.settings.chunk_max_chars,
            "chunk_min_chars": ctx.settings.chunk_min_chars,
            "chunk_overlap_chars": ctx.settings.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        source, s = Path(state.audit_source), ctx.settings
        chunking = (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        snapshot = build_snapshot(source, Path(state.data_dir), chunking)
        logged = load_logged(Path(state.audit_logged))
        sheet = source / _SHEET
        if not check_fidelity(snapshot, logged, sheet if sheet.exists() else None).passed:
            raise EvaluationError(["the snapshot is not the build's graph (C0 failed): nothing to replay"])
        plan = ConstructionPlan.model_validate_json((source / "plan.json").read_text(encoding="utf-8"))
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        if state.relink_choose and ctx.llm is None:
            raise LLMUnavailableError("--choose needs an LLM provider: the preset has no key")
        # the resolve stage's model and thinking level, so the replay asks what a build would ask
        llm = with_thinking(ctx.llm, s.extract_thinking) if state.relink_choose and ctx.llm else None
        if llm is not None:
            run.text(CHOICE_PROMPT, "prompts/resolve_record_choice.txt")
        thresholds = (s.domain_link_threshold, s.er_borderline)
        replay = relink(snapshot, plan, schema, thresholds, llm, s.extract_model)
        if replay.unexplained:  # the replay would not be the build's matching: its measurement means nothing
            raise EvaluationError(
                [f"unexplained change of {c.mention} {c.name!r}" for c in replay.unexplained]
            )
        folder = _write_build(ctx.out / RELINKED_BUILD, source, replay)
        replayed = build_snapshot(folder, Path(state.data_dir), chunking)
        counts, changed = relinked_counts(logged, replayed)
        run.metrics(
            keyed_mentions=replay.keyed,
            changes=len(replay.changes),
            unexplained=len(replay.unexplained),
            # how many links each rule change removed (R94: left_scope; R95a: containment, spelling)
            **{f"changes_{c}": sum(x.cause == c for x in replay.changes) for c in get_args(RelinkCause)},
            # the mentions with near misses and the chooser's outcome for each (`skipped` without --choose)
            choices=len(replay.choices),
            **{f"choice_{a}": sum(d.action == a for d in replay.choices) for a in get_args(ChoiceAction)},
            record_links_before=sum(a.kind == "record" for a in snapshot.references),
            record_links_after=sum(a.kind == "record" for a in replayed.references),
            counts_changed=len(changed),
        )
        report = RelinkReport(
            build=source.as_posix(), changes=replay.changes, counts=changed, choices=replay.choices
        )
        run.artifact(ctx.write(RELINK_FILE, report.model_dump_json(indent=1)))
        run.artifact(ctx.write(RELINKED_LOGGED, counts.model_dump_json(indent=1)))
        state.relink = replay


def _choice_params(s: Settings) -> dict[str, object]:
    """The params of a replay that asks the chooser: its model, thinking level and prompt version."""
    return {
        "model": s.extract_model,
        "thinking": s.extract_thinking,
        "record_choice_prompt_version": prompt_version(CHOICE_PROMPT),
    }


def _write_build(folder: Path, source: Path, replay: Relink) -> Path:
    """A build folder the snapshot reads: the build's files, with resolve.json's assignments replayed. The
    build's judge sheet is left out: its attachments are the build's, not the replay's."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in _BUILD_FILES:
        shutil.copyfile(source / name, folder / name)
    shutil.copytree(source / "staging", folder / "staging", dirs_exist_ok=True)
    resolved = json.loads((source / "resolve.json").read_text(encoding="utf-8"))
    resolved["assignments"] = [a.model_dump() for a in replay.references]
    (folder / "resolve.json").write_text(json.dumps(resolved, indent=1, ensure_ascii=False), encoding="utf-8")
    return folder
