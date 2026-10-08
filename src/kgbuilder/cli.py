"""Command line interface and composition root: one Typer command per pipeline stage, plus `run`/`reset`.

Role in the pipeline: the entry point. It is the only place that reads settings and builds the concrete
LLM client, cache, MLflow tracker, Neo4j driver and PostgreSQL table source; everything below receives them
through a `PipelineContext`.
Design: composition root (Factory). A command only chooses stages, fills `PipelineState` from its
arguments and prints the result. Expected failures (`KgBuilderError`) become a message and exit code 1.
Not here: pipeline logic (pipeline/) and anything that talks to the LLM or Neo4j directly.
"""

import logging
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import typer
from pydantic import ValidationError

from .anchor import Arm
from .config import PRESETS_FILE, PRICES_FILE, Settings, read_presets, read_prices
from .core.errors import ConfigurationError, KgBuilderError
from .graph.connection import open_driver
from .llm.base import CallListener, Embedder
from .llm.cache import CachedLLM
from .llm.deepseek import DeepSeekClient
from .llm.gemini import GeminiClient
from .llm.ollama import OllamaClient
from .pipeline import PipelineContext, PipelineState, run_all, run_stages
from .pipeline import anchor_stages as ans
from .pipeline import audit_stages as aus
from .pipeline import claim_stages as cls
from .pipeline import judging_stages as jus
from .pipeline import mention_stages as mes
from .pipeline import qa_stages as qs
from .pipeline import retrieval_stages as rs
from .pipeline import stages as st
from .pipeline.qa_systems import DEFAULT_SYSTEMS, SYSTEMS
from .resolution.resolver import ResolvePreview
from .sampling import preset_samples, write_sample
from .structured.postgres import PostgresTables
from .tracking.mlflow_tracker import create_tracker
from .validation.qa import QAReport
from .validation.report import ValidationReport
from .validation.retrieval_scores import RetrievalReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
OUT = Path("out")
# optional everywhere: without it a command reads DATA_DIR, which the smoke and dev presets set to a subset
DATA_DIR = typer.Argument(None, help="Data directory; default: the data_dir setting (the preset's dataset).")
PRESET_NAMES = typer.Argument(None, help="Presets to rebuild; default: every one with a sample block.")
QA_SYSTEMS = typer.Option(
    list(DEFAULT_SYSTEMS), help=f"Systems to ask ({', '.join(SYSTEMS)}); each gets its own run."
)
# the retrieval benchmark (R117): the systems whose chunk sources it ranks, and the build loaded in the graph
RETRIEVAL_SYSTEMS = typer.Option(..., help="Systems whose chunk source to rank (vector, graph_retrieval).")
RETRIEVAL_BUILD = typer.Option(
    ..., help="The build folder loaded in the graph: the targets are placed on it."
)
FROZEN_PLANS = typer.Option(
    None, help="Folder of an earlier kg qa run whose plans and text2cypher queries are replayed (R80)."
)
# the graph audit (R87): the dataset the build ingested, its logged counts, and the gold pairs of reach
AUDIT_DATA = typer.Option(..., help="The dataset folder the build ingested.")
AUDIT_LOGGED = typer.Option(..., help="The build's logged counts (tests/gold/r87/<dataset>_logged.json).")
REACH_CLAIMS = typer.Option(None, help="R68 blind claims whose (thing, chunk) pairs test reach.")
REACH_SAMPLE = typer.Option(None, help="The R68 sentence sample those claims were written on.")
# the anchor-graph evaluation (R90): the target gold of the build's dataset and the arm it walks
FROM_BUILD = typer.Option(None, help="A finished build folder: replay its triples.jsonl, no LLM (R102).")
MENTION_GOLD = typer.Option(..., help="The R101 mention gold folder (tests/gold/r101).")
MENTION_VERDICTS = typer.Option(None, help="The judge's verdicts of the mention sheet (R102).")
MENTION_PASS_FILE = typer.Option(None, help="Pass findings to score in place of the build's own (R105).")
CLAIM_VERDICTS = typer.Option(None, help="The judge's verdicts of the claim sheet (R110).")
RECALL_VERDICTS = typer.Option(None, help="The judge's matching of the recall sheet (R111).")
# `kg claim-recall` (R111): a committed claim sheet and the R77 gold it is matched against
RECALL_CLAIMS = typer.Option(..., help="A claim sheet of kg claim-eval (tests/gold/r110/<dataset>/).")
RECALL_GOLD = typer.Option(..., help="The R77 gold of the dataset's sample (<dataset>_assertion_gold.json).")
RECALL_SAMPLE = typer.Option(..., help="That gold's sentence sample (<dataset>_assertion_sample.json).")
# `kg mention-pass --from-build` (R105): the pass on a finished build's graph, rebuilt offline
PASS_FROM_BUILD = typer.Option(None, help="A finished build folder: run the pass on its graph, offline.")
PASS_DATA = typer.Option(None, help="With --from-build: the dataset folder the build ingested.")
PASS_LOGGED = typer.Option(None, help="With --from-build: the build's logged counts (its C0 gate).")
SAMPLE_BUILD = typer.Option(None, help="A finished build folder: sample its corpus, not the graph (R101).")
SAMPLE_DATA = typer.Option(None, help="With --build: the dataset folder the build ingested.")
ANCHOR_TARGETS = typer.Option(..., help="The target gold of the build's dataset (tests/gold/r89/).")
ANCHOR_ARM = typer.Option(Arm.ANCHOR, help="anchor: anchor edges only; layered: also through the claims.")
ANCHOR_REPORT = typer.Option(..., help="The anchor arm's report of kg anchor-eval (anchor_anchor.json).")
LAYERED_REPORT = typer.Option(..., help="The layered arm's report of kg anchor-eval (anchor_layered.json).")
# the judged criteria (R93): the folder of a dataset's committed sheets and verdicts, and R75's identity gold
JUDGED_DIR = typer.Option(
    ..., help="The folder of the committed sheets, code sides and verdicts (tests/gold/r93/)."
)
IDENTITY_GOLD = typer.Option(..., help="R75's identity gold of the dataset (tests/gold/r75/).")


