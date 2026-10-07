"""The mention pass (R101, text/mention_pass.py): every code-visible Out rule of the definition as a rejection
reason, the type a finding is stored with given its two answers (R104: the proposed type only when it can
hold the stated class), the pass over chunks with a scripted LLM, the rows it writes (one mention per document
and name, an existing mention's type winning, the stated class kept), the prompt's corpus-language guard, the
built-in fallback types, the audit snapshot replaying a build's pass file (or other findings in its place,
R105), the stage on Neo4j (params, metrics, artifacts), and the offline replay of the pass on a finished
build (R105: the graph as the pass found it, behind the C0 gate, nothing written but files).

The text is an invented observatory log (as the definition's examples, tests/gold/r101/rules.md). Only the
stage test needs Neo4j. The R102 rebuilds' logged counts (tests/gold/r102) must show the r77d claims replayed
unchanged and the pass's counts; R105's replayed findings (tests/gold/r105) must hold what their runs counted.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.audit import build_snapshot
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError, LLMResponseError
from kgbuilder.core.identity import mention_id
from kgbuilder.core.values import VALUE_TYPE
from kgbuilder.llm.base import prompt_version
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.mention_stages import ReplayMentionPassStage
from kgbuilder.pipeline.runner import FULL_PIPELINE, run_stages
from kgbuilder.pipeline.stage import TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.mention_pass import (
    MENTION_PROMPT,
    NO_TYPE,
    PASS_FILE,
    FoundThing,
    FoundThings,
    PassFinding,
    filed_type,
    find_mentions,
    pass_rows,
    read_findings,
    verify_found,
)
from kgbuilder.text.schema import (
    FALLBACK_TYPES,
    KIND_TYPE,
    PARTICULAR_TYPE,
    EntityType,
    FactType,
    TextSchema,
    validate_text_schema,
)
from kgbuilder.text.subject_graph import MentionRow, write_subject_graph

from .evaluation_corpora import quoted_four_grams
from .fakes import RecordingTracker, ScriptedLLM
from .test_audit import CHUNKING, KETTLE, LAMP, _logged
from .test_audit import _build as audit_build

LOG = "notes/night_log.md"
TEXT = (
    "No condensation was found on the mirror of North Dome. It was quiet at 14:00 on 3 May. "
    "The dome, the mirror and the shutter motor were checked."
)
CHUNK = Chunk(chunk_id=f"{LOG}#0", doc_id=LOG, index=0, text=TEXT, context="Night log")
SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Instrument", description="A piece of equipment."),
        EntityType(name="Person", description="A person.", identity="individual"),
        EntityType(name="Building", description="A building of the site.", identity="individual"),
        EntityType(
            name="Telescope",
            description="A telescope of the site.",
            identity="keyed",
            record_labels=["Scope"],
        ),
    ],
    fact_types=[FactType(predicate="USES", subject_type="Person", object_type="Instrument", description="d")],
)


@pytest.mark.parametrize(
    ("name", "type_", "stored", "reason"),
    [
        ("condensation", NO_TYPE, "condensation", None),  # absent, and still a mention
        ("the mirror", "Instrument", "mirror", None),  # the article is dropped, as names are written
        ("Night log", NO_TYPE, "Night log", None),  # the document's name counts (the C1 rule)
        ("North Dome", VALUE_TYPE, "North Dome", "value_type"),
        ("North Dome", "Vessel", "North Dome", "unknown_type"),
        ("North Dome", PARTICULAR_TYPE, "North Dome", "unknown_type"),  # a fallback is code's to give (R104)
        ("telescope", NO_TYPE, "telescope", "not_in_text"),
        ("mirr", NO_TYPE, "mirr", "not_in_text"),  # not whole words
        ("condensation was found on the mirror of", NO_TYPE, "condensation was found on the mirror of",
         "too_long"),
        ("dome, the mirror", NO_TYPE, "dome, the mirror", "clause"),
        ("It", NO_TYPE, "It", "pronoun"),
        ("on the", NO_TYPE, "on the", "function_words"),
        ("14:00", NO_TYPE, "14:00", "quantity_or_date"),
        ("3 May", NO_TYPE, "3 May", "quantity_or_date"),
        ("North Dome", "Building", "North Dome", "already_listed"),
    ],
)  # fmt: skip
def test_every_code_visible_out_rule_is_a_rejection_reason(name, type_, stored, reason):
    found = verify_found(
        FoundThing(name=name, mention_class="kind", type=type_), CHUNK, {"north dome"}, SCHEMA
    )
    assert found == (stored, reason)


@pytest.mark.parametrize(
    ("mention_class", "type_", "stored"),
    [
        ("particular", "Building", "Building"),  # an individual type holds a named thing ("North Dome")
        ("kind", "Building", KIND_TYPE),  # ... never a common noun ("the dome"): the class's fallback
        ("kind", "Instrument", "Instrument"),  # a concept type holds a kind
        ("particular", "Instrument", PARTICULAR_TYPE),  # ... never a named thing
        ("kind", "Telescope", "Telescope"),  # a keyed type holds both: a piece a record stands for
        ("particular", "Telescope", "Telescope"),
        ("particular", NO_TYPE, PARTICULAR_TYPE),  # no type fits: the class decides
        ("kind", NO_TYPE, KIND_TYPE),
    ],
)
def test_the_stored_type_is_the_proposed_one_only_when_it_can_hold_the_stated_class(
    mention_class, type_, stored
):
    assert filed_type(FoundThing(name="dome", mention_class=mention_class, type=type_), SCHEMA) == stored


def test_the_pass_asks_per_chunk_verifies_every_finding_and_survives_a_failed_call():
    other = Chunk(chunk_id="notes/other.md#0", doc_id="notes/other.md", index=0, text="The shutter jammed.")
    prompts: list[str] = []

    def script(prompt: str, schema: type) -> FoundThings:
        prompts.append(prompt)
        if "jammed" in prompt:
            raise LLMResponseError("model failed 3 times for FoundThings")
        return FoundThings(
            things=[
                FoundThing(name="shutter motor", mention_class="kind", type="Instrument"),
                FoundThing(name="Shutter motor", mention_class="kind", type=NO_TYPE),  # the same name again
                FoundThing(name="It", mention_class="kind", type=NO_TYPE),
                FoundThing(name="dome", mention_class="kind", type="Building"),  # a common noun: filed a kind
            ]
        )

    outcome = find_mentions(
        [CHUNK, other], {CHUNK.chunk_id: ["North Dome"]}, SCHEMA, ScriptedLLM(script), "m"
    )
    assert outcome.found == 4 and outcome.failed == 1
    assert [(f.name, f.type, f.mention_class, f.proposed_type) for f in outcome.accepted] == [
        ("shutter motor", "Instrument", "kind", "Instrument"),
        ("dome", KIND_TYPE, "kind", "Building"),
    ]
    assert [(r.name, r.proposed_type, r.reason) for r in outcome.rejected] == [
        ("Shutter motor", NO_TYPE, "duplicate"),
        ("It", NO_TYPE, "pronoun"),
    ]
    prompt = next(p for p in prompts if "North Dome" in p)
    assert "Already listed (do not list these again): North Dome" in prompt
    assert "- Instrument: A piece of equipment." in prompt and f"- {VALUE_TYPE}:" not in prompt
    # the fallback types are not offered: the class is its own answer, and "none" stands for no type
    assert f"- {PARTICULAR_TYPE}:" not in prompt and f"- {KIND_TYPE}:" not in prompt
    assert f'"{NO_TYPE}" when no' in prompt and "Text (Night log):" in prompt


def test_one_mention_per_document_and_name_an_existing_mentions_type_winning():
    claimed = MentionRow(
        id=mention_id("Instrument", "mirror", LOG), name="mirror", type="Instrument", doc_id=LOG
    )
    existing_pairs = {(f"{LOG}#0", claimed.id)}
    findings = [
        PassFinding(chunk_id=f"{LOG}#0", name="Mirror", type=KIND_TYPE),  # the claim's mention, same chunk
        PassFinding(chunk_id=f"{LOG}#1", name="mirror", type=KIND_TYPE),  # the claim's mention, a new chunk
        PassFinding(chunk_id=f"{LOG}#0", name="condensation", type=KIND_TYPE, mention_class="kind"),
        PassFinding(  # the pass's own, again: the first finding's type and class stay
            chunk_id=f"{LOG}#1", name="Condensation", type=PARTICULAR_TYPE, mention_class="particular"
        ),
        PassFinding(chunk_id="notes/other.md#0", name="condensation", type=KIND_TYPE),  # another document
    ]
    rows = pass_rows(findings, [claimed], existing_pairs)
    made = {(m.name, m.type, m.doc_id) for m in rows.mentions}
    assert made == {("condensation", KIND_TYPE, LOG), ("condensation", KIND_TYPE, "notes/other.md")}
    condensation = mention_id(KIND_TYPE, "condensation", LOG)
    assert rows.mentioned_in == [
        (f"{LOG}#1", claimed.id),  # the claim's mirror reached from a new chunk: its type, Instrument, wins
        (f"{LOG}#0", condensation),
        (f"{LOG}#1", condensation),
        ("notes/other.md#0", mention_id(KIND_TYPE, "condensation", "notes/other.md")),
    ]
    assert rows.reused == 1
    # the stated class of each new mention; a finding of a pre-R104 pass file states none
    assert rows.classes == {condensation: "kind"}


def test_the_prompt_speaks_no_corpus_language():
    text = MENTION_PROMPT + json.dumps(FoundThings.model_json_schema())
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "pump", "staff")
    words = set(text.lower().replace('"', " ").split())
    assert not [w for w in banned if w in words or f"{w}s" in words]
    assert quoted_four_grams(text) == []


def test_the_fallback_types_are_built_in_with_their_identity_class():
    assert SCHEMA.identity_of(PARTICULAR_TYPE) == "individual" and SCHEMA.identity_of(KIND_TYPE) == "concept"
    assert set(FALLBACK_TYPES) == {PARTICULAR_TYPE, KIND_TYPE}
    clash = SCHEMA.model_copy(
        update={"entity_types": [*SCHEMA.entity_types, EntityType(name=KIND_TYPE, description="d")]}
    )
    assert f"entity type '{KIND_TYPE}' is built in; do not define it" in validate_text_schema(clash)


def test_the_pass_runs_after_link_and_before_resolve():
    names = [s.name for s in FULL_PIPELINE]
    assert names.index("link") < names.index("mention_pass") < names.index("resolve")


def test_the_snapshot_replays_a_builds_pass_file(tmp_path):
    out, data = audit_build(tmp_path)
    findings = [  # crack: no claim names it; lid: the claim's mention, same chunk
        PassFinding(
            chunk_id=f"{LAMP}#0", name="crack", type=KIND_TYPE, mention_class="kind", proposed_type=NO_TYPE
        ),
        PassFinding(chunk_id=f"{KETTLE}#0", name="lid", type=KIND_TYPE),
    ]
    (out / PASS_FILE).write_text("\n".join(f.model_dump_json() for f in findings), encoding="utf-8")
    lamp = mention_id(KIND_TYPE, "crack", LAMP)
    resolved = json.loads((out / "resolve.json").read_text(encoding="utf-8"))
    resolved["assignments"].append(
        {"mention": lamp, "said": "crack", "kind": "concept", "canonical": "c1", "name": "crack",
         "type": KIND_TYPE, "reason": "same_name"}
    )  # fmt: skip
    (out / "resolve.json").write_text(json.dumps(resolved), encoding="utf-8")
    s = build_snapshot(out, data, CHUNKING)
    [passed] = [m for m in s.mentions if m.found_by_pass]
    assert (passed.id, passed.chunks, passed.derived) == (lamp, [f"{LAMP}#0"], False)
    assert passed.stated_class == "kind"
    assert {m.stated_class for m in s.mentions if not m.found_by_pass} == {None}
    counts = snapshot_counts(s)
    passes = [counts[f"mention_pass.{k}"] for k in ("mention_nodes", "mentions", "reused")]
    assert passes == [1, 1, 0]
    assert counts["extract.mention_nodes"] == sum(not (m.derived or m.found_by_pass) for m in s.mentions)
    # R105: findings given in place of the build's pass file; none at all is the graph the pass found
    before = build_snapshot(out, data, CHUNKING, findings=[])
    assert not any(m.found_by_pass for m in before.mentions) and before.pass_mentions_edges == 0
    other = [PassFinding(chunk_id=f"{KETTLE}#0", name="hinge", type="Part", mention_class="kind")]
    instead = build_snapshot(out, data, CHUNKING, findings=other)
    assert [(m.name, m.stated_class) for m in instead.mentions if m.found_by_pass] == [("hinge", "kind")]


@pytest.mark.neo4j
def test_the_stage_writes_the_new_mentions_and_logs_what_it_asked_and_refused(driver, tmp_path):
    write_lexical_graph(driver, [Document(doc_id=LOG, title="Night log", text=TEXT)], [CHUNK])
    triple = Triple(
        subject="North Dome", subject_type="Person", predicate="USES", object="mirror",
        object_type="Instrument", evidence="No condensation was found on the mirror of North Dome.",
        chunk_id=CHUNK.chunk_id,
    )  # fmt: skip
    write_subject_graph(driver, [triple], extractor="test")
    out = tmp_path / "out"
    out.mkdir()
    (out / TEXT_SCHEMA_FILE).write_text(SCHEMA.model_dump_json(), encoding="utf-8")
    llm = ScriptedLLM(
        lambda prompt, schema: FoundThings(
            things=[
                FoundThing(name="condensation", mention_class="kind", type=NO_TYPE),
                FoundThing(name="mirror", mention_class="kind", type="Instrument"),
                FoundThing(name="dome", mention_class="kind", type="Building"),
            ]
        )
    )
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker, llm=llm)
    run_stages(ctx, PipelineState(), [st.MentionPassStage()])
    run = tracker.run("mention_pass")
    assert run.logged_params["prompt_version"] == prompt_version(MENTION_PROMPT)
    assert run.logged_params["fallback_types"] == "Kind,Particular"
    m = run.logged_metrics
    counts = ["found", "accepted", "rejected_already_listed", "mention_nodes", "mentions"]
    assert [m[k] for k in counts] == [3, 2, 1, 2, 2]
    assert (m["particular"], m["retyped"]) == (0, 1)  # "dome", a kind typed as a building, is retyped
    assert {"prompts/mention_pass.txt", str(out / PASS_FILE)} <= set(run.artifacts)
    rows = driver.execute_query(
        "MATCH (:Chunk {chunk_id: $c})-[:MENTIONS]->(m:Mention) WHERE m.name IN ['condensation', 'dome'] "
        "RETURN m.name AS name, m.type AS t ORDER BY name",
        c=CHUNK.chunk_id,
    )[0]
    assert [(r["name"], r["t"]) for r in rows] == [("condensation", KIND_TYPE), ("dome", KIND_TYPE)]
    lines = [json.loads(x) for x in (out / PASS_FILE).read_text("utf-8").splitlines()]
    assert [(x["name"], x["type"], x["mention_class"], x["proposed_type"]) for x in lines] == [
        ("condensation", KIND_TYPE, "kind", NO_TYPE),
        ("dome", KIND_TYPE, "kind", "Building"),
    ]


def _replay(tmp_path, logged: Path, script) -> tuple[RecordingTracker, Path]:
    """`kg mention-pass --from-build` on the audit's invented build, no graph: the tracker and the out dir."""
    tracker, out = RecordingTracker(), tmp_path / "replay"
    ctx = PipelineContext(settings=Settings(), driver=None, out=out, tracker=tracker, llm=ScriptedLLM(script))
    state = PipelineState(audit_source=tmp_path / "build", data_dir=tmp_path / "data", audit_logged=logged)
    run_stages(ctx, state, [ReplayMentionPassStage()])
    return tracker, out


