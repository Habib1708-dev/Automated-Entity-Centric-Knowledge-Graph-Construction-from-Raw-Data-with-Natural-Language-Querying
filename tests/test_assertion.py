"""A claim's assertion (R77, layered-model Step 7): truth, modality and condition. Pure tests for the word
checks of `verify`, the observation id, repeats within a chunk, the query plan's filters and the scorer
of the assertion gold; Neo4j tests (the `driver` fixture) for what the subject graph stores, what the fact
reader and the judge sheet read back, and which claims a query plan finds by default. Examples are invented
(a kettle), like the prompt's."""

import hashlib
import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.core.identity import observation_id
from kgbuilder.core.text import norm
from kgbuilder.graph.connection import open_driver
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.query.plan import check_plan
from kgbuilder.text import extraction
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.extraction import (
    ChunkExtraction,
    RawTriple,
    RejectionReason,
    Triple,
    extract_chunk,
    verify,
)
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.text.subject_graph import write_subject_graph
from kgbuilder.validation.assertion import (
    AssertionGold,
    AssertionVerdicts,
    FieldsKept,
    GoldClaim,
    GoldSentence,
    MatchedClaim,
    MatchedSentence,
    score_assertion,
)
from kgbuilder.validation.checks.base import CheckContext, StoredFact
from kgbuilder.validation.coverage_sheet import build_coverage_sheet
from kgbuilder.validation.judge import JudgeMeta, build_sheet, fact_id
from kgbuilder.validation.sentences import draw_sample, sentence_id

from .evaluation_corpora import quoted_four_grams
from .fakes import RecordingTracker, ScriptedLLM
from .test_query_plan import GRAPH, plan
from .test_query_plan_graph import _with, run, runner, runner_parts  # noqa: F401 (a fixture)

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Component", description="a part of a thing"),
        EntityType(name="Behaviour", description="what a part does"),
    ],
    fact_types=[
        FactType(predicate="SHOWS", subject_type="Component", object_type="Behaviour", description="d"),
    ],
)
DOC = "kettle_notes.md"
CHUNK = (
    "The lid never leaked. The spout doesnt drip. The base may crack in frost. "
    "The kettle whistles when the water boils. The handle got hot."
)


def raw(evidence: str, obj: str = "leak", subject: str = "lid", **assertion) -> RawTriple:
    return RawTriple(
        subject=subject, subject_type="Component", predicate="SHOWS", object=obj, object_type="Behaviour",
        evidence=evidence, **assertion,
    )  # fmt: skip


def triple(evidence: str, obj: str = "leak", subject: str = "lid", **assertion) -> Triple:
    return Triple(**raw(evidence, obj, subject, **assertion).model_dump(), chunk_id=f"{DOC}#0")


# --- verification -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("triple_", "reason"),
    [
        # "got hot" denies nothing, so a negated claim cannot rest on it
        (
            raw("The handle got hot", "hot", "handle", truth="negated"),
            RejectionReason.NEGATION_NOT_IN_EVIDENCE,
        ),
        (raw("The lid never leaked", modality="possible"), RejectionReason.MODALITY_NOT_IN_EVIDENCE),
        # a conditional claim needs a condition, from the quote, with a word of condition in it
        (
            raw("The kettle whistles when the water boils", "whistles", "kettle", modality="conditional"),
            RejectionReason.CONDITION_NOT_IN_EVIDENCE,
        ),
        (
            raw(
                "The kettle whistles when the water boils", "whistles", "kettle", modality="conditional",
                condition="when the lid is open",
            ),
            RejectionReason.CONDITION_NOT_IN_EVIDENCE,
        ),
        (
            raw(
                "The kettle whistles when the water boils", "whistles", "kettle", modality="conditional",
                condition="the water boils",
            ),
            RejectionReason.CONDITION_NOT_IN_EVIDENCE,
        ),
        # a condition on a claim that is not conditional: code cannot tell which label is wrong
        (
            raw(
                "The kettle whistles when the water boils", "whistles", "kettle",
                condition="when the water boils",
            ),
            RejectionReason.CONDITION_NOT_CONDITIONAL,
        ),
    ],
)  # fmt: skip
def test_a_negated_possible_or_conditional_claim_needs_its_word_in_the_quote(triple_, reason):
    assert verify(triple_, CHUNK, SCHEMA).reason == reason


