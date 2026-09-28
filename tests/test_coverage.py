"""The coverage estimate (R68): Wilson intervals, sentence splitting and the seeded sample, the coverage
sheet assembled from a graph's reads, the judge's verdict file, its checks against the sheet and its
scores, and the tracking contract of the three stages. The pure parts need no Neo4j; the tests that take
the `driver` fixture build tiny hand-made graphs (marked `neo4j` by conftest)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.core.identity import observation_id
from kgbuilder.graph.connection import open_driver
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.schema import EntityType, FactType, TextSchema
from kgbuilder.validation.checks.base import StoredFact
from kgbuilder.validation.coverage import (
    Cause,
    ClaimVerdict,
    CoverageVerdicts,
    SentenceVerdict,
    load_coverage_verdicts,
    score_coverage,
)
from kgbuilder.validation.coverage_sheet import (
    CoverageSheet,
    SheetThing,
    build_coverage_sheet,
    read_chunk_things,
)
from kgbuilder.validation.interval import Proportion
from kgbuilder.validation.judge import JudgeMeta, fact_id
from kgbuilder.validation.sentences import draw_sample, sentence_id, split_sentences

from .fakes import RecordingTracker

DOC = "desk_reviews.md"
CHUNKS = [
    Chunk(
        chunk_id=f"{DOC}#0", doc_id=DOC, index=0, context="Oak Desk Reviews",
        text="# Oak Desk Reviews\n\nSturdy frame. Easy to build. Only 199.5 dollars.",
    ),
    Chunk(
        chunk_id=f"{DOC}#1", doc_id=DOC, index=1, context="Oak Desk Reviews",
        text="## Rating: 2/5\nThe top wobbles. The drawer does not stick. The seller shipped late.",
    ),
]  # fmt: skip
SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Component", description="a part of the thing"),
        EntityType(name="Condition", description="a state a part is in"),
    ],
    fact_types=[
        FactType(
            predicate="HAS_CONDITION", subject_type="Component", object_type="Condition",
            description="a part is in a state",
        ),
    ],
)  # fmt: skip
# the top's claim was merged into the kind "desk top": the sheet must show both names
WOBBLE = StoredFact(
    predicate="HAS_CONDITION", subject_type="Component", object_type="Condition", chunk_id=f"{DOC}#1",
    evidence="The top wobbles.", subject_names=["desk top", "top"], object_names=["wobbles"],
    subject_name="top", object_name="wobbles", things=["Oak Desk"], about=["Oak Desk"], polarity="negative",
)  # fmt: skip
FRAME = StoredFact(
    predicate="HAS_CONDITION", subject_type="Component", object_type="Condition", chunk_id=f"{DOC}#0",
    evidence="Sturdy frame.", subject_names=["frame"], object_names=["sturdy"], subject_name="frame",
    object_name="sturdy", things=["Oak Desk"], about=["Oak Desk"], polarity="positive",
)  # fmt: skip
DESK = SheetThing(name="Oak Desk", fields={"price": "199.5"})
THINGS = {c.chunk_id: [DESK] for c in CHUNKS}


def sid(text: str) -> str:
    return sentence_id(DOC, text)


def claim(**fields) -> ClaimVerdict:
    base = {"claim": "a claim", "polarity": "neutral", "about": "Oak Desk", "reason": "why"}
    return ClaimVerdict(**{**base, **fields})


def sheet() -> CoverageSheet:
    sample = draw_sample(CHUNKS, size=8, seed=68)
    return build_coverage_sheet(sample, CHUNKS, [WOBBLE, FRAME], THINGS, SCHEMA)


def verdicts() -> CoverageVerdicts:
    """Every kind of verdict once: covered by an observation, by a record field, and missed for three
    causes, one of them about something no thing of the chunk is."""
    by_text = {
        "# Oak Desk Reviews": [],
        "Sturdy frame.": [claim(polarity="positive", covered_by=[fact_id(FRAME)])],
        "Easy to build.": [claim(polarity="positive", cause="extraction", schema_type="HAS_CONDITION")],
        "Only 199.5 dollars.": [claim(covered_by=["Oak Desk.price"])],
        "## Rating: 2/5": [claim(polarity="negative", cause="no_schema_type")],
        "The top wobbles.": [claim(polarity="negative", covered_by=[fact_id(WOBBLE)])],
        "The drawer does not stick.": [
            claim(polarity="positive", cause="assertion", schema_type="HAS_CONDITION", near=[fact_id(WOBBLE)])
        ],
        "The seller shipped late.": [claim(polarity="negative", about=None, cause="no_schema_type")],
    }
    return CoverageVerdicts(
        judge=JudgeMeta(model="claude-fable-5-1", date="2026-09-28"),
        sheet="coverage_sheet.json",
        sentences=[SentenceVerdict(id=sid(text), claims=claims) for text, claims in by_text.items()],
    )


def test_a_proportion_reproduces_the_wilson_intervals_reported_by_hand():
    # R67 part 3 reported these, computed by a one-off script; the helper must give the same numbers
    for (k, n), expected in {(52, 54): (0.963, 0.875, 0.990), (0, 38): (0.0, 0.0, 0.092)}.items():
        share = Proportion.of(k, n)
        assert (round(share.rate, 3), round(share.low, 3), round(share.high, 3)) == expected


def test_an_empty_sample_has_no_rate_and_a_count_above_its_total_is_a_bug():
    empty = Proportion.of(0, 0)
    assert (empty.rate, empty.low, empty.high) == (None, None, None)
    with pytest.raises(ValueError):
        Proportion.of(3, 2)


def test_sentences_end_at_stops_never_inside_numbers_and_every_line_splits_on_its_own():
    text = (
        "# Oak Desk Reviews\n\n## Rating: 3/5\nThe top is 1.2m wide. It wobbles!\n\n---\n\n"
        'THE DRAWER STUCK AT 1,800.5 HOURS.  NO CHANGE. "IT IS NORMAL." SAID THE SHOP.\n*TR'
    )
    assert split_sentences(text) == [
        "# Oak Desk Reviews",
        "## Rating: 3/5",
        "The top is 1.2m wide.",
        "It wobbles!",
        "THE DRAWER STUCK AT 1,800.5 HOURS.",
        "NO CHANGE.",
        '"IT IS NORMAL."',
        "SAID THE SHOP.",
    ]  # the separator line and the one-word signature "*TR" are no sentences


def test_a_sample_is_fixed_by_its_seed_in_reading_order_and_counts_a_repeated_sentence_once():
    chunks = [
        Chunk(chunk_id="b.md#0", doc_id="b.md", index=0, text="Second doc one. Second doc two."),
        Chunk(chunk_id="a.md#0", doc_id="a.md", index=0, text="First one. First two. Great value."),
        Chunk(chunk_id="a.md#1", doc_id="a.md", index=1, text="Great value. Last one here."),
    ]
    sample = draw_sample(chunks, size=3, seed=7)
    assert sample.population == 6  # "Great value." twice in a.md is one sentence
    assert draw_sample(list(reversed(chunks)), size=3, seed=7) == sample  # input order does not matter
    reading = [
        "First one.",
        "First two.",
        "Great value.",
        "Last one here.",
        "Second doc one.",
        "Second doc two.",
    ]
    texts = [s.text for s in sample.sentences]
    assert texts == sorted(texts, key=reading.index)
    assert len({tuple(s.id for s in draw_sample(chunks, 3, seed).sentences) for seed in range(10)}) > 1
    assert len(draw_sample(chunks, size=50, seed=7).sentences) == 6  # never more than the text has


def test_the_sample_does_not_depend_on_how_the_text_was_chunked():
    # a later arm is measured on the same sample, whatever its chunk settings: ids come from the wording
    whole = [Chunk(chunk_id="a.md#0", doc_id="a.md", index=0, text="Alpha one. Beta two. Gamma three.")]
    cut = [
        Chunk(chunk_id="a.md#0", doc_id="a.md", index=0, text="Alpha one. Beta two."),
        Chunk(chunk_id="a.md#1", doc_id="a.md", index=1, text="Gamma three."),
    ]
    assert [s.id for s in draw_sample(whole, 2, 68).sentences] == [
        s.id for s in draw_sample(cut, 2, 68).sentences
    ]


def test_a_sentence_is_shown_with_the_observations_of_its_own_chunk_under_both_names():
    wobble = next(s for s in sheet().sentences if s.text == "The top wobbles.")
    assert wobble.chunk_id == f"{DOC}#1"
    # its own wording and the node it became after resolution; FRAME (the other chunk) is not shown
    assert [(o.id, o.subject, o.subject_entity) for o in wobble.observations] == [
        (fact_id(WOBBLE), "top", "desk top")
    ]
    assert wobble.item_ids() == {fact_id(WOBBLE), "Oak Desk.price"}


def test_the_sheet_finds_a_sentence_by_its_wording_in_a_graph_chunked_differently():
    merged = [CHUNKS[0].model_copy(update={"text": f"{CHUNKS[0].text}\n\n{CHUNKS[1].text}"})]
    placed = build_coverage_sheet(draw_sample(CHUNKS, 8, 68), merged, [], {}, SCHEMA)
    assert {s.chunk_id for s in placed.sentences} == {f"{DOC}#0"}


def test_a_sampled_sentence_missing_from_the_graph_is_an_error_not_a_smaller_sample():
    with pytest.raises(EvaluationError):
        build_coverage_sheet(draw_sample(CHUNKS, 8, 68), CHUNKS[:1], [], {}, SCHEMA)


def test_a_claim_is_either_covered_or_missed_for_a_cause_and_a_miss_names_its_schema_place():
    claim(covered_by=["x"])
    claim(cause="extraction", schema_type="HAS_CONDITION", near=["x"])
    claim(cause="no_schema_type")
    for wrong in (
        {},  # neither covered nor missed
        {"covered_by": ["x"], "cause": "extraction", "schema_type": "HAS_CONDITION"},  # both
        {"cause": "extraction"},  # a miss the schema could hold names the type that could hold it
        {"cause": "no_schema_type", "schema_type": "HAS_CONDITION"},  # a type contradicts the cause
        {"covered_by": ["x"], "near": ["y"]},  # near is evidence for a miss
    ):
        with pytest.raises(ValidationError):
            claim(**wrong)


def test_coverage_counts_what_is_stored_reachable_what_hangs_on_its_thing_and_misses_by_cause():
    report = score_coverage(sheet(), verdicts())
    assert (report.sentences, report.sentences_with_claims, report.claims) == (8, 7, 7)
    assert (report.coverage.k, report.coverage.n) == (3, 7)
    # the three stored claims and the three misses about the desk; not the seller's late shipping
    assert (report.reachable.k, report.reachable.n) == (6, 7)
    assert report.covered_by_record_only == 1  # the price: a record field, no observation
    assert (report.coverage_in_schema.k, report.coverage_in_schema.n) == (3, 5)  # 2 had no schema type
    assert {tone: (s.k, s.n) for tone, s in report.by_polarity.items()} == {
        "negative": (1, 3), "positive": (1, 3), "neutral": (1, 1),
    }  # fmt: skip
    assert {c: n for c, n in report.missed.items() if n} == {
        Cause.EXTRACTION: 1, Cause.NO_SCHEMA_TYPE: 2, Cause.ASSERTION: 1,
    }  # fmt: skip
    metrics = report.metrics()
    assert metrics["coverage"] == pytest.approx(3 / 7) and metrics["missed_identity"] == 0
    assert {f"missed_{c.value}" for c in Cause} <= metrics.keys()  # every cause, so names stay stable


def test_verdicts_must_judge_every_sentence_once_and_cite_only_what_the_sheet_shows():
    bad = verdicts()
    first, second = bad.sentences[1], bad.sentences[2]
    # the frame sentence cites the top's observation, which is stored for the other chunk
    first.claims = [claim(covered_by=[fact_id(WOBBLE)])]
    second.claims = [
        claim(about="Pine Chair", cause="extraction", schema_type="HAS_CONDITION"),
        claim(cause="extraction", schema_type="RATED_AS"),
    ]
    bad.sentences = [*bad.sentences[:6], SentenceVerdict(id="nope"), bad.sentences[1]]
    with pytest.raises(EvaluationError) as error:
        score_coverage(sheet(), bad)
    text = "; ".join(error.value.issues)
    for issue in ("more than one verdict", "have no verdict", "not on the sheet", "not stored for its chunk",
                  "'Pine Chair' is not a thing", "'RATED_AS' is no fact type"):  # fmt: skip
        assert issue in text


def test_an_unreadable_verdict_file_is_reported_as_an_evaluation_error(tmp_path):
    raw = verdicts().model_dump(mode="json")
    raw["sentences"][1]["claims"][0]["cause"] = "extraction"  # covered and missed at once
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EvaluationError, match="exactly one"):
        load_coverage_verdicts(path)


def test_the_coverage_stage_scores_a_sheet_without_touching_the_graph(tmp_path):
    sheet_file, verdicts_file = tmp_path / "coverage_sheet.json", tmp_path / "verdicts.json"
    sheet_file.write_text(sheet().model_dump_json(), encoding="utf-8")
    verdicts_file.write_text(verdicts().model_dump_json(), encoding="utf-8")
    # a driver to a port nothing listens on: any query would fail, so passing proves no graph is read
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out", tracker=tracker)
    state = PipelineState(coverage_sheet=sheet_file, verdicts=verdicts_file)
    try:
        run_stages(ctx, state, [st.CoverageStage()])
    finally:
        driver.close()
    run = tracker.run("coverage")
    assert run.logged_params["judge_model"] == "claude-fable-5-1"
    assert run.logged_params["sheet_hash"] and run.logged_params["judge_verdicts_hash"]
    assert run.logged_metrics["coverage"] == pytest.approx(3 / 7)
    assert run.logged_metrics["missed_no_schema_type"] == 2 and run.logged_metrics["claims_positive"] == 3
    assert {Path(a).name for a in run.artifacts} == {
        "coverage_sheet.json",
        "verdicts.json",
        "coverage_report.json",
    }
    assert state.coverage is not None and state.coverage.claims == 7


def test_a_chunk_hangs_on_its_documents_thing_and_its_sections_record_with_their_fields(driver):
    driver.execute_query(
        "CREATE (p:Product {product_id: 'P1', product_name: 'Oak Desk', price: 199.5, in_stock: true, "
        "blurb: $blurb}), (r:Order {order_id: 'R-2001', items: ['desk', 'lamp']}), "
        "(d:Document {doc_id: 'a.md'}), (d)-[:ABOUT {name: 'Oak Desk'}]->(p), "
        "(:Chunk {chunk_id: 'a.md#0'})-[:PART_OF]->(d), "
        "(c1:Chunk {chunk_id: 'a.md#1'})-[:PART_OF]->(d), (c1)-[:ABOUT {name: 'R-2001'}]->(r), "
        "(:Chunk {chunk_id: 'b.md#0'})-[:PART_OF]->(:Document {doc_id: 'b.md'})",
        blurb="x" * 100,
    )
    things = read_chunk_things(driver, ["a.md#0", "a.md#1", "b.md#0"])
    assert [t.name for t in things["a.md#0"]] == ["Oak Desk"]
    assert [t.name for t in things["a.md#1"]] == ["Oak Desk", "R-2001"]
    assert "b.md#0" not in things  # its document is about nothing: only a text search finds it
    desk = things["a.md#0"][0].fields
    assert (desk["price"], desk["in_stock"], things["a.md#1"][1].fields["items"]) == (
        "199.5",
        "true",
        "desk, lamp",
    )
    assert len(desk["blurb"]) == 80 and desk["blurb"].endswith("…")  # long text is the source, not a value


def test_the_sample_and_sheet_stages_read_the_graph_and_log_their_inputs(driver, tmp_path):
    text = f"{CHUNKS[0].text}\n\n---\n\n{CHUNKS[1].text}"
    write_lexical_graph(driver, [Document(doc_id=DOC, title="desk_reviews", text=text)], CHUNKS)
    driver.execute_query(
        "MATCH (d:Document {doc_id: $doc}), (c:Chunk {chunk_id: $chunk}) "
        "CREATE (p:Product {product_id: 'P1', product_name: 'Oak Desk'}), "
        "(d)-[:ABOUT {name: 'Oak Desk'}]->(p), "
        "(o:Observation {id: 'o1', predicate: 'HAS_CONDITION', chunk_id: $chunk, "
        "evidence: 'The top wobbles.', subject_name: 'top', object_name: 'wobbles', "
        "polarity: 'negative', time: ''})-[:FROM]->(c), "
        "(o)-[:SUBJECT]->(:Entity {id: 'e1', type: 'Component', name: 'desk top', aliases: ['top']}), "
        "(o)-[:OBJECT]->(:Entity {id: 'e2', type: 'Condition', name: 'wobbles'}), "
        "(p)-[:HAS_OBSERVATION {name: 'Oak Desk'}]->(o)",
        doc=DOC,
        chunk=f"{DOC}#1",
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / TEXT_SCHEMA_FILE).write_text(SCHEMA.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, tracker=tracker)
    sample_file = tmp_path / "gold" / "sample.json"  # a committed input, so outside out/

    run_stages(
        ctx, PipelineState(sample=sample_file, sample_size=8, sample_seed=68), [st.CoverageSampleStage()]
    )
    sampled = tracker.run("coverage_sample")
    assert sampled.logged_params["sample_seed"] == 68
    assert sampled.logged_metrics == {"sentences_total": 8, "sentences_sampled": 8}

    run_stages(ctx, PipelineState(sample=sample_file), [st.CoverageSheetStage()])
    run = tracker.run("coverage_sheet")
    assert run.logged_params["sample_hash"] and run.logged_params["text_schema_hash"]
    # the observation shows on the four sentences of its chunk; both chunks hang on the desk
    assert run.logged_metrics == {
        "sentences": 8, "sentences_with_observations": 4, "sentences_on_a_thing": 8, "observations_shown": 4,
    }  # fmt: skip
    written = CoverageSheet.model_validate_json((out / "coverage_sheet.json").read_text(encoding="utf-8"))
    wobble = next(s for s in written.sentences if s.text == "The top wobbles.")
    # the id the judge sheet uses for this claim, so coverage and precision verdicts name claims alike
    expected_id = observation_id(f"{DOC}#1", "HAS_CONDITION", "top", "wobbles")
    assert [(o.id, o.subject_entity) for o in wobble.observations] == [(expected_id, "desk top")]
