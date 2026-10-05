"""Path truth (R63) on hand-made facts, without Neo4j: a claim reached through a shared part is true only
for the things whose own documents state it; resolution's merges decide what "the same claim" is, not the
wording each review kept; kind structure between two parts is left out; the metric needs the schema's
derived types; it reaches `kg eval`'s metrics and `kg rescore`, whose MLflow run logs the schema's hash.
In the observation graph (R64) a path is thing -> observation, true when the observation's document is
about the thing, and a logged sheet carries what `kg rescore` needs to say so.
The stage test needs Neo4j only for the context."""

from kgbuilder.config import Settings
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.validation.checks.base import Attached, StoredFact
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


def attached(fact: StoredFact, things: list[str], about: list[str]) -> StoredFact:
    """The fact as an observation: hung on `things`, from a document about `about`."""
    return fact.model_copy(update={"things": things, "about": about})


def test_in_the_observation_graph_a_claim_reaches_only_the_thing_its_document_is_about():
    # the R62 leak case, stored as observations: the bed still has the drawer rails, not their defect
    graph = [
        attached(part_of("drawer rails", DRESSER, "dresser.md"), [DRESSER], [DRESSER]),
        attached(part_of("drawer rails", BED, "bed.md"), [BED], [BED]),
        attached(defect("drawer rails", "rough", "dresser.md"), [DRESSER], [DRESSER]),
    ]
    report = score_paths(graph, SCHEMA)
    assert (report.paths_total, report.paths_true) == (1, 1)  # derived observations are not paths


def test_an_observation_hung_on_a_thing_its_document_is_not_about_is_a_false_path():
    stale = attached(defect("drawer rails", "rough", "dresser.md"), [DRESSER, BED], [DRESSER])
    report = score_paths([stale], SCHEMA)
    assert (report.paths_total, report.paths_true) == (2, 1)
    [false] = report.false_paths
    assert (false.thing, false.via, false.stated_in) == (BED, "dresser.md#1", ["dresser.md"])
    assert false.claim == "drawer rails -[HAS_DEFECT]-> rough"


def test_rescore_reads_the_things_of_an_observation_graphs_sheet():
    desk_fact, shelf_fact = LOGGED.facts
    sheet = LOGGED.model_copy(
        update={
            "facts": [
                desk_fact.model_copy(update={"things": ["Desk"], "about": ["Desk"]}),
                # hung on the desk although its document is about the shelf
                shelf_fact.model_copy(update={"things": ["Desk"], "about": ["Shelf"]}),
            ]
        }
    )
    report = rescore(sheet, GoldSet(), schema=SCHEMA)
    assert (report.paths.paths_total, report.paths.paths_true) == (2, 1)
    assert [f.things for f in report.judge_sheet.facts] == [["Desk"], ["Desk"]]


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


# The attached graph (R76): each attachment is true when the evidence of its route holds.
PART_OF_SCHEMA = SCHEMA.model_copy(
    update={"fact_types": [f.model_copy(update={"part_of": f.derived}) for f in SCHEMA.fact_types]}
)


def hung(fact: StoredFact, *attachments: tuple[str, str, str], about=(), sections=()) -> StoredFact:
    """The fact with its attachments as (thing, route, evidence)."""
    return fact.model_copy(
        update={
            "attachments": [Attached(thing=t, how=h, evidence=e) for t, h, e in attachments],
            "things": [t for t, _, _ in attachments],
            "about": list(about),
            "sections": list(sections),
        }
    )


def test_in_the_attached_graph_a_path_is_true_when_its_routes_evidence_holds():
    rough = defect("drawer rails", "rough", "dresser.md").model_copy(
        update={"evidence": "The drawer rails of the Helsingborg Dresser feel rough."}
    )
    graph = [
        part_of("drawer rails", DRESSER, "dresser.md"),  # the part-of claim the part_of route rests on
        # one claim of a document about the dresser, read once per attachment
        hung(rough, (DRESSER, "key_in_sentence", "Helsingborg Dresser"), about=[DRESSER]),
        hung(rough, (DRESSER, "part_of", "quote"), about=[DRESSER]),
        hung(rough, ("11440801", "section", "dresser.md#1"), about=[DRESSER], sections=["11440801"]),
        hung(rough, (DRESSER, "document", "dresser.md"), about=[DRESSER]),
    ]
    report = score_paths(graph, PART_OF_SCHEMA)
    assert (report.paths_total, report.paths_true, report.false_paths) == (4, 4, [])
    # the observation graph's rule holds only where the document is about the thing
    assert report.paths_true_about == 3 and report.truth_about == 0.75


def test_an_attachment_whose_evidence_no_longer_holds_is_a_false_path():
    rough = defect("drawer rails", "rough", "dresser.md")  # its quote names nothing
    stale = [
        hung(rough, (DRESSER, "key_in_sentence", "Helsingborg Dresser")),  # the name is not in the quote
        hung(rough, (BED, "part_of", "quote")),  # no part-of claim of this document makes the rails the bed's
        hung(rough, ("11440801", "section", "dresser.md#1")),  # the chunk is ABOUT nothing (any more)
        hung(rough, (BED, "document", "dresser.md"), about=[DRESSER]),  # a stale document link
        hung(rough, (DRESSER, "by_magic", "?")),  # a route this reader does not know
    ]
    report = score_paths([part_of("drawer rails", DRESSER, "dresser.md"), *stale], PART_OF_SCHEMA)
    assert (report.paths_total, report.paths_true) == (5, 0)
    assert sorted(p.how for p in report.false_paths) == [
        "by_magic", "document", "key_in_sentence", "part_of", "section",
    ]  # fmt: skip


def test_the_old_rule_is_logged_next_to_path_truth_only_for_an_attached_graph():
    attached_graph = [
        hung(defect("drawer rails", "rough", "dresser.md"), ("X", "section", "c"), sections=["X"])
    ]
    metrics = EvalReport(paths=score_paths(attached_graph, PART_OF_SCHEMA)).metrics()
    assert (metrics["path_truth"], metrics["path_truth_about"]) == (1.0, 0.0)
    assert "path_truth_about" not in EvalReport(paths=score_paths(LEAK, SCHEMA)).metrics()


def test_rescore_reads_the_attachments_of_an_attached_graphs_sheet():
    desk_fact, _ = LOGGED.facts
    sheet = LOGGED.model_copy(
        update={
            "facts": [
                desk_fact.model_copy(
                    update={
                        "things": ["Desk"],
                        "attachments": [Attached(thing="Desk", how="section", evidence="c")],
                        "sections": ["Desk"],
                    }
                )
            ]
        }
    )
    report = rescore(sheet, GoldSet(), schema=SCHEMA)
    assert (report.paths.paths_total, report.paths.paths_true, report.paths.paths_true_about) == (1, 1, 0)
    assert report.judge_sheet.facts[0].attachments == [Attached(thing="Desk", how="section", evidence="c")]


def test_a_name_on_another_line_of_the_quote_does_not_hold_a_claim():
    # found in R76's generality run: a quote of several lines of a note; the claim is in the second line
    visit = defect("drawer rails", "rough", "dresser.md", own_wording="rough").model_copy(
        update={"evidence": "Mon: Helsingborg Dresser delivered.\nWed: the drawer rails felt rough."}
    )
    report = score_paths([hung(visit, (DRESSER, "key_in_sentence", "Helsingborg Dresser"))], PART_OF_SCHEMA)
    assert (report.paths_total, report.paths_true) == (1, 0)
