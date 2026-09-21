from pathlib import Path

import typer

from . import pipeline as pl
from .importer import get_driver
from .lexical import chunk_document
from .ingest import load_documents
from .plan import ConstructionPlan, validate_plan
from .profiler import profile_directory

app = typer.Typer(no_args_is_help=True, add_completion=False)
OUT = Path("out")


@app.command()
def profile(data_dir: Path, out: Path = OUT):
    """Stage JSON as CSV and profile all tables: types, uniqueness, foreign key candidates."""
    _, result = pl.stage_profile(data_dir, out)
    for f in result.files:
        keys = [c.name for c in f.columns if c.is_unique]
        typer.echo(f"{f.file}: {f.row_count} rows, unique columns: {keys}")
    for fk in result.foreign_keys:
        mark = "" if fk.name_match else "  (name mismatch)"
        typer.echo(f"  {fk.from_file}.{fk.from_column} -> {fk.to_file}.{fk.to_column} [{fk.inclusion:.0%}]{mark}")
    typer.echo(f"Wrote {out / 'profile.json'}")


@app.command()
def plan(data_dir: Path, goal: str = typer.Option(...), out: Path = OUT):
    """Propose and critique a construction plan with the LLM. Review out/plan.json before `kg build`."""
    _, prof = pl.stage_profile(data_dir, out)
    try:
        pl.stage_plan(prof, goal, out)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(f"Wrote {out / 'plan.json'}")


@app.command()
def build(data_dir: Path, out: Path = OUT):
    """Import out/plan.json (structured data) into Neo4j and print a reconciliation report."""
    staged = out / "staging"
    if not staged.exists():
        staged, _ = pl.stage_profile(data_dir, out)
    prof = profile_directory(staged)
    construction_plan = pl.load_plan(out)
    for issue in validate_plan(construction_plan, prof):
        typer.echo(f"invalid plan: {issue}")
    pl.stage_build(staged, construction_plan, prof, out)
    typer.echo((out / "build_report.json").read_text(encoding="utf-8"))


@app.command("ingest-text")
def ingest_text(data_dir: Path, out: Path = OUT, embed: bool = True):
    """Chunk md/txt/pdf documents and write the lexical graph."""
    docs, chunks = pl.stage_ingest_text(data_dir, out, embed)
    typer.echo(f"{len(docs)} documents, {len(chunks)} chunks")


@app.command("text-schema")
def text_schema(data_dir: Path, goal: str = typer.Option(...), out: Path = OUT):
    """Propose entity and fact types for the text. Review out/text_schema.json before `kg extract`."""
    chunks = [c for d in load_documents(data_dir) for c in chunk_document(d)]
    plan_file = out / "plan.json"
    construction_plan = pl.load_plan(out) if plan_file.exists() else None
    pl.stage_text_schema(goal, chunks, construction_plan, out)
    typer.echo(f"Wrote {out / 'text_schema.json'}")


@app.command()
def extract(data_dir: Path, out: Path = OUT):
    """Extract evidence-backed facts from the chunks into the subject graph."""
    chunks = [c for d in load_documents(data_dir) for c in chunk_document(d)]
    triples, rejected = pl.stage_extract(chunks, pl.load_text_schema(out), out)
    typer.echo(f"{len(triples)} facts stored, {len(rejected)} rejected (see {out / 'rejected.jsonl'})")


@app.command()
def resolve(out: Path = OUT):
    """Detect and merge duplicate entities."""
    r = pl.stage_resolve(out)
    typer.echo(f"entities {r['entities_before']} -> {r['entities_after']} ({r['merges']} merged)")


@app.command()
def link(out: Path = OUT):
    """Link documents and entities to the domain graph."""
    typer.echo(pl.stage_link(pl.load_plan(out)))


@app.command()
def validate(out: Path = OUT, gold: Path | None = None):
    """Validate structure, provenance, consistency and accuracy of the whole graph."""
    plan_file, schema_file = out / "plan.json", out / "text_schema.json"
    report = pl.stage_validate(
        pl.load_plan(out) if plan_file.exists() else None,
        pl.load_text_schema(out) if schema_file.exists() else None,
        None, out, gold,
    )
    _print_report(report)
    if not report.passed:
        raise typer.Exit(1)


@app.command()
def run(data_dir: Path, goal: str = typer.Option(...), out: Path = OUT, gold: Path | None = None, embed: bool = True):
    """Whole pipeline: profile, plan, build, ingest, schema, extract, resolve, link, validate."""
    report = pl.run_all(data_dir, goal, out, gold, embed)
    _print_report(report)
    if not report.passed:
        raise typer.Exit(1)


@app.command()
def reset():
    """Delete everything in the Neo4j database (use before a clean rerun)."""
    driver = get_driver()
    try:
        driver.execute_query("MATCH (n) DETACH DELETE n")
    finally:
        driver.close()
    typer.echo("Database cleared.")


def _print_report(report):
    for c in report.checks:
        typer.echo(f"[{'PASS' if c.passed else 'FAIL'}] {c.category:11} {c.name}: {c.detail}")
    typer.echo(f"metrics: {report.metrics}")