@app.callback()
def main(
    preset: str | None = typer.Option(
        None,
        help="Preset from presets.yaml: smoke ($0, tiny subset), dev (cents, subset), quality (full data).",
    ),
):
    """Build a knowledge graph from tables and documents. Options here apply to every command."""
    # The flag is one more way to set KG_PRESET: Settings reads it like any other variable, in its priority.
    if preset is not None:
        os.environ["KG_PRESET"] = preset


def run_tags(preset: str) -> dict[str, str]:
    """Tags put on every MLflow run, so a run can be traced back to the exact code and models that made it.

    `preset` is "none" when no preset was chosen. `git_sha` is left out when git or the repository is not
    available (an installed package, a zip download); a `-dirty` suffix marks runs made with uncommitted
    changes, and `git_dirty_files` names them (`git_tags`).
    """
    tags = {"code_version": version("kgbuilder"), "preset": preset or "none"}
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        tags |= git_tags(sha, status)
    except (OSError, subprocess.CalledProcessError) as e:
        logging.getLogger(__name__).info("no git_sha tag: %s", e)
    return tags


# tracked files a run names when the tree is dirty; more are summarised by their count, since an MLflow tag
# holds one short value and a reviewer needs the list only to see whether code was among them
_DIRTY_FILES_SHOWN = 20


def git_tags(sha: str, status: str) -> dict[str, str]:
    """The git tags of a run from `git rev-parse --short HEAD` and `git status --porcelain`: `git_sha`, with
    a `-dirty` suffix when a tracked file differs from the commit, and then `git_dirty_files`, the paths
    that differ (R91). The list lets a reviewer tell a run whose code no commit holds from one where only,
    say, a local settings file differed and the code is exactly `sha`."""
    # a porcelain line is "XY path" (or "XY old -> new" for a rename): the path starts at column 3
    paths = [line[3:].strip() for line in status.splitlines() if line.strip()]
    if not paths:
        return {"git_sha": sha}
    shown = paths[:_DIRTY_FILES_SHOWN]
    more = len(paths) - len(shown)
    return {
        "git_sha": f"{sha}-dirty",
        "git_dirty_files": ", ".join(shown) + (f" (+{more} more)" if more else ""),
    }


def gemini_key(settings: Settings) -> str:
    """The Gemini API key chosen by `gemini_key`; empty when the paid key is chosen but not set.

    Raises `ConfigurationError` when the free key is chosen but missing: falling back to the paid key would
    turn a run meant to be free into a billed one without anyone noticing.
    """
    if settings.gemini_key == "paid":
        return settings.gemini_api_key
    if not settings.gemini_free_api_key:
        raise ConfigurationError(
            "this preset uses the free Gemini key, but GEMINI_FREE_API_KEY is not set in .env "
            "(create a key in a Google AI Studio project without billing; see README)"
        )
    return settings.gemini_free_api_key


def build_provider(
    settings: Settings, listener: CallListener
) -> GeminiClient | OllamaClient | DeepSeekClient | None:
    """The LLM adapter named by `llm_provider`, or None when Gemini is chosen but has no API key.

    Raises `LLMUnavailableError` when DeepSeek is chosen without its key: unlike a missing Gemini key, it
    is never the default, so someone chose it and should hear that it cannot work.
    """
    if settings.llm_provider == "deepseek":
        return DeepSeekClient(
            settings.deepseek_api_key,
            settings.deepseek_url,
            settings.llm_max_attempts,
            listener=listener,
            timeout_s=settings.llm_timeout_s,
        )
    if settings.llm_provider == "ollama":
        return OllamaClient(
            settings.ollama_url,
            settings.embed_model,
            settings.ollama_num_ctx,
            settings.llm_max_attempts,
            listener=listener,
            timeout_s=settings.llm_timeout_s,
        )
    key = gemini_key(settings)
    if not key:
        return None
    return GeminiClient(
        key,
        settings.embed_model,
        settings.llm_max_attempts,
        listener=listener,
        timeout_s=settings.llm_timeout_s,
    )


def build_embedder(
    settings: Settings, listener: CallListener, provider: GeminiClient | OllamaClient | DeepSeekClient | None
) -> Embedder | None:
    """The embedder: the provider itself, except for DeepSeek, which has no embedding model: then Gemini, or
    None when it has no key (ingest then stores no vectors and resolution compares spelling only)."""
    if settings.llm_provider != "deepseek":
        return provider
    key = gemini_key(settings)
    if not key:
        return None
    return GeminiClient(
        key,
        settings.embed_model,
        settings.llm_max_attempts,
        listener=listener,
        timeout_s=settings.llm_timeout_s,
    )


