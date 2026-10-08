"""The retrieval benchmark's stages (R117): `kg retrieve-eval` end to end on a hand-made graph of a small
build (the graph route: its params, metrics, seeds and report), a gold whose evidence the loaded graph lacks
refused before any embedding, the graph digest equal over a rebuild and changed by any change of content,
and `kg retrieve-compare` pairing two reports at every budget. Needs Neo4j, except the compare stage."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.graph.digest import graph_digest
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.retrieval_stages import RetrieveCompareStage, RetrieveEvalStage
from kgbuilder.validation.qa_gold import QAGold, RecordEvidence
from kgbuilder.validation.retrieval_scores import (
    RetrievalFingerprint,
    RetrievalOutcome,
    load_retrieval_report,
    score_retrieval,
)
from kgbuilder.validation.target_gold import QuestionTargets, Target, TargetGold
from tests.fakes import RecordingTracker
from tests.test_audit import KETTLE, LAMP, TEXTS, _build
from tests.test_query import FixedEmbedder

# the build's two products and their review documents as the build, ingest and link stages write them: what
# the graph route reads (records, the documents about them, their chunks), nothing else
GRAPH = """
CREATE (lamp:Product {product_id: 'P-1', product_name: 'Alder Lamp'}),
       (kettle:Product {product_id: 'P-2', product_name: 'Birch Kettle'}),
       (dl:Document {doc_id: $lamp})-[:ABOUT]->(lamp), (dk:Document {doc_id: $kettle})-[:ABOUT]->(kettle),
       (:Chunk {chunk_id: $lamp + '#0', text: $lamp_text, context: 'Alder Lamp Reviews',
                embedding: [0.0, 1.0]})-[:PART_OF]->(dl),
       (:Chunk {chunk_id: $kettle + '#0', text: $kettle_text, context: 'Birch Kettle Reviews',
                embedding: [1.0, 0.0]})-[:PART_OF]->(dk)
