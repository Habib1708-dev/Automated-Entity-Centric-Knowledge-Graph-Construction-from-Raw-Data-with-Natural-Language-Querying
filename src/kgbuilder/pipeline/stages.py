"""The concrete pipeline stages, in execution order.

Role in the pipeline: each class adapts one feature module to the `Stage` protocol: it takes inputs from
`PipelineState`, calls the feature, stores the result, and logs metrics and artifacts.
Design: stages contain no business logic, only wiring and logging. What each stage must log is
specified in the `mlflow-tracking` skill; metric names are a contract, keep them stable.
"""

import json
from dataclasses import asdict
from pathlib import Path

from ..core.errors import InvalidPlanError, ProposalRejectedError
from ..core.text import norm
from ..llm.base import prompt_version
from ..llm.refine import Refinement
from ..llm.thinking import with_thinking
from ..resolution import concepts, individuals
from ..resolution.attachment import attach_claims
from ..resolution.blocking import Blocking, blocking_from
from ..resolution.derivation import DerivationReport, derive_facts
from ..resolution.identity import IdentityReport, IdentitySettings, resolve_identity
from ..resolution.identity_graph import clear_identity
from ..resolution.linking import link_graphs
from ..resolution.matchers import EmbeddingMatcher, FuzzyNameMatcher
from ..resolution.mentions import read_mentions
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
from ..text.record_documents import record_documents
from ..text.subject_graph import write_subject_graph
from ..tracking.base import Run
from ..validation.assertion import load_assertion_gold, load_assertion_verdicts, score_assertion
from ..validation.checks.base import CheckContext
from ..validation.coverage import load_coverage_verdicts, score_coverage
from ..validation.coverage_sheet import CoverageSheet, build_coverage_sheet, read_chunk_things
from ..validation.evaluate import evaluate
from ..validation.gold import load_gold
from ..validation.judge import JudgeSheet, load_verdicts
from ..validation.rescore import rescore
from ..validation.sentences import SentenceSample, draw_sample
from ..validation.validator import validate_graph
from .inputs import digest, input_file
from .stage import PLAN_FILE, PROFILE_FILE, STAGING_DIR, TEXT_SCHEMA_FILE, PipelineContext, PipelineState


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


def _er_settings(ctx: PipelineContext) -> dict[str, object]:
    """The entity-resolution settings (thresholds and blocking rule), logged the same way by the resolve
    run and its preview, and whether meaning-based candidates could be computed at all (they need an
    embedder)."""
    s = ctx.settings
    return {
        "er_auto_merge": s.er_auto_merge,
        "er_borderline": s.er_borderline,
        "er_embedding_blocking": s.er_embedding_blocking,
        "er_embedding_candidates": s.er_embedding_candidates,
        "er_neighbours": s.er_neighbours,
        "embed_model": s.embed_model if ctx.embedder is not None else None,
    }


def _identity_metrics(report: IdentityReport) -> dict[str, float]:
    """The metrics of an identity run (R75): where the mentions went, and the concept decisions as before."""
    to_records = [a for a in report.assignments if a.kind == "record"]
    return {
        "mentions": report.mentions,
        "mentions_to_records": len(to_records),
        "mentions_to_individuals": report.count("individual"),
        "mentions_to_concepts": report.count("concept"),
        "mentions_ambiguous": len(report.ambiguous),
        "records_referred": len({a.canonical for a in to_records}),
        # comparable with `entities_linked` of the link runs before R75, which counted entities (one per
        # type and name) with a link: here the distinct types and names among the linked mentions
        "entities_linked": len({(a.type, norm(a.said)) for a in to_records}),
        **{f"linked_by_{reason}": sum(a.reason == reason for a in to_records) for reason in _LINK_REASONS},
        "individuals": len({a.canonical for a in report.assignments if a.kind == "individual"}),
        # individuals across documents (R75 b2): pairs nominated, asked, joined, and refused for each reason
        "individual_candidates": len(report.individual_decisions),
        **{
            f"individual_{action}": sum(d.action == action for d in report.individual_decisions)
            for action in _INDIVIDUAL_ACTIONS
        },
        # the concept pairs each guard kept apart; every guard is present, 0 when it blocked nothing
        **{f"blocked_{name}": report.blocked.get(name, 0) for name in _GUARDS},
        # the names before R75's metrics kept, now counting concepts
        "before": report.concepts_before,
        "after": report.concepts_after,
        "merges": report.merges,
        "passes": report.passes,
        "candidates": len(report.decisions),
        "llm_adjudications": sum(d.action.startswith("llm_") for d in report.decisions),
        "skipped_borderline": sum(d.action == "skipped_borderline" for d in report.decisions),
    }


