"""The identity stage (R75, layered-model Step 5): what each mention refers to, written as one REFERS_TO edge
per mention. Pure: the flattening reader's two rules. With Neo4j: a keyed mention finds its record by an
attribute in its sentence, two same-named records without one leave the mention ambiguous, an individual
of one name stays one per document, a value's spellings share one concept, the resolve stage logs its
params, metrics and audit file and refuses identity classes the plan cannot satisfy, and the flattening
reader gives the triples of the shape before R75 on the same claims."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import ProposalRejectedError
from kgbuilder.core.values import VALUE_TYPE
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PLAN_FILE, TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.resolution.identity import IdentitySettings, resolve_identity
from kgbuilder.resolution.linking import link_graphs
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.text.subject_graph import write_subject_graph
from kgbuilder.validation.checks.base import CheckContext, ClaimRow, flatten

from .fakes import RecordingTracker
from .sample_plans import node

SETTINGS = IdentitySettings(auto_merge=92, borderline=80, link_threshold=90)


def row(
    id: str, subject: str, obj: str, subject_name: str, chunk: str = "a.md#0", evidence: str = "q"
) -> ClaimRow:
    return ClaimRow(
        id=id, predicate="P", subject_type="T", object_type="T", chunk_id=chunk, evidence=evidence,
        subject_names=[subject_name], object_names=["o"], subject_name=subject_name, object_name="o",
        subject_id=subject, object_id=obj,
    )  # fmt: skip


def test_the_flattening_reader_leaves_out_self_references_and_reads_one_of_two_repeats():
    """The rules apply_merges used to apply to the graph (R28, R64): "X relates to X" says nothing, and one
    statement extracted under two spellings is one claim; the first by its own wording survives."""
    rows = [
        row("o3", "s", "s", "a"),  # both ends one entity
        row("o2", "s", "o", "Tables"),
        row("o1", "s", "o", "Table"),  # the repeat of o2 with the earlier wording
        row("o4", "s", "o", "Table", chunk="a.md#1"),  # another chunk: separate evidence
    ]
    assert [f.subject_name for f in flatten(rows)] == ["Table", "Table"]
    assert sorted(f.chunk_id for f in flatten(rows)) == ["a.md#0", "a.md#1"]


# An invented institute: two staff records of one name, told apart by their team
PLAN = ConstructionPlan(
    nodes=[
        node("staff.csv", "Staff", "staff_id", ["name", "team"]).model_copy(update={"name_column": "name"})
    ],
    relationships=[],
)
SCHEMA = TextSchema(
    entity_types=[
        EntityType(
            name="Person", description="d", identity="keyed", record_labels=["Staff"], key_attributes=["team"]
        ),
        EntityType(name="Visitor", description="d", identity="individual"),
        EntityType(name="Topic", description="d"),
    ],
    fact_types=[
        FactType(predicate="PRESENTED", subject_type="Person", object_type="Topic", description="d"),
        FactType(predicate="MET", subject_type="Visitor", object_type="Person", description="d"),
        FactType(predicate="COSTS", subject_type="Topic", object_type=VALUE_TYPE, description="d"),
    ],
)
DOCS = {
    "minutes.md": "Present: Maria Lopez (Finance Office). Maria Lopez presented the budget.",
    "fieldwork.md": "Maria Lopez took the cores. Ben Ash met Maria Lopez.",
    "visit.md": "Ben Ash met Maria Lopez at noon. The budget costs 25kg of paperwork, or 25 kg.",
}


def claim(subject: str, stype: str, predicate: str, obj: str, otype: str, doc: str, evidence: str) -> Triple:
    return Triple(
        subject=subject, subject_type=stype, predicate=predicate, object=obj, object_type=otype,
        evidence=evidence, chunk_id=f"{doc}#0",
    )  # fmt: skip


def build(driver) -> None:
    driver.execute_query(
        "CREATE (:Staff {staff_id: 'S-104', name: 'Maria Lopez', team: 'Soil Ecology'}), "
        "(:Staff {staff_id: 'S-219', name: 'Maria Lopez', team: 'Finance Office'})"
    )
    documents = [Document(doc_id=d, title=d.split(".")[0], text=t) for d, t in DOCS.items()]
    chunks = [Chunk(chunk_id=f"{d}#0", doc_id=d, index=0, text=t) for d, t in DOCS.items()]
    write_lexical_graph(driver, documents, chunks)
    write_subject_graph(
        driver,
        [
            claim("Maria Lopez", "Person", "PRESENTED", "budget", "Topic", "minutes.md",
                  "Maria Lopez presented the budget."),
            claim("Ben Ash", "Visitor", "MET", "Maria Lopez", "Person", "fieldwork.md",
                  "Ben Ash met Maria Lopez."),
            claim("Ben Ash", "Visitor", "MET", "Maria Lopez", "Person", "visit.md",
                  "Ben Ash met Maria Lopez at noon."),
            claim("budget", "Topic", "COSTS", "25kg", VALUE_TYPE, "visit.md",
                  "The budget costs 25kg of paperwork, or 25 kg."),
            claim("budget", "Topic", "COSTS", "25 kg", VALUE_TYPE, "visit.md",
                  "The budget costs 25kg of paperwork, or 25 kg."),
        ],
        extractor="test",
    )  # fmt: skip
    link_graphs(driver, PLAN)


def identity_of(driver, doc: str, name: str) -> tuple[str, str, str]:
    """(kind, canonical, reason) of the mention called `name` in `doc`."""
    [record] = driver.execute_query(
        "MATCH (m:Mention {doc_id: $doc, name: $name})-[r:REFERS_TO]->() "
        "RETURN r.kind AS k, r.canonical AS c, r.reason AS why",
        doc=doc,
        name=name,
    )[0]
    return record["k"], record["c"], record["why"]


@pytest.mark.neo4j
def test_an_attribute_in_the_sentence_finds_the_record_and_without_one_the_name_stays_apart(driver):
    build(driver)
    report = resolve_identity(driver, SCHEMA, PLAN, None, "m", SETTINGS)
    # "Maria Lopez (Finance Office)": the team tells the two staff records apart
    assert identity_of(driver, "minutes.md", "Maria Lopez") == ("record", "Staff:S-219", "attribute")
    # nothing in the field log tells them apart: no link, logged as ambiguous, an individual of its own
    kind, canonical, reason = identity_of(driver, "fieldwork.md", "Maria Lopez")
    assert (kind, reason) == ("individual", "ambiguous_record")
    assert [(a.name, a.records) for a in report.ambiguous if a.doc_id == "fieldwork.md"] == [
        ("Maria Lopez", ["Staff:S-104", "Staff:S-219"])
    ]
    # and the visit's Maria Lopez is another individual: the same name is no evidence of the same person
    assert identity_of(driver, "visit.md", "Maria Lopez")[1] != canonical


@pytest.mark.neo4j
def test_an_individual_type_gives_one_individual_per_document_and_values_share_one_concept(driver):
    build(driver)
    resolve_identity(driver, SCHEMA, PLAN, None, "m", SETTINGS)
    ash = {identity_of(driver, doc, "Ben Ash") for doc in ("fieldwork.md", "visit.md")}
    assert {(k, why) for k, _, why in ash} == {("individual", "own_name")} and len(ash) == 2
    # "25kg" and "25 kg" in one sentence are two wordings of one number: one Value concept
    values = {identity_of(driver, "visit.md", wording) for wording in ("25kg", "25 kg")}
    assert len({c for _, c, _ in values}) == 1 and {k for k, _, _ in values} == {"concept"}
    [name] = driver.execute_query("MATCH (c:Concept {type: $t}) RETURN c.name AS n", t=VALUE_TYPE)[0]
    assert name["n"] == "25 kg"


@pytest.mark.neo4j
def test_the_flattening_reader_gives_each_claim_its_own_wording_and_its_entitys_names(driver):
    """The judge sheet and exact matching read what they read before R75: one triple per claim, its own
    wording, and the names of the entity it is about (a record's display name first)."""
    build(driver)
    before = {(f.predicate, f.own_subject, f.own_object, f.chunk_id) for f in CheckContext(driver).facts}
    resolve_identity(driver, SCHEMA, PLAN, None, "m", SETTINGS)
    facts = CheckContext(driver).facts
    # one quote read as "25kg" and as "25 kg" is one statement once both wordings are one number: the
    # repeat rule keeps the first wording (R64), and every other claim reads as before
    repeat = ("COSTS", "budget", "25kg", "visit.md#0")
    assert {(f.predicate, f.own_subject, f.own_object, f.chunk_id) for f in facts} == before - {repeat}
    [presented] = [f for f in facts if f.predicate == "PRESENTED"]
    assert presented.subject_names == ["Maria Lopez"]  # the record's name, which is also the mention's
    [costs] = [f for f in facts if f.predicate == "COSTS"]
    assert costs.object_names == ["25 kg", "25kg"]  # the concept's canonical spelling, then every wording


def stage_context(driver, tmp_path, schema: TextSchema) -> tuple[PipelineContext, RecordingTracker]:
    out = tmp_path / "out"
    out.mkdir()
    (out / PLAN_FILE).write_text(PLAN.model_dump_json(), encoding="utf-8")
    (out / TEXT_SCHEMA_FILE).write_text(schema.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    return PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker), tracker


@pytest.mark.neo4j
def test_the_resolve_stage_logs_where_the_mentions_went_and_writes_every_decision(driver, tmp_path):
    build(driver)
    ctx, tracker = stage_context(driver, tmp_path, SCHEMA)
    run_stages(ctx, PipelineState(), [st.ResolveStage()])
    run = tracker.run("resolve")
    assert {"domain_link_threshold", "er_auto_merge", "prompt_version"} <= set(run.logged_params)
    metrics = run.logged_metrics
    assert (metrics["mentions"], metrics["mentions_to_records"], metrics["mentions_ambiguous"]) == (9, 1, 2)
    assert (metrics["linked_by_attribute"], metrics["records_referred"], metrics["entities_linked"]) == (
        1,
        1,
        1,
    )
    assert metrics["mentions_to_individuals"] == 4 and metrics["mentions_to_concepts"] == 4
    audit = json.loads((ctx.out / "resolve.json").read_text(encoding="utf-8"))
    assert len(audit["assignments"]) == 9 and all(a["reason"] for a in audit["assignments"])

    run_stages(ctx, PipelineState(), [st.UndoResolveStage()])
    assert tracker.run("resolve_undo").logged_metrics["identity_edges_removed"] == 9
    assert driver.execute_query("MATCH ()-[r:REFERS_TO]->() RETURN count(r) AS n")[0][0]["n"] == 0


@pytest.mark.neo4j
def test_the_resolve_stage_refuses_a_keyed_type_whose_label_the_plan_lacks(driver, tmp_path):
    build(driver)
    wrong = SCHEMA.model_copy(
        update={"entity_types": [t.model_copy(update={"record_labels": ["Pump"]}) if t.name == "Person" else t
                                 for t in SCHEMA.entity_types]}
    )  # fmt: skip
    ctx, _ = stage_context(driver, tmp_path, wrong)
    with pytest.raises(ProposalRejectedError, match="'Pump' is not a label"):
        run_stages(ctx, PipelineState(), [st.ResolveStage()])
