"""Command line interface and composition root: one Typer command per pipeline stage, plus `run`/`reset`.

Role in the pipeline: the entry point. It is the only place that reads settings and builds the concrete
LLM client, cache, MLflow tracker and Neo4j driver; everything below receives them through a
`PipelineContext`.
Design: composition root (Factory). Expected failures (`KgBuilderError`) become a message and exit code 1.
Not here: pipeline logic (pipeline.py) and anything that talks to the LLM or Neo4j directly.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer

from . import pipeline as pl
from .config import Settings
from .core.errors import KgBuilderError
from .graph.connection import open_driver
from .llm.cache import CachedLLM
from .llm.gemini import GeminiClient
from .tracking.mlflow_tracker import create_tracker
from .validate import ValidationReport

app = typer.Typer(no_args_is_help=True, add_completion=False)
OUT = Path("out")


def build_context(out: Path) -> pl.PipelineContext:
    """Wire the concrete adapters. Without an API key the context has no LLM; LLM-free stages still work."""
    settings = Settings()
    tracker = create_tracker(settings.mlflow_tracking_uri, settings.mlflow_experiment)
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
    return pl.PipelineContext(
        settings=settings, driver=driver, out=out, llm=llm, embedder=embedder, tracker=tracker
    )


@contextmanager
def session(out: Path) -> Iterator[pl.PipelineContext]:
    """A context for one command: closes the driver, and reports expected failures without a traceback."""
    ctx = build_context(out)
    try:
        yield ctx
    except KgBuilderError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        ctx.driver.close()


@app.command()
def profile(data_dir: Path, out: Path = OUT):
    """Stage JSON as CSV and profile all tables: types, uniqueness, foreign key candidates."""
    with session(out) as ctx:
        _, result = pl.stage_profile(ctx, data_dir)
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
        _, prof = pl.stage_profile(ctx, data_dir)
        pl.stage_plan(ctx, prof, goal)
    typer.echo(f"Wrote {out / 'plan.json'}")


@app.command()
def build(data_dir: Path, out: Path = OUT):
    """Import out/plan.json (structured data) into Neo4j and print a reconciliation report."""
    with session(out) as ctx:
        construction_plan = pl.require(pl.load_plan(out), "out/plan.json", "kg plan")
        staged, prof = pl.stage_profile(ctx, data_dir)
        pl.stage_build(ctx, staged, construction_plan, prof)
    typer.echo((out / "build_report.json").read_text(encoding="utf-8"))


@app.command("ingest-text")
def ingest_text(data_dir: Path, out: Path = OUT, embed: bool = True):
    """Chunk md/txt/pdf documents and write the lexical graph."""
    with session(out) as ctx:
        chunks = pl.stage_ingest_text(ctx, data_dir, embed)
    typer.echo(f"{len({c.doc_id for c in chunks})} documents, {len(chunks)} chunks")


@app.command("text-schema")
def text_schema(goal: str = typer.Option(...), out: Path = OUT):
    """Propose entity and fact types from the ingested chunks. Review out/text_schema.json next."""
    with session(out) as ctx:
        pl.stage_text_schema(ctx, goal, pl.stored_chunks(ctx), pl.load_plan(out))
    typer.echo(f"Wrote {out / 'text_schema.json'}")


@app.command()
def extract(out: Path = OUT):
    """Extract evidence-backed facts from the ingested chunks into the subject graph."""
    with session(out) as ctx:
        schema = pl.require(pl.load_text_schema(out), "out/text_schema.json", "kg text-schema")
        triples, rejected = pl.stage_extract(ctx, pl.stored_chunks(ctx), schema)
    typer.echo(f"{len(triples)} facts stored, {len(rejected)} rejected (see {out / 'rejected.jsonl'})")


@app.command()
def resolve(out: Path = OUT):
    """Detect and merge duplicate entities."""
    with session(out) as ctx:
        r = pl.stage_resolve(ctx)
    typer.echo(f"entities {r.entities_before} -> {r.entities_after} ({r.merges} merged)")


@app.command()
def link(out: Path = OUT):
    """Link documents and entities to the domain graph."""
    with session(out) as ctx:
        report = pl.stage_link(ctx, pl.require(pl.load_plan(out), "out/plan.json", "kg plan"))
    typer.echo(report.model_dump())


@app.command()
def validate(out: Path = OUT, gold: Path | None = None):
    """Validate structure, provenance, consistency and accuracy of the whole graph."""
    with session(out) as ctx:
        report = pl.stage_validate(ctx, pl.load_plan(out), pl.load_text_schema(out), None, gold)
    _print_report(report)


@app.command()
def run(
    data_dir: Path,
    goal: str = typer.Option(...),
    out: Path = OUT,
    gold: Path | None = None,
    embed: bool = True,
):
    """Whole pipeline: profile, plan, build, ingest, schema, extract, resolve, link, validate."""
    with session(out) as ctx:
        report = pl.run_all(ctx, data_dir, goal, gold, embed)
    _print_report(report)


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
