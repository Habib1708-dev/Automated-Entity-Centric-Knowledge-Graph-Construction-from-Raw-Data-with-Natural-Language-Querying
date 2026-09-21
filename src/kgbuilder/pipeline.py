"""Stage functions and the end-to-end run. Each stage is one MLflow run and writes its artifacts to `out/`."""

import json
from pathlib import Path

from . import llm
from .extract import Rejected, Triple, extract_all, write_subject_graph
from .importer import construct_domain_graph, get_driver
from .ingest import Document, load_documents, stage_structured
from .lexical import Chunk, chunk_document, write_lexical_graph
from .link import link_graphs
from .plan import ConstructionPlan, validate_plan
from .profiler import DataProfile, profile_directory
from .resolve import resolve_entities
from .schema import propose_plan
from .textschema import TextSchema, propose_text_schema
from .tracking import track
from .validate import ValidationReport, validate_graph


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def stage_profile(data_dir: Path, out: Path) -> tuple[Path, DataProfile]:
    staged = stage_structured(data_dir, out / "staging")
    with track("profile", data_dir=data_dir) as run:
        profile = profile_directory(staged)
        run.metrics(files=len(profile.files), foreign_key_candidates=len(profile.foreign_keys), rows=sum(f.row_count for f in profile.files))
        run.artifact(_write(out / "profile.json", profile.model_dump_json(indent=2)))
    return staged, profile


def stage_plan(profile: DataProfile, goal: str, out: Path) -> ConstructionPlan:
    with track("plan", goal=goal, model=llm.settings.schema_model) as run:
        result = propose_plan(goal, profile)
        run.metrics(rounds=result.rounds, accepted=int(result.accepted), open_issues=len(result.open_issues))
        run.artifact(_write(out / "plan.json", result.plan.model_dump_json(indent=2)))
    if not result.accepted:
        raise RuntimeError("plan not accepted: " + "; ".join(result.open_issues))
    return result.plan


def stage_build(staged: Path, plan: ConstructionPlan, profile: DataProfile, out: Path) -> dict[str, int]:
    issues = validate_plan(plan, profile)
    if issues:
        raise RuntimeError("invalid plan: " + "; ".join(issues))
    with track("build_domain") as run:
        report = construct_domain_graph(staged, plan)
        run.metrics(
            rules=len(report.rules),
            rows_written=sum(r.rows_written for r in report.rules),
            rows_dropped=sum(r.rows_unmatched + r.rows_skipped_null_key for r in report.rules),
            clean=int(report.clean),
        )
        run.artifact(_write(out / "build_report.json", report.model_dump_json(indent=2)))
    return {n.label: profile.file(n.source_file).row_count for n in plan.nodes}


def stage_ingest_text(data_dir: Path, out: Path, embed: bool = True) -> tuple[list[Document], list[Chunk]]:
    with track("ingest_text", data_dir=data_dir) as run:
        docs = load_documents(data_dir)
        chunks = [c for d in docs for c in chunk_document(d)]
        embeddings = None
        if embed and chunks and llm.available():
            embeddings = dict(zip((c.chunk_id for c in chunks), llm.embed([c.text for c in chunks])))
        driver = get_driver()
        try:
            write_lexical_graph(driver, docs, chunks, embeddings)
        finally:
            driver.close()
        run.metrics(documents=len(docs), chunks=len(chunks), embedded=int(bool(embeddings)))
    return docs, chunks


def stage_text_schema(goal: str, chunks: list[Chunk], plan: ConstructionPlan | None, out: Path) -> TextSchema:
    with track("text_schema", goal=goal) as run:
        schema, rounds, issues = propose_text_schema(goal, chunks, plan)
        run.metrics(rounds=rounds, open_issues=len(issues), entity_types=len(schema.entity_types), fact_types=len(schema.fact_types))
        run.artifact(_write(out / "text_schema.json", schema.model_dump_json(indent=2)))
    if issues:
        raise RuntimeError("text schema not accepted: " + "; ".join(issues))
    return schema


def stage_extract(chunks: list[Chunk], schema: TextSchema, out: Path) -> tuple[list[Triple], list[Rejected]]:
    with track("extract", model=llm.settings.extract_model, chunks=len(chunks)) as run:
        triples, rejected = extract_all(chunks, schema)
        driver = get_driver()
        try:
            counts = write_subject_graph(driver, triples)
        finally:
            driver.close()
        total = len(triples) + len(rejected)
        run.metrics(**counts, rejected=len(rejected), accept_rate=len(triples) / total if total else 1.0)
        run.artifact(_write(out / "triples.jsonl", "\n".join(t.model_dump_json() for t in triples)))
        run.artifact(_write(out / "rejected.jsonl", "\n".join(r.model_dump_json() for r in rejected)))
    return triples, rejected


def stage_resolve(out: Path) -> dict:
    with track("resolve") as run:
        driver = get_driver()
        try:
            report = resolve_entities(driver)
        finally:
            driver.close()
        run.metrics(before=report.entities_before, after=report.entities_after, merges=report.merges, self_loops_removed=report.self_loops_removed)
        run.artifact(_write(out / "resolve.json", report.model_dump_json(indent=2)))
    return report.model_dump()


def stage_link(plan: ConstructionPlan) -> dict:
    with track("link") as run:
        driver = get_driver()
        try:
            report = link_graphs(driver, plan)
        finally:
            driver.close()
        run.metrics(**report.model_dump())
    return report.model_dump()


def stage_validate(
    plan: ConstructionPlan | None, schema: TextSchema | None, expected: dict[str, int] | None, out: Path, gold: Path | None = None
) -> ValidationReport:
    with track("validate") as run:
        driver = get_driver()
        try:
            report = validate_graph(driver, plan, schema, expected, gold)
        finally:
            driver.close()
        run.metrics(**report.metrics, checks_passed=sum(c.passed for c in report.checks), checks_total=len(report.checks))
        run.artifact(_write(out / "validation.json", report.model_dump_json(indent=2)))
    return report


def load_plan(out: Path) -> ConstructionPlan:
    return ConstructionPlan.model_validate_json((out / "plan.json").read_text(encoding="utf-8"))


def load_text_schema(out: Path) -> TextSchema:
    return TextSchema.model_validate_json((out / "text_schema.json").read_text(encoding="utf-8"))


def run_all(data_dir: Path, goal: str, out: Path, gold: Path | None = None, embed: bool = True) -> ValidationReport:
    with track("pipeline", data_dir=data_dir, goal=goal):
        staged, profile = stage_profile(data_dir, out)
        plan = stage_plan(profile, goal, out) if profile.files else None
        expected = stage_build(staged, plan, profile, out) if plan else None
        docs, chunks = stage_ingest_text(data_dir, out, embed)
        schema = None
        if chunks:
            schema = stage_text_schema(goal, chunks, plan, out)
            stage_extract(chunks, schema, out)
            stage_resolve(out)
        if plan:
            stage_link(plan)
        return stage_validate(plan, schema, expected, out, gold)
