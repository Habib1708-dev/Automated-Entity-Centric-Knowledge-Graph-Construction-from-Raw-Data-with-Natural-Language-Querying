"""Stage functions and the end-to-end run. Each stage is one MLflow run and writes its artifacts to `out/`.

Role in the pipeline: orchestration only. A stage function calls one feature module, logs to MLflow and
persists the result; `run_all` chains the stages.
Design: every external dependency (settings, LLM, embedder, Neo4j driver, tracker) arrives in a
`PipelineContext` built by the composition root (cli.py) or by a test. Nothing here constructs a client or reads globals.
"""

from dataclasses import dataclass, field
from pathlib import Path

from neo4j import Driver

from .config import Settings
from .core.errors import InvalidPlanError, LLMUnavailableError, MissingInputError, ProposalRejectedError
from .extract import PROMPT as EXTRACT_PROMPT
from .extract import Rejected, Triple, extract_all, write_subject_graph
from .importer import construct_domain_graph
from .ingest import Document, load_documents, stage_structured
from .lexical import Chunk, chunk_document, write_lexical_graph
from .link import LinkReport, link_graphs
from .llm.base import Embedder, LLMClient, prompt_version
from .plan import ConstructionPlan, validate_plan
from .profiler import DataProfile, profile_directory
from .resolve import ADJUDICATE_PROMPT, ResolveReport, resolve_entities
from .schema import CRITIC_PROMPT, PROPOSER_PROMPT, propose_plan
from .textschema import PROMPT as TEXT_SCHEMA_PROMPT
from .textschema import TextSchema, propose_text_schema
from .tracking.base import NullTracker, Tracker
from .validate import ValidationReport, validate_graph


@dataclass
class PipelineContext:
    """Everything a stage needs from the outside world. `llm`/`embedder` are None without an API key."""

    settings: Settings
    driver: Driver
    out: Path
    llm: LLMClient | None = None
    embedder: Embedder | None = None
    tracker: Tracker = field(default_factory=NullTracker)

    def require_llm(self) -> LLMClient:
        """Return the LLM client, or fail with a clear message for stages that cannot work without one."""
        if self.llm is None:
            raise LLMUnavailableError("this stage needs an LLM: set GEMINI_API_KEY (see .env.example)")
        return self.llm


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def stage_profile(ctx: PipelineContext, data_dir: Path) -> tuple[Path, DataProfile]:
    """Stage JSON/CSV under `out/staging` and profile the tables."""
    with ctx.tracker.start_run("profile", data_dir=data_dir) as run:
        staged = stage_structured(data_dir, ctx.out / "staging")
        profile = profile_directory(staged)
        run.metrics(
            files=len(profile.files),
            foreign_key_candidates=len(profile.foreign_keys),
            rows=sum(f.row_count for f in profile.files),
        )
        run.artifact(_write(ctx.out / "profile.json", profile.model_dump_json(indent=2)))
    return staged, profile


def stage_plan(ctx: PipelineContext, profile: DataProfile, goal: str) -> ConstructionPlan:
    """Propose and critique the construction plan. The plan file is written even when it is rejected."""
    s = ctx.settings
    with ctx.tracker.start_run(
        "plan",
        goal=goal,
        model=s.schema_model,
        temperature=s.llm_temperature,
        prompt_version=prompt_version(PROPOSER_PROMPT),
        critic_prompt_version=prompt_version(CRITIC_PROMPT),
    ) as run:
        run.text(PROPOSER_PROMPT, "prompts/plan_proposer.txt")
        run.text(CRITIC_PROMPT, "prompts/plan_critic.txt")
        result = propose_plan(goal, profile, ctx.require_llm(), s.schema_model, s.llm_temperature)
        run.metrics(rounds=result.rounds, accepted=int(result.accepted), open_issues=len(result.open_issues))
        run.artifact(_write(ctx.out / "plan.json", result.plan.model_dump_json(indent=2)))
    if not result.accepted:
        raise ProposalRejectedError("plan", result.open_issues)
    return result.plan


