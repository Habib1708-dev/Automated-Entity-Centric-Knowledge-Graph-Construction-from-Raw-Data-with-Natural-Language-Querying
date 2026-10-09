"""The query stage against Neo4j (R71): each traversal pattern on a hand-made graph, the hop limit, walks that
must not pass through documents, the node names and chunks the store reads, the chunk vector index, and
`kg qa`'s stage end to end with a scripted planner and reader (params, metrics, plans and the answers
file, R74; with `--plans`, an earlier run's plans replayed, R80), and the records-plus-vector system on
the same graph: its schema cut to the record layer and its plans over records only (R73, R74).
R117: `kg qa` logs the graph digest and refuses a gold whose evidence the graph lacks. R126: what a chunk
concerns and what a claim joins, the start nodes of chunk and claim retrieval. Needs Neo4j."""

import json
import time

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError, EvaluationError
from kgbuilder.graph.digest import graph_digest
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.qa_stages import QAStage
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.answers import load_system_answers
from kgbuilder.query.graph_store import ChunkNodes, NamedNode, Neo4jGraphStore
from kgbuilder.query.plan import PlanStep, QueryPlan
from kgbuilder.query.reader import ReaderAnswer, ReaderCitation
from kgbuilder.text.lexical import CHUNK_VECTOR_INDEX
from kgbuilder.validation.qa_gold import QAGold

from .fakes import RecordingTracker, ScriptedLLM
from .graphs import PLAN, build


def test_each_traversal_pattern_reaches_the_chunks_of_its_own_path(driver):
    ids = build(driver)
    reached = Neo4jGraphStore(driver, PLAN, hops=2).reach([ids["Quill Press"]], ["k-wobble"])
    # the press's observations and the documents about it
    assert reached["thing_observations"] == {"notes.md#0", "notes.md#1", "both.md#0"}
    # the claims whose mentions refer to the kind, and the chunks that mention it in either spelling
    assert reached["kind_observations"] == {"notes.md#0", "log.md#1"}
    # part and ticket one hop away, the maker two, and the claim naming the part (its mention refers to the
    # part record, R75); the other press only through a document: not related
    assert reached["related_records"] == {
        "record/Ticket/T-1#0", "log.md#0", "log.md#2", "log.md#3", "notes.md#0",
    }  # fmt: skip
    assert set(reached) == {"thing_observations", "kind_observations", "related_records"}


def test_a_record_reaches_the_claims_and_chunks_of_the_mentions_that_refer_to_it(driver):
    """R75: "the spindle" of the notes refers to the part record, so the part's text holds that claim; until
    R75 a kind reached its record through a pattern of its own (`referred_records`)."""
    ids = build(driver)
    reached = Neo4jGraphStore(driver, PLAN, hops=1).reach([ids["Spindle"]], [])
    assert reached["thing_observations"] == {"log.md#3", "notes.md#0"}


def test_the_hop_limit_bounds_the_related_records(driver):
    ids = build(driver)
    reached = Neo4jGraphStore(driver, PLAN, hops=1).reach([ids["Quill Press"]], [])
    # no maker; the claim naming the part comes with the part (R75)
    assert reached["related_records"] == {"record/Ticket/T-1#0", "log.md#0", "log.md#3", "notes.md#0"}
    assert reached["kind_observations"] == set()


def test_the_store_reads_node_names_and_chunks_in_the_order_asked(driver):
    build(driver)
    store = Neo4jGraphStore(driver, PLAN, hops=2)
    names = {(n.kind, n.name, tuple(n.aliases)) for n in store.node_names()}
    assert ("thing", "Quill Press", ()) in names and ("thing", "T-1", ()) in names  # a key names a ticket
    # a record is also known by the names of its mentions; a kind by every spelling of its mentions
    assert ("thing", "Spindle", ("spindle",)) in names
    assert ("kind", "wobbles", ("wobbles", "wobbling")) in names
    assert not [n for n in store.node_names() if n.kind == "kind" and n.name == "Spindle"]
    # R117: each name's stable ref, which a rebuild keeps: a record by label and key, a kind by its id
    refs = {(n.kind, n.name): n.ref for n in store.node_names()}
    assert refs[("thing", "Quill Press")] == "Press:P1" and refs[("thing", "T-1")] == "Ticket:T-1"
    assert refs[("kind", "wobbles")] == "k-wobble"
    chunks = store.chunks(["notes.md#1", "missing#0", "notes.md#0"])
    assert [c.chunk_id for c in chunks] == ["notes.md#1", "notes.md#0"]
    assert chunks[1].context == "Quill Press notes" and chunks[1].embedding == [1.0, 0.0]


