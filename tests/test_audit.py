"""Tests of the graph audit's part a (R87): the offline snapshot, the fidelity gate and the code checks.

Each test builds a small invented build folder (two product reviews, three record tables, triples, a
resolve.json) in a temporary directory: no Neo4j, no LLM. The build plants one error of each kind the
checks must find: a mention of the lamp's review linked to the kettle's lid (outside the lamp's scope),
and a claim about the kettle's lid hinge hung on the lid because "lid" stands inside "lid hinge". The
replay of R94 must unlink exactly that lamp mention under the current matching rules, and call any other
difference unexplained.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.audit import build_snapshot, check_fidelity, compute_reach, gold_pairs, run_checks
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.audit.inputs import read_records, read_relations, scopes
from kgbuilder.audit.relink import RelinkReport, relink
from kgbuilder.config import Settings
from kgbuilder.core.identity import concept_id, individual_id, mention_id
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.audit_stages import (
    CHECKS_FILE,
    RELINK_FILE,
    RELINKED_BUILD,
    RELINKED_LOGGED,
    AuditRelinkStage,
    AuditSnapshotStage,
)
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.schema import TextSchema
from tests.fakes import RecordingTracker

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


def _relink(s, out: Path):
    plan = ConstructionPlan.model_validate_json((out / "plan.json").read_text(encoding="utf-8"))
    return relink(s, plan, _schema(out), threshold=90.0)


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
    assert replay.unexplained == []


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
    assert run.logged_metrics["record_links_after"] == run.logged_metrics["record_links_before"] - 1
    replayed = build_snapshot(tmp_path / "relink" / RELINKED_BUILD, data, CHUNKING)
    counts = LoggedCounts.model_validate_json(
        (tmp_path / "relink" / RELINKED_LOGGED).read_text(encoding="utf-8")
    )
    assert check_fidelity(replayed, counts, None).passed  # the replayed build passes its own gate
    report = RelinkReport.model_validate_json((tmp_path / "relink" / RELINK_FILE).read_text(encoding="utf-8"))
    assert report.counts["resolve.mentions_to_records"] == [6, 5]
    assert all(name.startswith(("resolve.", "attach.")) for name in report.counts)
