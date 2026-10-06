"""Tests of the graph audit's part a (R87): the offline snapshot, the fidelity gate and the code checks.

Each test builds a small invented build folder (two product reviews, three record tables, triples, a
resolve.json) in a temporary directory: no Neo4j, no LLM. The build plants one error of each kind the
checks must find: a mention of the lamp's review linked to the kettle's lid (outside the lamp's scope),
and a claim about the kettle's lid hinge hung on the lid because "lid" stands inside "lid hinge". The
replay of R94 must unlink exactly that lamp mention under the current matching rules, and call any other
difference unexplained. A link the build made by a rule R95a retired (containment, a spelling that differs
inside a word) is unlinked too, with that cause. With a (scripted) chooser, a mention whose near miss it
chooses is linked by that choice (R95b); `--choose` needs an LLM and logs what it asks with. R98 adds a
person named in both reviews, which the build joined: the faithful replay of the individuals must give
resolve.json back field by field and refuse a build it does not reproduce; the measured replay explains a join
it undoes and writes its decisions into the build it gives.
"""

import json
import re
from pathlib import Path

import pytest

from kgbuilder.audit import build_snapshot, check_fidelity, compute_reach, gold_pairs, run_checks
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.audit.inputs import read_records, read_relations, scopes
from kgbuilder.audit.reidentify import ReidentifyReport
from kgbuilder.audit.relink import RelinkReport, relink
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError, LLMUnavailableError
from kgbuilder.core.identity import concept_id, individual_id, mention_id
from kgbuilder.llm.base import prompt_version
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.audit_stages import (
    CHECKS_FILE,
    REIDENTIFY_FILE,
    RELINK_FILE,
    RELINKED_BUILD,
    RELINKED_LOGGED,
    AuditRelinkStage,
    AuditSnapshotStage,
)
from kgbuilder.resolution.individuals import IDENTITY_PROMPT, SameIndividual
from kgbuilder.resolution.record_choice import CHOICE_PROMPT, RecordChoice
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.schema import TextSchema
from tests.fakes import RecordingTracker, ScriptedLLM

CHUNKING = (1500, 200, 0)
LAMP = "reviews/alder_lamp_reviews.md"
KETTLE = "reviews/birch_kettle_reviews.md"

PLAN = {
    "nodes": [
        {
            "source_file": "products.csv",
            "label": "Product",
            "unique_column": "product_id",
            "properties": ["product_name"],
            "description": "A product.",
            "name_column": "product_name",
        },
        {
            "source_file": "assemblies.csv",
            "label": "Assembly",
            "unique_column": "assembly_id",
            "properties": ["part_name"],
            "description": "A part of a product.",
            "name_column": "part_name",
        },
        {
            "source_file": "components.csv",
            "label": "Component",
            "unique_column": "component_id",
            "properties": ["part_name"],
            "description": "A piece of an assembly.",
            "name_column": "part_name",
        },
    ],
    "relationships": [
        {
            "source_file": "assemblies.csv",
            "relationship_type": "PART_OF",
            "from_label": "Assembly",
            "from_column": "assembly_id",
            "to_label": "Product",
            "to_column": "product_id",
            "properties": [],
        },
        {
            "source_file": "components.csv",
            "relationship_type": "PART_OF",
            "from_label": "Component",
            "from_column": "component_id",
            "to_label": "Assembly",
            "to_column": "assembly_id",
            "properties": [],
        },
    ],
}

SCHEMA = {
    "entity_types": [
        {"name": "Product", "description": "A product.", "identity": "keyed", "record_labels": ["Product"]},
        {"name": "Part", "description": "A part.", "identity": "keyed",
         "record_labels": ["Assembly", "Component"]},
        {"name": "Quality", "description": "A state.", "identity": "concept"},
    ],
    "fact_types": [
        {"predicate": "EXHIBITS", "subject_type": "Part", "object_type": "Quality",
         "description": "A state."},
        {"predicate": "PART_OF", "subject_type": "Part", "object_type": "Product", "description": "A piece.",
         "derived": True, "part_of": True},
    ],
}  # fmt: skip