def stage_build(
    ctx: PipelineContext, staged: Path, plan: ConstructionPlan, profile: DataProfile
) -> dict[str, int]:
    """Import the domain graph. Returns the expected node count per label, for later validation."""
    with ctx.tracker.start_run("build_domain") as run:
        # re-validated here because a human may have edited out/plan.json since it was proposed
        issues = validate_plan(plan, profile)
        if issues:
            raise InvalidPlanError(issues)
        report = construct_domain_graph(ctx.driver, staged, plan)
        run.metrics(
            rules=len(report.rules),
            rows_written=sum(r.rows_written for r in report.rules),
            rows_dropped=sum(r.rows_unmatched + r.rows_skipped_null_key for r in report.rules),
            clean=int(report.clean),
        )
        run.artifact(_write(ctx.out / "build_report.json", report.model_dump_json(indent=2)))
    return {n.label: profile.file(n.source_file).row_count for n in plan.nodes}


def chunk_documents(ctx: PipelineContext, docs: list[Document]) -> list[Chunk]:
    """Chunk with the configured sizes. One definition, so every caller produces the same chunk ids."""
    s = ctx.settings
    return [c for d in docs for c in chunk_document(d, s.chunk_max_chars, s.chunk_min_chars)]


def stage_ingest_text(
    ctx: PipelineContext, data_dir: Path, embed: bool = True
) -> tuple[list[Document], list[Chunk]]:
    """Load and chunk documents, embed the chunks when an embedder is available, write the lexical graph."""
    s = ctx.settings
    with ctx.tracker.start_run(
        "ingest_text", data_dir=data_dir, chunk_max_chars=s.chunk_max_chars, chunk_min_chars=s.chunk_min_chars
    ) as run:
        docs = load_documents(data_dir)
        chunks = chunk_documents(ctx, docs)
        embeddings = None
        if embed and chunks and ctx.embedder is not None:
            vectors = ctx.embedder.embed([c.text for c in chunks])
            embeddings = {c.chunk_id: v for c, v in zip(chunks, vectors, strict=True)}
        write_lexical_graph(ctx.driver, docs, chunks, embeddings)
        run.metrics(
            documents=len(docs),
            chunks=len(chunks),
            embedded=int(bool(embeddings)),
            avg_chunk_chars=sum(len(c.text) for c in chunks) / len(chunks) if chunks else 0.0,
        )
    return docs, chunks


def stage_text_schema(
    ctx: PipelineContext, goal: str, chunks: list[Chunk], plan: ConstructionPlan | None
) -> TextSchema:
    """Propose entity and fact types for the text. The schema file is written even when it is rejected."""
    s = ctx.settings
    with ctx.tracker.start_run(
        "text_schema",
        goal=goal,
        model=s.schema_model,
        temperature=s.llm_temperature,
        prompt_version=prompt_version(TEXT_SCHEMA_PROMPT),
    ) as run:
        run.text(TEXT_SCHEMA_PROMPT, "prompts/text_schema.txt")
        schema, rounds, issues = propose_text_schema(
            goal, chunks, ctx.require_llm(), s.schema_model, plan, s.llm_temperature
        )
        run.metrics(
            rounds=rounds,
            open_issues=len(issues),
            entity_types=len(schema.entity_types),
            fact_types=len(schema.fact_types),
        )
        run.artifact(_write(ctx.out / "text_schema.json", schema.model_dump_json(indent=2)))
    if issues:
        raise ProposalRejectedError("text schema", issues)
    return schema


def stage_extract(
    ctx: PipelineContext, chunks: list[Chunk], schema: TextSchema
) -> tuple[list[Triple], list[Rejected]]:
    """Extract evidence-verified triples and write the subject graph."""
    s = ctx.settings
    with ctx.tracker.start_run(
        "extract",
        model=s.extract_model,
        temperature=s.llm_temperature,
        prompt_version=prompt_version(EXTRACT_PROMPT),
        workers=s.extract_workers,
    ) as run:
        run.text(EXTRACT_PROMPT, "prompts/extract.txt")
        triples, rejected = extract_all(
            chunks, schema, ctx.require_llm(), s.extract_model, s.llm_temperature, s.extract_workers
        )
        counts = write_subject_graph(ctx.driver, triples, extractor=s.extract_model)
        total = len(triples) + len(rejected)
        run.metrics(
            **counts,
            chunks=len(chunks),
            rejected=len(rejected),
            accept_rate=len(triples) / total if total else 1.0,
            triples_per_chunk=len(triples) / len(chunks) if chunks else 0.0,
        )
        run.artifact(_write(ctx.out / "triples.jsonl", "\n".join(t.model_dump_json() for t in triples)))
        run.artifact(_write(ctx.out / "rejected.jsonl", "\n".join(r.model_dump_json() for r in rejected)))
    return triples, rejected