def test_what_the_quote_states_passes_with_its_assertion():
    assert verify(raw("The lid never leaked", truth="negated"), CHUNK, SCHEMA) is None
    # informal text drops the apostrophe of "doesn't"
    assert verify(raw("The spout doesnt drip", "drip", "spout", truth="negated"), CHUNK, SCHEMA) is None
    assert (
        verify(raw("The base may crack in frost", "crack", "base", modality="possible"), CHUNK, SCHEMA)
        is None
    )
    whistles = raw(
        "The kettle whistles when the water boils", "whistles", "kettle", modality="conditional",
        condition="when the water boils",
    )  # fmt: skip
    assert verify(whistles, CHUNK, SCHEMA) is None
    assert extraction._negates(extraction._words(norm("we couldn't get it to close")))


# --- ids and repeats ------------------------------------------------------------------------------------


def test_an_affirmed_actual_claim_keeps_its_id_and_each_label_makes_another_claim():
    # the id as R44-R76 computed it: every verdict file before R77 refers to claims by it
    before = hashlib.sha1("|".join([f"{DOC}#0", "SHOWS", "lid", "leak"]).encode()).hexdigest()[:12]
    assert observation_id(f"{DOC}#0", "SHOWS", "lid", "leak") == before
    ids = {
        observation_id(f"{DOC}#0", "SHOWS", "lid", "leak", **labels)
        for labels in (
            {},
            {"truth": "negated"},
            {"modality": "possible"},
            {"modality": "conditional", "condition": "when the water boils"},
            {"modality": "conditional", "condition": "when it is cold"},
        )
    }
    assert len(ids) == 5
    # a timed claim keeps its R66 id as well
    timed = "|".join([f"{DOC}#0", "SHOWS", "lid", "leak", "after a week"])
    assert (
        observation_id(f"{DOC}#0", "SHOWS", "lid", "leak", "after a week")
        == (hashlib.sha1(timed.encode()).hexdigest()[:12])
    )


def test_a_claim_and_its_denial_in_one_chunk_are_two_claims():
    chunk = Chunk(chunk_id=f"{DOC}#0", doc_id=DOC, index=0, text=CHUNK, context="Kettle notes")
    reply = ChunkExtraction(
        triples=[
            raw("The lid never leaked", truth="negated"),
            raw("The lid never leaked"),  # the model's two readings of one quote: code keeps both
            raw("The lid never leaked", truth="negated"),  # a repeat
        ]
    )
    result = extract_chunk(chunk, SCHEMA, ScriptedLLM(lambda p, s: reply), "m")
    assert [t.truth for t in result.triples] == ["negated", "affirmed"]


# --- the graph ------------------------------------------------------------------------------------------