TEXTS = {
    LAMP: "# Alder Lamp Reviews\n\nThe shade is cracked. The lid is loose.\n",
    KETTLE: "# Birch Kettle Reviews\n\nThe lid fits well. The lid hinge is stiff.\n",
}

# (subject, object, evidence, document); every subject is a Part, every object a Quality
TRIPLES = [
    ("shade", "cracked", "The shade is cracked.", LAMP),
    ("lid", "loose", "The lid is loose.", LAMP),
    ("lid", "fits well", "The lid fits well.", KETTLE),
    ("lid hinge", "stiff", "The lid hinge is stiff.", KETTLE),
]

# what each Part or Product mention refers to; the lamp's "lid" is the planted wrong link
RECORD_LINKS = {
    ("Part", "shade", LAMP): ("Assembly:A-1", "Shade"),
    ("Part", "lid", LAMP): ("Assembly:A-2", "Lid"),
    ("Part", "lid", KETTLE): ("Assembly:A-2", "Lid"),
    ("Part", "lid hinge", KETTLE): ("Component:S-1", "Lid Hinge"),
    ("Product", "Alder Lamp", LAMP): ("Product:P-1", "Alder Lamp"),  # created by derivation
    ("Product", "Birch Kettle", KETTLE): ("Product:P-2", "Birch Kettle"),
}


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def _build(tmp_path: Path) -> tuple[Path, Path]:
    """A finished build folder and its dataset."""
    data, out = tmp_path / "data", tmp_path / "build"
    for doc_id, text in TEXTS.items():
        (data / doc_id).parent.mkdir(parents=True, exist_ok=True)
        (data / doc_id).write_text(text, encoding="utf-8")
    staging = out / "staging"
    staging.mkdir(parents=True)
    _write_csv(staging / "products.csv", "product_id,product_name", ["P-1,Alder Lamp", "P-2,Birch Kettle"])
    # A-1 appears twice: one record, the later row's non-empty value wins
    _write_csv(
        staging / "assemblies.csv",
        "assembly_id,part_name,product_id",
        ["A-1,,P-1", "A-1,Shade,P-1", "A-2,Lid,P-2"],
    )
    _write_csv(
        staging / "components.csv", "component_id,part_name,assembly_id", ["S-1,Lid Hinge,A-2", ",Loose,A-2"]
    )
    (out / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    (out / "text_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (out / "profile.json").write_text(json.dumps({"files": [], "foreign_keys": []}), encoding="utf-8")
    lines = [
        json.dumps({"subject": s, "subject_type": "Part", "predicate": "EXHIBITS", "object": o,
                    "object_type": "Quality", "evidence": e, "chunk_id": f"{d}#0"})
        for s, o, e, d in TRIPLES
    ]  # fmt: skip
    (out / "triples.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assignments = [
        {"mention": mention_id(t, n, d), "said": n, "kind": "record", "canonical": ref, "name": name,
         "type": t, "reason": "name", "score": 100.0}
        for (t, n, d), (ref, name) in RECORD_LINKS.items()
    ] + [
        {"mention": mention_id("Quality", o, d), "said": o, "kind": "concept",
         "canonical": concept_id("Quality", o), "name": o, "type": "Quality", "reason": "same_name"}
        for _, o, _, d in TRIPLES
    ]  # fmt: skip
    (out / "resolve.json").write_text(json.dumps({"assignments": assignments}), encoding="utf-8")
    return out, data


def _snapshot(tmp_path: Path):
    out, data = _build(tmp_path)
    return build_snapshot(out, data, CHUNKING), out, data


def _schema(out: Path) -> TextSchema:
    return TextSchema.model_validate_json((out / "text_schema.json").read_text(encoding="utf-8"))


def test_records_follow_the_importer_one_per_key_the_later_value_winning_and_null_keys_skipped(tmp_path):
    out, _ = _build(tmp_path)
    plan = ConstructionPlan.model_validate(PLAN)
    records = {r.id: r for r in read_records(out / "staging", plan)}
    assert set(records) == {"Product:P-1", "Product:P-2", "Assembly:A-1", "Assembly:A-2", "Component:S-1"}
    assert records["Assembly:A-1"].name == "Shade"
    relations = read_relations(out / "staging", plan, list(records.values()))
    reach = scopes(list(records.values()), relations)
    # two hops: the kettle reaches its lid and the lid's hinge; the lamp never reaches the kettle's parts
    assert reach["Product:P-2"] == {"Product:P-2", "Assembly:A-2", "Component:S-1"}
    assert "Assembly:A-2" not in reach["Product:P-1"]


def test_the_snapshot_rebuilds_links_derived_claims_and_attachments(tmp_path):
    s, _, _ = _snapshot(tmp_path)
    assert {(link.source, link.thing, link.how) for link in s.documents_about} == {
        (LAMP, "Product:P-1", "file"),
        (KETTLE, "Product:P-2", "file"),
    }
    derived = {(c.subject_name, c.object_name) for c in s.claims if c.derived}
    # one PART_OF per part mention, onto a product mention derivation names after the plan node
    assert derived == {("shade", "Alder Lamp"), ("lid", "Alder Lamp"), ("lid", "Birch Kettle"),
                       ("lid hinge", "Birch Kettle")}  # fmt: skip
    assert {m.name for m in s.mentions if m.derived} == {"Alder Lamp", "Birch Kettle"}
    edges = {(a.observation, a.thing, a.how) for a in s.attachments}
    stiff = next(c.id for c in s.claims if c.object_name == "stiff")
    assert (stiff, "Component:S-1", "key_in_sentence") in edges
    assert (stiff, "Assembly:A-2", "key_in_sentence") in edges  # "lid" inside "lid hinge": the build's rule


def test_fidelity_passes_on_its_own_counts_and_reports_every_difference(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    counts = snapshot_counts(s)
    logged = LoggedCounts(dataset="t", build="b", git_sha="x", runs={}, counts=counts)
    assert check_fidelity(s, logged, None).passed
    wrong = logged.model_copy(
        update={"counts": {**counts, "attach.attachments": counts["attach.attachments"] + 1}}
    )
    report = check_fidelity(s, wrong, None)
    assert not report.passed and [c.name for c in report.comparisons if not c.ok] == ["attach.attachments"]

    loose = next(c for c in s.claims if c.object_name == "loose")
    sheet = tmp_path / "judge_sheet.json"
    on_graph = [{"thing": a.name, "how": a.how} for a in s.attachments if a.observation == loose.id]
    sheet.write_text(json.dumps({"facts": [{"id": loose.id, "attachments": on_graph}]}), encoding="utf-8")
    assert check_fidelity(s, logged, sheet).passed
    facts = [{"id": loose.id, "attachments": on_graph[:-1]}, {"id": "not-a-claim", "attachments": []}]
    sheet.write_text(json.dumps({"facts": facts}), encoding="utf-8")
    report = check_fidelity(s, logged, sheet)
    assert report.sheet_facts_missing == ["not-a-claim"] and list(report.sheet_attachment_diffs) == [loose.id]


def test_the_checks_find_the_planted_errors_and_full_provenance(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    checks = run_checks(s, _schema(out))
    assert all(p.rate == 1.0 for p in checks.provenance.values())
    by_kind = {kind: [f for f in checks.flags if f.kind == kind] for kind in checks.flag_counts}
    assert [f.item for f in by_kind["cross_scope_link"]] == [mention_id("Part", "lid", LAMP)]
    # both claims of the lamp's review about its "lid" (the extracted one and the derived PART_OF) hang on
    # the kettle's lid
    lamp_lid = {c.id for c in s.claims if c.subject_name == "lid" and c.doc_id == LAMP}
    assert len(lamp_lid) == 2
    assert {f.item for f in by_kind["cross_scope_attachment"]} == {f"{c} -> Assembly:A-2" for c in lamp_lid}
    hinge = {c.id for c in s.claims if c.subject_name == "lid hinge"}  # "stiff" and the derived PART_OF
    assert {f.item for f in by_kind["compound_name"]} == {f"{c} -> Assembly:A-2" for c in hinge}
    assert checks.flag_counts["label_mismatch"] == 0 and checks.flag_counts["fuzzy_name"] == 0


def test_a_record_of_a_label_the_type_does_not_name_is_a_label_mismatch(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    shade = mention_id("Part", "shade", LAMP)
    references = [
        a.model_copy(update={"canonical": "Product:P-1"}) if a.mention == shade else a for a in s.references
    ]
    checks = run_checks(s.model_copy(update={"references": references}), _schema(out))
    assert [f.item for f in checks.flags if f.kind == "label_mismatch"] == [shade]


def test_a_quote_not_in_its_chunk_fails_provenance(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    claims = [c.model_copy(update={"evidence": "Never written."}) if c.object_name == "cracked" else c
              for c in s.claims]  # fmt: skip
    checks = run_checks(s.model_copy(update={"claims": claims}), _schema(out))
    assert checks.provenance["extracted.evidence_in_chunk"].k == 3
    assert len(checks.provenance_misses["extracted.evidence_in_chunk"]) == 1


def test_reach_counts_pairs_per_pattern_and_leaves_out_chunks_without_claims(tmp_path):
    s, _, _ = _snapshot(tmp_path)
    claims, sample = tmp_path / "claims.json", tmp_path / "sample.json"
    sample.write_text(json.dumps({"sentences": [
        {"id": "s1", "doc_id": LAMP, "chunk_id": f"{LAMP}#0"},
        {"id": "s2", "doc_id": KETTLE, "chunk_id": f"{KETTLE}#9"},
    ]}), encoding="utf-8")  # fmt: skip
    claims.write_text(json.dumps({"sentences": [
        {"id": "s1", "claims": [{"claim": "the shade is cracked", "about": "Alder Lamp"},
                                {"claim": "the lid is loose", "about": "Shade"}]},
        {"id": "s2", "claims": [{"claim": "nothing stored here", "about": "Birch Kettle"}]},
    ]}), encoding="utf-8")  # fmt: skip
    report = compute_reach(s, gold_pairs(claims, sample))
    assert report.pairs == 2 and report.no_claim_chunks == 1 and report.any_pattern.rate == 1.0
    assert (
        report.per_pattern["P2"].k == 1
    )  # the lamp reaches its document's chunk; the shade only by its claims
    # the kettle's lid record reaches the lamp's chunk through the planted link and the claim it attached
    assert report.foreign_chunks == {"P1": 1, "P4": 1}


def test_the_stage_logs_fidelity_and_checks_and_writes_its_reports(tmp_path):
    s, out, data = _snapshot(tmp_path)
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t", build="b", git_sha="abc", runs={}, counts=snapshot_counts(s)
        ).model_dump_json(),
        encoding="utf-8",
    )
    tracker = RecordingTracker()
    # no graph: the audit never opens one
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "audit", tracker=tracker)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged)
    state = run_stages(ctx, state, [AuditSnapshotStage()])
    run = tracker.run("audit_snapshot")
    assert run.logged_params["build_git_sha"] == "abc" and run.logged_params["chunk_max_chars"]
    assert (
        run.logged_metrics["fidelity_passed"] == 1.0 and run.logged_metrics["flags_cross_scope_link"] == 1.0
    )
    assert {Path(a).name for a in run.artifacts} >= {
        "snapshot.json",
        "fidelity.json",
        CHECKS_FILE,
        "logged.json",
    }
    assert state.fidelity.passed


@pytest.mark.parametrize("missing", ["plan.json", "triples.jsonl", "resolve.json"])
def test_a_folder_that_is_not_a_finished_build_is_refused(tmp_path, missing):
    out, data = _build(tmp_path)
    (out / missing).unlink()
    with pytest.raises(FileNotFoundError):
        build_snapshot(out, data, CHUNKING)


# --- the record matching replayed under the current rules (R94) --------------------------------------------


def _relink(s, out: Path, llm=None):
    plan = ConstructionPlan.model_validate_json((out / "plan.json").read_text(encoding="utf-8"))
    return relink(s, plan, _schema(out), (90.0, 80.0), llm, "model-x")


def test_the_replay_unlinks_only_the_link_that_left_its_documents_scope(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    replay = _relink(s, out)
    lamp_lid = mention_id("Part", "lid", LAMP)
    [change] = replay.changes  # every other keyed decision is the build's own
    assert (change.mention, change.before, change.after, change.explained) == (
        lamp_lid, "Assembly:A-2", None, True,
    )  # fmt: skip
    [lid] = [a for a in replay.references if a.mention == lamp_lid]
    assert (lid.kind, lid.canonical, lid.reason) == ("individual", individual_id(lamp_lid), "no_record")
    assert replay.unexplained == [] and change.cause == "left_scope"


@pytest.mark.parametrize(
    ("component", "reason", "cause"),
    [("Hinge", "contained", "containment"), ("Lid Hnge", "name", "spelling")],
)
def test_a_link_the_build_made_by_a_retired_rule_is_unlinked_with_its_cause(
    tmp_path, component, reason, cause
):
    """R95a: the build linked the kettle's "lid hinge" to a component it does not name today: one called
    "Hinge", whose name stood inside the mention's (containment), or "Lid Hnge", 94 alike but different
    inside a word (spelling)."""
    s, out, _ = _snapshot(tmp_path)
    hinge = mention_id("Part", "lid hinge", KETTLE)
    records = [r.model_copy(update={"name": component}) if r.id == "Component:S-1" else r for r in s.records]
    built = [
        a.model_copy(update={"name": component, "reason": reason}) if a.mention == hinge else a
        for a in s.references
    ]
    replay = _relink(s.model_copy(update={"records": records, "references": built}), out)
    changes = {c.mention: c for c in replay.changes}
    assert (changes[hinge].before, changes[hinge].after, changes[hinge].cause) == (
        "Component:S-1",
        None,
        cause,
    )
    assert changes[mention_id("Part", "lid", LAMP)].cause == "left_scope"  # R94's change keeps its own cause
    assert len(changes) == 2 and replay.unexplained == []
    [alone] = [a for a in replay.references if a.mention == hinge]
    assert (alone.kind, alone.reason) == ("individual", "no_record")


def test_a_replay_that_differs_elsewhere_is_unexplained(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    shade = mention_id("Part", "shade", LAMP)
    # a build that had linked the lamp's shade to the kettle's lid: the replay links it to the lamp's Shade
    wrong = [
        a.model_copy(update={"canonical": "Assembly:A-2"}) if a.mention == shade else a for a in s.references
    ]
    [change] = [
        c for c in _relink(s.model_copy(update={"references": wrong}), out).changes if c.mention == shade
    ]
    assert (change.before, change.after, change.explained) == ("Assembly:A-2", "Assembly:A-1", False)


def _lamp_shade(s):
    """The invented build with the lamp's assembly A-1 named "Lamp Shade": the review's "shade" no longer
    names it (no rule links it), but it is a near miss in the lamp's scope."""
    # the record's name, its name column's cell, and the build's edges to it, which carry the record's name
    # as `particulars._record_assignment` writes them
    renamed = {"name": "Lamp Shade", "properties": {"part_name": "Lamp Shade"}}
    records = [r.model_copy(update=renamed) if r.id == "Assembly:A-1" else r for r in s.records]
    edges = [
        a.model_copy(update={"name": "Lamp Shade"}) if a.canonical == "Assembly:A-1" else a
        for a in s.references
    ]
    return s.model_copy(update={"records": records, "references": edges})


def test_a_near_miss_the_chooser_picks_is_linked_by_its_choice_and_none_unlinks_it(tmp_path):
    s, out, _ = _snapshot(tmp_path)
    shade = mention_id("Part", "shade", LAMP)
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        return RecordChoice(record="Assembly:A-1", quote="The shade is cracked.")

    replay = _relink(_lamp_shade(s), out, ScriptedLLM(script))
    [choice] = replay.choices
    assert (choice.mention, choice.candidates, choice.action, choice.by) == (
        shade, ["Assembly:A-1"], "chosen", "model-x",
    )  # fmt: skip
    # the same record as the build's, now reached by the choice: a change, so its edge says why it holds
    changes = {c.mention: c for c in replay.changes}
    assert (changes[shade].before, changes[shade].after, changes[shade].cause) == (
        "Assembly:A-1", "Assembly:A-1", "chosen",
    )  # fmt: skip
    [edge] = [a for a in replay.references if a.mention == shade]
    assert (edge.kind, edge.canonical, edge.reason, edge.by, edge.evidence, edge.score) == (
        "record", "Assembly:A-1", "chosen", "model-x", "The shade is cracked.", None,
    )  # fmt: skip
    assert replay.unexplained == []
    # what the LLM saw: the sentence and the record's data, as the build's graph held it
    assert "[Alder Lamp Reviews] The shade is cracked." in prompts[0]
    assert "- Assembly:A-1: Lamp Shade\n  data: part_name = Lamp Shade\n" in prompts[0]
    assert "relations: PART_OF -> Product:P-1 (Alder Lamp)" in prompts[0]

    # "none" (or no chooser at all) leaves the mention unlinked: the build's spelling link is a retired rule's
    refused = _relink(_lamp_shade(s), out, ScriptedLLM(lambda prompt, schema: RecordChoice(record="none")))
    assert [c.action for c in refused.choices] == ["none"]
    without = _relink(_lamp_shade(s), out)
    assert [c.action for c in without.choices] == ["skipped"]
    for r in (refused, without):
        changed = {c.mention: c for c in r.changes}
        assert (changed[shade].after, changed[shade].cause) == (None, "spelling") and r.unexplained == []


def _logged(tmp_path: Path, s) -> Path:
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t", build="b", git_sha="abc", runs={}, counts=snapshot_counts(s)
        ).model_dump_json(),
        encoding="utf-8",
    )
    return logged


def test_the_relink_stage_asks_the_chooser_only_with_choose_and_logs_what_it_asks_with(tmp_path):
    s, out, data = _snapshot(tmp_path)
    state = PipelineState(
        audit_source=out, data_dir=data, audit_logged=_logged(tmp_path, s), relink_choose=True
    )
    ctx = PipelineContext(
        settings=Settings(), driver=None, out=tmp_path / "relink", tracker=RecordingTracker()
    )
    with pytest.raises(LLMUnavailableError):  # --choose without a provider: refused, not silently skipped
        run_stages(ctx, state, [AuditRelinkStage()])
    tracker = RecordingTracker()
    llm = ScriptedLLM(lambda prompt, schema: RecordChoice(record="none"))
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "relink", llm=llm, tracker=tracker)
    run_stages(ctx, state, [AuditRelinkStage()])
    run = tracker.run("audit_relink")
    assert run.logged_params["choose"] == 1 and run.logged_params["model"] == ctx.settings.extract_model
    assert run.logged_params["record_choice_prompt_version"] == prompt_version(CHOICE_PROMPT)
    assert "prompts/resolve_record_choice.txt" in run.artifacts
    # the invented build has no near miss, so nothing was asked and the R94 change is the only one
    assert run.logged_metrics["choices"] == 0 and llm.calls == []
    assert run.logged_metrics["changes"] == 1.0 and run.logged_metrics["changes_chosen"] == 0.0


def test_the_relink_stage_writes_a_build_the_snapshot_reads_and_counts_resting_on_identity(tmp_path):
    s, out, data = _snapshot(tmp_path)
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t", build="b", git_sha="abc", runs={}, counts=snapshot_counts(s)
        ).model_dump_json(),
        encoding="utf-8",
    )
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "relink", tracker=tracker)
    run_stages(ctx, PipelineState(audit_source=out, data_dir=data, audit_logged=logged), [AuditRelinkStage()])
    run = tracker.run("audit_relink")
    assert run.logged_metrics["changes"] == 1.0 and run.logged_metrics["unexplained"] == 0.0
    assert (
        run.logged_metrics["changes_left_scope"] == 1.0 and run.logged_metrics["changes_containment"] == 0.0
    )
    assert run.logged_metrics["record_links_after"] == run.logged_metrics["record_links_before"] - 1
    replayed = build_snapshot(tmp_path / "relink" / RELINKED_BUILD, data, CHUNKING)
    counts = LoggedCounts.model_validate_json(
        (tmp_path / "relink" / RELINKED_LOGGED).read_text(encoding="utf-8")
    )
    assert check_fidelity(replayed, counts, None).passed  # the replayed build passes its own gate
    report = RelinkReport.model_validate_json((tmp_path / "relink" / RELINK_FILE).read_text(encoding="utf-8"))
    assert report.counts["resolve.mentions_to_records"] == [6, 5]
    assert all(name.startswith(("resolve.", "attach.")) for name in report.counts)