def stage_resolve(ctx: PipelineContext) -> ResolveReport:
    """Merge duplicate entities. Works without an LLM; borderline pairs are then left unmerged."""
    s = ctx.settings
    with ctx.tracker.start_run(
        "resolve",
        model=s.extract_model,
        er_auto_merge=s.er_auto_merge,
        er_borderline=s.er_borderline,
        prompt_version=prompt_version(ADJUDICATE_PROMPT),
        llm_adjudication=int(ctx.llm is not None),
    ) as run:
        report = resolve_entities(ctx.driver, ctx.llm, s.extract_model, s.er_auto_merge, s.er_borderline)
        run.metrics(
            before=report.entities_before,
            after=report.entities_after,
            merges=report.merges,
            llm_adjudications=sum(d.action.startswith("llm_") for d in report.decisions),
            self_loops_removed=report.self_loops_removed,
        )
        run.artifact(_write(ctx.out / "resolve.json", report.model_dump_json(indent=2)))
    return report


def stage_link(ctx: PipelineContext, plan: ConstructionPlan) -> LinkReport:
    """Link documents and entities to the domain graph."""
    with ctx.tracker.start_run("link", domain_link_threshold=ctx.settings.domain_link_threshold) as run:
        report = link_graphs(ctx.driver, plan, ctx.settings.domain_link_threshold)
        run.metrics(**report.model_dump())
    return report


def stage_validate(
    ctx: PipelineContext,
    plan: ConstructionPlan | None,
    schema: TextSchema | None,
    expected: dict[str, int] | None,
    gold: Path | None = None,
) -> ValidationReport:
    """Run all graph checks and, with a gold file, the accuracy check."""
    with ctx.tracker.start_run("validate", gold=gold) as run:
        report = validate_graph(ctx.driver, plan, schema, expected, gold)
        run.metrics(
            **report.metrics,
            checks_passed=sum(c.passed for c in report.checks),
            checks_total=len(report.checks),
        )
        run.artifact(_write(ctx.out / "validation.json", report.model_dump_json(indent=2)))
    return report


def load_plan(out: Path) -> ConstructionPlan | None:
    """The reviewed plan from `out/plan.json`, or None when the structured path has not run."""
    path = out / "plan.json"
    return ConstructionPlan.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def load_text_schema(out: Path) -> TextSchema | None:
    """The reviewed schema from `out/text_schema.json`, or None when it has not been proposed."""
    path = out / "text_schema.json"
    return TextSchema.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def require(value, what: str, produced_by: str):
    """Return `value`, or explain which command has to run first."""
    if value is None:
        raise MissingInputError(f"{what} not found; run `{produced_by}` first")
    return value


def run_all(
    ctx: PipelineContext, data_dir: Path, goal: str, gold: Path | None = None, embed: bool = True
) -> ValidationReport:
    """Whole pipeline. The structured and the text path are each skipped when their input is absent."""
    with ctx.tracker.start_run("pipeline", data_dir=data_dir, goal=goal):
        staged, profile = stage_profile(ctx, data_dir)
        plan = stage_plan(ctx, profile, goal) if profile.files else None
        expected = stage_build(ctx, staged, plan, profile) if plan else None
        _, chunks = stage_ingest_text(ctx, data_dir, embed)
        schema = None
        if chunks:
            schema = stage_text_schema(ctx, goal, chunks, plan)
            stage_extract(ctx, chunks, schema)
            stage_resolve(ctx)
        if plan:
            stage_link(ctx, plan)
        return stage_validate(ctx, plan, schema, expected, gold)