_LINK_REASONS = ("key", "name", "contained", "key_in_sentence", "attribute", "variant_attribute")
_INDIVIDUAL_ACTIONS = ("joined", "apart", "quote_not_verified", "different_records", "skipped")
_GUARDS = ("opposed_polarity", "same_sentence", "part_and_whole", "compound_name")


def _er_blocking(ctx: PipelineContext) -> Blocking | None:
    """The blocking rule the settings name; None = spelling candidates only."""
    s = ctx.settings
    return blocking_from(s.er_embedding_blocking, s.er_embedding_candidates, s.er_neighbours)


class ProfileStage(BaseStage):
    """Stage JSON/CSV under `out/staging` and profile the tables."""

    name = "profile"

    def params(self, ctx, state):
        return {"data_dir": state.data_dir}

    def run(self, ctx, state, run):
        data_dir = state.need("data_dir", "pass the data directory")
        staging = stage_structured(data_dir, ctx.out / STAGING_DIR)
        state.staged_dir = staging.staged_dir
        state.profile = profile_directory(staging.staged_dir)
        run.metrics(
            files=len(state.profile.files),
            files_skipped=len(staging.skipped),
            foreign_key_candidates=len(state.profile.foreign_keys),
            rows=sum(f.row_count for f in state.profile.files),
            # columns holding running text (R67): these records become documents at ingest
            prose_columns=sum(c.is_prose for f in state.profile.files for c in f.columns),
        )
        run.artifact(ctx.write(PROFILE_FILE, state.profile.model_dump_json(indent=2)))


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
            "thinking": s.schema_thinking,
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
            with_thinking(ctx.require_llm(), s.schema_thinking),
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
        # records with prose columns become documents too (R67). Opportunistic: without a plan, a
        # profile and staged tables (a text-only dataset, or ingest before profile) there are none.
        plan = state.load_plan(ctx, required=False)
        profile = state.load_profile(ctx)
        staged = Path(state.staged_dir) if state.staged_dir else ctx.out / STAGING_DIR
        record_docs = (
            record_documents(staged, plan, profile)
            if plan is not None and profile is not None and staged.is_dir()
            else []
        )
        docs = [*docs, *record_docs]
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
            record_documents=len(record_docs),
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
            "thinking": s.schema_thinking,
            "prompt_version": prompt_version(text_schema.PROMPT),
            "critic_prompt_version": prompt_version(text_schema.CRITIC_PROMPT),
            "schema_context_chars": s.schema_context_chars,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(text_schema.PROMPT, "prompts/text_schema_proposer.txt")
        run.text(text_schema.CRITIC_PROMPT, "prompts/text_schema_critic.txt")
        chunks = state.load_chunks(ctx)
        context = text_schema.select_context(chunks, s.schema_context_chars)
        # how much of the text the proposer saw: all of it unless the corpus exceeds the budget
        run.metrics(
            context_chunks=len(context),
            chunks_total=len(chunks),
            context_chars=sum(len(c.text) for c in context),
        )
        result = text_schema.propose_text_schema(
            state.need("goal", "pass --goal"),
            context,
            with_thinking(ctx.require_llm(), s.schema_thinking),
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
        # with the plan, so that a hand-edited identity class is checked against the labels it names
        issues = text_schema.validate_text_schema(schema, state.load_plan(ctx, required=False))
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
            "thinking": s.extract_thinking,
            "prompt_version": prompt_version(extraction.PROMPT),
            "workers": s.extract_workers,
            "passes": s.extract_passes,
            "glean_prompt_version": prompt_version(extraction.GLEAN_SUFFIX),
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(extraction.PROMPT, "prompts/extract.txt")
        if s.extract_passes > 1:
            run.text(extraction.GLEAN_SUFFIX, "prompts/extract_glean.txt")
        chunks = state.load_chunks(ctx)
        result = extraction.extract_all(
            chunks,
            state.load_text_schema(ctx),
            with_thinking(ctx.require_llm(), s.extract_thinking),
            s.extract_model,
            s.llm_temperature,
            s.extract_workers,
            s.extract_passes,
        )
        counts = write_subject_graph(ctx.driver, result.triples, extractor=s.extract_model)
        state.extraction = result
        run.metrics(
            **counts.model_dump(),
            chunks=len(chunks),
            rejected=len(result.rejected),
            accept_rate=result.accept_rate,
            rejected_off_schema_rate=result.off_schema_rate,
            # what each pass added: pass 2 and later show the value of asking again (R61)
            **{f"facts_pass{n + 1}": k for n, k in enumerate(result.accepted_per_pass)},
            triples_per_chunk=len(result.triples) / len(chunks),
            # which verification rule fires most tells you what to fix in the prompt
            **{f"rejected_{reason}": n for reason, n in result.rejections_by_reason().items()},
        )
        run.artifact(ctx.write("triples.jsonl", "\n".join(t.model_dump_json() for t in result.triples)))
        run.artifact(ctx.write("rejected.jsonl", "\n".join(r.model_dump_json() for r in result.rejected)))
        run.artifact(ctx.write("off_schema.json", json.dumps(result.off_schema_signatures(), indent=2)))


class ResolveStage(_TextStage):
    """Decide what every mention refers to (R75): a record, an individual or a concept, written as identity
    edges. Works without an LLM; borderline concept pairs are then left apart."""

    name = "resolve"

    def params(self, ctx, state):
        s = ctx.settings
        return {
            **_er_settings(ctx),
            "domain_link_threshold": s.domain_link_threshold,
            "model": s.extract_model,
            "thinking": s.extract_thinking,
            "prompt_version": prompt_version(concepts.ADJUDICATE_PROMPT),
            "individual_prompt_version": prompt_version(individuals.IDENTITY_PROMPT),
            "llm_adjudication": int(ctx.llm is not None),
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.text(concepts.ADJUDICATE_PROMPT, "prompts/resolve_concepts.txt")
        run.text(individuals.IDENTITY_PROMPT, "prompts/resolve_individuals.txt")
        schema = state.load_text_schema(ctx, required=False)
        plan = state.load_plan(ctx, required=False)
        issues = text_schema.identity_issues(schema, plan) if schema is not None else []
        if issues:  # a keyed type must name labels the plan has, or no record could be found for it
            raise ProposalRejectedError("text schema identity classes", issues)
        report = resolve_identity(
            ctx.driver,
            schema,
            plan,
            with_thinking(ctx.llm, s.extract_thinking) if ctx.llm else None,
            s.extract_model,
            IdentitySettings(
                auto_merge=s.er_auto_merge, borderline=s.er_borderline, link_threshold=s.domain_link_threshold
            ),
            ctx.embedder,
            _er_blocking(ctx),
        )
        state.resolution = report
        run.metrics(**_identity_metrics(report))
        # resolve.json is the audit log: every mention's canonical entity with the reason that decided it
        run.artifact(ctx.write("resolve.json", report.model_dump_json(indent=2)))


class PreviewResolveStage(_TextStage):
    """List the pairs a resolve run would consider, with scores and routes; no LLM call, no write."""

    name = "resolve_preview"
    PREVIEW_FILE = "resolve_preview.json"

    def params(self, ctx, state):
        return _er_settings(ctx)

    def run(self, ctx, state, run):
        s = ctx.settings
        schema = state.load_text_schema(ctx, required=False)
        mentions = [
            m for m in read_mentions(ctx.driver) if schema is None or schema.identity_of(m.type) == "concept"
        ]
        guards = concepts.concept_guards(ctx.driver, mentions, schema)
        preview = concepts.preview_concepts(
            mentions, s.er_auto_merge, s.er_borderline, ctx.embedder, _er_blocking(ctx), guards
        )
        state.resolve_preview = preview
        run.metrics(
            entities=preview.entities,
            candidates=len(preview.pairs),
            candidates_fuzzy=sum(p.signal == FuzzyNameMatcher.name for p in preview.pairs),
            candidates_embedding=sum(p.signal == EmbeddingMatcher.name for p in preview.pairs),
            would_auto_merge=sum(p.route == "auto" for p in preview.pairs),
        )
        run.artifact(ctx.write(self.PREVIEW_FILE, preview.model_dump_json(indent=2)))


class UndoResolveStage(BaseStage):
    """Remove the identity layer of the last resolve run: every mention's REFERS_TO edge and every concept
    and individual node (R75; the merges and their snapshots of before are gone)."""

    name = "resolve_undo"

    def run(self, ctx, state, run):
        removed = clear_identity(ctx.driver)
        state.resolution = None
        run.metrics(identity_edges_removed=removed)


class LinkStage(BaseStage):
    """Link documents and sections to the domain graph and write the facts the text schema derives. Runs
    before `resolve` (R75); which things each claim is about is the attach stage's, after `resolve` (R76)."""

    name = "link"

    def applies(self, state):
        return state.plan is not None

    def run(self, ctx, state, run):
        plan = state.load_plan(ctx)
        state.links = link_graphs(ctx.driver, plan)
        # derivation needs the ABOUT links written just above; without a text schema there is no text path
        schema = state.load_text_schema(ctx, required=False)
        derived = (
            derive_facts(ctx.driver, schema, plan)
            if schema is not None
            else DerivationReport(facts_derived=0, mentions_created=0, skipped_no_evidence=0)
        )
        run.metrics(**state.links.model_dump(), **derived.model_dump())


class AttachStage(_TextStage):
    """Attach every claim to the records and individuals it is about, each edge with the route that
    justified it (R76). After `resolve`: two routes read the identity edges. No LLM."""

    name = "attach"

    def run(self, ctx, state, run):
        report = attach_claims(
            ctx.driver, state.load_plan(ctx, required=False), state.load_text_schema(ctx, required=False)
        )
        state.attachment = report
        run.metrics(
            **report.model_dump(exclude={"by_route"}),
            **{f"attached_{how}": n for how, n in report.by_route.items()},
        )


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
            load_gold(input_file(state.gold, "gold file")) if state.gold else None,
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
    """Score the graph against gold data (exact match, ER accuracy, questions) and, with a verdict file,
    against the judge's verdicts. Always writes the judge sheet: the list of what a judge would decide."""

    name = "eval"
    SHEET_FILE = "judge_sheet.json"

    def params(self, ctx, state):
        gold = input_file(state.need("gold", "pass the gold file"), "gold file")
        params: dict[str, object] = {"gold": gold, "gold_hash": digest(gold), "verdicts": state.verdicts}
        schema = ctx.out / TEXT_SCHEMA_FILE
        if schema.exists():
            # path truth depends on which fact types the schema derives, so the schema identifies it
            params["text_schema_hash"] = digest(schema)
        if state.verdicts:
            verdicts = input_file(state.verdicts, "verdict file")
            # the judge model and the verdict content identify what "validated" means for this run
            params.update(
                judge_model=load_verdicts(verdicts).judge.model, judge_verdicts_hash=digest(verdicts)
            )
        return params

    def run(self, ctx, state, run):
        gold = input_file(state.gold, "gold file")
        verdicts = load_verdicts(input_file(state.verdicts, "verdict file")) if state.verdicts else None
        report = evaluate(ctx.driver, load_gold(gold), verdicts, state.load_text_schema(ctx, required=False))
        state.evaluation = report
        run.metrics(**report.metrics())
        run.artifact(gold)  # the gold file defines what the scores mean, so it travels with them
        if report.judge_sheet is not None:
            run.artifact(ctx.write(self.SHEET_FILE, report.judge_sheet.model_dump_json(indent=2)))
        if state.verdicts:
            run.artifact(Path(state.verdicts))
        run.artifact(ctx.write("eval_report.json", report.model_dump_json(indent=2, exclude={"judge_sheet"})))


class RescoreStage(EvalStage):
    """Score the judge sheet an earlier eval run logged with today's matching and gold (R48): no graph,
    no LLM. Same params as `eval` plus the source sheet and its hash; its own output file names, so the
    current graph's judge sheet under out/ is never overwritten."""

    name = "rescore"
    SHEET_FILE = "rescore_sheet.json"

    def params(self, ctx, state):
        sheet = input_file(state.need("sheet", "pass the logged judge sheet"), "judge sheet")
        return {**super().params(ctx, state), "sheet": sheet, "sheet_hash": digest(sheet)}

    def run(self, ctx, state, run):
        sheet = input_file(state.sheet, "judge sheet")
        gold = input_file(state.gold, "gold file")
        verdicts = load_verdicts(input_file(state.verdicts, "verdict file")) if state.verdicts else None
        logged = JudgeSheet.model_validate_json(sheet.read_text(encoding="utf-8"))
        report = rescore(logged, load_gold(gold), verdicts, state.load_text_schema(ctx, required=False))
        state.evaluation = report
        run.metrics(**report.metrics())
        for source in (sheet, gold, *([Path(state.verdicts)] if state.verdicts else [])):
            run.artifact(source)
        run.artifact(ctx.write(self.SHEET_FILE, report.judge_sheet.model_dump_json(indent=2)))
        run.artifact(
            ctx.write("rescore_report.json", report.model_dump_json(indent=2, exclude={"judge_sheet"}))
        )


class CoverageSampleStage(BaseStage):
    """Draw the fixed sentence sample of the coverage estimate (R68) from the graph's chunks and write it
    to the sample file, which is committed and judged like a gold file (so not under out/)."""

    name = "coverage_sample"

    def params(self, ctx, state):
        return {
            "sample": state.need("sample", "pass the sample file to write"),
            "sample_size": state.need("sample_size", "pass the sample size"),
            "sample_seed": state.need("sample_seed", "pass the seed"),
        }

    def run(self, ctx, state, run):
        sample = draw_sample(state.load_chunks(ctx), state.sample_size, state.sample_seed)
        target = Path(state.sample)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sample.model_dump_json(indent=2), encoding="utf-8")
        run.metrics(sentences_total=sample.population, sentences_sampled=len(sample.sentences))
        run.artifact(target)


class CoverageSheetStage(BaseStage):
    """Write the coverage sheet: every sentence of a sample file, found again in the current graph, with
    the observations of its chunk and the things and records the chunk hangs on (R68)."""

    name = "coverage_sheet"
    SHEET_FILE = "coverage_sheet.json"

    def params(self, ctx, state):
        sample = input_file(state.need("sample", "pass the sample file"), "sample file")
        params: dict[str, object] = {"sample": sample, "sample_hash": digest(sample)}
        schema = ctx.out / TEXT_SCHEMA_FILE
        if schema.exists():
            # the sheet shows the schema's fact types, and a missed claim is judged against them
            params["text_schema_hash"] = digest(schema)
        return params

    def run(self, ctx, state, run):
        sample_file = input_file(state.sample, "sample file")
        sample = SentenceSample.model_validate_json(sample_file.read_text(encoding="utf-8"))
        chunks = state.load_chunks(ctx)
        things = read_chunk_things(ctx.driver, [c.chunk_id for c in chunks])
        facts = CheckContext(ctx.driver).facts
        sheet = build_coverage_sheet(sample, chunks, facts, things, state.load_text_schema(ctx))
        run.metrics(
            sentences=len(sheet.sentences),
            sentences_with_observations=sum(bool(s.observations) for s in sheet.sentences),
            # a sentence whose chunk hangs on no thing can only be found by searching the text
            sentences_on_a_thing=sum(bool(s.things) for s in sheet.sentences),
            observations_shown=sum(len(s.observations) for s in sheet.sentences),
        )
        run.artifact(sample_file)
        run.artifact(ctx.write(self.SHEET_FILE, sheet.model_dump_json(indent=2)))


class CoverageStage(BaseStage):
    """Score the judge's coverage verdicts against their sheet (R68), with no graph and no LLM: coverage
    and reachability with Wilson intervals, by polarity and schema place, and the misses per cause."""

    name = "coverage"
    REPORT_FILE = "coverage_report.json"

    def params(self, ctx, state):
        sheet = input_file(state.need("coverage_sheet", "pass the coverage sheet"), "coverage sheet")
        verdicts = input_file(state.need("verdicts", "pass the verdict file"), "verdict file")
        # the sheet and the verdicts identify what was judged and how, as gold and verdicts do for `eval`
        return {
            "sheet": sheet,
            "sheet_hash": digest(sheet),
            "verdicts": verdicts,
            "judge_verdicts_hash": digest(verdicts),
            "judge_model": load_coverage_verdicts(verdicts).judge.model,
        }

    def run(self, ctx, state, run):
        sheet_file = input_file(state.coverage_sheet, "coverage sheet")
        verdicts_file = input_file(state.verdicts, "verdict file")
        sheet = CoverageSheet.model_validate_json(sheet_file.read_text(encoding="utf-8"))
        report = score_coverage(sheet, load_coverage_verdicts(verdicts_file))
        state.coverage = report
        run.metrics(**report.metrics())
        run.artifact(sheet_file)
        run.artifact(verdicts_file)
        run.artifact(ctx.write(self.REPORT_FILE, report.model_dump_json(indent=2)))


class AssertionStage(BaseStage):
    """Score the judge's matching of the assertion gold against a coverage sheet (R77), with no graph and no
    LLM: per field (truth, modality, condition) the share of matched claims that keep their label, by the
    judge and exactly, overall and per gold value."""

    name = "assertion"
    REPORT_FILE = "assertion_report.json"

    def params(self, ctx, state):
        sheet = input_file(state.need("coverage_sheet", "pass the coverage sheet"), "coverage sheet")
        gold = input_file(state.need("assertion_gold", "pass the assertion gold"), "assertion gold")
        verdicts = input_file(state.need("verdicts", "pass the verdict file"), "verdict file")
        # the sheet, the gold and the verdicts identify what was judged and how, as for `eval`
        return {
            "sheet": sheet,
            "sheet_hash": digest(sheet),
            "gold": gold,
            "gold_hash": digest(gold),
            "verdicts": verdicts,
            "judge_verdicts_hash": digest(verdicts),
            "judge_model": load_assertion_verdicts(verdicts).judge.model,
        }

    def run(self, ctx, state, run):
        sheet_file = input_file(state.coverage_sheet, "coverage sheet")
        gold_file = input_file(state.assertion_gold, "assertion gold")
        verdicts_file = input_file(state.verdicts, "verdict file")
        sheet = CoverageSheet.model_validate_json(sheet_file.read_text(encoding="utf-8"))
        report = score_assertion(
            sheet, load_assertion_gold(gold_file), load_assertion_verdicts(verdicts_file)
        )
        state.assertion = report
        run.metrics(**report.metrics())
        for file in (sheet_file, gold_file, verdicts_file):
            run.artifact(file)
        run.artifact(ctx.write(self.REPORT_FILE, report.model_dump_json(indent=2)))