def test_r126_the_store_reads_what_a_chunk_concerns_and_what_a_claim_joins(driver):
    build(driver)
    # a mention no identity decision bound, and an individual the claim is attached to besides the press
    driver.execute_query(
        "MATCH (c:Chunk {chunk_id: 'notes.md#1'}), (o:Observation {id: 'o1'}) "
        "CREATE (c)-[:MENTIONS]->(:Mention {id: 'm-late', name: 'late', type: 'Event', doc_id: 'notes.md'}), "
        "(:Individual {id: 'i-crew', name: 'night crew'})-[:HAS_OBSERVATION]->(o)"
    )
    store = Neo4jGraphStore(driver, PLAN, hops=2)
    nodes = store.chunk_nodes(["notes.md#0", "log.md#0", "log.md#1", "both.md#0", "notes.md#1", "missing#0"])
    # W3: the document's ABOUT when the chunk has none (the press), and what its mentions refer to
    assert nodes["notes.md#0"] == ChunkNodes(
        about=["Press:P1"],
        named=[NamedNode(ref="Part:S1", names=["spindle"]), NamedNode(ref="k-wobble", names=["wobbles"])],
    )
    assert nodes["log.md#0"].about == ["Ticket:T-1"]  # the chunk's own ABOUT wins over its document's
    assert nodes["both.md#0"].about == ["Press:P1", "Press:P2"]
    assert nodes["log.md#1"] == ChunkNodes(about=[], named=[NamedNode(ref="k-wobble", names=["wobbling"])])
    assert nodes["notes.md#1"].named == []  # a mention without a REFERS_TO edge is no start node
    assert "missing#0" not in nodes
    # arm B: the subject's entity, the object's, then the attached things by ref (the press, the individual)
    assert store.claim_nodes(["o1", "missing"]) == {"o1": ["Part:S1", "k-wobble", "Press:P1", "i-crew"]}


def _index_dimensions(driver) -> int | None:
    records, _, _ = driver.execute_query(
        "SHOW INDEXES YIELD name, options WHERE name = $name "
        "RETURN options.indexConfig['vector.dimensions'] AS dims",
        name=CHUNK_VECTOR_INDEX,
    )
    return records[0]["dims"] if records else None


def test_the_baseline_searches_the_chunk_vector_index(driver):
    # the database keeps its index across tests and real runs, so reuse its size; drop only one made here,
    # or a later ingest would keep a test-sized index and store no real vectors in it (see R12's finding)
    dims = _index_dimensions(driver)
    created = dims is None
    if created:
        dims = 3
        driver.execute_query(
            f"CREATE VECTOR INDEX {CHUNK_VECTOR_INDEX} FOR (c:Chunk) ON (c.embedding) OPTIONS "
            f"{{indexConfig: {{`vector.dimensions`: {dims}, `vector.similarity_function`: 'cosine'}}}}"
        )
    try:
        axis = [[1.0 if j == i else 0.0 for j in range(dims)] for i in range(2)]
        driver.execute_query(
            "CREATE (:Chunk {chunk_id: 'a#0', embedding: $a}), (:Chunk {chunk_id: 'b#0', embedding: $b})",
            a=axis[0],
            b=axis[1],
        )
        driver.execute_query("CALL db.awaitIndexes(30)")
        store = Neo4jGraphStore(driver, None, hops=1)
        deadline = time.monotonic() + 10  # vector index updates can become visible a moment after commit
        while (found := store.nearest_chunks(axis[0], 1)) != ["a#0"] and time.monotonic() < deadline:
            time.sleep(0.2)
        assert found == ["a#0"]
    finally:
        if created:
            driver.execute_query(f"DROP INDEX {CHUNK_VECTOR_INDEX} IF EXISTS")


def test_the_database_refuses_to_plan_writes_unknown_names_and_bad_syntax(driver):
    build(driver)
    store = Neo4jGraphStore(driver, PLAN, hops=1)
    assert store.explain("MATCH (p:Press) WHERE p.name = $n RETURN p.name LIMIT 5", {"n": "x"}) == []
    assert "not read-only" in store.explain("MATCH (p:Press) SET p.name = 'x'", {})[0]
    unknown = " ".join(store.explain("MATCH (p:Nope)-[:NEVER]->(q) RETURN q.absent LIMIT 5", {}))
    assert "`Nope`" in unknown and "`NEVER`" in unknown and "`absent`" in unknown
    assert "cannot plan it" in store.explain("MATCH (p RETURN p", {})[0]