def build_context(out: Path) -> PipelineContext:
    """Wire the concrete adapters. Without a provider the context has no LLM; LLM-free stages still work."""
    try:
        settings = Settings()
    except ValidationError as e:  # a bad value in .env, the environment or a preset
        raise ConfigurationError(str(e)) from e
    tracker = create_tracker(
        settings.mlflow_tracking_uri,
        settings.mlflow_experiment,
        run_tags(settings.kg_preset),
        read_prices(PRICES_FILE),
    )
    llm = embedder = None
    # both layers report to the tracker: the provider its live calls (with token usage), the cache its hits
    provider = build_provider(settings, tracker.record_llm_call)
    if provider is not None:
        llm = CachedLLM(provider, settings.cache_dir, tracker.record_llm_call)
        embedder = build_embedder(settings, tracker.record_llm_call, provider)
    # the driver connects lazily, so commands that never query Neo4j (profile, plan) work without it
    driver = open_driver(settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password)
    # a schema set means the tables come from PostgreSQL (R114); it connects only when profile stages them
    tables = None
    if settings.postgres_schema:
        tables = PostgresTables(settings.postgres_url, settings.postgres_schema)
    return PipelineContext(
        settings=settings, driver=driver, out=out, llm=llm, embedder=embedder, tracker=tracker, tables=tables
    )


@contextmanager
def session(out: Path) -> Iterator[PipelineContext]:
    """A context for one command: closes the driver, and reports expected failures without a traceback."""
    try:
        ctx = build_context(out)
    except KgBuilderError as e:  # the settings could not be loaded: nothing was opened yet
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    try:
        yield ctx
    except KgBuilderError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        ctx.driver.close()


def _data(ctx: PipelineContext, data_dir: Path | None) -> Path:
    """The directory given on the command line, else the `data_dir` setting (the preset's dataset)."""
    return data_dir or ctx.settings.data_dir


def _ask_reviewer(stage: str, path: Path) -> bool:
    """The approval pause of `kg run --review`: the human may edit the file before answering."""
    return typer.confirm(f"[{stage}] review {path} (you may edit it now). Continue?", default=True)


@app.command()
def profile(data_dir: Path | None = DATA_DIR, out: Path = OUT):
    """Stage JSON as CSV and profile all tables: types, uniqueness, foreign key candidates."""
    with session(out) as ctx:
        result = run_stages(ctx, PipelineState(data_dir=_data(ctx, data_dir)), [st.ProfileStage()]).profile
    for f in result.files:
        keys = [c.name + (" (primary key)" if c.primary_key else "") for c in f.columns if c.is_unique]
        typer.echo(f"{f.file}: {f.row_count} rows, unique columns: {keys}")
    for fk in result.foreign_keys:
        mark = ("" if fk.name_match else "  (name mismatch)") + ("  (declared)" if fk.declared else "")
        source, target = f"{fk.from_file}.{fk.from_column}", f"{fk.to_file}.{fk.to_column}"
        typer.echo(f"  {source} -> {target} [{fk.inclusion:.0%}]{mark}")
    typer.echo(f"Wrote {out / 'profile.json'}")


@app.command()
def plan(data_dir: Path | None = DATA_DIR, goal: str = typer.Option(...), out: Path = OUT):
    """Propose and critique a construction plan with the LLM. Review out/plan.json before `kg build`."""
    with session(out) as ctx:
        run_stages(
            ctx, PipelineState(data_dir=_data(ctx, data_dir), goal=goal), [st.ProfileStage(), st.PlanStage()]
        )
    typer.echo(f"Wrote {out / 'plan.json'}")


@app.command()
def build(data_dir: Path | None = DATA_DIR, out: Path = OUT):
    """Import out/plan.json (structured data) into Neo4j and print a reconciliation report."""
    with session(out) as ctx:
        run_stages(ctx, PipelineState(data_dir=_data(ctx, data_dir)), [st.ProfileStage(), st.BuildStage()])
    typer.echo((out / "build_report.json").read_text(encoding="utf-8"))


@app.command("ingest-text")
def ingest_text(data_dir: Path | None = DATA_DIR, out: Path = OUT, embed: bool = True):
    """Chunk md/txt/pdf documents and write the lexical graph."""
    with session(out) as ctx:
        chunks = run_stages(
            ctx, PipelineState(data_dir=_data(ctx, data_dir), embed=embed), [st.IngestTextStage()]
        ).chunks
    typer.echo(f"{len({c.doc_id for c in chunks})} documents, {len(chunks)} chunks")


@app.command("text-schema")
def text_schema(goal: str = typer.Option(...), out: Path = OUT):
    """Propose entity and fact types from the ingested chunks. Review out/text_schema.json next."""
    with session(out) as ctx:
        run_stages(ctx, PipelineState(goal=goal), [st.TextSchemaStage()])
    typer.echo(f"Wrote {out / 'text_schema.json'}")


