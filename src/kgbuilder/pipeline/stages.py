"""The concrete pipeline stages, in execution order.

Role in the pipeline: each class adapts one feature module to the `Stage` protocol: it takes inputs from
`PipelineState`, calls the feature, stores the result, and logs metrics and artifacts.
Design: stages contain no business logic, only wiring and logging. What each stage must log is
specified in the `mlflow-tracking` skill; metric names are a contract, keep them stable.
"""

import json
from dataclasses import asdict
from pathlib import Path

from ..core.errors import InvalidPlanError, MissingInputError, ProposalRejectedError
from ..llm.base import prompt_version
from ..llm.refine import Refinement
from ..resolution import resolver
from ..resolution.linking import link_graphs
from ..structured import proposer
from ..structured.importer import BATCH_SIZE, construct_domain_graph
from ..structured.plan import validate_plan
from ..structured.profiler import profile_directory
from ..structured.staging import stage_structured
from ..text import extraction
from ..text import schema as text_schema
from ..text.chunking import chunk_document
from ..text.documents import load_documents
from ..text.lexical import write_lexical_graph
from ..text.subject_graph import write_subject_graph
from ..tracking.base import Run
from ..validation.evaluate import evaluate, load_gold
from ..validation.validator import validate_graph
from .stage import PLAN_FILE, TEXT_SCHEMA_FILE, PipelineContext, PipelineState


