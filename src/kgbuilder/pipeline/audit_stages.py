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
With `--join` (R98) it also decides the individuals again from the replayed record matches
(audit/reidentify.py): the adjudicator is asked as `kg resolve` asks it, the meaning pairs come from the
embedder, every changed mention must be explained by its group (`joined`, `unjoined`), and the written build
carries the replayed `individual_decisions`. A stated kind that no record fits is a concept since R107, which
no replay decides: a replay that would make one where the build has a particular is refused.
`--join --faithful` writes no build: it is the gate that the replay is the build's, from the build's own
record matches and logged meaning pairs, and it fails on any difference from resolve.json.
Not here: the judged metrics, the scoring of verdicts.
"""

import json
import shutil
from pathlib import Path
from typing import get_args

from ..audit import build_snapshot, check_fidelity, compute_reach, gold_pairs, load_logged, run_checks
from ..audit.reidentify import (
    BuiltIdentity,
    JoinCause,
    ReidentifyReport,
    built_matches,
    identity_changes,
    logged_meaning,
    reidentify,
    undecided_kinds,
    unfaithful,
    with_build_targets,
)
from ..audit.relink import Relink, RelinkCause, RelinkReport, relink, relinked_counts
from ..audit.snapshot import GraphSnapshot
from ..core.errors import EvaluationError, LLMUnavailableError
from ..llm.base import LLMClient, prompt_version
from ..llm.thinking import with_thinking
from ..resolution.blocking import blocking_from
from ..resolution.identity_graph import Assignment
from ..resolution.individuals import IDENTITY_PROMPT, Action, IndividualDecision, embedding_for, meaning_pairs
from ..resolution.particulars import JoinSettings
from ..resolution.record_choice import CHOICE_PROMPT, ChoiceAction
from ..structured.plan import ConstructionPlan
from ..text.mention_pass import PASS_FILE
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
REIDENTIFY_FILE = "reidentify.json"
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
            "join": int(state.relink_join),
            "faithful": int(state.relink_faithful),
            **_llm_params(ctx, state),
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
        llm = self._llm(ctx, state, run)
        if state.relink_faithful:
            self._faithful(ctx, state, run, snapshot, schema, llm)
            return
        thresholds = (s.domain_link_threshold, s.er_borderline)
        chooser = llm if state.relink_choose else None
        replay = relink(snapshot, plan, schema, thresholds, chooser, s.extract_model)
        if replay.unexplained:  # the replay would not be the build's matching: its measurement means nothing
            raise EvaluationError(
                [f"unexplained change of {c.mention} {c.name!r}" for c in replay.unexplained]
            )
        if replay.to_concepts:  # R107: they become concepts, and no replay decides concepts
            raise EvaluationError(
                [
                    f"{m} is a stated kind that lost its record: kg resolve decides its concept"
                    for m in replay.to_concepts
                ]
            )
        references, decisions = replay.references, None
        if state.relink_join:
            references, decisions = self._join(ctx, state, run, snapshot, schema, replay, llm)
        folder = _write_build(ctx.out / RELINKED_BUILD, source, references, decisions)
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

    @staticmethod
    def _llm(ctx, state, run) -> LLMClient | None:
        """The resolve stage's model with its thinking level, so the replay asks what a build would ask;
        None when the replay asks nothing. Refused without a provider: a replay that silently skipped the
        LLM would measure another rule. Logs the prompts it may send."""
        if not (state.relink_choose or state.relink_join):
            return None
        if ctx.llm is None:
            raise LLMUnavailableError("--choose and --join need an LLM provider: the preset has no key")
        if state.relink_choose:
            run.text(CHOICE_PROMPT, "prompts/resolve_record_choice.txt")
        if state.relink_join:
            run.text(IDENTITY_PROMPT, "prompts/resolve_individuals.txt")
        return with_thinking(ctx.llm, ctx.settings.extract_thinking)

    @staticmethod
    def _faithful(
        ctx, state, run, snapshot: GraphSnapshot, schema: TextSchema, llm: LLMClient | None
    ) -> None:
        """The gate: the individuals decided again from the build's own record matches and meaning pairs
        must be resolve.json's, field by field. Writes the report, then fails on any difference."""
        if state.relink_choose:
            raise EvaluationError(
                ["--faithful replays the build's own record links; --choose would change them"]
            )
        source = Path(state.audit_source)
        built = BuiltIdentity.model_validate_json((source / "resolve.json").read_text(encoding="utf-8"))
        matches = built_matches(snapshot, built, schema)
        particulars = reidentify(snapshot, schema, matches, logged_meaning(built), llm, _join_settings(ctx))
        issues = unfaithful(built, particulars)
        run.metrics(
            faithful=int(not issues),
            faithful_issues=len(issues),
            particular_mentions=len(particulars.assignments),
            **_decision_metrics(particulars.decisions, built.individual_decisions),
        )
        report = ReidentifyReport(
            build=source.as_posix(), faithful=True, issues=issues, decisions=particulars.decisions
        )
        run.artifact(ctx.write(REIDENTIFY_FILE, report.model_dump_json(indent=1)))
        state.reidentified = report
        if issues:  # the replay is not the build's: no measurement may rest on it
            more = [f"... {len(issues) - 20} more"] if len(issues) > 20 else []
            raise EvaluationError(issues[:20] + more)

    @staticmethod
    def _join(
        ctx, state, run, snapshot: GraphSnapshot, schema: TextSchema, replay: Relink, llm: LLMClient | None
    ) -> tuple[list[Assignment], list[IndividualDecision]]:
        """The individuals decided again from the record replay's matches, with the embedder's meaning pairs;
        refused when a mention's change has no cause. Returns the references to write and the decisions."""
        s, source = ctx.settings, Path(state.audit_source)
        blocking = blocking_from(s.er_embedding_blocking, s.er_embedding_candidates, s.er_neighbours)
        if blocking is not None and ctx.embedder is None:  # fewer pairs than the build would nominate
            raise LLMUnavailableError("--join needs the embedder: the settings nominate pairs by meaning")

        def meaning(units):
            return meaning_pairs(units, embedding_for(units, ctx.embedder, blocking), blocking)

        particulars = reidentify(snapshot, schema, replay.matches, meaning, llm, _join_settings(ctx))
        changes = identity_changes(snapshot, particulars.assignments, {c.mention for c in replay.changes})
        built = BuiltIdentity.model_validate_json((source / "resolve.json").read_text(encoding="utf-8"))
        run.metrics(
            identity_changes=len(changes),
            identity_unexplained=sum(not c.explained for c in changes),
            **{f"changes_{c}": sum(x.cause == c for x in changes) for c in get_args(JoinCause)},
            **_decision_metrics(particulars.decisions, built.individual_decisions),
        )
        report = ReidentifyReport(
            build=source.as_posix(), faithful=False, changes=changes, decisions=particulars.decisions
        )
        run.artifact(ctx.write(REIDENTIFY_FILE, report.model_dump_json(indent=1)))
        state.reidentified = report
        if unexplained := [c for c in changes if not c.explained]:
            raise EvaluationError(
                [f"unexplained identity change of {c.mention} {c.name!r}" for c in unexplained]
            )
        if undecided := undecided_kinds(snapshot, particulars):  # R107: a concept no replay can decide
            raise EvaluationError(
                [
                    f"{m.id} {m.name!r} is a stated kind no record fits: kg resolve decides its concept"
                    for m in undecided
                ]
            )
        # a stated kind the build resolved as a concept and the replay now links keeps only its record edge
        placed = {a.mention for a in particulars.assignments}
        concepts = [a for a in snapshot.references if a.kind == "concept" and a.mention not in placed]
        replayed = with_build_targets(snapshot, particulars.assignments)
        return sorted(replayed + concepts, key=lambda a: a.mention), particulars.decisions


