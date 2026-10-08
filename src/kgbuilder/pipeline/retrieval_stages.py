"""The retrieval benchmark's stages (R117): `kg retrieve-eval` and `kg retrieve-compare`.

Role in the pipeline: on a loaded graph, before any reader. `kg retrieve-eval` asks one reading system's
chunk source (qa_systems.py) every question of a QA gold file, without the reader, and scores the chunks it
ranks against the gold's evidence and the nodes it starts from (its seeds) against the target gold (R89)
placed on the loaded build; `kg retrieve-compare` pairs two of its reports question by question. No
generating model is called: the only paid calls are embeddings (each question, and graph_retrieval's node
names once).
Design: wiring and logging only, like qa_stages.py. One MLflow run per system (`retrieve_eval_<system>`):
params name the source's settings, the budgets, the gold and targets with their hashes, the build and the
graph digest; metrics are every score with its n, the latency and the embedding volume; the artifact is the
report with every outcome row, so systems can be paired later. Questions are ranked one at a time: the
Gemini embedder has no retry, and R73 hit its rate limit with parallel calls.
Not here: the scores (validation/retrieval_scores.py), the sources (query/systems.py), the placement of the
targets (anchor/targets.py).
"""

import json
import time
from dataclasses import replace
from pathlib import Path

from ..anchor import PlacedTarget, TargetPlacer, read_staged
from ..audit import build_snapshot
from ..core.errors import EvaluationError
from ..llm.counting import CountingEmbedder
from ..query.plan_run import ChunkSource
from ..structured.plan import ConstructionPlan
from ..validation.qa_gold import QAGold, QAQuestion, load_qa_gold
from ..validation.retrieval_scores import (
    RetrievalFingerprint,
    RetrievalOutcome,
    compare_retrieval,
    load_retrieval_report,
    score_retrieval,
)
from ..validation.target_gold import load_target_gold
from .inputs import digest, input_file
from .qa_graph import check_graph
from .qa_stages import side_label
from .qa_systems import check_source, source_params, source_parts
from .stage import PipelineContext, PipelineState
from .stages import BaseStage

Placed = dict[str, list[PlacedTarget]]  # question id -> its targets with their nodes in the build