@app.command()
def extract(out: Path = OUT, from_build: Path | None = FROM_BUILD):
    """Extract evidence-backed facts from the ingested chunks into the subject graph; with --from-build, write
    an earlier build's claims again instead, re-verified, without the LLM (R102)."""
    stage = st.ReplayExtractStage() if from_build is not None else st.ExtractStage()
    with session(out) as ctx:
        result = run_stages(ctx, PipelineState(extract_source=from_build), [stage]).extraction
    typer.echo(
        f"{len(result.triples)} facts stored, {len(result.rejected)} rejected (see {out / 'rejected.jsonl'})"
    )


@app.command()
def resolve(out: Path = OUT, undo: bool = False, preview: bool = False):
    """Decide what every mention refers to: a record, an individual or a concept (run after `kg link`).
    `--undo` removes the identity layer; `--preview` lists the concept pairs with their scores and changes
    nothing."""
    if undo and preview:
        raise typer.BadParameter("--undo and --preview exclude each other")
    with session(out) as ctx:
        if preview:
            _print_preview(run_stages(ctx, PipelineState(), [st.PreviewResolveStage()]).resolve_preview)
            typer.echo(f"Wrote {out / st.PreviewResolveStage.PREVIEW_FILE}")
            return
        if undo:
            run_stages(ctx, PipelineState(), [st.UndoResolveStage()])
            typer.echo("The identity layer of the last resolve run was removed.")
            return
        r = run_stages(ctx, PipelineState(), [st.ResolveStage()]).resolution
    typer.echo(
        f"mentions {r.mentions}: {r.count('record')} to records, {r.count('individual')} to individuals, "
        f"{r.count('concept')} to concepts ({r.concepts_before} -> {r.concepts_after}, {r.merges} merged)"
    )


@app.command()
def link(out: Path = OUT):
    """Link documents and sections to the domain graph and derive facts (before `kg resolve`)."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(), [st.LinkStage()]).links
    typer.echo(report.model_dump())


@app.command("mention-pass")
def mention_pass(
    out: Path = OUT,
    from_build: Path | None = PASS_FROM_BUILD,
    data: Path | None = PASS_DATA,
    logged: Path | None = PASS_LOGGED,
):
    """List what each chunk names or talks about that no claim names, verify it in code and write the new
    mentions (after `kg link`, before `kg resolve`; one LLM call per chunk; R101). With --from-build, run it
    on a finished build's graph rebuilt offline instead and write only the findings files (R105)."""
    if from_build is not None and (data is None or logged is None):
        raise typer.BadParameter("--from-build needs --data and --logged")
    stage = mes.ReplayMentionPassStage() if from_build is not None else st.MentionPassStage()
    with session(out) as ctx:
        run_stages(ctx, PipelineState(audit_source=from_build, data_dir=data, audit_logged=logged), [stage])
    typer.echo(f"Wrote {out / st.PASS_FILE} and {out / st.PASS_REJECTED_FILE}")


@app.command()
def attach(out: Path = OUT):
    """Attach every claim to the records and individuals it is about (after `kg resolve`)."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(), [st.AttachStage()]).attachment
    typer.echo(report.model_dump())


@app.command()
def validate(out: Path = OUT, gold: Path | None = None):
    """Validate structure, provenance, consistency and (with --gold) accuracy of the whole graph."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(gold=gold), [st.ValidateStage()]).validation
    _print_report(report)


@app.command("eval")
def evaluate(gold: Path, out: Path = OUT, verdicts: Path | None = None):
    """Score the graph against a gold file (format: validation/gold.py); with --verdicts, also against
    the judge's verdict file (validation/judge.py). Always writes the judge sheet under out/."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(gold=gold, verdicts=verdicts), [st.EvalStage()]).evaluation
    for name, value in report.metrics().items():
        typer.echo(f"{name:24} {value:.3f}")
    for q in report.questions:
        typer.echo(f"[{'PASS' if q.correct else 'FAIL'}] {q.question} -> {q.answered}")
    if report.judge_sheet is not None:
        sheet = report.judge_sheet
        er = f", {len(sheet.er.to_judge())} of {len(sheet.er.pairs)} ER pairs" if sheet.er else ""
        typer.echo(
            f"Wrote {out / st.EvalStage.SHEET_FILE}: {len(sheet.to_judge())} of {len(sheet.facts)} facts, "
            f"{len(sheet.gold_to_find())} of {len(sheet.gold)} gold triples{er} need a judge"
        )
    typer.echo(f"Wrote {out / 'eval_report.json'}")


@app.command()
def rescore(sheet: Path, gold: Path, out: Path = OUT, verdicts: Path | None = None):
    """Score the judge sheet an earlier eval run logged (mlruns/<exp>/<run>/artifacts/judge_sheet.json)
    with today's matching and gold, without the graph; with --verdicts, the judge's validated scores."""
    state = PipelineState(sheet=sheet, gold=gold, verdicts=verdicts)
    with session(out) as ctx:
        report = run_stages(ctx, state, [st.RescoreStage()]).evaluation
    for name, value in report.metrics().items():
        typer.echo(f"{name:24} {value:.3f}")
    sheet_out = report.judge_sheet
    typer.echo(
        f"Wrote {out / st.RescoreStage.SHEET_FILE}: {len(sheet_out.to_judge())} facts, "
        f"{len(sheet_out.gold_to_find())} gold triples, {len(sheet_out.er.to_judge())} ER pairs need a judge"
    )


@app.command("coverage-sample")
def coverage_sample(
    target: Path,
    size: int = typer.Option(40, min=1, help="Sentences to draw (R68 judges about 40 per dataset)."),
    # R68's number, fixed before any sentence was seen, so the seed cannot have been tuned to a result
    seed: int = typer.Option(68, help="Seed of the sample: the same seed draws the same sentences."),
    build: Path | None = SAMPLE_BUILD,
    data: Path | None = SAMPLE_DATA,
    out: Path = OUT,
):
    """Draw a fixed random sample of sentences from the graph's chunks (or, with --build, from a finished
    build's corpus rebuilt offline, R101) and write it to TARGET (R68)."""
    state = PipelineState(
        sample=target, sample_size=size, sample_seed=seed, audit_source=build, data_dir=data
    )
    with session(out) as ctx:
        run_stages(ctx, state, [st.CoverageSampleStage()])
    typer.echo(f"Wrote {target}")