# --- R98: the individuals replayed, faithful to the build or measured ------------------------------------

# A person named in both reviews, once by an initial: the build joined them on the adjudicator's quotes
PERSON = {LAMP: ("Ada Lin", "Ada Lin of Northwind repaired the shade.", "shade"),
          KETTLE: ("A. Lin", "A. Lin of Northwind repaired the lid.", "lid")}  # fmt: skip
MODEL = Settings().extract_model


def _quotes(prompt: str, schema):
    """The adjudicator: the same person, quoting each side's own sentence (or, for any other pair, not)."""
    a, b = (re.search(rf"^{side}: (.+)$", prompt, re.MULTILINE).group(1) for side in "AB")
    sentence = {name: s for name, s, _ in PERSON.values()}
    if {a, b} != set(sentence):
        return SameIndividual(same=False)
    return SameIndividual(same=True, quote_a=sentence[a], quote_b=sentence[b])


def _with_person(tmp_path: Path, joined: bool = True) -> tuple[Path, Path, list[str]]:
    """The invented build with the person added to text, schema and triples, and resolve.json as `kg
    resolve` writes it: the two mentions joined (or, `joined=False`, logged apart), every record edge with
    its element id. Returns the build, its data and the person's mention ids, founder first."""
    out, data = _build(tmp_path)
    for doc, (_, sentence, _) in PERSON.items():
        (data / doc).write_text(TEXTS[doc].rstrip("\n") + f" {sentence}\n", encoding="utf-8")
    schema = json.loads((out / "text_schema.json").read_text(encoding="utf-8"))
    schema["entity_types"].append({"name": "Person", "description": "A person.", "identity": "individual"})
    schema["fact_types"].append(
        {"predicate": "REPAIRED", "subject_type": "Person", "object_type": "Part", "description": "A repair."}
    )
    (out / "text_schema.json").write_text(json.dumps(schema), encoding="utf-8")
    with (out / "triples.jsonl").open("a", encoding="utf-8") as f:
        for doc, (name, sentence, part) in PERSON.items():
            f.write(json.dumps({"subject": name, "subject_type": "Person", "predicate": "REPAIRED",
                                "object": part, "object_type": "Part", "evidence": sentence,
                                "chunk_id": f"{doc}#0"}) + "\n")  # fmt: skip
    ids = sorted(mention_id("Person", name, doc) for doc, (name, _, _) in PERSON.items())
    founder, other = ids  # one mention and two words each: the founding mention is the first by id
    said = {mention_id("Person", name, doc): name for doc, (name, _, _) in PERSON.items()}
    sentence = {mention_id("Person", name, doc): s for doc, (name, s, _) in PERSON.items()}
    evidence = f"A: {sentence[founder]} | B: {sentence[other]}"
    resolved = json.loads((out / "resolve.json").read_text(encoding="utf-8"))
    for a in resolved["assignments"]:
        if a["kind"] == "record":
            a["target"] = f"element-{a['canonical']}"
    person = {"kind": "individual", "type": "Person", "score": None, "target": None}
    resolved["assignments"] += [
        {**person, "mention": founder, "said": said[founder], "canonical": individual_id(founder),
         "name": said[founder], "reason": "own_name", "evidence": "", "by": "code"},
        {**person, "mention": other, "said": said[other], "canonical": individual_id(founder),
         "name": said[founder], "reason": "adjudicated", "evidence": evidence, "by": MODEL}
        if joined else
        {**person, "mention": other, "said": said[other], "canonical": individual_id(other),
         "name": said[other], "reason": "own_name", "evidence": "", "by": "code"},
    ]  # fmt: skip
    resolved["individual_decisions"] = [
        {"a": founder, "b": other, "a_name": said[founder], "b_name": said[other], "type": "Person",
         "signal": "variant", "action": "joined" if joined else "apart",
         "evidence": evidence if joined else "", "by": MODEL}
    ]  # fmt: skip
    (out / "resolve.json").write_text(json.dumps(resolved), encoding="utf-8")
    return out, data, ids