class BaseStage:
    """Defaults shared by all stages: always applies, no params, nothing to review."""

    name = "stage"
    review_file: str | None = None

    def applies(self, state: PipelineState) -> bool:
        return True

    def params(self, ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
        return {}

    def reload(self, ctx: PipelineContext, state: PipelineState) -> None:
        return None


def _log_refinement(ctx: PipelineContext, run: Run, result: Refinement, history_file: str) -> None:
    """Metrics and the per-round issue history of a propose/validate/critique loop."""
    first = result.history[0]
    run.metrics(
        rounds=result.rounds,
        accepted=int(result.accepted),
        open_issues=len(result.open_issues),
        # how far the first attempt was from mechanically valid: the number to watch when tuning a prompt
        validation_errors_round_1=len(first.issues) if first.source == "code" else 0,
    )
    run.artifact(ctx.write(history_file, json.dumps([asdict(r) for r in result.history], indent=2)))


def _gold_file(path: Path) -> Path:
    if not Path(path).exists():
        raise MissingInputError(f"gold file '{path}' not found")
    return Path(path)


class ProfileStage(BaseStage):
    """Stage JSON/CSV under `out/staging` and profile the tables."""

    name = "profile"

    def params(self, ctx, state):
        return {"data_dir": state.data_dir}

    def run(self, ctx, state, run):
        data_dir = state.need("data_dir", "pass the data directory")
        staging = stage_structured(data_dir, ctx.out / "staging")
        state.staged_dir = staging.staged_dir
        state.profile = profile_directory(staging.staged_dir)
        run.metrics(
            files=len(state.profile.files),
            files_skipped=len(staging.skipped),
            foreign_key_candidates=len(state.profile.foreign_keys),
            rows=sum(f.row_count for f in state.profile.files),
        )
        run.artifact(ctx.write("profile.json", state.profile.model_dump_json(indent=2)))


class PlanStage(BaseStage):
    """Propose and critique the construction plan. The plan file is written even when it is rejected."""

    name = "plan"
    review_file = PLAN_FILE

    def applies(self, state):
        return bool(state.profile and state.profile.files)  # no tables: no structured path

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "goal": state.goal,
            "model": s.schema_model,
            "temperature": s.llm_temperature,
            "prompt_version": prompt_version(proposer.PROPOSER_PROMPT),
            "critic_prompt_version": prompt_version(proposer.CRITIC_PROMPT),
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(proposer.PROPOSER_PROMPT, "prompts/plan_proposer.txt")
        run.text(proposer.CRITIC_PROMPT, "prompts/plan_critic.txt")
        result = proposer.propose_plan(
            state.need("goal", "pass --goal"),
            state.need("profile", "run the profile stage first"),
            ctx.require_llm(),
            s.schema_model,
            s.llm_temperature,
        )
        _log_refinement(ctx, run, result, "plan_rounds.json")
        run.artifact(ctx.write(PLAN_FILE, result.value.model_dump_json(indent=2)))
        if not result.accepted:
            raise ProposalRejectedError("plan", result.open_issues)
        state.plan = result.value

    def reload(self, ctx, state):
        state.plan = None
        state.load_plan(ctx)


class BuildStage(BaseStage):
    """Import the domain graph from the (possibly hand-edited) plan."""

    name = "build_domain"

    def applies(self, state):
        return state.plan is not None

    def params(self, ctx, state):
        return {"batch_size": BATCH_SIZE}

    def run(self, ctx, state, run):
        plan = state.load_plan(ctx)
        profile = state.need("profile", "run the profile stage first")
        # validated again here because a human may have edited out/plan.json since it was proposed
        issues = validate_plan(plan, profile)
        if issues:
            raise InvalidPlanError(issues)
        report = construct_domain_graph(ctx.driver, state.need("staged_dir", "run the profile stage"), plan)
        state.expected_counts = {n.label: profile.file(n.source_file).row_count for n in plan.nodes}
        run.metrics(
            rules=len(report.rules),
            nodes_written=report.written("node"),
            relationships_written=report.written("relationship"),
            rows_dropped=report.rows_dropped,
            clean=int(report.clean),
        )
        run.artifact(ctx.write("build_report.json", report.model_dump_json(indent=2)))


class IngestTextStage(BaseStage):
    """Load and chunk documents, embed the chunks when possible, write the lexical graph."""

    name = "ingest_text"

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "data_dir": state.data_dir,
            "chunk_max_chars": s.chunk_max_chars,
            "chunk_min_chars": s.chunk_min_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
            "embed_model": s.embed_model,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        docs = load_documents(state.need("data_dir", "pass the data directory"), exclude=ctx.out)
        chunks = [
            c
            for d in docs
            for c in chunk_document(d, s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
        ]
        embeddings = None
        if state.embed and chunks and ctx.embedder is not None:
            vectors = ctx.embedder.embed([c.text for c in chunks])
            embeddings = {c.chunk_id: v for c, v in zip(chunks, vectors, strict=True)}
        stale = write_lexical_graph(ctx.driver, docs, chunks, embeddings)
        state.chunks = chunks
        run.metrics(
            documents=len(docs),
            chunks=len(chunks),
            embedded=int(bool(embeddings)),
            avg_chunk_chars=sum(len(c.text) for c in chunks) / len(chunks) if chunks else 0.0,
            stale_chunks_removed=stale,
        )


class _TextStage(BaseStage):
    """Base of the stages that only make sense when there is text."""

    def applies(self, state):
        return bool(state.chunks)


class TextSchemaStage(_TextStage):
    """Propose entity and fact types. The schema file is written even when it is rejected."""

    name = "text_schema"
    review_file = TEXT_SCHEMA_FILE

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "goal": state.goal,
            "model": s.schema_model,
            "temperature": s.llm_temperature,
            "prompt_version": prompt_version(text_schema.PROMPT),
            "critic_prompt_version": prompt_version(text_schema.CRITIC_PROMPT),
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(text_schema.PROMPT, "prompts/text_schema_proposer.txt")
        run.text(text_schema.CRITIC_PROMPT, "prompts/text_schema_critic.txt")
        result = text_schema.propose_text_schema(
            state.need("goal", "pass --goal"),
            state.load_chunks(ctx),
            ctx.require_llm(),
            s.schema_model,
            state.load_plan(ctx, required=False),
            s.llm_temperature,
        )
        _log_refinement(ctx, run, result, "text_schema_rounds.json")
        run.metrics(entity_types=len(result.value.entity_types), fact_types=len(result.value.fact_types))
        run.artifact(ctx.write(TEXT_SCHEMA_FILE, result.value.model_dump_json(indent=2)))
        if not result.accepted:
            raise ProposalRejectedError("text schema", result.open_issues)
        state.text_schema = result.value

    def reload(self, ctx, state):
        state.text_schema = None
        schema = state.load_text_schema(ctx)
        issues = text_schema.validate_text_schema(schema)
        if issues:  # a hand edit must pass the same gate as the LLM's proposal
            raise ProposalRejectedError("edited text schema", issues)


class ExtractStage(_TextStage):
    """Extract evidence-verified triples and write the subject graph."""

    name = "extract"

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "model": s.extract_model,
            "temperature": s.llm_temperature,
            "prompt_version": prompt_version(extraction.PROMPT),
            "workers": s.extract_workers,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(extraction.PROMPT, "prompts/extract.txt")
        chunks = state.load_chunks(ctx)
        result = extraction.extract_all(
            chunks,
            state.load_text_schema(ctx),
            ctx.require_llm(),
            s.extract_model,
            s.llm_temperature,
            s.extract_workers,
        )
        counts = write_subject_graph(ctx.driver, result.triples, extractor=s.extract_model)
        state.extraction = result
        run.metrics(
            **counts.model_dump(),
            chunks=len(chunks),
            rejected=len(result.rejected),
            accept_rate=result.accept_rate,
            triples_per_chunk=len(result.triples) / len(chunks),
            # which verification rule fires most tells you what to fix in the prompt
            **{f"rejected_{reason}": n for reason, n in result.rejections_by_reason().items()},
        )
        run.artifact(ctx.write("triples.jsonl", "\n".join(t.model_dump_json() for t in result.triples)))
        run.artifact(ctx.write("rejected.jsonl", "\n".join(r.model_dump_json() for r in result.rejected)))


class ResolveStage(_TextStage):
    """Merge duplicate entities. Works without an LLM; borderline pairs are then left unmerged."""

    name = "resolve"

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "model": s.extract_model,
            "er_auto_merge": s.er_auto_merge,
            "er_borderline": s.er_borderline,
            "er_embedding_candidates": s.er_embedding_candidates,
            "prompt_version": prompt_version(resolver.ADJUDICATE_PROMPT),
            "llm_adjudication": int(ctx.llm is not None),
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        report = resolver.resolve_entities(
            ctx.driver,
            ctx.llm,
            s.extract_model,
            s.er_auto_merge,
            s.er_borderline,
            ctx.embedder,
            s.er_embedding_candidates,
        )
        state.resolution = report
        run.metrics(
            before=report.entities_before,
            after=report.entities_after,
            merges=report.merges,
            candidates=len(report.decisions),
            llm_adjudications=sum(d.action.startswith("llm_") for d in report.decisions),
            skipped_borderline=sum(d.action == "skipped_borderline" for d in report.decisions),
            self_loops_removed=report.self_loops_removed,
        )
        # resolve.json is both the audit log and the input of `kg resolve --undo`
        run.artifact(ctx.write("resolve.json", report.model_dump_json(indent=2)))


class UndoResolveStage(BaseStage):
    """Undo the merges of the last resolve run, from the snapshot in out/resolve.json."""

    name = "resolve_undo"

    def run(self, ctx, state, run):
        path = ctx.out / "resolve.json"
        if not path.exists():
            raise MissingInputError(f"{path} not found; there is no resolve run to undo")
        report = resolver.ResolveReport.model_validate_json(path.read_text(encoding="utf-8"))
        restored = resolver.undo_merges(ctx.driver, report)
        state.resolution = None
        run.metrics(entities_restored=restored)


class LinkStage(BaseStage):
    """Link documents and entities to the domain graph."""

    name = "link"

    def applies(self, state):
        return state.plan is not None

    def params(self, ctx, state):
        return {"domain_link_threshold": ctx.settings.domain_link_threshold}

    def run(self, ctx, state, run):
        state.links = link_graphs(ctx.driver, state.load_plan(ctx), ctx.settings.domain_link_threshold)
        run.metrics(**state.links.model_dump())


class ValidateStage(BaseStage):
    """Run all graph checks and, with a gold file, the accuracy check."""

    name = "validate"

    def params(self, ctx, state):
        return {"gold": state.gold, "gold_min_recall": ctx.settings.gold_min_recall}

    def run(self, ctx, state, run):
        report = validate_graph(
            ctx.driver,
            state.load_plan(ctx, required=False),
            state.load_text_schema(ctx, required=False),
            state.expected_counts,
            load_gold(_gold_file(state.gold)) if state.gold else None,
            ctx.settings.gold_min_recall,
        )
        state.validation = report
        run.metrics(
            **report.metrics,
            checks_passed=sum(c.passed for c in report.checks),
            checks_total=len(report.checks),
        )
        run.artifact(ctx.write("validation.json", report.model_dump_json(indent=2)))


class EvalStage(BaseStage):
    """Score the graph against gold data: precision/recall/F1, ER accuracy, question answers."""

    name = "eval"

    def params(self, ctx, state):
        return {"gold": state.gold}

    def run(self, ctx, state, run):
        gold = _gold_file(state.need("gold", "pass the gold file"))
        state.evaluation = evaluate(ctx.driver, load_gold(gold))
        run.metrics(**state.evaluation.metrics())
        run.artifact(gold)  # the gold file defines what the scores mean, so it travels with them
        run.artifact(ctx.write("eval_report.json", state.evaluation.model_dump_json(indent=2)))