@app.command("coverage-sheet")
def coverage_sheet(sample: Path, out: Path = OUT):
    """Find every sentence of SAMPLE in the current graph and write the coverage sheet for the judge: the
    observations of its chunk and the things and records the chunk hangs on (R68)."""
    with session(out) as ctx:
        run_stages(ctx, PipelineState(sample=sample), [st.CoverageSheetStage()])
    typer.echo(f"Wrote {out / st.CoverageSheetStage.SHEET_FILE}")


@app.command()
def coverage(sheet: Path, verdicts: Path, out: Path = OUT):
    """Score the judge's coverage verdicts against their sheet, without the graph (R68)."""
    state = PipelineState(coverage_sheet=sheet, verdicts=verdicts)
    with session(out) as ctx:
        report = run_stages(ctx, state, [st.CoverageStage()]).coverage
    for name, value in report.metrics().items():
        shown = "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
        typer.echo(f"{name:30} {shown}")
    typer.echo(f"Wrote {out / st.CoverageStage.REPORT_FILE}")


@app.command()
def assertion(sheet: Path, gold: Path, verdicts: Path, out: Path = OUT):
    """Score the judge's matching of the assertion GOLD against SHEET (a coverage sheet), without the graph:
    how well the graph keeps each claim's truth, modality and condition (R77)."""
    state = PipelineState(coverage_sheet=sheet, assertion_gold=gold, verdicts=verdicts)
    with session(out) as ctx:
        report = run_stages(ctx, state, [st.AssertionStage()]).assertion
    for name, value in report.metrics().items():
        shown = "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
        typer.echo(f"{name:30} {shown}")
    typer.echo(f"Wrote {out / st.AssertionStage.REPORT_FILE}")


@app.command("audit-snapshot")
def audit_snapshot(
    build: Path,
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    reach_claims: Path | None = REACH_CLAIMS,
    reach_sample: Path | None = REACH_SAMPLE,
    out: Path = OUT,
):
    """Rebuild BUILD's graph offline (no graph, no model), check it against the build's logged counts and
    judge sheet, and run the graph audit's code checks (R87 part a)."""
    if (reach_claims is None) != (reach_sample is None):
        typer.echo("error: pass both --reach-claims and --reach-sample, or neither", err=True)
        raise typer.Exit(1)
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        reach_gold=(reach_claims, reach_sample) if reach_claims and reach_sample else None,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [aus.AuditSnapshotStage()])
    typer.echo(f"fidelity passed: {state.fidelity.passed}")
    for name, value in state.audit.metrics().items():
        typer.echo(f"{name:44} {value:.3f}")
    typer.echo(f"Wrote {out / aus.SNAPSHOT_FILE}, {out / aus.FIDELITY_FILE}, {out / aus.CHECKS_FILE}")


@app.command("audit-relink")
def audit_relink(
    build: Path,
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    out: Path = OUT,
    choose: bool = typer.Option(False, "--choose", help="Ask the LLM to choose among near misses (paid)."),
    join: bool = typer.Option(False, "--join", help="Also decide the individuals again (LLM and embedder)."),
    faithful: bool = typer.Option(
        False, "--faithful", help="With --join: from the build's own inputs, which must reproduce it."
    ),
):
    """Replay BUILD's record matching under the current rules (no graph; no model unless --choose or --join)
    and write the build folder and logged counts it gives, refusing a replay that differs in anything but the
    change (R94). With --choose, the mentions with near misses are offered to the resolve model (R95b). With
    --join, the individuals are decided again from the replayed records; --join --faithful only checks that
    the build's own inputs give back its resolve.json (R98)."""
    if faithful and not join:
        raise typer.BadParameter("--faithful is a mode of --join")
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        relink_choose=choose,
        relink_join=join,
        relink_faithful=faithful,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [aus.AuditRelinkStage()])
    if faithful:
        decisions = len(state.reidentified.decisions)
        typer.echo(f"Faithful: {decisions} individual decisions and every assignment reproduced")
        typer.echo(f"Wrote {out / aus.REIDENTIFY_FILE}")
        return
    for c in state.relink.changes:
        typer.echo(
            f"{c.mention} {c.name!r} @{c.doc_id}: {c.before} -> {c.after} ({c.cause or 'unexplained'})"
        )
    typer.echo(f"{len(state.relink.changes)} changes of {state.relink.keyed} keyed mentions")
    for c in state.reidentified.changes if state.reidentified else []:
        typer.echo(f"{c.mention} {c.name!r} @{c.doc_id}: {c.before} -> {c.after} ({c.cause})")
    typer.echo(f"Wrote {out / aus.RELINKED_BUILD}, {out / aus.RELINKED_LOGGED}, {out / aus.RELINK_FILE}")


