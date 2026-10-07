"""The identity stage (R75, layered-model Step 5): what each mention refers to, written as one REFERS_TO edge
per mention. Pure: the flattening reader's two rules. With Neo4j: a keyed mention finds its record by an
attribute in its sentence, two same-named records without one leave the mention ambiguous, an individual
of one name stays one per document, a value's spellings share one concept, the resolve stage logs its
params, metrics and audit file and refuses identity classes the plan cannot satisfy, the flattening
reader gives the triples of the shape before R75 on the same claims, and an LLM's choice among a
mention's near misses links only when code verifies it (R95b). R107, pure and on Neo4j: a keyed mention the
mention pass stated a kind that no record fits is set aside from the particulars, never paired with another
named thing, and resolved as a concept of the built-in `Kind` type; R107's resolve of R103's three graphs
logged only identity changes (tests/gold/r107)."""

import json
from pathlib import Path

import pytest

from kgbuilder.audit.fidelity import LoggedCounts
from kgbuilder.config import Settings
from kgbuilder.core.errors import ProposalRejectedError
from kgbuilder.core.identity import concept_id
from kgbuilder.core.values import VALUE_TYPE
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PLAN_FILE, TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.resolution.concepts import SamePair
from kgbuilder.resolution.identity import IdentitySettings, resolve_identity
from kgbuilder.resolution.individuals import SameIndividual
from kgbuilder.resolution.linking import link_graphs
from kgbuilder.resolution.mentions import MentionRecord, MentionText
from kgbuilder.resolution.particulars import JoinSettings, assign_particulars
from kgbuilder.resolution.record_choice import RecordChoice
from kgbuilder.resolution.records import RecordCandidate, RecordLink, RecordMatch
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import KIND_TYPE, EntityType, FactType, MentionClass, TextSchema
from kgbuilder.text.subject_graph import mention_row, write_mentions, write_subject_graph
from kgbuilder.validation.checks.base import CheckContext, ClaimRow, flatten

from .fakes import RecordingTracker, ScriptedLLM
from .sample_plans import node, rel

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


def _vehicle(name: str, doc: str, stated: MentionClass | None) -> MentionRecord:
    """A mention of an invented keyed `Vehicle` type, with the class the pass stated (None: a claim's)."""
    return MentionRecord(
        id=f"{doc}/{name}", name=name, type="Vehicle", doc_id=doc, chunks=1, stated_class=stated
    )