class RetrieveEvalStage(BaseStage):
    """Rank every question with one system's chunk source, without the reader (`kg retrieve-eval`), and
    score the chunks and seeds it ranks."""

    def __init__(self, system: str):
        check_source(system)  # a plan system has no single ranked list: refused before anything runs
        self.system = system
        self.name = f"retrieve_eval_{system}"
        self.report_file = f"retrieval_{system}.json"

    def params(self, ctx, state):
        gold = input_file(state.need("gold", "pass the QA gold file"), "QA gold file")
        targets = input_file(state.need("anchor_targets", "pass the target gold (--targets)"), "target gold")
        s = ctx.settings
        return {
            **source_params(s, self.system),
            "retrieval_budgets": s.retrieval_budgets,
            "gold": gold,
            "gold_hash": digest(gold),
            "targets": targets,
            "targets_hash": digest(targets),
            "build": input_file(
                state.need("audit_source", "pass the loaded build (--build)"), "build folder"
            ),
            "data_dir": input_file(state.need("data_dir", "pass the dataset folder (--data)"), "data folder"),
            # the chunker settings must be the build's, or the snapshot's chunk ids are not the graph's
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        gold = load_qa_gold(Path(state.gold))
        # a graph without the gold's evidence is refused before any embedding call, so it costs nothing
        graph = check_graph(ctx.driver, gold)
        run.params(graph_digest=graph.value)
        state.plan, placed = _placed_targets(ctx, state, gold)  # the loaded build's plan names its records
        parts = source_parts(ctx, state)
        embedder = CountingEmbedder(parts.embedder)
        depth = max(s.retrieval_budgets)
        source = check_source(self.system)(replace(parts, embedder=embedder), depth)
        outcomes = [_rank(source, q, placed[q.id], self.system, depth) for q in gold.questions]
        fingerprint = RetrievalFingerprint(
            gold_hash=digest(Path(state.gold)),
            targets_hash=digest(Path(state.anchor_targets)),
            graph_digest=graph.value,
            embed_model=s.embed_model,
        )
        report = score_retrieval(outcomes, s.retrieval_budgets, self.system, fingerprint)
        state.retrieval[self.system] = report
        run.metrics(**report.metrics(), embedded_texts=embedder.texts, embedded_chars=embedder.chars)
        run.artifact(ctx.write(self.report_file, report.model_dump_json(indent=1)))
        for file in (Path(state.gold), Path(state.anchor_targets)):
            run.artifact(file)


def _placed_targets(
    ctx: PipelineContext, state: PipelineState, gold: QAGold
) -> tuple[ConstructionPlan, Placed]:
    """The build's plan, and every question's targets placed on the build's snapshot (record refs and
    canonical ids). Raises `EvaluationError` when the target gold was written for another QA gold or leaves
    a question out."""
    targets = load_target_gold(Path(state.anchor_targets))
    if digest(input_file(Path(targets.qa_gold), "the target gold's QA gold")) != digest(Path(state.gold)):
        raise EvaluationError([f"the target gold covers {targets.qa_gold}, not {state.gold}"])
    if missing := sorted({q.id for q in gold.questions} - {t.id for t in targets.questions}):
        raise EvaluationError([f"the target gold has no entry for {len(missing)} questions: {missing[:3]}"])
    build, s = Path(state.audit_source), ctx.settings
    chunking = (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
    snapshot = build_snapshot(build, Path(state.data_dir), chunking)
    plan = ConstructionPlan.model_validate_json((build / "plan.json").read_text(encoding="utf-8"))
    files = {r.file for q in targets.questions for t in q.targets for r in t.records}
    return plan, TargetPlacer(snapshot, plan, read_staged(build / "staging", files)).place(targets)


def _rank(
    source: ChunkSource, question: QAQuestion, targets: list[PlacedTarget], system: str, depth: int
) -> RetrievalOutcome:
    """One question ranked by `source`, timed, with its gold evidence and placed targets."""
    start = time.perf_counter()
    ranked, trace = source.ranked(question.question)
    latency_ms = (time.perf_counter() - start) * 1000
    return RetrievalOutcome(
        question_id=question.id,
        type=question.type,
        system=system,
        gold_chunks=sorted({c.chunk_id for c in question.chunks}),
        ranked=[c.chunk_id for c in ranked[:depth]],
        seeds=trace.seeds if trace is not None else None,
        gold_targets=[t.nodes for t in targets],
        latency_ms=round(latency_ms, 1),
    )


class RetrieveCompareStage(BaseStage):
    """Pair two reports of `kg retrieve-eval` question by question at every budget both have, with the exact
    McNemar test (`kg retrieve-compare`); no graph, no model. System `a` is the first report, `b` the
    second."""

    name = "retrieve_compare"
    REPORT_FILE = "retrieve_compare.json"

    def params(self, ctx, state):
        a, b = state.need("retrieval_reports", "pass two reports of kg retrieve-eval")
        a, b = input_file(a, "retrieval report"), input_file(b, "retrieval report")
        return {"a": a, "a_hash": digest(a), "b": b, "b_hash": digest(b)}

    def run(self, ctx, state, run):
        a_path, b_path = (input_file(p, "retrieval report") for p in state.retrieval_reports)
        a, b = load_retrieval_report(a_path), load_retrieval_report(b_path)
        budgets = sorted(set(a.budgets) & set(b.budgets))
        if not budgets:
            raise EvaluationError([f"the reports share no budget ({a.budgets}, {b.budgets})"])
        comparisons = [compare_retrieval(a, b, k, side_label(a_path), side_label(b_path)) for k in budgets]
        state.retrieval_comparison = comparisons
        for comparison in comparisons:
            run.metrics(**comparison.metrics())
        report = json.dumps([c.model_dump(mode="json") for c in comparisons], indent=1)
        run.artifact(ctx.write(self.REPORT_FILE, report))
        run.artifact(a_path)
        run.artifact(b_path)