@app.command("mention-eval")
def mention_eval(
    build: Path,
    dataset: str = typer.Option(..., help="The dataset's name: picks its gold and sample."),
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    gold_dir: Path = MENTION_GOLD,
    verdicts: Path | None = MENTION_VERDICTS,
    pass_file: Path | None = MENTION_PASS_FILE,
    out: Path = OUT,
):
    """Rebuild BUILD's graph offline (no graph, no model) and score its mentions against the R101 gold:
    exact recall, the sheet the judge answers (near-name candidates, a seeded sample of the pass's mentions)
    and, with --verdicts, recall with the judged mapping and the pass's precision (R102). With --pass-file,
    those findings stand in for the build's own pass (R105)."""
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        anchor_dataset=dataset,
        mention_gold_dir=gold_dir,
        mention_verdicts=verdicts,
        mention_pass_file=pass_file,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [mes.MentionEvalStage()])
    s = state.mention_scores
    typer.echo(
        f"recall exact {s.recall_exact.k}/{s.recall_exact.n}; candidates {s.candidates}; misses {s.misses}"
    )
    if s.precision is not None:
        typer.echo(f"precision {s.precision.k}/{s.precision.n}")
    typer.echo(f"Wrote {out / mes.MENTION_SHEET} and {out / mes.MENTION_REPORT}")


@app.command("claim-eval")
def claim_eval(
    build: Path,
    dataset: str = typer.Option(..., help="The dataset's name, written into the sheet."),
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    verdicts: Path | None = CLAIM_VERDICTS,
    out: Path = OUT,
):
    """Rebuild BUILD's graph offline (no graph, no model) and write the sheet of its claims the judge answers;
    with --verdicts, score the judge's answers: strict and content precision per origin, faults (R110)."""
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        anchor_dataset=dataset,
        claim_verdicts=verdicts,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [cls.ClaimEvalStage()])
    if (s := state.claim_scores) is not None:
        for origin, o in s.by_origin.items():
            typer.echo(
                f"{origin}: precision {o.precision.k}/{o.precision.n}, "
                f"content {o.content_precision.k}/{o.content_precision.n}"
            )
        typer.echo(f"Wrote {out / cls.CLAIM_REPORT}")
    typer.echo(f"Wrote {out / cls.CLAIM_SHEET}")


@app.command("claim-recall")
def claim_recall(
    claims: Path = RECALL_CLAIMS,
    gold: Path = RECALL_GOLD,
    sample: Path = RECALL_SAMPLE,
    dataset: str = typer.Option(..., help="The dataset's name, written into the sheet."),
    verdicts: Path | None = RECALL_VERDICTS,
    out: Path = OUT,
):
    """Join a claim sheet with R77's reader claims into the sheet the judge matches (no graph, no model); with
    --verdicts, score recall: overall, on the random strata, within the schema, misses per cause (R111)."""
    state = PipelineState(
        claim_sheet=claims,
        assertion_gold=gold,
        sample=sample,
        anchor_dataset=dataset,
        recall_verdicts=verdicts,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [cls.ClaimRecallStage()])
    if (s := state.recall_scores) is not None:
        typer.echo(f"recall {s.recall.k}/{s.recall.n}; random strata {s.recall_random.k}/{s.recall_random.n}")
        typer.echo(f"Wrote {out / cls.RECALL_REPORT}")
    typer.echo(f"Wrote {out / cls.RECALL_SHEET}")


@app.command("anchor-eval")
def anchor_eval(
    build: Path,
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    targets: Path = ANCHOR_TARGETS,
    arm: Arm = ANCHOR_ARM,
    out: Path = OUT,
):
    """Rebuild BUILD's graph offline (no graph, no model) and compute the anchor-graph criteria the code
    decides alone in one ARM: fidelity, provenance, findability, evidence reach, selectivity, connectivity,
    size and cost (R90)."""
    state = PipelineState(audit_source=build, data_dir=data, audit_logged=logged, anchor_targets=targets)
    with session(out) as ctx:
        state = run_stages(ctx, state, [ans.AnchorEvalStage(arm)])
    for name, value in state.anchor.metrics().items():
        typer.echo(f"{name:44} {value:.3f}")
    typer.echo(f"Wrote {out / ans.report_file(arm)}")


@app.command("anchor-compare")
def anchor_compare(
    build: Path,
    data: Path = AUDIT_DATA,
    targets: Path = ANCHOR_TARGETS,
    anchor_report: Path = ANCHOR_REPORT,
    layered_report: Path = LAYERED_REPORT,
    out: Path = OUT,
):
    """Compute arm C (vector retrieval: embeds BUILD's chunks and the questions with the build's embedding
    model, a paid call of well under a cent) and pair the anchor, layered and vector arms question by
    question with McNemar's exact test (R92)."""
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        anchor_targets=targets,
        anchor_reports=(anchor_report, layered_report),
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [ans.AnchorCompareStage()])
    for name, value in state.anchor_comparison.metrics().items():
        typer.echo(f"{name:52} {value:.3f}")
    typer.echo(f"Wrote {out / ans.COMPARE_FILE}")