def _join_stage(tmp_path: Path, out: Path, data: Path, llm, faithful: bool) -> tuple[RecordingTracker, Path]:
    s = build_snapshot(out, data, CHUNKING)
    state = PipelineState(
        audit_source=out,
        data_dir=data,
        audit_logged=_logged(tmp_path, s),
        relink_join=True,
        relink_faithful=faithful,
    )
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "replay", llm=llm, tracker=tracker)
    run_stages(ctx, state, [AuditRelinkStage()])
    return tracker, tmp_path / "replay"


def test_the_faithful_replay_reproduces_a_join_the_build_made(tmp_path):
    out, data, (founder, other) = _with_person(tmp_path)
    llm = ScriptedLLM(_quotes)
    tracker, folder = _join_stage(tmp_path, out, data, llm, faithful=True)
    run = tracker.run("audit_relink")
    assert run.logged_metrics["faithful"] == 1 and run.logged_metrics["faithful_issues"] == 0
    assert run.logged_metrics["decision_joined"] == 1 and run.logged_params["join"] == 1
    assert run.logged_params["individual_prompt_version"] == prompt_version(IDENTITY_PROMPT)
    report = ReidentifyReport.model_validate_json((folder / REIDENTIFY_FILE).read_text(encoding="utf-8"))
    assert report.faithful and report.issues == [] and [d.action for d in report.decisions] == ["joined"]
    # the person was asked about once; the records are the build's own, so the lamp's planted "lid" stays
    # on the kettle's lid record and is never asked about
    assert llm.calls == [("SameIndividual", MODEL)]
    assert not (folder / RELINKED_BUILD).exists()  # the gate writes no build