def test_a_read_runs_and_a_write_is_refused_by_the_read_transaction_itself(driver):
    build(driver)
    store = Neo4jGraphStore(driver, PLAN, hops=1, cypher_timeout_s=5)
    result = store.run_read("MATCH (p:Press) RETURN p.name ORDER BY p.name", {})
    assert result.rows == [["Lark Press"], ["Quill Press"]] and result.error is None
    # the text check would never let this through; the server refuses it on its own too
    refused = store.run_read("CREATE (:Press {name: 'intruder'})", {})
    assert refused.rows == [] and "read access mode" in refused.error
    assert driver.execute_query("MATCH (p:Press {name: 'intruder'}) RETURN p").records == []


def test_the_schema_lists_labels_with_examples_relationships_and_claim_patterns(driver):
    build(driver)
    schema = Neo4jGraphStore(driver, PLAN, hops=1).schema()
    press = next(info for info in schema.labels if info.label == "Press")
    assert press.count == 2 and [p.name for p in press.properties] == [
        "active",
        "name",
        "press_id",
        "since",
        "year",
    ]
    # each property with its type and its examples written as Cypher literals: a number compared with a
    # quoted '2019', or a date with a string prefix, matches nothing (found in R71's held-out run)
    text = schema.text()
    assert "year (INTEGER) e.g. 2016, 2019" in text and "active (BOOLEAN) e.g. false, true" in text
    assert "since (DATE) e.g. date('2015-06-30'), date('2020-01-02')" in text
    assert "name (STRING) e.g. 'Lark Press', 'Quill Press'" in text
    chunk = next(info for info in schema.labels if info.label == "Chunk")
    # long text and vectors are never shown: no filter or count needs them
    assert {p.name for p in chunk.properties} == {"chunk_id"}
    assert any(
        r.type == "MADE_BY" and (r.source, r.target) == ("Part", "Maker") for r in schema.relationships
    )
    assert [(c.subject_type, c.predicate, c.object_type) for c in schema.claims] == [
        ("Component", "HAS_CONDITION", "Condition")
    ]
    mention = next(info for info in schema.labels if info.label == "Mention")
    # the identity edges with what they point at: a concept or a record (R75)
    assert (
        mention.count == 3
        and "kind (STRING) e.g. 'record'" in text
        and "kind (STRING) e.g. 'concept'" in text
    )
    assert "reason" not in {p.name for r in schema.relationships for p in r.properties}  # audit fields hidden


class AxisEmbedder:
    """Every text on the first axis, so a chunk on that axis ranks first."""

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def qa_gold(tmp_path, questions: list[dict]):
    gold = QAGold.model_validate(
        {
            "dataset": "test", "written_by": "claude", "date": "2026-10-05",
            "corpus": {
                "data_dir": "x", "chunk_max_chars": 1500, "chunk_min_chars": 200, "chunk_overlap_chars": 0
            },
            "questions": questions,
        }
    )  # fmt: skip
    path = tmp_path / "gold.json"
    path.write_text(gold.model_dump_json(), encoding="utf-8")
    return path


def plan(*steps: dict) -> QueryPlan:
    return QueryPlan(steps=[PlanStep(**s) for s in steps])


def qa_context(driver, out, script, tracker):
    out.mkdir(exist_ok=True)
    (out / PLAN_FILE).write_text(PLAN.model_dump_json(), encoding="utf-8")
    settings = Settings(qa_model="reader-model", qa_top_k=2, qa_link_neighbours=0, qa_workers=1)
    return PipelineContext(
        settings=settings,
        driver=driver,
        out=out,
        llm=ScriptedLLM(script),
        embedder=AxisEmbedder(),
        tracker=tracker,
    )