@pytest.mark.neo4j
def test_the_subject_graph_stores_the_assertion_and_the_readers_read_it_back(driver):
    driver.execute_query("CREATE (:Chunk {chunk_id: $c, text: $t})", c=f"{DOC}#0", t=CHUNK)
    whistles = triple(
        "The kettle whistles when the water boils", "whistles", "kettle", modality="conditional",
        condition="when the water boils",
    )  # fmt: skip
    written = [
        triple("The lid never leaked", truth="negated"),
        triple("The base may crack in frost", "crack", "base", modality="possible"),
        whistles,
        triple("The handle got hot", "hot", "handle"),
    ]
    counts = write_subject_graph(driver, written, extractor="m")
    assert (counts.observations_negated, counts.observations_possible, counts.observations_conditional) == (
        1, 1, 1,
    )  # fmt: skip
    stored, _, _ = driver.execute_query(
        "MATCH (o:Observation) RETURN o.id AS id, o.truth AS truth, o.modality AS modality, "
        "o.condition AS condition ORDER BY o.object_name"
    )
    assert [(r["truth"], r["modality"], r["condition"]) for r in stored] == [
        ("affirmed", "possible", ""),
        ("affirmed", "actual", ""),
        ("negated", "actual", ""),
        ("affirmed", "conditional", "when the water boils"),
    ]
    # the fact reader flattens them, and the judge sheet rebuilds every stored id from them
    facts = CheckContext(driver=driver).facts
    assert {(f.own_object, f.truth, f.modality, f.condition) for f in facts} == {
        ("leak", "negated", "actual", ""),
        ("crack", "affirmed", "possible", ""),
        ("whistles", "affirmed", "conditional", "when the water boils"),
        ("hot", "affirmed", "actual", ""),
    }
    assert {fact_id(f) for f in facts} == {r["id"] for r in stored}
    sheet = build_sheet(facts, [])
    assert {(f.id, f.truth, f.modality) for f in sheet.facts} <= {
        (r["id"], r["truth"], r["modality"]) for r in stored
    }


@pytest.mark.neo4j
def test_a_claim_stored_before_r77_reads_as_affirmed_and_actual(driver):
    driver.execute_query(
        "CREATE (c:Chunk {chunk_id: 'a.md#0'}), (s:Mention {id: 's', type: 'Component', name: 'lid'}), "
        "(t:Mention {id: 't', type: 'Behaviour', name: 'leak'}), "
        "(o:Observation {id: 'o', predicate: 'SHOWS', chunk_id: 'a.md#0', evidence: 'x', "
        "subject_name: 'lid', object_name: 'leak'}), "
        "(o)-[:SUBJECT]->(s), (o)-[:OBJECT]->(t), (o)-[:FROM]->(c)"
    )
    [fact] = CheckContext(driver=driver).facts
    assert (fact.truth, fact.modality, fact.condition) == ("affirmed", "actual", "")


# --- query plans ----------------------------------------------------------------------------------------


def test_a_denied_or_possible_claim_is_asked_for_only_with_the_questions_words():
    invented = plan(
        {"op": "find_claims", "predicate": "HAS_CONDITION", "truth": "negated", "truth_words": "never",
         "modality": "possible", "modality_words": "might"},
        {"op": "count", "input": 0},
    )  # fmt: skip
    checked = check_plan(invented, GRAPH, "How many presses wobble?")
    step = checked.plan.steps[0]
    assert checked.issues == [] and (step.truth, step.modality) == ("affirmed", "actual")
    assert len(checked.dropped) == 2
    asked = check_plan(invented, GRAPH, "How many presses might never wobble?")
    assert asked.dropped == [] and (asked.plan.steps[0].truth, asked.plan.steps[0].modality) == (
        "negated", "possible",
    )  # fmt: skip


@pytest.mark.neo4j
def test_a_plan_counts_the_claims_that_hold_unless_the_question_asks_for_others(runner_parts, driver):  # noqa: F811
    # the test graph's claim o1 ("spindle wobbles", stored before R77) holds; two more claims of the same
    # kind on the same press: one the text denies, one it calls possible
    store, _ = runner_parts
    schema = _with(
        driver,
        store,
        "MATCH (p:Press {press_id: 'P1'}), (o1:Observation {id: 'o1'})-[:SUBJECT]->(s), (o1)-[:OBJECT]->(w), "
        "(o1)-[:FROM]->(c) "
        "UNWIND [['o2', 'negated', 'actual'], ['o3', 'affirmed', 'possible']] AS x "
        "CREATE (o:Observation {id: x[0], predicate: 'HAS_CONDITION', truth: x[1], modality: x[2]}), "
        "(o)-[:SUBJECT]->(s), (o)-[:OBJECT]->(w), (o)-[:FROM]->(c), (p)-[:HAS_OBSERVATION]->(o)",
    )
    r = runner(store, schema)
    press = {"op": "find_entity", "name": "quill press", "label": "Press"}
    count = {"op": "count", "input": 1}

    def claims(question: str, **assertion) -> float | None:
        step = {"op": "find_claims", "input": 0, "predicate": "HAS_CONDITION", **assertion}
        return run(r, schema, question, press, step, count).number

    assert claims("How often does it wobble?") == 1.0
    assert claims("How often does it not wobble?", truth="negated", truth_words="not wobble") == 1.0
    assert claims("Could it wobble?", modality="possible", modality_words="could") == 1.0


