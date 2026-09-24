"""Path truth (R63) on hand-made facts, without Neo4j: a claim reached through a shared part is true only
for the things whose own documents state it; resolution's merges decide what "the same claim" is, not the
wording each review kept; kind structure between two parts is left out; the metric needs the schema's
derived types; it reaches `kg eval`'s metrics and `kg rescore`, whose MLflow run logs the schema's hash.
The stage test needs Neo4j only for the context."""

from kgbuilder.config import Settings
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.validation.checks.base import StoredFact
from kgbuilder.validation.evaluate import EvalReport
from kgbuilder.validation.gold import GoldSet
from kgbuilder.validation.paths import score_paths
from kgbuilder.validation.rescore import rescore

from .fakes import RecordingTracker
from .test_rescore import LOGGED

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Product", description="a product"),
        EntityType(name="Component", description="a part"),
        EntityType(name="Defect", description="a flaw"),
    ],
    fact_types=[
        FactType(
            predicate="PART_OF", subject_type="Component", object_type="Product", description="part",
            derived=True,
        ),
        FactType(predicate="HAS_DEFECT", subject_type="Component", object_type="Defect", description="flaw"),
        FactType(predicate="PART_OF", subject_type="Component", object_type="Component", description="sub"),
    ],
)  # fmt: skip


def part_of(part: str, product: str, doc: str) -> StoredFact:
    """The derived fact: the part is named in a document about the product."""
    return StoredFact(
        predicate="PART_OF", subject_type="Component", object_type="Product", chunk_id=f"{doc}#0",
        evidence="quote", subject_names=[part], object_names=[product],
    )  # fmt: skip


def defect(part: str, flaw: str, doc: str, own_wording: str | None = None, aliases: tuple = ()) -> StoredFact:
    return StoredFact(
        predicate="HAS_DEFECT", subject_type="Component", object_type="Defect", chunk_id=f"{doc}#1",
        evidence="quote", subject_names=[part], object_names=[flaw, *aliases], object_name=own_wording,
    )  # fmt: skip


# the R62 case in miniature: both products name drawer rails, only the dresser's review calls them rough
DRESSER, BED = "Helsingborg Dresser", "Linkoping Bed"
LEAK = [
    part_of("drawer rails", DRESSER, "dresser.md"),
    part_of("drawer rails", BED, "bed.md"),
    defect("drawer rails", "rough", "dresser.md"),
]


def test_a_claim_reached_through_a_shared_part_is_false_for_the_other_product():
    report = score_paths(LEAK, SCHEMA)
    assert (report.paths_total, report.paths_true) == (2, 1)
    assert report.truth == 0.5
    [leak] = report.false_paths
    assert leak.thing == BED
    assert leak.via == "drawer rails"
    assert leak.claim == "drawer rails -[HAS_DEFECT]-> rough"
    assert leak.stated_in == ["dresser.md"]


def test_the_same_claim_in_the_other_products_own_document_is_true():
    report = score_paths([*LEAK, defect("drawer rails", "rough", "bed.md")], SCHEMA)
    assert (report.paths_total, report.paths_true) == (2, 2)
    assert report.false_paths == []


def test_the_same_claim_is_the_same_merged_entity_whatever_the_review_called_it():
    # resolution merged "rough edges" into "rough": the bed review's own wording differs, the node does not
    merged = defect("drawer rails", "rough", "bed.md", own_wording="rough edges", aliases=("rough edges",))
    report = score_paths([*LEAK, merged], SCHEMA)
    assert report.paths_true == 2


def test_claims_about_the_product_itself_are_not_paths():
    # only claims reached through a part count: a product's own defect needs no shared node to reach it
    own = StoredFact(
        predicate="HAS_DEFECT", subject_type="Product", object_type="Defect", chunk_id="bed.md#2",
        evidence="quote", subject_names=[BED], object_names=["wobbles"],
    )  # fmt: skip
    assert score_paths([*LEAK, own], SCHEMA).paths_total == 2


def test_kind_structure_between_two_parts_is_not_a_path():
    # how a kind is built ("drawer pulls PART_OF drawer rails") is shared on purpose, not a claim on the bed
    structure = StoredFact(
        predicate="PART_OF", subject_type="Component", object_type="Component", chunk_id="dresser.md#3",
        evidence="quote", subject_names=["drawer pulls"], object_names=["drawer rails"],
    )  # fmt: skip
    assert score_paths([*LEAK, structure], SCHEMA).paths_total == 2


def test_without_derived_fact_types_there_are_no_paths():
    schema = SCHEMA.model_copy(update={"fact_types": [f for f in SCHEMA.fact_types if not f.derived]})
    report = score_paths(LEAK, schema)
    assert report.paths_total == 0
    assert report.truth == 1.0  # nothing was asserted through a shared part


def test_path_truth_is_an_eval_metric():
    metrics = EvalReport(paths=score_paths(LEAK, SCHEMA)).metrics()
    assert metrics["path_truth"] == 0.5
    assert (metrics["paths_true"], metrics["paths_total"]) == (1, 2)


def test_rescore_adds_path_truth_only_with_the_schema():
    # the logged sheet's facts are both veneer defects; without a derived fact there is no path to judge
    assert rescore(LOGGED, GoldSet()).paths is None
    assert rescore(LOGGED, GoldSet(), schema=SCHEMA).paths.paths_total == 0


def test_rescore_stage_logs_path_truth_and_the_schema_it_used(driver, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / TEXT_SCHEMA_FILE).write_text(SCHEMA.model_dump_json(), encoding="utf-8")
    sheet_file = tmp_path / "judge_sheet.json"
    sheet_file.write_text(LOGGED.model_dump_json(), encoding="utf-8")
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(GoldSet().model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker)

    run_stages(ctx, PipelineState(gold=gold_file, sheet=sheet_file), [st.RescoreStage()])
    run = tracker.run("rescore")
    assert run.logged_params["text_schema_hash"]
    assert run.logged_metrics["path_truth"] == 1.0 and run.logged_metrics["paths_total"] == 0
