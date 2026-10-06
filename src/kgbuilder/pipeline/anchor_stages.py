"""The anchor-graph evaluation's stages: `kg anchor-eval` (R90 part b) and `kg anchor-compare` (R92).

Role in the pipeline: after a finished build; no graph, no model. It rebuilds the build's graph offline and
gates it on fidelity (R87: C0) with the provenance checks (C1), places the target gold (R89) on the
snapshot, and computes the criteria code decides alone (anchor/criteria.py: C2, C5, C7, C8, C9) in one arm.
Design: wiring and logging only, like audit_stages.py. One MLflow run per build and arm: its params name the
build, the dataset, the target and QA gold with their hashes, the arm, the budgets and the hub line; its
metrics are every criterion; its artifact the full report, with every per-question outcome, so arms can be
paired later. The criteria are computed even when the fidelity gate fails; `c0_fidelity_passed` says
whether they describe the build.
`kg anchor-compare` reads the two arms' reports of one build, computes arm C (vector retrieval: the build's
chunk texts and the questions embedded with the build's embedding model, the only model call of the
evaluation) and pairs the arms question by question (anchor/compare.py). One MLflow run per build: params
name the reports with their hashes and the embedding model; metrics are arm C's C5 and every pairing;
the artifact holds the per-question outcomes and arm C's rankings with their cosines.
Not here: the judged criteria (C3, C4, C6: pipeline/judging_stages.py).
"""

from pathlib import Path

from ..anchor import Arm, TargetPlacer, read_staged, record_nodes
from ..anchor.compare import compare_arms
from ..anchor.report import AnchorReport, evaluate
from ..anchor.vector import cosine_rank, vector_reach
from ..audit import build_snapshot, check_fidelity, load_logged, run_checks
from ..audit.inputs import read_corpus
from ..core.errors import EvaluationError, LLMUnavailableError
from ..structured.plan import ConstructionPlan
from ..structured.profiler import DataProfile
from ..text.schema import TextSchema
from ..validation.qa_gold import load_qa_gold
from ..validation.target_gold import load_target_gold
from .inputs import digest, input_file
from .stages import BaseStage

# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


COMPARE_FILE = "anchor_compare.json"


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


class AnchorCompareStage(BaseStage):
    """Compute arm C (vector retrieval) for one build and pair the three arms question by question."""

    name = "anchor_compare"

    def params(self, ctx, state):
        source = input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder")
        targets = input_file(state.need("anchor_targets", "pass the target gold"), "target gold")
        reports = state.need("anchor_reports", "pass the anchor and layered reports of kg anchor-eval")
        anchor, layered = (input_file(r, "anchor-eval report") for r in reports)
        s = ctx.settings
        return {
            "build": source,
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
            "targets": targets,
            "targets_hash": digest(targets),
            "anchor_report": anchor,
            "anchor_report_hash": digest(anchor),
            "layered_report": layered,
            "layered_report_hash": digest(layered),
            # arm C must embed with the build's model: the direction compares the build's own chunk vectors
            "embed_model": s.embed_model,
            "anchor_budgets": s.anchor_budgets,
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        if ctx.embedder is None:
            raise LLMUnavailableError("arm C needs the embedding model: set the Gemini key")
        anchor, layered = (
            AnchorReport.model_validate_json(Path(r).read_text(encoding="utf-8"))
            for r in state.anchor_reports
        )
        if (anchor.arm, layered.arm) != (Arm.ANCHOR, Arm.LAYERED):
            raise EvaluationError(
                [f"pass the anchor report, then the layered one (got {anchor.arm}, {layered.arm})"]
            )
        source, s = Path(state.audit_source), ctx.settings
        qa = load_qa_gold(Path(load_target_gold(Path(state.anchor_targets)).qa_gold))
        plan = ConstructionPlan.model_validate_json((source / "plan.json").read_text(encoding="utf-8"))
        profile = DataProfile.model_validate_json((source / "profile.json").read_text(encoding="utf-8"))
        chunking = (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        chunks = read_corpus(Path(state.data_dir), source / "staging", plan, profile, chunking).chunks
        # the graph arms' C5 questions, so every pairing is over the same questions
        pool = [q.question for q in anchor.reach["gold_start"].questions]
        texts = {q.id: q.question for q in qa.questions}
        # the ingest stage embedded each chunk's text, nothing else (pipeline/stages.py, IngestTextStage)
        chunk_vectors = dict(
            zip([c.chunk_id for c in chunks], ctx.embedder.embed([c.text for c in chunks]), strict=True)
        )
        question_vectors = ctx.embedder.embed([texts[q] for q in pool])
        rankings = {
            q: cosine_rank(v, chunk_vectors, max(s.anchor_budgets))
            for q, v in zip(pool, question_vectors, strict=True)
        }
        vector = vector_reach(qa, pool, rankings, s.anchor_budgets)
        comparison = compare_arms(anchor, layered, vector, rankings, s.anchor_budgets)
        state.anchor_comparison = comparison
        run.metrics(
            **comparison.metrics(),
            embedded_chunks=len(chunks),
            embedded_questions=len(pool),
            # the Gemini API reports no tokens for embeddings: the characters sent are the size of the call
            embedded_chars=sum(len(c.text) for c in chunks) + sum(len(texts[q]) for q in pool),
        )
        run.artifact(ctx.write(COMPARE_FILE, comparison.model_dump_json(indent=1)))