# --- the scorer -----------------------------------------------------------------------------------------

SHEET_CHUNK = Chunk(chunk_id=f"{DOC}#0", doc_id=DOC, index=0, text=CHUNK, context="Kettle notes")
LEAK = StoredFact(
    predicate="SHOWS", subject_type="Component", object_type="Behaviour", chunk_id=f"{DOC}#0",
    evidence="The lid never leaked", subject_names=["lid"], object_names=["leak"], subject_name="lid",
    object_name="leak", truth="negated",
)  # fmt: skip
# "doesnt drip" stored with the negation in its name: kept by meaning, not by the field
DRIP = StoredFact(
    predicate="SHOWS", subject_type="Component", object_type="Behaviour", chunk_id=f"{DOC}#0",
    evidence="The spout doesnt drip", subject_names=["spout"], object_names=["no drip"], subject_name="spout",
    object_name="no drip",
)  # fmt: skip
WHISTLE = StoredFact(
    predicate="SHOWS", subject_type="Component", object_type="Behaviour", chunk_id=f"{DOC}#0",
    evidence="The kettle whistles when the water boils", subject_names=["kettle"], object_names=["whistles"],
    subject_name="kettle", object_name="whistles", modality="conditional", condition="the water boils",
)  # fmt: skip


def scored_sheet():
    sample = draw_sample([SHEET_CHUNK], size=5, seed=77)
    return build_coverage_sheet(sample, [SHEET_CHUNK], [LEAK, DRIP, WHISTLE], {}, SCHEMA)


def gold_and_verdicts() -> tuple[AssertionGold, AssertionVerdicts]:
    labelled = {
        "The lid never leaked.": [GoldClaim(claim="the lid never leaked", truth="negated",
                                            modality="actual")],
        "The spout doesnt drip.": [GoldClaim(claim="the spout does not drip", truth="negated",
                                             modality="actual")],
        "The base may crack in frost.": [GoldClaim(claim="the base may crack", truth="affirmed",
                                                   modality="possible")],
        "The kettle whistles when the water boils.": [
            GoldClaim(claim="the kettle whistles", truth="affirmed", modality="conditional",
                      condition="when the water boils"),
        ],
        "The handle got hot.": [],
    }  # fmt: skip
    matched = {
        "The lid never leaked.": [MatchedClaim(claim="the lid never leaked", matched=[fact_id(LEAK)],
                                               fields=FieldsKept(truth=True, modality=True, condition=True),
                                               reason="stored negated")],
        "The spout doesnt drip.": [MatchedClaim(claim="the spout does not drip", matched=[fact_id(DRIP)],
                                                fields=FieldsKept(truth=True, modality=True, condition=True),
                                                reason="the name carries the negation")],
        "The base may crack in frost.": [MatchedClaim(claim="the base may crack", reason="not extracted")],
        "The kettle whistles when the water boils.": [
            MatchedClaim(claim="the kettle whistles", matched=[fact_id(WHISTLE)],
                         fields=FieldsKept(truth=True, modality=True, condition=True), reason="conditional")
        ],
        "The handle got hot.": [],
    }  # fmt: skip
    gold = AssertionGold(
        labeller={"model": "claude-fable-5-1"},
        sentences=[
            GoldSentence(id=sentence_id(DOC, t), stratum="cue", claims=c) for t, c in labelled.items()
        ],
    )
    verdicts = AssertionVerdicts(
        judge=JudgeMeta(model="claude-fable-5-1", date="2026-10-05"),
        sheet="assertion_sheet.json",
        gold="gold.json",
        sentences=[MatchedSentence(id=sentence_id(DOC, t), claims=c) for t, c in matched.items()],
    )
    return gold, verdicts