def test_a_stated_kind_no_record_fits_is_set_aside_and_never_paired_with_a_named_thing():
    """R107, after R103's held-out error: "CARS" and "CAR" of two complaints, stated kinds of a keyed type
    that no record fits, became individuals and were joined. Now they are set aside for the concepts, as is a
    stated kind two records tie on; a stated kind a record fits keeps its link, and a stated particular or a
    claim's mention (no stated class) that no record fits is still an individual."""
    rogue = RecordCandidate(element_id="e-1", label="Model", name="Rogue", key="M-1")
    sport = RecordCandidate(element_id="e-2", label="Model", name="Rogue Sport", key="M-2")
    cars, car, tied = (
        _vehicle("CARS", "c1.md", "kind"),
        _vehicle("CAR", "c2.md", "kind"),
        _vehicle("model", "c3.md", "kind"),
    )
    rogues = _vehicle("Rogues", "c4.md", "kind")
    named, claimed = _vehicle("Unit 54", "c5.md", "particular"), _vehicle("car", "c6.md", None)
    keyed = [cars, car, tied, rogues, named, claimed]
    link = RecordLink(record=rogue, reason="name", score=96.0, evidence="Rogues", scoped=True)
    matches = {m.id: RecordMatch() for m in keyed} | {
        tied.id: RecordMatch(tied=[rogue, sport]),
        rogues.id: RecordMatch(link=link),
    }
    texts = [
        MentionText(mention=m.id, document=m.doc_id, chunk_id=f"{m.doc_id}#0", text=f"The {m.name} stalled.")
        for m in keyed
    ]

    def assign(mentions: list[MentionRecord]):
        return assign_particulars(
            mentions,
            [],
            matches,
            texts,
            lambda units: set(),
            None,
            JoinSettings(borderline=80, model="m"),
            {},
        )

    after = assign(keyed)
    assert [m.id for m in after.kinds] == [cars.id, car.id, tied.id]
    assert {a.mention: (a.kind, a.reason) for a in after.assignments} == {
        rogues.id: ("record", "name"),
        named.id: ("individual", "no_record"),
        claimed.id: ("individual", "no_record"),
    }
    asked = {x for d in after.decisions for x in (d.a, d.b)}
    assert asked.isdisjoint({cars.id, car.id, tied.id})
    assert [a.mention for a in after.ambiguous] == [tied.id]  # several records still fit it equally
    # before R107 (no stated class) "CARS" and "CAR" were two named things, nominated as one pair
    before = assign([m.model_copy(update={"stated_class": None}) for m in keyed])
    assert before.kinds == [] and frozenset((cars.id, car.id)) in {
        frozenset((d.a, d.b)) for d in before.decisions
    }


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
def test_a_stated_kind_no_record_fits_refers_to_a_kind_concept_shared_across_documents(driver):
    """R107 on the graph: the pass wrote "colleague" in two documents, stated a kind in both, typed with the
    keyed `Person` in one and the fallback `Kind` in the other; no staff record is a colleague. Both refer to
    one `Kind` concept, not an individual and a concept. "Priya Shah", stated a particular, whom no record
    names, is still an individual."""
    build(driver)
    extra = {"rota.md": "The duty colleague signs the rota.", "handover.md": "A colleague met Priya Shah."}
    write_lexical_graph(
        driver,
        [Document(doc_id=d, title=d.split(".")[0], text=t) for d, t in extra.items()],
        [Chunk(chunk_id=f"{d}#0", doc_id=d, index=0, text=t) for d, t in extra.items()],
    )
    rota = mention_row("Person", "colleague", "rota.md#0")
    handover = mention_row(KIND_TYPE, "colleague", "handover.md#0")
    priya = mention_row("Person", "Priya Shah", "handover.md#0")
    rows = [rota, handover, priya]
    classes: dict[str, MentionClass] = {rota.id: "kind", handover.id: "kind", priya.id: "particular"}
    write_mentions(driver, rows, {(f"{r.doc_id}#0", r.id) for r in rows}, classes)
    report = resolve_identity(driver, SCHEMA, PLAN, None, "m", SETTINGS)
    assert report.kinds_without_record == [rota.id]
    kind, canonical, reason = identity_of(driver, "rota.md", "colleague")
    assert (kind, canonical, reason) == ("concept", concept_id(KIND_TYPE, "colleague"), "same_name")
    assert identity_of(driver, "handover.md", "colleague")[:2] == (kind, canonical)
    [concept] = driver.execute_query("MATCH (c:Concept {id: $c}) RETURN c.type AS t", c=canonical)[0]
    assert concept["t"] == KIND_TYPE
    priya_kind, _, priya_reason = identity_of(driver, "handover.md", "Priya Shah")
    assert (priya_kind, priya_reason) == ("individual", "no_record")


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
    assert {"domain_link_threshold", "er_auto_merge", "prompt_version", "individual_prompt_version"} <= set(
        run.logged_params
    )
    assert {"prompts/resolve_concepts.txt", "prompts/resolve_individuals.txt"} <= set(run.artifacts)
    assert run.logged_metrics["blocked_same_sentence"] == 0 and "individual_joined" in run.logged_metrics
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


# R75 b2: one individual across documents, joined only with evidence the text gives
PIKE_DOCS = {
    "news.md": "Dr Jonathan Pike of the institute spoke at the flood meeting.",
    "minutes.md": "Jon Pike (chair) opened the meeting on soil cores.",
    "seminar.md": "Talk by Dr. J. Pike (Soil Ecology) on peat.",
    "field.md": "The corer was freed by J. Pike after an hour.",
    "letter.md": "Judith Pike wrote about the street lights.",
}
PIKE_PLAN = ConstructionPlan(
    nodes=[
        node("staff.csv", "Staff", "staff_id", ["name", "team"]).model_copy(update={"name_column": "name"})
    ],
    relationships=[],
)
PIKE_SCHEMA = TextSchema(
    entity_types=[
        EntityType(
            name="Person", description="d", identity="keyed", record_labels=["Staff"], key_attributes=["team"]
        ),
        EntityType(name="Topic", description="d"),
    ],
    fact_types=[FactType(predicate="SPOKE_ON", subject_type="Person", object_type="Topic", description="d")],
)
PIKE_NAMES = {
    "news.md": "Dr Jonathan Pike",
    "minutes.md": "Jon Pike",
    "seminar.md": "Dr. J. Pike",
    "field.md": "J. Pike",
    "letter.md": "Judith Pike",
}


