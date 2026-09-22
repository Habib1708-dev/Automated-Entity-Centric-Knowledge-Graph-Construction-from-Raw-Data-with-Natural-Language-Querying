"""Command line interface and composition root: one Typer command per pipeline stage, plus `run`/`reset`.

Role in the pipeline: the entry point. It is the only place that reads settings and builds the concrete
LLM client, cache, MLflow tracker and Neo4j driver; everything below receives them through a
`PipelineContext`.
Design: composition root (Factory). A command only chooses stages, fills `PipelineState` from its
arguments and prints the result. Expected failures (`KgBuilderError`) become a message and exit code 1.
Not here: pipeline logic (pipeline/) and anything that talks to the LLM or Neo4j directly.
"""

import logging
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import typer

from .config import Settings
from .core.errors import KgBuilderError
from .graph.connection import open_driver
from .llm.cache import CachedLLM
from .llm.gemini import GeminiClient
from .pipeline import PipelineContext, PipelineState, run_all, run_stages
from .pipeline import stages as st
from .tracking.mlflow_tracker import create_tracker
from .validation.report import ValidationReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
OUT = Path("out")


def run_tags() -> dict[str, str]:
    """Tags put on every MLflow run, so a run can be traced back to the exact code that produced it.

    `git_sha` is left out when git or the repository is not available (an installed package, a zip
    download); a `-dirty` suffix marks runs made with uncommitted changes, whose code no commit holds.
    """
    tags = {"code_version": version("kgbuilder")}
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


def build_context(out: Path) -> PipelineContext:
    """Wire the concrete adapters. Without an API key the context has no LLM; LLM-free stages still work."""
    settings = Settings()
    tracker = create_tracker(settings.mlflow_tracking_uri, settings.mlflow_experiment, run_tags())
    llm = embedder = None
    if settings.gemini_api_key:
        # both layers report to the tracker: Gemini its live calls (with token usage), the cache its hits
        gemini = GeminiClient(
            settings.gemini_api_key,
            settings.embed_model,
            settings.llm_max_attempts,
            listener=tracker.record_llm_call,
        )
        llm, embedder = CachedLLM(gemini, settings.cache_dir, tracker.record_llm_call), gemini
    # the driver connects lazily, so commands that never query Neo4j (profile, plan) work without it
    driver = open_driver(settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password)
    return PipelineContext(
        settings=settings, driver=driver, out=out, llm=llm, embedder=embedder, tracker=tracker
    )


@contextmanager
def session(out: Path) -> Iterator[PipelineContext]:
    """A context for one command: closes the driver, and reports expected failures without a traceback."""
    ctx = build_context(out)
    try:
        yield ctx
    except KgBuilderError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        ctx.driver.close()


def _ask_reviewer(stage: str, path: Path) -> bool:
    """The approval pause of `kg run --review`: the human may edit the file before answering."""
    return typer.confirm(f"[{stage}] review {path} (you may edit it now). Continue?", default=True)


@app.command()
def profile(data_dir: Path, out: Path = OUT):
    """Stage JSON as CSV and profile all tables: types, uniqueness, foreign key candidates."""
    with session(out) as ctx:
        result = run_stages(ctx, PipelineState(data_dir=data_dir), [st.ProfileStage()]).profile
    for f in result.files:
        keys = [c.name for c in f.columns if c.is_unique]
        typer.echo(f"{f.file}: {f.row_count} rows, unique columns: {keys}")
    for fk in result.foreign_keys:
        mark = "" if fk.name_match else "  (name mismatch)"
        source, target = f"{fk.from_file}.{fk.from_column}", f"{fk.to_file}.{fk.to_column}"
        typer.echo(f"  {source} -> {target} [{fk.inclusion:.0%}]{mark}")
    typer.echo(f"Wrote {out / 'profile.json'}")


@app.command()
def plan(data_dir: Path, goal: str = typer.Option(...), out: Path = OUT):
    """Propose and critique a construction plan with the LLM. Review out/plan.json before `kg build`."""
    with session(out) as ctx:
        run_stages(ctx, PipelineState(data_dir=data_dir, goal=goal), [st.ProfileStage(), st.PlanStage()])
    typer.echo(f"Wrote {out / 'plan.json'}")


@app.command()
def build(data_dir: Path, out: Path = OUT):
    """Import out/plan.json (structured data) into Neo4j and print a reconciliation report."""
    with session(out) as ctx:
        run_stages(ctx, PipelineState(data_dir=data_dir), [st.ProfileStage(), st.BuildStage()])
    typer.echo((out / "build_report.json").read_text(encoding="utf-8"))


@app.command("ingest-text")
def ingest_text(data_dir: Path, out: Path = OUT, embed: bool = True):
    """Chunk md/txt/pdf documents and write the lexical graph."""
    with session(out) as ctx:
        chunks = run_stages(ctx, PipelineState(data_dir=data_dir, embed=embed), [st.IngestTextStage()]).chunks
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
def resolve(out: Path = OUT, undo: bool = False):
    """Detect and merge duplicate entities. `--undo` reverts the last run (then re-run `kg link`)."""
    with session(out) as ctx:
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
def evaluate(gold: Path, out: Path = OUT):
    """Score the graph against a hand-labelled gold file (format: see validation/evaluate.py)."""
    with session(out) as ctx:
        report = run_stages(ctx, PipelineState(gold=gold), [st.EvalStage()]).evaluation
    for name, value in report.metrics().items():
        typer.echo(f"{name:20} {value:.3f}")
    for q in report.questions:
        typer.echo(f"[{'PASS' if q.correct else 'FAIL'}] {q.question} -> {q.answered}")
    typer.echo(f"Wrote {out / 'eval_report.json'}")


@app.command()
def run(
    data_dir: Path,
    goal: str = typer.Option(...),
    out: Path = OUT,
    gold: Path | None = None,
    embed: bool = True,
    review: bool = typer.Option(False, help="Pause after the plan and the text schema for human review."),
):
    """Whole pipeline: profile, plan, build, ingest, schema, extract, resolve, link, validate."""
    state = PipelineState(data_dir=data_dir, goal=goal, gold=gold, embed=embed)
    with session(out) as ctx:
        run_all(ctx, state, approve=_ask_reviewer if review else None)
    _print_report(state.validation)


@app.command()
def reset(out: Path = OUT):
    """Delete everything in the Neo4j database (use before a clean rerun)."""
    with session(out) as ctx:
        ctx.driver.execute_query("MATCH (n) DETACH DELETE n")
    typer.echo("Database cleared.")


def _print_report(report: ValidationReport) -> None:
    """Print every check, then exit 1 when any failed so scripts and CI can react."""
    for c in report.checks:
        typer.echo(f"[{'PASS' if c.passed else 'FAIL'}] {c.category:11} {c.name}: {c.detail}")
    typer.echo(f"metrics: {report.metrics}")
    if not report.passed:
        raise typer.Exit(1)