def test_the_scorer_counts_each_field_by_the_judge_and_exactly():
    gold, verdicts = gold_and_verdicts()
    report = score_assertion(scored_sheet(), gold, verdicts)
    assert (report.claims, report.matched.k) == (4, 3)
    assert report.kept["truth"].rate == 1.0
    # the drip's negation is in its name: kept by meaning, but a count over the field would miss it
    assert (report.exact["truth"].k, report.exact["truth"].n) == (2, 3)
    assert (report.exact_by_value["truth_negated"].k, report.exact_by_value["truth_negated"].n) == (1, 2)
    # the stored condition holds fewer words of the clause than the label: still the same condition
    assert report.exact_by_value["condition_conditional"].rate == 1.0
    assert report.matched.n == 4 and report.kept_by_value["modality_possible"].n == 0
    metrics = report.metrics()
    assert metrics["truth_exact"] == pytest.approx(2 / 3) and metrics["truth_negated_n"] == 2


def test_a_verdict_file_must_keep_the_gold_claims_and_cite_its_own_chunk():
    gold, verdicts = gold_and_verdicts()
    verdicts.sentences[0].claims[0].claim = "the lid leaked"
    verdicts.sentences[1].claims[0].matched = ["000000000000"]
    with pytest.raises(
        EvaluationError, match="the claims differ from the gold's.*not observations of its chunk"
    ):
        score_assertion(scored_sheet(), gold, verdicts)
    with pytest.raises(ValueError, match="exactly when"):
        GoldClaim(claim="x", truth="affirmed", modality="actual", condition="if so")
    with pytest.raises(ValueError, match="exactly when"):
        MatchedClaim(claim="x", matched=["a"], reason="no fields")


def test_the_assertion_stage_scores_without_touching_the_graph(tmp_path):
    gold, verdicts = gold_and_verdicts()
    files = {"sheet": scored_sheet(), "gold": gold, "verdicts": verdicts}
    paths = {name: tmp_path / f"{name}.json" for name in files}
    for name, model in files.items():
        paths[name].write_text(model.model_dump_json(), encoding="utf-8")
    # a driver to a port nothing listens on: any query would fail, so passing proves no graph is read
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)
    state = PipelineState(
        coverage_sheet=paths["sheet"], assertion_gold=paths["gold"], verdicts=paths["verdicts"]
    )
    try:
        run_stages(ctx, state, [st.AssertionStage()])
    finally:
        driver.close()
    logged = tracker.run("assertion")
    assert logged.logged_params["judge_model"] == "claude-fable-5-1" and logged.logged_params["gold_hash"]
    assert logged.logged_metrics["truth_kept"] == 1.0 and logged.logged_metrics["claims_matched"] == 3
    assert {Path(a).name for a in logged.artifacts} == {
        "sheet.json", "gold.json", "verdicts.json", "assertion_report.json",
    }  # fmt: skip


# --- the prompt -----------------------------------------------------------------------------------------


def test_the_extraction_prompt_speaks_no_corpus_language():
    # R77 removed "this dresser", the last corpus word of the extraction prompt (task file, cleanups)
    text = (
        extraction.PROMPT
        + extraction.GLEAN_SUFFIX
        + json.dumps(ChunkExtraction.model_json_schema())  # the field descriptions reach the model too
    )
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "dresser", "defect", "pump")
    assert not [w for w in banned if w in text.lower()]
    assert quoted_four_grams(text) == []