def test_the_faithful_replay_refuses_a_build_it_does_not_reproduce(tmp_path):
    out, data, (founder, other) = _with_person(tmp_path, joined=False)  # logged apart; the adjudicator joins
    with pytest.raises(EvaluationError) as refused:
        _join_stage(tmp_path, out, data, ScriptedLLM(_quotes), faithful=True)
    issues = refused.value.issues
    assert any("individual decision 0" in i and "action 'apart' -> 'joined'" in i for i in issues)
    assert any(f"assignment of {other}" in i and "reason 'own_name' -> 'adjudicated'" in i for i in issues)
    report = ReidentifyReport.model_validate_json(
        (tmp_path / "replay" / REIDENTIFY_FILE).read_text(encoding="utf-8")
    )
    assert report.issues == issues  # the report is written before the refusal, for the reader


def test_the_measured_replay_explains_a_join_it_undoes_and_writes_its_decisions(tmp_path):
    out, data, (founder, other) = _with_person(tmp_path)
    apart = ScriptedLLM(lambda prompt, schema: SameIndividual(same=False))
    tracker, folder = _join_stage(tmp_path, out, data, apart, faithful=False)
    run = tracker.run("audit_relink")
    # R94's change of the lamp's "lid" keeps its own cause; the person's second mention is unjoined
    assert run.logged_metrics["changes_left_scope"] == 1 and run.logged_metrics["changes_unjoined"] == 1
    assert run.logged_metrics["identity_unexplained"] == 0 and run.logged_metrics["decision_joined"] == 0
    report = ReidentifyReport.model_validate_json((folder / REIDENTIFY_FILE).read_text(encoding="utf-8"))
    [change] = report.changes  # the founder keeps its canonical entity
    assert (change.mention, change.before, change.after, change.cause) == (
        other, individual_id(founder), individual_id(other), "unjoined",
    )  # fmt: skip
    resolved = json.loads((folder / RELINKED_BUILD / "resolve.json").read_text(encoding="utf-8"))
    names = {name for name, _, _ in PERSON.values()}
    [person] = [d for d in resolved["individual_decisions"] if {d["a_name"], d["b_name"]} == names]
    assert person["action"] == "apart"
    edges = {a["mention"]: a for a in resolved["assignments"]}
    assert edges[mention_id("Part", "lid", KETTLE)]["target"] == "element-Assembly:A-2"  # the build's element
    replayed = build_snapshot(folder / RELINKED_BUILD, data, CHUNKING)
    counts = LoggedCounts.model_validate_json((folder / RELINKED_LOGGED).read_text(encoding="utf-8"))
    assert check_fidelity(replayed, counts, None).passed
