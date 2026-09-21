"""End-to-end run against Neo4j with a scripted LLM (every stage except the model's judgement), plus
unit tests for evidence verification, text-schema validation, chunking and JSON staging."""

import json

import pytest

from kgbuilder import pipeline
from kgbuilder.config import Settings
from kgbuilder.extract import ChunkExtraction, RawTriple, verify
from kgbuilder.llm.refine import Critique
from kgbuilder.resolve import SamePair
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.schema import EntityType, FactType, TextSchema, validate_text_schema

from .fakes import RecordingTracker, ScriptedLLM
from .sample_plans import GOOD_PLAN

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Product", description="a product"),
        EntityType(name="Problem", description="a defect"),
    ],
    fact_types=[
        FactType(
            predicate="HAS_PROBLEM", subject_type="Product", object_type="Problem", description="has defect"
        )
    ],
)

# Three reviews separated by rules, so the chunker yields one chunk per review.
REVIEWS = (
    "# Table Reviews\n\n---\n\n## Rating 2/5\nThe Table wobbles badly because the legs are loose. "
    "Very disappointing purchase overall.\n\n"
    "---\n\n## Rating 3/5\nMy Table has a scratched surface after one week of normal use and light "
    "cleaning, not great.\n\n"
    "---\n\n## Rating 1/5\nThe Tables wobble as well when placed on an uneven floor, which the seller "
    "never mentioned anywhere.\n"
)


def triple(subject: str, obj: str, evidence: str) -> RawTriple:
    return RawTriple(
        subject=subject,
        subject_type="Product",
        predicate="HAS_PROBLEM",
        object=obj,
        object_type="Problem",
        evidence=evidence,
    )


def script(prompt, schema):
    """Canned replies per requested schema; extraction replies depend on which review is in the prompt."""
    if schema is ConstructionPlan:
        return GOOD_PLAN
    if schema is Critique:
        return Critique(verdict="valid", issues=[])
    if schema is TextSchema:
        return SCHEMA
    if schema is SamePair:
        return SamePair(same=False)
    assert schema is ChunkExtraction
    if "wobbles badly" in prompt:
        return ChunkExtraction(
            triples=[
                triple("Table", "wobbles", "The Table wobbles badly"),
                # hallucinated quote: must be rejected
                triple("Table", "cracked leg", "the leg cracked in half"),
            ]
        )
    if "scratched surface" in prompt:
        return ChunkExtraction(triples=[triple("Table", "scratched surface", "a scratched surface")])
    return ChunkExtraction(triples=[triple("Tables", "wobble", "The Tables wobble")])


@pytest.mark.neo4j
def test_full_pipeline(driver, data_dir, tmp_path):
    (data_dir / "dirty.csv").unlink()
    (data_dir / "table_reviews.md").write_text(REVIEWS, encoding="utf-8")
    (data_dir / "extra.json").write_text(
        json.dumps({"items": [{"id": 1, "meta": {"a": 2}}]}), encoding="utf-8"
    )
    gold = tmp_path / "gold.json"
    gold.write_text(
        json.dumps([{"subject": "Table", "predicate": "HAS_PROBLEM", "object": "wobbles"}]), encoding="utf-8"
    )
    out = tmp_path / "out"
    tracker = RecordingTracker()
    ctx = pipeline.PipelineContext(
        settings=Settings(), driver=driver, out=out, llm=ScriptedLLM(script), tracker=tracker
    )

    report = pipeline.run_all(ctx, data_dir, "find product problems", gold=gold, embed=False)

    failed = [c for c in report.checks if not c.passed]
    assert not failed, failed
    rejected = [json.loads(line) for line in (out / "rejected.jsonl").read_text().splitlines() if line]
    assert len(rejected) == 1 and "verbatim" in rejected[0]["reason"]
    # "Table" and "Tables" were merged by entity resolution
    names = [r["n"] for r in driver.execute_query("MATCH (e:Entity {type:'Product'}) RETURN e.name AS n")[0]]
    assert len(names) == 1

    def count(query: str) -> int:
        return driver.execute_query(query)[0][0]["c"]

    # document is linked to the domain product, entity refers to it too
    assert count("MATCH (:Document)-[:ABOUT]->(:Product {product_id:'P1'}) RETURN count(*) AS c") == 1
    assert count("MATCH (:Entity)-[:REFERS_TO]->(:Product) RETURN count(*) AS c") == 1
    assert report.metrics["evidence_verified_rate"] == 1.0
    assert report.metrics["gold_recall"] == 1.0

    # tracking contract (mlflow-tracking skill): one run per stage, with the params that explain the result
    assert [r.name for r in tracker.runs] == [
        "pipeline", "profile", "plan", "build_domain", "ingest_text",
        "text_schema", "extract", "resolve", "link", "validate",
    ]  # fmt: skip
    assert {"model", "temperature", "prompt_version", "critic_prompt_version"} <= set(
        tracker.run("plan").logged_params
    )
    assert {"chunk_max_chars", "chunk_min_chars"} <= set(tracker.run("ingest_text").logged_params)
    assert {"er_auto_merge", "er_borderline"} <= set(tracker.run("resolve").logged_params)
    assert {"accept_rate", "triples_per_chunk", "rejected"} <= set(tracker.run("extract").logged_metrics)
    assert "prompts/extract.txt" in tracker.run("extract").artifacts


def test_verify_rejects_ungrounded_and_off_schema():
    text = "The Table wobbles badly."
    ok = triple("Table", "wobbles", "The Table wobbles")
    assert verify(ok, text, SCHEMA) is None
    assert "verbatim" in verify(ok.model_copy(update={"evidence": "made up"}), text, SCHEMA)
    assert "schema" in verify(ok.model_copy(update={"predicate": "LOVES"}), text, SCHEMA)
    assert "does not appear" in verify(ok.model_copy(update={"object": "cracks"}), text, SCHEMA)


def test_text_schema_validation():
    bad = TextSchema(
        entity_types=[EntityType(name="product", description="x")],
        fact_types=[FactType(predicate="has", subject_type="Product", object_type="Nope", description="x")],
    )
    assert len(validate_text_schema(bad)) >= 3
    assert validate_text_schema(SCHEMA) == []