def _join_settings(ctx) -> JoinSettings:
    """The resolve stage's joining settings: its borderline and the model logged on each decision."""
    return JoinSettings(borderline=ctx.settings.er_borderline, model=ctx.settings.extract_model)


def _decision_metrics(
    decisions: list[IndividualDecision], built: list[IndividualDecision]
) -> dict[str, float]:
    """The replayed individual decisions per action, and the pairs nominated by meaning (the build's too)."""
    return {
        "individual_decisions": len(decisions),
        **{f"decision_{a}": sum(d.action == a for d in decisions) for a in get_args(Action)},
        "nominated_by_meaning": sum(d.signal == "meaning" for d in decisions),
        "nominated_by_meaning_build": sum(d.signal == "meaning" for d in built),
    }


def _llm_params(ctx, state) -> dict[str, object]:
    """The params of a replay that asks an LLM: its model, thinking level and the prompt versions it may
    send; with a measured --join also the blocking rule and embedding model of the meaning pairs."""
    s = ctx.settings
    if not (state.relink_choose or state.relink_join):
        return {}
    params: dict[str, object] = {"model": s.extract_model, "thinking": s.extract_thinking}
    if state.relink_choose:
        params["record_choice_prompt_version"] = prompt_version(CHOICE_PROMPT)
    if state.relink_join:
        params["individual_prompt_version"] = prompt_version(IDENTITY_PROMPT)
    if state.relink_join and not state.relink_faithful:
        params |= {
            "er_embedding_blocking": s.er_embedding_blocking,
            "er_embedding_candidates": s.er_embedding_candidates,
            "er_neighbours": s.er_neighbours,
            "embed_model": s.embed_model if ctx.embedder is not None else None,
        }
    return params


def _write_build(
    folder: Path, source: Path, references: list[Assignment], decisions: list[IndividualDecision] | None
) -> Path:
    """A build folder the snapshot reads: the build's files, with resolve.json's assignments replayed (and,
    when the individuals were replayed, their decisions). The build's judge sheet is left out: its
    attachments are the build's, not the replay's."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in _BUILD_FILES:
        shutil.copyfile(source / name, folder / name)
    if (source / PASS_FILE).exists():  # the mention pass's findings (R101), when the build ran it
        shutil.copyfile(source / PASS_FILE, folder / PASS_FILE)
    shutil.copytree(source / "staging", folder / "staging", dirs_exist_ok=True)
    resolved = json.loads((source / "resolve.json").read_text(encoding="utf-8"))
    resolved["assignments"] = [a.model_dump() for a in references]
    if decisions is not None:
        resolved["individual_decisions"] = [d.model_dump() for d in decisions]
    (folder / "resolve.json").write_text(json.dumps(resolved, indent=1, ensure_ascii=False), encoding="utf-8")
    return folder