def build_pike(driver) -> None:
    driver.execute_query(
        "CREATE (:Staff {staff_id: 'S-131', name: 'Jonathan Pike', team: 'Soil Ecology'}), "
        "(:Staff {staff_id: 'S-150', name: 'Aiko Tanaka', team: 'Hydrology'})"
    )
    documents = [Document(doc_id=d, title=d.split(".")[0], text=t) for d, t in PIKE_DOCS.items()]
    chunks = [Chunk(chunk_id=f"{d}#0", doc_id=d, index=0, text=t) for d, t in PIKE_DOCS.items()]
    write_lexical_graph(driver, documents, chunks)
    claims = [
        claim(name, "Person", "SPOKE_ON", "peat", "Topic", doc, PIKE_DOCS[doc])
        for doc, name in PIKE_NAMES.items()
    ]
    write_subject_graph(driver, claims, extractor="test")
    link_graphs(driver, PIKE_PLAN)


def pike_judge(prompt: str, schema: type) -> SameIndividual | RecordChoice:
    """A scripted adjudicator: the chair of the minutes is the record's Pike, with two real quotes; the field
    log's J. Pike too, but with a quote the field log does not contain; everyone else not the same. Since R99
    the documents' variants of the record's name ("Jon Pike", "J. Pike") are first offered to the record
    chooser, which answers none here, so the joining of individuals decides them as before."""
    if schema is RecordChoice:
        return RecordChoice(record="none")
    if "Jon Pike\n" in prompt and "Jonathan Pike" in prompt:  # the chair's line and the record's Pike
        return SameIndividual(
            answer="same",
            quote_a="Dr Jonathan Pike of the institute spoke at the flood meeting.",
            quote_b="Jon Pike (chair) opened the meeting on soil cores.",
        )
    if "J. Pike" in prompt and "Jonathan Pike" in prompt and "Jon Pike" not in prompt:
        return SameIndividual(
            answer="same", quote_a="Jonathan Pike leads the group.", quote_b="J. Pike freed it."
        )
    return SameIndividual(answer="different")


@pytest.mark.neo4j
def test_one_individual_across_documents_is_joined_only_with_verified_evidence(driver):
    build_pike(driver)
    report = resolve_identity(driver, PIKE_SCHEMA, PIKE_PLAN, ScriptedLLM(pike_judge), "judge", SETTINGS)
    # a title is no part of the name; a variant reaches the record through its attribute
    assert identity_of(driver, "news.md", "Dr Jonathan Pike") == ("record", "Staff:S-131", "name")
    assert identity_of(driver, "seminar.md", "Dr. J. Pike") == ("record", "Staff:S-131", "variant_attribute")
    # the chair joins by an adjudication whose two quotes code found, each in its own document
    assert identity_of(driver, "minutes.md", "Jon Pike") == ("record", "Staff:S-131", "adjudicated")
    [edge] = driver.execute_query(
        "MATCH (:Mention {name: 'Jon Pike'})-[r:REFERS_TO]->() RETURN r.evidence AS e, r.by AS by"
    )[0]
    assert edge["by"] == "judge" and "Jon Pike (chair)" in edge["e"]
    # a yes with a quote its document does not hold is refused; the same initial is no evidence either
    field = identity_of(driver, "field.md", "J. Pike")
    letter = identity_of(driver, "letter.md", "Judith Pike")
    assert field[0] == letter[0] == "individual" and field[1] != letter[1]
    actions = {d.action for d in report.individual_decisions}
    assert "quote_not_verified" in actions and "joined" in actions
    # outside any scope only a variant of a record's name is a candidate (R99): never Judith Pike
    asked = {(c.name, tuple(c.candidates), c.action) for c in report.record_choices}
    assert asked == {("Jon Pike", ("Staff:S-131",), "none"), ("J. Pike", ("Staff:S-131",), "none")}


GEAR_SCHEMA = TextSchema(
    entity_types=[EntityType(name="Piece", description="d")],
    fact_types=[
        FactType(
            predicate="PIECE_OF", subject_type="Piece", object_type="Piece", description="d", part_of=True
        )
    ],
)