def test_the_pass_replays_offline_on_a_builds_graph_as_the_pass_found_it(tmp_path):
    out, data = audit_build(tmp_path)
    old = [PassFinding(chunk_id=f"{LAMP}#0", name="crack", type=KIND_TYPE)]  # the build's own pass
    (out / PASS_FILE).write_text("\n".join(f.model_dump_json() for f in old), encoding="utf-8")
    resolved = json.loads((out / "resolve.json").read_text(encoding="utf-8"))  # resolve saw the old pass
    resolved["assignments"].append(
        {"mention": mention_id(KIND_TYPE, "crack", LAMP), "said": "crack", "kind": "concept",
         "canonical": "c1", "name": "crack", "type": KIND_TYPE, "reason": "same_name"}
    )  # fmt: skip
    (out / "resolve.json").write_text(json.dumps(resolved), encoding="utf-8")
    logged = _logged(tmp_path, build_snapshot(out, data, CHUNKING))
    prompts: list[str] = []

    def script(prompt: str, schema: type) -> FoundThings:
        prompts.append(prompt)
        if "Alder Lamp" in prompt:  # "shade" is a claim's mention of the chunk: already listed
            return FoundThings(things=[
                FoundThing(name="shade", mention_class="kind", type="Part"),
                FoundThing(name="Lamp", mention_class="kind", type=NO_TYPE),
            ])  # fmt: skip
        return FoundThings(things=[FoundThing(name="hinge", mention_class="kind", type="Part")])

    tracker, replay = _replay(tmp_path, logged, script)
    run = tracker.run("mention_pass")
    assert run.logged_params["build"] == out and "logged_hash" in run.logged_params
    assert run.logged_params["prompt_version"] == prompt_version(MENTION_PROMPT)
    m = run.logged_metrics
    assert [m[k] for k in ("chunks", "found", "accepted", "rejected_already_listed", "mention_nodes")] == [
        2, 3, 2, 1, 2,
    ]  # fmt: skip
    # the names the prompt calls listed are the claims' and derivation's, never the build's own pass
    lamp = next(p for p in prompts if "Alder Lamp" in p)
    listed = lamp.split("do not list these again): ")[1].splitlines()[0].split("; ")
    assert listed == ["Alder Lamp", "cracked", "lid", "loose", "shade"]  # no "crack": that was the old pass
    lines = [json.loads(x) for x in (replay / PASS_FILE).read_text("utf-8").splitlines()]
    assert [(x["name"], x["type"]) for x in lines] == [("Lamp", KIND_TYPE), ("hinge", "Part")]