@app.command("anchor-sheets")
def anchor_sheets(
    build: Path,
    dataset: str = typer.Option(..., help="The dataset's name, written into the sheets."),
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    anchor_report: Path = ANCHOR_REPORT,
    layered_report: Path = LAYERED_REPORT,
    out: Path = OUT,
):
    """Rebuild BUILD's graph offline (no graph, no model) and write the blind judging sheets of C3
    (identity), C4 (record linking) and C6 (purity) with their code sides (R93)."""
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        anchor_reports=(anchor_report, layered_report),
        anchor_dataset=dataset,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [jus.AnchorSheetsStage()])
    for name, value in jus.sheet_counts(state.anchor_sheets).items():
        typer.echo(f"{name:44} {value:.0f}")
    typer.echo(f"Wrote the sheets and their code sides to {out}")


@app.command("anchor-judged")
def anchor_judged(
    build: Path,
    judged: Path = JUDGED_DIR,
    identity_gold: Path = IDENTITY_GOLD,
    data: Path = AUDIT_DATA,
    logged: Path = AUDIT_LOGGED,
    anchor_report: Path = ANCHOR_REPORT,
    out: Path = OUT,
):
    """Score the judged criteria C3 (identity), C4 (record linking) and C6 (purity) of BUILD from the judge's
    verdict files, on the reviewed and the blind labels (R93). No graph, no model."""
    state = PipelineState(
        audit_source=build,
        data_dir=data,
        audit_logged=logged,
        anchor_judged_dir=judged,
        identity_gold=identity_gold,
        anchor_placements=anchor_report,
    )
    with session(out) as ctx:
        state = run_stages(ctx, state, [jus.AnchorJudgedStage()])
    for name, value in state.anchor_judged.metrics().items():
        typer.echo(f"{name:44} {value:.3f}")
    typer.echo(f"Wrote {out / jus.JUDGED_FILE}")


@app.command()
def ask(
    question: str,
    system: str = typer.Option("graph", help=f"The system to ask: one of {', '.join(SYSTEMS)}."),
    out: Path = OUT,
):
    """Answer one question from the current graph, with the chunks it cites (R71)."""
    with session(out) as ctx:
        answer = run_stages(ctx, PipelineState(question=question), [qs.AskStage(system)]).answer
    for name, value in (("entities", answer.entities), ("number", answer.number), ("text", answer.text)):
        if value is not None:
            typer.echo(f"{name}: {value}")
    for citation in answer.citations:
        typer.echo(f'  [{citation.chunk_id}] "{citation.quote}"')
    typer.echo(f"Wrote {out / qs.AskStage.ANSWER_FILE}")


@app.command()
def qa(
    gold: Path,
    system: list[str] = QA_SYSTEMS,
    plans: Path | None = FROZEN_PLANS,
    out: Path = OUT,
):
    """Answer every question of a QA gold file with each system and log what code can score; free-text
    answers wait for the judge (`kg qa-score`). Writes out/answers_<system>.jsonl (R71). With --plans the
    plan systems replay that run's queries, so a changed graph is measured by its answers alone (R80)."""
    state = PipelineState(gold=gold, frozen_plans=plans)
    with session(out) as ctx:
        reports = run_stages(ctx, state, [qs.QAStage(s) for s in system]).qa_reports
    for name, report in reports.items():
        _print_qa(name, report)
        typer.echo(f"Wrote {out / f'answers_{name}.jsonl'}")


@app.command("qa-score")
def qa_score(gold: Path, answers: Path, verdicts: Path | None = None, out: Path = OUT):
    """Score an answers file of `kg qa` with the judge's verdicts on its free-text answers; no graph (R71)."""
    state = PipelineState(gold=gold, answers=answers, verdicts=verdicts)
    with session(out) as ctx:
        reports = run_stages(ctx, state, [qs.QAScoreStage()]).qa_reports
    for name, report in reports.items():
        _print_qa(name, report)