@pytest.mark.neo4j
def test_a_part_and_its_whole_are_never_joined(driver):
    """Two names alike enough to be asked about (70 by spelling), which a claim of a part-of fact type says
    are a piece and its whole. They are no compound of each other (that rule is tested in test_guards.py)
    and stand in two sentences, so the part-of claim alone keeps them apart."""
    text = "The gear housing cracked. It is a piece of the gear hub."
    write_lexical_graph(driver, [Document(doc_id="g.md", title="g", text=text)], [
        Chunk(chunk_id="g.md#0", doc_id="g.md", index=0, text=text)
    ])  # fmt: skip
    write_subject_graph(
        driver,
        [claim("gear housing", "Piece", "PIECE_OF", "gear hub", "Piece", "g.md", text)],
        extractor="t",
    )
    always_same = ScriptedLLM(lambda prompt, schema: SamePair(same=True))
    loose = IdentitySettings(auto_merge=99, borderline=50, link_threshold=90)
    report = resolve_identity(driver, GEAR_SCHEMA, None, always_same, "m", loose)
    assert report.merges == 0 and report.blocked == {"part_and_whole": 1}
    # without the schema's part-of flag, the same pair goes to the adjudicator, which says yes
    driver.execute_query("MATCH ()-[r:REFERS_TO]->() DELETE r")
    unflagged = GEAR_SCHEMA.model_copy(
        update={"fact_types": [f.model_copy(update={"part_of": False}) for f in GEAR_SCHEMA.fact_types]}
    )
    assert resolve_identity(driver, unflagged, None, always_same, "m", loose).merges == 1


@pytest.mark.neo4j
def test_kg_eval_scores_identity_pairs_on_the_live_graph(driver, tmp_path):
    build_pike(driver)
    resolve_identity(driver, PIKE_SCHEMA, PIKE_PLAN, ScriptedLLM(pike_judge), "judge", SETTINGS)
    gold = tmp_path / "identity_gold.json"
    side = {doc: {"doc_id": doc, "names": [name]} for doc, name in PIKE_NAMES.items()}
    quote = {doc: {"doc_id": doc, "quote": text} for doc, text in PIKE_DOCS.items()}

    def gold_pair(a: str, b: str, same: bool) -> dict:
        return {"a": side[a], "b": side[b], "same": same, "evidence": [quote[a], quote[b]]}

    pairs = [
        gold_pair("news.md", "minutes.md", True),  # joined by the verified adjudication
        gold_pair("news.md", "field.md", True),  # its adjudication was refused: a missed join
        gold_pair("letter.md", "field.md", False),  # kept apart
    ]
    gold.write_text(json.dumps({"identity_pairs": pairs}), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)
    run_stages(ctx, PipelineState(gold=gold), [st.EvalStage()])
    metrics = tracker.run("eval").logged_metrics
    assert (metrics["identity_precision"], metrics["identity_recall"], metrics["identity_apart_rate"]) == (
        1.0,
        0.5,
        1.0,
    )
    assert metrics["identity_pairs_scored"] == 3 and metrics["identity_not_extracted"] == 0


# R95b: an invented telescope whose notes name its parts in other words; no rule links them, an LLM chooses
SCOPE_DOC = "lyra_telescope_notes.md"
SCOPE_TEXT = "The brass focuser sticks in the cold. The thread of the focuser is worn."
SCOPE_PLAN = ConstructionPlan(
    nodes=[
        node("telescopes.csv", "Telescope", "telescope_id", ["name"]).model_copy(
            update={"name_column": "name"}
        ),
        node("units.csv", "Unit", "unit_id", ["name", "finish"]).model_copy(update={"name_column": "name"}),
    ],
    relationships=[rel("units.csv", "PART_OF", "Unit", "unit_id", "Telescope", "telescope_id")],
)
SCOPE_SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Component", description="d", identity="keyed", record_labels=["Unit"]),
        EntityType(name="Trait", description="d"),
    ],
    fact_types=[FactType(predicate="HAS", subject_type="Component", object_type="Trait", description="d")],
)


def build_telescope(driver) -> None:
    driver.execute_query(
        "CREATE (t:Telescope {telescope_id: 'T-1', name: 'Lyra Telescope'}), "
        "(f:Unit {unit_id: 'U-1', name: 'Focuser', finish: 'brass'})-[:PART_OF]->(t), "
        "(:Unit {unit_id: 'U-2', name: 'Focuser Knob'})-[:PART_OF]->(f)"
    )
    document = Document(doc_id=SCOPE_DOC, title="lyra_telescope_notes", text=SCOPE_TEXT)
    write_lexical_graph(
        driver, [document], [Chunk(chunk_id=f"{SCOPE_DOC}#0", doc_id=SCOPE_DOC, index=0, text=SCOPE_TEXT)]
    )
    write_subject_graph(
        driver,
        [
            claim("brass focuser", "Component", "HAS", "sticks", "Trait", SCOPE_DOC,
                  "The brass focuser sticks in the cold."),
            claim("thread of the focuser", "Component", "HAS", "worn", "Trait", SCOPE_DOC,
                  "The thread of the focuser is worn."),
        ],
        extractor="test",
    )  # fmt: skip
    link_graphs(driver, SCOPE_PLAN)


