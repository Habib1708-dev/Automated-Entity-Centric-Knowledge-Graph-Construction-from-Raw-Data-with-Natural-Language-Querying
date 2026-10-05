"""The query stage against Neo4j (R71): each traversal pattern on a hand-made graph, the hop limit, walks that
must not pass through documents, the node names and chunks the store reads, the chunk vector index, and
`kg qa`'s stage end to end with a scripted planner and reader (params, metrics, plans and the answers
file, R74), and the records-plus-vector system on the same graph: its schema cut to the record layer and
its plans over records only (R73, R74).
Needs Neo4j."""

import json
import time

from kgbuilder.config import Settings
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.qa_stages import QAStage
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.answers import load_system_answers
from kgbuilder.query.graph_store import Neo4jGraphStore
from kgbuilder.query.plan import PlanStep, QueryPlan
from kgbuilder.query.reader import ReaderAnswer, ReaderCitation
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.lexical import CHUNK_VECTOR_INDEX
from kgbuilder.validation.qa_gold import QAGold

from .fakes import RecordingTracker, ScriptedLLM
from .sample_plans import node

PLAN = ConstructionPlan(
    nodes=[
        node("presses.csv", "Press", "press_id", ["name"]),
        node("parts.csv", "Part", "part_id", ["name"]),
        node("makers.csv", "Maker", "maker_id", ["name"]),
        node("tickets.csv", "Ticket", "ticket_id"),
    ],
    relationships=[],
)

# A press with a part (one hop), the part's maker (two hops) and a ticket about the press (one hop); a second
# press shares only a document with the first, which must not make them related. Every chunk has one role.
GRAPH = """
CREATE (press:Press {press_id: 'P1', name: 'Quill Press', year: 2019, active: true,
                     since: date('2020-01-02')}),
       (other:Press {press_id: 'P2', name: 'Lark Press', year: 2016, active: false,
                     since: date('2015-06-30')}),
       (part:Part {part_id: 'S1', name: 'Spindle'}), (maker:Maker {maker_id: 'M1', name: 'Norcast'}),
       (ticket:Ticket {ticket_id: 'T-1'}),
       (part)-[:PART_OF]->(press), (part)-[:MADE_BY]->(maker), (ticket)-[:CONCERNS]->(press),
       (notes:Document {doc_id: 'notes.md'}), (record:Document {doc_id: 'record/Ticket/T-1'}),
       (log:Document {doc_id: 'log.md'}), (both:Document {doc_id: 'both.md'}),
       (notes)-[:ABOUT]->(press), (record)-[:ABOUT]->(ticket),
       (both)-[:ABOUT]->(press), (both)-[:ABOUT]->(other),
       (c1:Chunk {chunk_id: 'notes.md#0', text: 'The spindle wobbles.', context: 'Quill Press notes',
                  embedding: [1.0, 0.0]})-[:PART_OF]->(notes),
       (c2:Chunk {chunk_id: 'notes.md#1', text: 'Delivered late.', context: 'Quill Press notes',
                  embedding: [0.0, 1.0]})-[:PART_OF]->(notes),
       (c3:Chunk {chunk_id: 'record/Ticket/T-1#0', text: 'Ticket text.', context: 'T-1'})
         -[:PART_OF]->(record),
       (c4:Chunk {chunk_id: 'log.md#0', text: 'On the ticket.', context: 'Log'})-[:PART_OF]->(log),
       (c5:Chunk {chunk_id: 'log.md#1', text: 'Some wobbling seen.', context: 'Log'})-[:PART_OF]->(log),
       (c6:Chunk {chunk_id: 'log.md#2', text: 'On the maker.', context: 'Log'})-[:PART_OF]->(log),
       (c7:Chunk {chunk_id: 'log.md#3', text: 'On the part.', context: 'Log'})-[:PART_OF]->(log),
       (c8:Chunk {chunk_id: 'both.md#0', text: 'Both presses.', context: 'Both'})-[:PART_OF]->(both),
       (c9:Chunk {chunk_id: 'log.md#4', text: 'On the other press.', context: 'Log'})-[:PART_OF]->(log),
       (c4)-[:ABOUT]->(ticket), (c6)-[:ABOUT]->(maker), (c7)-[:ABOUT]->(part), (c9)-[:ABOUT]->(other),
       (wobble:Concept {id: 'k-wobble', name: 'wobbles', type: 'Condition'}),
       (wobbles:Mention {id: 'm-wobbles', name: 'wobbles', type: 'Condition', doc_id: 'notes.md'})
         -[:REFERS_TO {canonical: 'k-wobble', name: 'wobbles', kind: 'concept'}]->(wobble),
       (wobbling:Mention {id: 'm-wobbling', name: 'wobbling', type: 'Condition', doc_id: 'log.md'})
         -[:REFERS_TO {canonical: 'k-wobble', name: 'wobbles', kind: 'concept'}]->(wobble),
       (spindle:Mention {id: 'm-spindle', name: 'spindle', type: 'Component', doc_id: 'notes.md'})
         -[:REFERS_TO {canonical: 'Part:S1', name: 'Spindle', kind: 'record', reason: 'name'}]->(part),
       (o1:Observation {id: 'o1', predicate: 'HAS_CONDITION', polarity: 'negative'}),
       (o1)-[:SUBJECT]->(spindle), (o1)-[:OBJECT]->(wobbles), (o1)-[:FROM]->(c1),
       (press)-[:HAS_OBSERVATION]->(o1), (c1)-[:MENTIONS]->(spindle), (c1)-[:MENTIONS]->(wobbles),
       (c5)-[:MENTIONS]->(wobbling)
"""


def build(driver) -> dict[str, str]:
    """Write the graph; return the element id of each domain node by its name or key."""
    driver.execute_query(GRAPH)
    records, _, _ = driver.execute_query(
        "MATCH (n) WHERE n:Press OR n:Part OR n:Maker OR n:Ticket "
        "RETURN coalesce(n.name, n.ticket_id) AS name, elementId(n) AS id"
    )
    return {r["name"]: r["id"] for r in records}


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
    chunks = store.chunks(["notes.md#1", "missing#0", "notes.md#0"])
    assert [c.chunk_id for c in chunks] == ["notes.md#1", "notes.md#0"]
    assert chunks[1].context == "Quill Press notes" and chunks[1].embedding == [1.0, 0.0]


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
