"""End-to-end run against Neo4j with a scripted LLM (every stage except the model's judgement), plus
unit tests for evidence verification, text-schema validation, chunking and JSON staging."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import InvalidPlanError
from kgbuilder.llm.refine import Critique
from kgbuilder.pipeline import PipelineContext, PipelineState, run_all, run_stages
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import ReviewDeclinedError
from kgbuilder.resolution.concepts import SamePair
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.extraction import (
    GLEAN_SUFFIX,
    ChunkExtraction,
    ExtractionResult,
    RawTriple,
    Rejected,
    RejectionReason,
    Triple,
    build_prompt,
    extract_chunk,
    verify,
)
from kgbuilder.text.mention_pass import FoundThings
from kgbuilder.text.schema import EntityType, FactType, TextSchema, validate_text_schema

from .evaluation_corpora import quoted_four_grams
from .fakes import RecordingTracker, ScriptedLLM
from .sample_plans import GOOD_PLAN

SCHEMA = TextSchema(
    entity_types=[
        # named like the plan's label, so keyed to it (R75: the schema check refuses it otherwise)
        EntityType(name="Product", description="a product", identity="keyed", record_labels=["Product"]),
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
    if schema is FoundThings:  # the mention pass (R101): nothing the claims do not already name
        return FoundThings()
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
    # "Table" and "Tables" both refer to the one product record (R75: the type is keyed to Product), and the
    # problem kinds "wobbles" and "wobble" were joined by entity resolution
    products = "MATCH (:Mention {type: 'Product'})-[:REFERS_TO]->(p:Product) RETURN count(DISTINCT p) AS n"
    assert driver.execute_query(products)[0][0]["n"] == 1
    assert tracker.run("ingest_text").logged_metrics["chunks"] == 3
    assert tracker.run("resolve").logged_metrics["merges"] >= 1
    assert tracker.run("resolve").logged_metrics["mentions_to_records"] == 2
    assert tracker.run("resolve").logged_metrics["passes"] >= 1

    def count(query: str) -> int:
        return driver.execute_query(query)[0][0]["c"]

    # the document is linked to the domain product, and both of its product mentions refer to it
    assert count("MATCH (:Document)-[:ABOUT]->(:Product {product_id:'P1'}) RETURN count(*) AS c") == 1
    assert count("MATCH (:Mention)-[:REFERS_TO]->(:Product) RETURN count(*) AS c") == 2
    assert report.metrics["evidence_verified_rate"] == 1.0
    assert report.metrics["gold_recall"] == 1.0

    # tracking contract (mlflow-tracking skill): one run per stage, with the params that explain the result
    assert [r.name for r in tracker.runs] == [
        "pipeline", "profile", "plan", "build_domain", "ingest_text",
        "text_schema", "extract", "link", "mention_pass", "resolve", "attach", "validate",
    ]  # fmt: skip
    assert {"model", "temperature", "prompt_version", "critic_prompt_version"} <= set(
        tracker.run("plan").logged_params
    )
    assert {"chunk_max_chars", "chunk_min_chars"} <= set(tracker.run("ingest_text").logged_params)
    assert "schema_context_chars" in tracker.run("text_schema").logged_params
    assert (
        tracker.run("text_schema").logged_metrics["context_chunks"]
        == tracker.run("text_schema").logged_metrics["chunks_total"]
        == 3
    )
    assert {"er_auto_merge", "er_borderline"} <= set(tracker.run("resolve").logged_params)
    assert tracker.run("link").logged_metrics["facts_derived"] == 0  # the scripted schema derives nothing
    # every claim hangs on the product (R76): the attach run logs its edges per route
    attach_metrics = tracker.run("attach").logged_metrics
    assert attach_metrics["observations_attached"] == attach_metrics["observations_total"] > 0
    assert {f"attached_{how}" for how in ("key_in_sentence", "part_of", "section", "document")} <= set(
        attach_metrics
    )
    extract_metrics = tracker.run("extract").logged_metrics
    assert {"accept_rate", "triples_per_chunk", "rejected"} <= set(extract_metrics)
    assert extract_metrics["rejected_off_schema_rate"] == 0.0
    assert "facts_pass1" in extract_metrics and "facts_pass2" not in extract_metrics  # one pass by default
    assert tracker.run("extract").logged_params["passes"] == 1
    assert json.loads((out / "off_schema.json").read_text()) == {}
    assert report.metrics["predicates_distinct"] == 1  # the scripted schema has one fact type
    assert (
        extract_metrics["rejected_evidence_not_verbatim"] == 1 and extract_metrics["rejected_off_schema"] == 0
    )
    # the claims' qualifiers (R66): the scripted triples carry no tone, number or time
    assert extract_metrics["observations_neutral"] == extract_metrics["facts"]
    assert extract_metrics["observations_with_value"] == extract_metrics["observations_with_time"] == 0
    assert extract_metrics["rejected_time_not_in_evidence"] == 0
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


def test_a_second_pass_shows_the_found_facts_and_adds_only_new_verified_ones():
    chunk = Chunk(
        chunk_id="r.md#0",
        doc_id="r.md",
        index=0,
        text="The table wobbles. The table cracks.",
        context="Reviews",
    )
    wobbles = triple("table", "wobbles", "The table wobbles.")
    cracks = triple("table", "cracks", "The table cracks.")
    invented = triple("table", "cracks", "The table was dropped.")  # evidence not in the chunk
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        if "<already_extracted>" not in prompt:
            return ChunkExtraction(triples=[wobbles])
        return ChunkExtraction(triples=[wobbles.model_copy(update={"subject": "TABLE"}), cracks, invented])

    result = extract_chunk(chunk, SCHEMA, ScriptedLLM(script), model="m", passes=2)
    assert [(t.object, t.evidence) for t in result.triples] == [
        ("wobbles", "The table wobbles."),
        ("cracks", "The table cracks."),
    ]  # the repeat of pass 1 (other casing) is dropped, the ungrounded fact rejected
    assert result.accepted_per_pass == [1, 1]
    assert [r.reason for r in result.rejected] == [RejectionReason.EVIDENCE_NOT_VERBATIM]
    assert "- table -[HAS_PROBLEM]-> wobbles" in prompts[1] and prompts[1].startswith(prompts[0])

    single = ScriptedLLM(script)
    assert extract_chunk(chunk, SCHEMA, single, model="m").accepted_per_pass == [1]
    assert len(single.calls) == 1


def test_the_second_pass_rules_speak_no_corpus_language():
    rule = GLEAN_SUFFIX.lower()
    assert "return an empty list" in rule and "do not repeat" in rule
    assert "generally known" in rule and "comparison" in rule  # R62: the over-reach rules
    assert not any(w in rule for w in ("defect", "failure", "complaint", "product", "drawer", "vehicle"))
    # no four consecutive words of any evaluation corpus (the R34 rule, extended to pass 2)
    assert not quoted_four_grams(GLEAN_SUFFIX.split("</already_extracted>")[1])


def test_a_name_that_is_only_a_pronoun_is_rejected():
    text = "It works less every day. This one broke. The table wobbles."
    assert verify(triple("It", "wobbles", "The table wobbles."), text, SCHEMA).reason == (
        RejectionReason.PRONOUN_ARGUMENT
    )
    assert verify(triple("table", "this one", "This one broke."), text, SCHEMA).reason == (
        RejectionReason.PRONOUN_ARGUMENT
    )
    # a real name that contains a pronoun-like word is not touched
    assert verify(triple("table", "wobbles", "The table wobbles."), text, SCHEMA) is None


def test_off_schema_rejections_are_counted_per_missing_fact_type():
    ok = Triple(**triple("table", "wobbles", "x").model_dump(), chunk_id="d#0")

    def off(predicate: str) -> Rejected:
        t = ok.model_copy(update={"predicate": predicate})
        return Rejected(triple=t, reason=RejectionReason.OFF_SCHEMA, detail="")

    grounded = Rejected(triple=ok, reason=RejectionReason.EVIDENCE_NOT_VERBATIM, detail="")
    result = ExtractionResult(triples=[ok], rejected=[off("LOVES"), off("HATES"), off("LOVES"), grounded])
    # 3 of 5 returned triples had no fact type; the grounding rejection is not a schema gap
    assert result.off_schema_rate == 0.6
    assert result.off_schema_signatures() == {
        "Product -[LOVES]-> Problem": 2,
        "Product -[HATES]-> Problem": 1,
    }
    assert ExtractionResult(triples=[], rejected=[]).off_schema_rate == 0.0


def test_verify_accepts_a_name_from_the_document_context_but_never_a_quote_from_it():
    text = "It wobbles badly."
    named = triple("Gothenburg Table", "wobbles", "It wobbles")
    assert verify(named, text, SCHEMA).reason == RejectionReason.ARGUMENT_NOT_IN_CHUNK
    assert verify(named, text, SCHEMA, context="Gothenburg Table Reviews") is None
    # the document name is metadata, not a statement: it cannot serve as evidence
    quoted = triple("Gothenburg Table", "wobbles", "Gothenburg Table Reviews")
    assert verify(quoted, text, SCHEMA, context="Gothenburg Table Reviews").reason == (
        RejectionReason.EVIDENCE_NOT_VERBATIM
    )


def test_derived_fact_types_are_hidden_from_the_extractor_and_rejected_if_it_returns_them():
    derived = SCHEMA.model_copy(
        update={
            "fact_types": SCHEMA.fact_types
            + [
                FactType(
                    predicate="PART_OF",
                    subject_type="Problem",
                    object_type="Product",
                    description="d",
                    derived=True,
                )
            ]
        }
    )
    chunk = Chunk(chunk_id="r.md#0", doc_id="r.md", index=0, text="The table wobbles.")
    assert "PART_OF" not in build_prompt(chunk, derived) and "HAS_PROBLEM" in build_prompt(chunk, derived)
    part_of = RawTriple(
        subject="wobbles", subject_type="Problem", predicate="PART_OF", object="table", object_type="Product",
        evidence="The table wobbles.",
    )  # fmt: skip
    rejection = verify(part_of, chunk.text, derived)
    assert rejection.reason == RejectionReason.OFF_SCHEMA and "derived" in rejection.detail
    assert derived.allows("Problem", "PART_OF", "Product")  # a stored derived fact still conforms


def test_extraction_prompt_asks_for_every_claim_in_domain_neutral_words():
    # R30: the deterministic part of the change is the wording; its effect is measured by a judge pass.
    # R34: the rule must not speak the corpus's language, or it steers extraction on any other dataset.
    chunk = Chunk(chunk_id="r.md#1", doc_id="r.md", index=1, text="It wobbles.", context="Malmo Desk Reviews")
    rule = build_prompt(chunk, SCHEMA).split("- Be exhaustive:")[1].split("\n- ")[0]
    assert "one triple per distinct claim" in rule and "hedged claim" in rule
    assert not any(word in rule.lower() for word in ("defect", "failure", "complaint", "assembly", "product"))


def test_extraction_prompt_keeps_circumstances_out_of_entity_names():
    # R47: "the drawer sometimes sticks when i open it too fast" gave the failure the name "sticks when i
    # open it too fast", which no resolver can match with "stick". The rule is about grammar (a clause
    # saying when or under which condition), so it holds for any dataset.
    chunk = Chunk(chunk_id="r.md#1", doc_id="r.md", index=1, text="It wobbles.", context="Malmo Desk Reviews")
    rules = build_prompt(chunk, SCHEMA).split("Rules:")[1].split("<document>")[0]
    naming = next(rule for rule in rules.split("\n- ") if "exactly as written" in rule)
    assert "only the words that name the thing" in naming and "stay in the evidence" in naming
    assert not any(
        word in naming.lower()
        for word in ("defect", "failure", "complaint", "assembly", "product", "drawer", "stick", "open")
    )


def test_no_extraction_rule_quotes_the_evaluation_corpus():
    """A rule that borrows the gold documents' wording steers the model toward them (found in R34): no four
    consecutive words of the rules may occur in an evaluation corpus (evaluation_corpora.py)."""
    chunk = Chunk(chunk_id="r.md#1", doc_id="r.md", index=1, text="x", context="x")
    # up to the <document> block, not the first "<document>": a rule names the tag (R66 found the test
    # stopped there, so every rule after it went unchecked)
    rules_text = build_prompt(chunk, SCHEMA).split("Rules:")[1].split("\n\n<document>")[0]
    assert "Return an empty list when nothing qualifies" in rules_text  # the last rule is in view
    quoted = quoted_four_grams(rules_text)
    assert not quoted, f"prompt rules quote the corpus: {quoted}"


def test_extraction_prompt_shows_the_document_context_above_the_chunk():
    chunk = Chunk(chunk_id="r.md#1", doc_id="r.md", index=1, text="It wobbles.", context="Malmo Desk Reviews")
    prompt = build_prompt(chunk, SCHEMA)
    assert "<document>Malmo Desk Reviews</document>" in prompt
    assert prompt.index("<document>") < prompt.index('<chunk id="r.md#1">')


def test_text_schema_validation():
    bad = TextSchema(
        entity_types=[EntityType(name="product", description="x")],
        fact_types=[FactType(predicate="has", subject_type="Product", object_type="Nope", description="x")],
    )
    assert len(validate_text_schema(bad)) >= 3
    assert validate_text_schema(SCHEMA, GOOD_PLAN) == []  # its keyed type names the plan's label


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


def test_the_plan_stage_sends_and_logs_the_schema_thinking_level(data_dir, tmp_path):
    (data_dir / "dirty.csv").unlink()
    llm, tracker = ScriptedLLM(script), RecordingTracker()
    settings = Settings(_env_file=None, schema_thinking="medium", extract_thinking="low")
    # profile and plan never query Neo4j, so no driver is needed
    ctx = PipelineContext(settings=settings, driver=None, out=tmp_path / "out", llm=llm, tracker=tracker)
    run_stages(ctx, PipelineState(data_dir=data_dir, goal="g"), [st.ProfileStage(), st.PlanStage()])
    assert llm.thinking and set(llm.thinking) == {"medium"}  # proposer and critic alike
    assert tracker.run("plan").logged_params["thinking"] == "medium"