@pytest.mark.neo4j
def test_an_llm_chooses_among_near_misses_and_code_links_only_its_verified_choice(driver, tmp_path):
    build_telescope(driver)
    prompts: list[str] = []

    def script(prompt: str, schema: type):
        """The brass focuser is the record Focuser; the thread of the focuser is none of the records."""
        if schema is not RecordChoice:
            return schema(answer="different") if schema is SameIndividual else schema(same=False)
        prompts.append(prompt)
        if '"brass focuser"' in prompt:
            return RecordChoice(record="Unit:U-1", quote="The brass focuser sticks in the cold.")
        return RecordChoice(record="none")

    out = tmp_path / "out"
    out.mkdir()
    (out / PLAN_FILE).write_text(SCOPE_PLAN.model_dump_json(), encoding="utf-8")
    (out / TEXT_SCHEMA_FILE).write_text(SCOPE_SCHEMA.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(
        settings=Settings(), driver=driver, out=out, llm=ScriptedLLM(script), tracker=tracker
    )
    run_stages(ctx, PipelineState(), [st.ResolveStage()])

    [edge] = driver.execute_query(
        "MATCH (:Mention {name: 'brass focuser'})-[r:REFERS_TO]->(u:Unit) RETURN r, u.unit_id AS unit"
    )[0]
    assert (edge["unit"], edge["r"]["reason"], edge["r"]["by"]) == (
        "U-1",
        "chosen",
        ctx.settings.extract_model,
    )
    assert edge["r"]["evidence"] == "The brass focuser sticks in the cold." and edge["r"]["score"] is None
    assert identity_of(driver, SCOPE_DOC, "thread of the focuser")[::2] == ("individual", "no_record")
    # both near misses, with the cells and one-hop relations read from the graph
    [shown] = [p for p in prompts if '"brass focuser"' in p]
    assert "[lyra_telescope_notes.md] The brass focuser sticks in the cold." in shown
    assert (
        "- Unit:U-1: Focuser\n  data: finish = brass; name = Focuser\n"
        "  relations: PART_OF -> Telescope:T-1 (Lyra Telescope); Unit:U-2 (Focuser Knob) PART_OF -> this"
    ) in shown
    assert (
        "- Unit:U-2: Focuser Knob\n  data: name = Focuser Knob\n  relations: PART_OF -> Unit:U-1 (Focuser)"
    ) in shown
    run = tracker.run("resolve")
    assert "record_choice_prompt_version" in run.logged_params
    assert "prompts/resolve_record_choice.txt" in run.artifacts
    metrics = run.logged_metrics
    assert (metrics["record_choices"], metrics["record_choice_chosen"], metrics["record_choice_none"]) == (
        2,
        1,
        1,
    )
    assert metrics["linked_by_chosen"] == 1
    audit = json.loads((out / "resolve.json").read_text(encoding="utf-8"))
    assert sorted(d["action"] for d in audit["record_choices"]) == ["chosen", "none"]


GOLD = Path(__file__).resolve().parent / "gold"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r107_resolved_r103s_graphs_again_and_changed_only_identity(dataset):
    """R107 part b's logged counts (copied from MLflow): R103's claims replayed and its pass answered from the
    cache, so every count before resolve is R103's; resolve moved mentions from individuals (and generality's
    two pumps from a record) to concepts; only resolve paid, the cost the runs index reports."""
    runs = json.loads((GOLD / "r107" / "runs.json").read_text(encoding="utf-8"))["datasets"][dataset]
    after = LoggedCounts.model_validate_json((GOLD / "r107" / f"{dataset}_logged.json").read_text("utf-8"))
    before = LoggedCounts.model_validate_json((GOLD / "r103" / f"{dataset}_logged.json").read_text("utf-8"))
    built = [k for k in before.counts if not k.startswith(("resolve.", "attach."))]
    assert {k: after.counts[k] for k in built} == {k: before.counts[k] for k in built}
    assert after.counts["resolve.mentions"] == before.counts["resolve.mentions"]
    assert after.counts["resolve.mentions_to_individuals"] < before.counts["resolve.mentions_to_individuals"]
    assert after.counts["resolve.mentions_to_concepts"] > before.counts["resolve.mentions_to_concepts"]
    assert after.counts["resolve.mentions_to_records"] <= before.counts["resolve.mentions_to_records"]
    paid = {k for k, v in after.usage.items() if k.endswith("cost_usd") and v > 0}
    assert paid == {"resolve.cost_usd"} and after.usage["resolve.cost_usd"] == pytest.approx(runs["cost_usd"])
    assert after.runs == runs["runs"]