def test_kg_qa_answers_every_question_writes_the_answers_and_logs_what_code_can_score(driver, tmp_path):
    build(driver)
    gold_file = qa_gold(
        tmp_path,
        [
            {"id": "Q1", "type": "lookup", "question": "Which press has a spindle that wobbles?",
             "expected": {"entities": [{"name": "Quill Press"}]}, "route": "retrieval",
             "chunks": [{"chunk_id": "notes.md#0", "quote": "The spindle wobbles."}]},
            {"id": "Q2", "type": "aggregation", "question": "How many notes name the spindle?",
             "expected": {"number": 1}, "route": "exact",
             "chunks": [{"chunk_id": "notes.md#0", "quote": "spindle"}]},
            {"id": "Q3", "type": "lookup", "question": "What happened to the Quill Press delivery?",
             "expected": {"text": "It was late."}, "route": "retrieval",
             "chunks": [{"chunk_id": "notes.md#1", "quote": "Delivered late."}]},
        ],
    )  # fmt: skip
    reply = ReaderAnswer(
        entities=["Quill Press"],
        citations=[ReaderCitation(chunk_id="notes.md#0", quote="the spindle wobbles")],
    )
    plans = {  # Q2's first plan names a label the graph lacks and is refused; the retry counts claims
        "Which press": [plan({"op": "answer_from_chunks"})],
        "How many": [
            plan({"op": "filter_records", "label": "Note"}, {"op": "count", "input": 0}),
            plan(  # the spindle is a part record its mention refers to (R75)
                {"op": "find_entity", "name": "spindle", "label": "Part"},
                {"op": "find_claims", "input": 0},
                {"op": "count", "input": 1, "unit": "documents"},
            ),
        ],
        "What happened": [
            plan({"op": "find_entity", "name": "Quill Press"}, {"op": "answer_from_chunks", "input": 0})
        ],
    }

    def script(prompt, schema):
        if schema is QueryPlan:
            question = prompt.rsplit("<question>", 1)[1]
            return next(v for k, v in plans.items() if question.startswith(k)).pop(0)
        return reply

    tracker = RecordingTracker()
    out = tmp_path / "out"
    run_stages(qa_context(driver, out, script, tracker), PipelineState(gold=gold_file), [QAStage("graph")])

    run = tracker.run("qa_graph")
    assert run.logged_params["system"] == "graph" and run.logged_params["model"] == "reader-model"
    assert {
        "prompt_version", "planner_prompt_version", "read_check_prompt_version", "cypher_prompt_version",
        "qa_hops", "qa_step_cap", "qa_check_limit", "qa_check_chunks", "gold_hash", "workers",
    } <= set(run.logged_params)  # fmt: skip
    assert run.logged_params["graph_digest"] == graph_digest(driver).value  # the graph answered from (R117)
    metrics = run.logged_metrics
    # Q1 read from the text the graph route reached, Q2 counted by code; Q3 is free text, for the judge
    assert metrics["answer_accuracy"] == 1.0 and metrics["answer_accuracy_n"] == 2
    assert metrics["answers_unjudged"] == 1
    assert (metrics["plans_proposed"], metrics["plans_refused"], metrics["answers_retried"]) == (4, 1, 1)
    assert (metrics["answers_using_find_claims"], metrics["answers_using_answer_from_chunks"]) == (1, 2)
    assert (metrics["fallback_text2cypher"], metrics["fallback_retrieval"]) == (0, 0)
    assert metrics["citation_faithfulness"] == 1.0
    answers = load_system_answers(out / "answers_graph.jsonl")
    assert [a.question_id for a in answers] == ["Q1", "Q2", "Q3"]
    assert answers[0].retrieved[0] == "notes.md#0"
    refused, ran = answers[1].plan.attempts
    assert "unknown label 'Note'" in refused.issues[0] and [s.op for s in ran.steps] == [
        "find_entity", "find_claims", "count",
    ]  # fmt: skip
    assert answers[1].number == 1.0 and answers[1].route is None
    assert any(path.endswith("answers_graph.jsonl") for path in run.artifacts)
    report = json.loads((out / "qa_report_graph.json").read_text(encoding="utf-8"))
    assert report["k"] == 2


def test_kg_qa_refuses_a_gold_whose_evidence_the_graph_lacks_before_any_call(driver, tmp_path):
    """R117: another dataset's graph loaded would score every system against chunks it cannot return."""
    build(driver)
    gold_file = qa_gold(
        tmp_path,
        [
            {"id": "Q1", "type": "lookup", "question": "Which press wobbles?", "expected": {"text": "x"},
             "route": "retrieval", "chunks": [{"chunk_id": "elsewhere.md#0", "quote": "wobbles"}]},
        ],
    )  # fmt: skip
    out = tmp_path / "out"
    ctx = qa_context(driver, out, lambda prompt, schema: pytest.fail("no model call"), RecordingTracker())
    with pytest.raises(EvaluationError, match="not in the loaded graph"):
        run_stages(ctx, PipelineState(gold=gold_file), [QAStage("graph")])
    assert ctx.llm.calls == [] and not (out / "answers_graph.jsonl").exists()


