"""End-to-end run against Neo4j with a scripted LLM (every stage except the model's judgement), plus
unit tests for evidence verification, text-schema validation, chunking and JSON staging."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import InvalidPlanError
from kgbuilder.llm.refine import Critique
from kgbuilder.pipeline import PipelineContext, PipelineState, run_all
from kgbuilder.pipeline.runner import ReviewDeclinedError
from kgbuilder.resolution.resolver import SamePair
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.extraction import ChunkExtraction, RawTriple, RejectionReason, verify
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
    # small chunks so that each review is its own chunk; 90 so that "Table"/"Tables" (90.9) auto-merge
    settings = Settings(chunk_min_chars=50, er_auto_merge=90)
    ctx = PipelineContext(settings=settings, driver=driver, out=out, llm=ScriptedLLM(script), tracker=tracker)

    state = PipelineState(data_dir=data_dir, goal="find product problems", gold=gold, embed=False)
    report = run_all(ctx, state).validation

    failed = [c for c in report.checks if not c.passed]
    assert not failed, failed
    rejected = [json.loads(line) for line in (out / "rejected.jsonl").read_text().splitlines() if line]
    assert [r["reason"] for r in rejected] == ["evidence_not_verbatim"]
    # "Table" and "Tables" were merged by entity resolution
    names = [r["n"] for r in driver.execute_query("MATCH (e:Entity {type:'Product'}) RETURN e.name AS n")[0]]
    assert len(names) == 1
    assert tracker.run("ingest_text").logged_metrics["chunks"] == 3
    assert tracker.run("resolve").logged_metrics["merges"] >= 1

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
    extract_metrics = tracker.run("extract").logged_metrics
    assert {"accept_rate", "triples_per_chunk", "rejected"} <= set(extract_metrics)
    assert (
        extract_metrics["rejected_evidence_not_verbatim"] == 1 and extract_metrics["rejected_off_schema"] == 0
    )
    assert "prompts/extract.txt" in tracker.run("extract").artifacts


def test_verify_rejects_ungrounded_and_off_schema():
    text = "The **Table** wobbles   badly."
    ok = triple("table", "wobbles", "The Table wobbles")  # case, markdown and spacing are normalised
    assert verify(ok, text, SCHEMA) is None

    def reason(**changes) -> RejectionReason:
        return verify(ok.model_copy(update=changes), text, SCHEMA).reason

    assert reason(evidence="made up") == RejectionReason.EVIDENCE_NOT_VERBATIM
    assert reason(evidence="  ") == RejectionReason.EVIDENCE_NOT_VERBATIM
    assert reason(predicate="LOVES") == RejectionReason.OFF_SCHEMA
    assert reason(object="cracks") == RejectionReason.ARGUMENT_NOT_IN_CHUNK
    assert reason(object="") == RejectionReason.EMPTY_ARGUMENT
    assert reason(object="TABLE") == RejectionReason.SELF_REFERENCE


def test_text_schema_validation():
    bad = TextSchema(
        entity_types=[EntityType(name="product", description="x")],
        fact_types=[FactType(predicate="has", subject_type="Product", object_type="Nope", description="x")],
    )
    assert len(validate_text_schema(bad)) >= 3
    assert validate_text_schema(SCHEMA) == []


def llm_free_context(driver, out) -> PipelineContext:
    return PipelineContext(settings=Settings(), driver=driver, out=out, llm=ScriptedLLM(script))


@pytest.mark.neo4j
def test_review_pause_can_stop_the_run_and_a_hand_edit_wins(driver, data_dir, tmp_path):
    (data_dir / "dirty.csv").unlink()
    state = PipelineState(data_dir=data_dir, goal="g", embed=False)
    with pytest.raises(ReviewDeclinedError, match="plan"):
        run_all(llm_free_context(driver, tmp_path / "declined"), state, approve=lambda stage, path: False)

    def drop_suppliers(stage, path):
        """The reviewer removes the Supplier node but forgets its relationship: an invalid plan."""
        plan = ConstructionPlan.model_validate_json(path.read_text(encoding="utf-8"))
        plan.nodes = [n for n in plan.nodes if n.label != "Supplier"]
        path.write_text(plan.model_dump_json(), encoding="utf-8")
        return True

    state = PipelineState(data_dir=data_dir, goal="g", embed=False)
    with pytest.raises(InvalidPlanError, match="Supplier"):
        run_all(llm_free_context(driver, tmp_path / "edited"), state, approve=drop_suppliers)
    assert state.plan.node("Supplier") is None  # the edited file replaced the LLM's plan


@pytest.mark.neo4j
def test_an_empty_data_dir_skips_every_stage_that_has_no_input(driver, tmp_path):
    (tmp_path / "data").mkdir()
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)
    state = run_all(ctx, PipelineState(data_dir=tmp_path / "data", goal="g"))
    assert [r.name for r in tracker.runs] == ["pipeline", "profile", "ingest_text", "validate"]
    assert state.validation.passed