@app.command("qa-compare")
def qa_compare(a: Path, b: Path, out: Path = OUT):
    """Compare two outcome files of `kg qa-score` question by question: the questions only one system
    answered right and the exact McNemar p-value, overall and per type; no graph, no model (R73)."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(outcomes=(a, b)), [qs.QACompareStage()]).paired
    typer.echo(f"a = {report.a}\nb = {report.b}")
    rows = [("all", report.overall), *((t.value, c) for t, c in report.by_type.items())]
    for label, c in rows:
        mark = "  differs (p < 0.05)" if c.differs else ""
        typer.echo(
            f"{label:19} a {c.a_correct}/{c.questions}  b {c.b_correct}/{c.questions}  "
            f"only a {c.only_a}  only b {c.only_b}  p {c.p_value:.3f}{mark}"
        )
    typer.echo(f"Wrote {out / qs.QACompareStage.REPORT_FILE}")


@app.command("retrieve-eval")
def retrieve_eval(
    gold: Path,
    targets: Path = ANCHOR_TARGETS,
    build: Path = RETRIEVAL_BUILD,
    data: Path = AUDIT_DATA,
    system: list[str] = RETRIEVAL_SYSTEMS,
    out: Path = OUT,
):
    """Rank every question of a QA gold file with each system's chunk source, without the reader, and score
    the chunks against the gold's evidence and the start nodes against the target gold placed on BUILD, the
    build loaded in the graph (R117). Embeds each question; graph_retrieval also its node names once."""
    state = PipelineState(gold=gold, anchor_targets=targets, audit_source=build, data_dir=data)
    with session(out) as ctx:
        reports = run_stages(ctx, state, [rs.RetrieveEvalStage(s) for s in system]).retrieval
    for name, report in reports.items():
        _print_retrieval(name, report)
        typer.echo(f"Wrote {out / f'retrieval_{name}.json'}")


@app.command("retrieve-compare")
def retrieve_compare(a: Path, b: Path, out: Path = OUT):
    """Pair two reports of `kg retrieve-eval` question by question at every budget both have: complete
    evidence and found targets, with the exact McNemar p-value; no graph, no model (R117)."""
    with session(out) as ctx:
        comparisons = run_stages(
            ctx, PipelineState(retrieval_reports=(a, b)), [rs.RetrieveCompareStage()]
        ).retrieval_comparison
    for c in comparisons:
        for measure, report in (("complete", c.complete), ("seeds found", c.seed_found)):
            if report is not None:
                o = report.overall
                typer.echo(
                    f"{measure}@{c.k:<3} a {o.a_correct}/{o.questions}  b {o.b_correct}/{o.questions}  "
                    f"only a {o.only_a}  only b {o.only_b}  p {o.p_value:.3f}"
                )
    typer.echo(f"Wrote {out / rs.RetrieveCompareStage.REPORT_FILE}")


@app.command()
def run(
    data_dir: Path | None = DATA_DIR,
    goal: str = typer.Option(...),
    out: Path = OUT,
    gold: Path | None = None,
    embed: bool = True,
    review: bool = typer.Option(False, help="Pause after the plan and the text schema for human review."),
):
    """Whole pipeline: profile, plan, build, ingest, schema, extract, resolve, link, validate."""
    with session(out) as ctx:
        state = PipelineState(data_dir=_data(ctx, data_dir), goal=goal, gold=gold, embed=embed)
        run_all(ctx, state, approve=_ask_reviewer if review else None)
    _print_report(state.validation)


@app.command()
def sample(
    presets: list[str] = PRESET_NAMES,
):
    """Rebuild the small datasets of the cheap presets from the full data (no LLM, no Neo4j)."""
    try:
        for name, (spec, target) in preset_samples(read_presets(PRESETS_FILE), presets or []).items():
            report = write_sample(spec, target)
            typer.echo(f"{name}: {target}  rows {report.rows}  sections {report.sections}")
    except KgBuilderError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e


@app.command()
def reset(out: Path = OUT):
    """Delete everything in the Neo4j database (use before a clean rerun)."""
    with session(out) as ctx:
        ctx.driver.execute_query("MATCH (n) DETACH DELETE n")
    typer.echo("Database cleared.")


def _print_preview(preview: ResolvePreview) -> None:
    """One line per candidate pair: signal, score, route (auto merge or LLM), type and the two names."""
    for p in preview.pairs:
        typer.echo(f"{p.signal:9} {p.score:5.1f} {p.route:4} {p.type:12} {p.a} | {p.b}")
    typer.echo(f"{len(preview.pairs)} candidate pairs among {preview.entities} entities")


def _print_qa(system: str, report: QAReport) -> None:
    """One line per score of a system: overall with its interval, then per question type."""
    typer.echo(f"== {system}")
    rows = [("all", report.overall), *((t.value, s) for t, s in report.by_type.items())]
    for label, scores in rows:
        cells = [
            f"{name} {'-' if p.rate is None else f'{p.rate:.3f}'} ({p.k}/{p.n})"
            for name, p in (
                ("accuracy", scores.correct),
                ("recall@k", scores.recall_at_k),
                ("recall_all@k", scores.recall_all_at_k),
                ("faithful", scores.faithful),
            )
        ]
        unjudged = f"  unjudged {scores.unjudged}" if scores.unjudged else ""
        typer.echo(f"{label:19} " + "  ".join(cells) + unjudged)


def _print_retrieval(system: str, report: RetrievalReport) -> None:
    """One line per budget of a system's retrieval scores (overall), then its latency."""
    typer.echo(f"== {system}")
    o = report.overall
    for k in report.budgets:
        shares = [("evidence", o.evidence_recall), ("complete", o.complete)]
        shares += [("seed recall", o.seed_recall), ("seeds found", o.seed_found)] if report.seeded else []
        cells = [
            f"{name} {'-' if (p := by_k[k]).rate is None else f'{p.rate:.3f}'} ({p.k}/{p.n})"
            for name, by_k in shares
        ]
        typer.echo(f"@{k:<4} " + "  ".join(cells))
    typer.echo(f"latency p50 {report.latency_p50_ms} ms  p95 {report.latency_p95_ms} ms")


def _print_report(report: ValidationReport) -> None:
    """Print every check, then exit 1 when any failed so scripts and CI can react."""
    for c in report.checks:
        typer.echo(f"[{'PASS' if c.passed else 'FAIL'}] {c.category:11} {c.name}: {c.detail}")
    typer.echo(f"metrics: {report.metrics}")
    if not report.passed:
        raise typer.Exit(1)