def test_kg_qa_with_plans_replays_an_earlier_runs_plans_and_writes_none(driver, tmp_path):
    build(driver)
    gold_file = qa_gold(
        tmp_path,
        [{"id": "Q2", "type": "aggregation", "question": "How many notes name the spindle?",
          "expected": {"number": 1}, "route": "exact",
          "chunks": [{"chunk_id": "notes.md#0", "quote": "spindle"}]}],
    )  # fmt: skip
    counted = plan(
        {"op": "find_entity", "name": "spindle", "label": "Part"},
        {"op": "find_claims", "input": 0},
        {"op": "count", "input": 1, "unit": "documents"},
    )
    earlier = tmp_path / "earlier"
    planning = qa_context(driver, earlier, lambda prompt, schema: counted, RecordingTracker())
    run_stages(planning, PipelineState(gold=gold_file), [QAStage("graph")])

    asked: list[str] = []
    tracker = RecordingTracker()
    out = tmp_path / "out"
    replaying = qa_context(driver, out, lambda prompt, schema: asked.append(schema.__name__), tracker)
    run_stages(replaying, PipelineState(gold=gold_file, frozen_plans=earlier), [QAStage("graph")])

    answer = load_system_answers(out / "answers_graph.jsonl")[0]
    assert answer.number == 1.0 and answer.plan.frozen and answer.plan.attempts[0].plan == counted
    assert asked == []  # no plan, no query and no reading was asked of the model
    run = tracker.run("qa_graph")
    frozen_file = earlier / "answers_graph.jsonl"
    assert run.logged_params["frozen_plans"] == frozen_file and run.logged_params["frozen_plans_hash"]
    assert run.logged_metrics["answers_frozen"] == 1 and str(frozen_file) in run.artifacts
    # replaying into the folder the plans come from would overwrite them
    with pytest.raises(ConfigurationError, match="another folder"):
        run_stages(
            qa_context(driver, earlier, lambda prompt, schema: None, RecordingTracker()),
            PipelineState(gold=gold_file, frozen_plans=earlier),
            [QAStage("graph")],
        )


def test_the_record_layer_of_a_real_graph_leaves_out_documents_chunks_and_claims(driver):
    build(driver)
    schema = Neo4jGraphStore(driver, PLAN, hops=1).schema()
    labels = {rule.label for rule in PLAN.nodes}
    records = schema.records_only(labels)
    assert {i.label for i in records.labels} == labels
    assert {r.type for r in records.relationships} == {"PART_OF", "MADE_BY", "CONCERNS"}
    # PART_OF stays allowed: it joins a part to its press, though it also joins chunks to documents
    assert schema.names_outside(labels) == {
        "Chunk", "Concept", "Document", "Mention", "Observation", "ABOUT", "FROM", "HAS_OBSERVATION",
        "MENTIONS", "OBJECT", "REFERS_TO", "SUBJECT",
    }  # fmt: skip


def test_kg_qa_asks_records_plus_vector_over_the_record_layer_only(driver, tmp_path):
    build(driver)
    gold_file = qa_gold(
        tmp_path,
        [{"id": "Q1", "type": "aggregation", "question": "How many presses are there?",
          "expected": {"number": 2}, "route": "exact", "sql": "SELECT 2"}],
    )  # fmt: skip
    plans = [  # the first reads claims, which this system may not; the retry counts the records
        plan({"op": "find_claims", "predicate": "HAS_CONDITION"}, {"op": "count", "input": 0}),
        plan({"op": "filter_records", "label": "Press"}, {"op": "count", "input": 0}),
    ]
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        return plans.pop(0)

    tracker = RecordingTracker()
    out = tmp_path / "out"
    run_stages(
        qa_context(driver, out, script, tracker), PipelineState(gold=gold_file), [QAStage("records_vector")]
    )

    run = tracker.run("qa_records_vector")
    assert run.logged_params["system"] == "records_vector" and "planner_prompt_version" in run.logged_params
    assert "qa_hops" not in run.logged_params  # no traversal: its chunk source is vector search
    metrics = run.logged_metrics
    assert metrics["answer_accuracy"] == 1.0 and (metrics["plans_proposed"], metrics["plans_refused"]) == (
        2,
        1,
    )
    (answer,) = load_system_answers(out / "answers_records_vector.jsonl")
    assert answer.system == "records_vector" and answer.number == 2.0
    assert "reads the records only" in answer.plan.attempts[0].issues[0]
    # the planner was never shown the text layer
    assert all(":Chunk" not in p and ":Observation" not in p and "Claim patterns" not in p for p in prompts)