"""

QUESTIONS = {
    "Q1": "Is the Birch Kettle lid stiff?",
    "Q2": "Which part of the Alder Lamp is cracked?",
    "Q3": "How many products are there?",
}


def load(driver) -> None:
    driver.execute_query(GRAPH, lamp=LAMP, kettle=KETTLE, lamp_text=TEXTS[LAMP], kettle_text=TEXTS[KETTLE])


def gold_files(tmp_path, kettle_chunk: str = f"{KETTLE}#0"):
    """A QA gold over the build (two questions with chunk evidence, one with a record) and its targets."""
    qa = QAGold.model_validate(
        {
            "dataset": "t", "written_by": "test", "date": "2026-10-08",
            "corpus": {"data_dir": "x", "chunk_max_chars": 1500, "chunk_min_chars": 200,
                       "chunk_overlap_chars": 0},
            "questions": [
                {"id": "Q1", "type": "lookup", "question": QUESTIONS["Q1"], "expected": {"text": "yes"},
                 "route": "retrieval",
                 "chunks": [{"chunk_id": kettle_chunk, "quote": "The lid hinge is stiff."}]},
                {"id": "Q2", "type": "multi_hop", "question": QUESTIONS["Q2"],
                 "expected": {"entities": [{"name": "shade"}]}, "route": "retrieval",
                 "chunks": [{"chunk_id": f"{LAMP}#0", "quote": "The shade is cracked."}]},
                {"id": "Q3", "type": "aggregation", "question": QUESTIONS["Q3"], "expected": {"number": 2},
                 "route": "exact", "records": [{"file": "products.csv", "row": {"product_id": "P-1"}}]},
            ],
        }
    )  # fmt: skip
    qa_file, targets_file = tmp_path / "qa.json", tmp_path / "targets.json"
    qa_file.write_text(qa.model_dump_json(), encoding="utf-8")

    def product(name: str, key: str) -> Target:
        return Target(name=name, records=[RecordEvidence(file="products.csv", row={"product_id": key})])

    targets = TargetGold(
        dataset="t", qa_gold=qa_file.as_posix(), written_by="test", date="2026-10-08",
        questions=[
            QuestionTargets(id="Q1", targets=[product("Birch Kettle", "P-2")]),
            QuestionTargets(id="Q2", targets=[product("Alder Lamp", "P-1")]),
            QuestionTargets(id="Q3", note="it ranges over every product"),
        ],
    )  # fmt: skip
    targets_file.write_text(targets.model_dump_json(), encoding="utf-8")
    return qa_file, targets_file


def eval_run(driver, tmp_path, embedder, qa_file, targets_file):
    build, data = _build(tmp_path)
    # meaning links off: the seeds are the names the questions spell
    ctx = PipelineContext(
        settings=Settings(qa_link_neighbours=0), driver=driver, out=tmp_path / "eval", embedder=embedder,
        tracker=RecordingTracker(),
    )  # fmt: skip
    state = PipelineState(gold=qa_file, anchor_targets=targets_file, audit_source=build, data_dir=data)
    return ctx, run_stages(ctx, state, [RetrieveEvalStage("graph_retrieval")])


def test_retrieve_eval_ranks_every_question_without_the_reader_and_scores_chunks_and_seeds(driver, tmp_path):
    load(driver)
    embedder = FixedEmbedder()
    ctx, state = eval_run(driver, tmp_path, embedder, *gold_files(tmp_path))
    run = ctx.tracker.run("retrieve_eval_graph_retrieval")
    params = run.logged_params
    # the budgets of the A-against-B headline (the plan change after R121): 1, 3, 5, 10, 20
    budgets = params["retrieval_budgets"]
    assert params["graph_digest"] == graph_digest(driver).value and budgets == [1, 3, 5, 10, 20]
    assert (params["system"], params["qa_hops"], params["qa_link_neighbours"]) == ("graph_retrieval", 2, 0)
    assert {"gold_hash", "targets_hash", "embed_model", "build", "chunk_max_chars"} <= set(params)
    metrics = run.logged_metrics
    # Q1 and Q2 have chunk evidence, which the linked product's documents reach; Q3 has neither
    assert (metrics["complete_at_5"], metrics["complete_at_5_n"]) == (1.0, 2)
    assert (metrics["seed_recall_at_5"], metrics["seed_recall_at_5_n"], metrics["targets_unplaced"]) == (
        1.0,
        2,
        0,
    )
    # one embedding per question, one question at a time; no node names, as no meaning link is made
    assert embedder.batches == [[q] for q in QUESTIONS.values()]
    assert (metrics["embedded_texts"], metrics["embedded_chars"]) == (3, sum(map(len, QUESTIONS.values())))
    report = load_retrieval_report(tmp_path / "eval" / "retrieval_graph_retrieval.json")
    assert report == state.retrieval["graph_retrieval"]
    by_id = {o.question_id: o for o in report.outcomes}
    # the seeds are stable refs, the ids the targets are placed on, never element ids
    assert {q: o.seeds for q, o in by_id.items()} == {"Q1": ["Product:P-2"], "Q2": ["Product:P-1"], "Q3": []}
    assert by_id["Q1"].ranked == [f"{KETTLE}#0"] and by_id["Q2"].gold_targets == [["Product:P-1"]]


def test_a_gold_whose_evidence_the_graph_lacks_is_refused_before_any_embedding(driver, tmp_path):
    load(driver)
    embedder = FixedEmbedder()
    with pytest.raises(
        EvaluationError, match="1 of the gold's 2 evidence chunks are not in the loaded graph"
    ):
        eval_run(driver, tmp_path, embedder, *gold_files(tmp_path, kettle_chunk="reviews/elsewhere.md#0"))
    assert embedder.batches == [] and not (tmp_path / "eval" / "retrieval_graph_retrieval.json").exists()


def test_the_graph_digest_follows_the_content_and_not_the_element_ids(driver):
    load(driver)
    first = graph_digest(driver)
    assert first.labels == {"Chunk": 2, "Document": 2, "Product": 2}
    assert first.relationships == {"ABOUT": 2, "PART_OF": 2}
    assert first.chunk_ids == {f"{LAMP}#0", f"{KETTLE}#0"}
    driver.execute_query("MATCH (n) DETACH DELETE n")
    load(driver)  # the same content again: every node has a new element id
    assert graph_digest(driver) == first
    changes = [
        "MATCH (c:Chunk) WHERE c.text STARTS WITH '# Birch' SET c.text = 'Other.'",  # one text changed
        "MATCH (p:Product {product_id: 'P-1'}) SET p:Discontinued",  # one label more
        "MATCH (a:Product {product_id: 'P-1'}), (b:Product {product_id: 'P-2'}) CREATE (a)-[:LIKE]->(b)",
        "MATCH (c:Chunk) WHERE c.text STARTS WITH '# Alder' DETACH DELETE c",  # a chunk gone
    ]
    for change in changes:
        driver.execute_query("MATCH (n) DETACH DELETE n")
        load(driver)
        driver.execute_query(change)
        assert graph_digest(driver).value != first.value, change


FINGERPRINT = RetrievalFingerprint(gold_hash="g", targets_hash="t", graph_digest="d", embed_model="e")


def report_file(path, system: str, ranked: dict[str, list[str]], seeds: dict[str, list[str]] | None):
    outcomes = [
        RetrievalOutcome(
            question_id=q, type="lookup", system=system, gold_chunks=["c1"], ranked=chunks,
            seeds=None if seeds is None else seeds[q], gold_targets=[["n1"]], latency_ms=5.0,
        )
        for q, chunks in ranked.items()
    ]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        score_retrieval(outcomes, [5, 10], system, FINGERPRINT).model_dump_json(), encoding="utf-8"
    )
    return path


def test_retrieve_compare_pairs_two_reports_at_every_budget_without_a_graph(tmp_path):
    vector = report_file(tmp_path / "r" / "retrieval_vector.json", "vector", {"Q1": ["c1"], "Q2": []}, None)
    graph = report_file(
        tmp_path / "r" / "retrieval_graph_retrieval.json",
        "graph_retrieval",
        {"Q1": [], "Q2": ["c9", "c8", "c7", "c6", "c5", "c1"]},
        {"Q1": ["n1"], "Q2": []},
    )
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "cmp", tracker=tracker)
    state = run_stages(ctx, PipelineState(retrieval_reports=(vector, graph)), [RetrieveCompareStage()])
    metrics = tracker.run("retrieve_compare").logged_metrics
    # Q1 only the vector system completes at 5; at 10 the graph route completes Q2 too
    assert (metrics["complete_at_5_only_a"], metrics["complete_at_5_only_b"]) == (1, 0)
    assert (metrics["complete_at_10_only_a"], metrics["complete_at_10_only_b"]) == (1, 1)
    assert "seed_found_at_5_questions" not in metrics  # vector search starts from no node
    assert [c.k for c in state.retrieval_comparison] == [5, 10]
    assert state.retrieval_comparison[0].complete.a == "r/retrieval_vector.json"
    written = json.loads((tmp_path / "cmp" / "retrieve_compare.json").read_text(encoding="utf-8"))
    assert [c["k"] for c in written] == [5, 10]
