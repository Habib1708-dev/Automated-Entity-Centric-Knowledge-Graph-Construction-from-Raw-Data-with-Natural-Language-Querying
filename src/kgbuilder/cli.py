"""Command line interface and composition root: one Typer command per pipeline stage, plus `run`/`reset`.

Role in the pipeline: the entry point. It is the only place that reads settings and builds the concrete
LLM client, cache, MLflow tracker and Neo4j driver; everything below receives them through a
`PipelineContext`.
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

from .config import PRESETS_FILE, PRICES_FILE, Settings, read_presets, read_prices
from .core.errors import ConfigurationError, KgBuilderError
from .graph.connection import open_driver
from .llm.base import CallListener, Embedder
from .llm.cache import CachedLLM
from .llm.deepseek import DeepSeekClient
from .llm.gemini import GeminiClient
from .llm.ollama import OllamaClient
from .pipeline import PipelineContext, PipelineState, run_all, run_stages
from .pipeline import qa_stages as qs
from .pipeline import stages as st
from .resolution.resolver import ResolvePreview
from .sampling import preset_samples, write_sample
from .tracking.mlflow_tracker import create_tracker
from .validation.qa import QAReport
from .validation.report import ValidationReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
OUT = Path("out")
# optional everywhere: without it a command reads DATA_DIR, which the smoke and dev presets set to a subset
DATA_DIR = typer.Argument(None, help="Data directory; default: the data_dir setting (the preset's dataset).")
PRESET_NAMES = typer.Argument(None, help="Presets to rebuild; default: every one with a sample block.")
QA_SYSTEMS = typer.Option(
    list(qs.SYSTEMS), help="Systems to ask (graph, vector, records_vector); each gets its own run."
)


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
    changes, whose code no commit holds.
    """
    tags = {"code_version": version("kgbuilder"), "preset": preset or "none"}
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tags["git_sha"] = f"{sha}-dirty" if dirty else sha
    except (OSError, subprocess.CalledProcessError) as e:
        logging.getLogger(__name__).info("no git_sha tag: %s", e)
    return tags


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
    return PipelineContext(
        settings=settings, driver=driver, out=out, llm=llm, embedder=embedder, tracker=tracker
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
        keys = [c.name for c in f.columns if c.is_unique]
        typer.echo(f"{f.file}: {f.row_count} rows, unique columns: {keys}")
    for fk in result.foreign_keys:
        mark = "" if fk.name_match else "  (name mismatch)"
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
def extract(out: Path = OUT):
    """Extract evidence-backed facts from the ingested chunks into the subject graph."""
    with session(out) as ctx:
        result = run_stages(ctx, PipelineState(), [st.ExtractStage()]).extraction
    typer.echo(
        f"{len(result.triples)} facts stored, {len(result.rejected)} rejected (see {out / 'rejected.jsonl'})"
    )


@app.command()
def resolve(out: Path = OUT, undo: bool = False, preview: bool = False):
    """Detect and merge duplicate entities. `--undo` reverts the last run (then re-run `kg link`);
    `--preview` lists the candidate pairs with their scores and changes nothing."""
    if undo and preview:
        raise typer.BadParameter("--undo and --preview exclude each other")
    with session(out) as ctx:
        if preview:
            _print_preview(run_stages(ctx, PipelineState(), [st.PreviewResolveStage()]).resolve_preview)
            typer.echo(f"Wrote {out / st.PreviewResolveStage.PREVIEW_FILE}")
            return
        if undo:
            run_stages(ctx, PipelineState(), [st.UndoResolveStage()])
            typer.echo("Merges of the last resolve run were undone.")
            return
        r = run_stages(ctx, PipelineState(), [st.ResolveStage()]).resolution
    typer.echo(f"entities {r.entities_before} -> {r.entities_after} ({r.merges} merged)")


@app.command()
def link(out: Path = OUT):
    """Link documents and entities to the domain graph."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(), [st.LinkStage()]).links
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
    out: Path = OUT,
):
    """Draw a fixed random sample of sentences from the graph's chunks and write it to TARGET (R68)."""
    state = PipelineState(sample=target, sample_size=size, sample_seed=seed)
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
def ask(
    question: str,
    system: str = typer.Option("graph", help="graph (the retrieval route) or vector (the baseline)."),
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
def qa(gold: Path, system: list[str] = QA_SYSTEMS, out: Path = OUT):
    """Answer every question of a QA gold file with each system and log what code can score; free-text
    answers wait for the judge (`kg qa-score`). Writes out/answers_<system>.jsonl (R71)."""
    with session(out) as ctx:
        reports = run_stages(ctx, PipelineState(gold=gold), [qs.QAStage(s) for s in system]).qa_reports
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


def _print_report(report: ValidationReport) -> None:
    """Print every check, then exit 1 when any failed so scripts and CI can react."""
    for c in report.checks:
        typer.echo(f"[{'PASS' if c.passed else 'FAIL'}] {c.category:11} {c.name}: {c.detail}")
    typer.echo(f"metrics: {report.metrics}")
    if not report.passed:
        raise typer.Exit(1)