def test_the_replay_refuses_a_build_whose_snapshot_is_not_its_graph(tmp_path):
    out, data = audit_build(tmp_path)
    logged = _logged(tmp_path, build_snapshot(out, data, CHUNKING))
    counts = json.loads(logged.read_text(encoding="utf-8"))
    counts["counts"]["extract.mention_nodes"] += 1  # the build logged a graph the snapshot is not
    logged.write_text(json.dumps(counts), encoding="utf-8")
    with pytest.raises(EvaluationError):
        _replay(tmp_path, logged, lambda prompt, schema: FoundThings())


R102 = Path(__file__).resolve().parent / "gold" / "r102"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r102_rebuild_logged_the_replayed_claims_and_the_pass(dataset):
    """The rebuilds' logged counts (copied from MLflow): the r77d claims replayed unchanged, the pass's own
    counts present, and the cost the runs index reports."""
    runs = json.loads((R102 / "runs.json").read_text(encoding="utf-8"))["datasets"][dataset]
    logged = LoggedCounts.model_validate_json((R102 / f"{dataset}_logged.json").read_text(encoding="utf-8"))
    r77d = LoggedCounts.model_validate_json(
        (R102.parent / "r87" / f"{dataset}_logged.json").read_text(encoding="utf-8")
    )
    before_pass = [k for k in r77d.counts if k.startswith(("ingest_text.", "extract.", "link."))]
    assert {k: logged.counts[k] for k in before_pass} == {k: r77d.counts[k] for k in before_pass}
    assert logged.counts["mention_pass.mention_nodes"] > 0
    cost = sum(v for k, v in logged.usage.items() if k.endswith("cost_usd"))
    assert cost == pytest.approx(runs["cost_usd"]) and set(logged.runs) == set(runs["runs"])


R105 = R102.parent / "r105"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r105_replays_committed_the_findings_their_runs_logged(dataset):
    """The replays' findings files (copied from out/r105_<dataset>) hold what their MLflow runs counted: the
    accepted findings, the stated particulars, the retyped ones and the refusals; the total cost adds up."""
    index = json.loads((R105 / "runs.json").read_text(encoding="utf-8"))
    counts = index["datasets"][dataset]["counts"]
    findings = read_findings(R105 / dataset / "pass_findings.jsonl")
    assert len(findings) == counts["accepted"]
    assert sum(f.mention_class == "particular" for f in findings) == counts["particular"]
    assert sum(f.proposed_type not in (NO_TYPE, f.type) for f in findings) == counts["retyped"]
    rejected = (R105 / dataset / "pass_rejected.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([x for x in rejected if x]) == counts["found"] - counts["accepted"]
    total = sum(d["cost_usd"] for d in index["datasets"].values())
    assert total == pytest.approx(index["cost_usd_total"])
