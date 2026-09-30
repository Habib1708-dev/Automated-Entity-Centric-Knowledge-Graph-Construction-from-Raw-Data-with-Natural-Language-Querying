"""The query stage against Neo4j (R71): each traversal pattern on a hand-made graph, the hop limit, walks that
must not pass through documents, the node names and chunks the store reads, the chunk vector index, and
`kg qa`'s stage end to end with a scripted reader (params, metrics and the answers file). Needs Neo4j."""

import json
import time

from kgbuilder.config import Settings
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.qa_stages import QAStage
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.answers import load_system_answers
from kgbuilder.query.exact import CypherParameter, CypherProposal
from kgbuilder.query.graph_store import Neo4jGraphStore
from kgbuilder.query.reader import ReaderAnswer, ReaderCitation
from kgbuilder.query.router import RouteChoice
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
       (wobble:Entity {id: 'k-wobble', name: 'wobbles', type: 'Condition', aliases: ['wobbling']}),
       (spindle:Entity {id: 'k-spindle', name: 'spindle', type: 'Component', aliases: ['spindle']}),
       (o1:Observation {id: 'o1', predicate: 'HAS_CONDITION', polarity: 'negative'}),
       (o1)-[:SUBJECT]->(spindle), (o1)-[:OBJECT]->(wobble), (o1)-[:FROM]->(c1),
       (press)-[:HAS_OBSERVATION]->(o1), (c5)-[:MENTIONS]->(wobble), (spindle)-[:REFERS_TO]->(part)
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
    reached = Neo4jGraphStore(driver, PLAN, hops=2).reach([ids["Quill Press"]], ["k-wobble", "k-spindle"])
    # the press's observations and the documents about it
    assert reached["thing_observations"] == {"notes.md#0", "notes.md#1", "both.md#0"}
    # the claims with a kind at either end, and the chunks that mention a kind
    assert reached["kind_observations"] == {"notes.md#0", "log.md#1"}
    # part and ticket one hop away, the maker two; the other press only through a document: not related
    assert reached["related_records"] == {"record/Ticket/T-1#0", "log.md#0", "log.md#2", "log.md#3"}
    # the kind "spindle" refers to the part record
    assert reached["referred_records"] == {"log.md#3"}


def test_the_hop_limit_bounds_the_related_records(driver):
    ids = build(driver)
    reached = Neo4jGraphStore(driver, PLAN, hops=1).reach([ids["Quill Press"]], [])
    assert reached["related_records"] == {"record/Ticket/T-1#0", "log.md#0", "log.md#3"}  # no maker
    assert reached["kind_observations"] == set() and reached["referred_records"] == set()


def test_the_store_reads_node_names_and_chunks_in_the_order_asked(driver):
    build(driver)
    store = Neo4jGraphStore(driver, PLAN, hops=2)
    names = {(n.kind, n.name, tuple(n.aliases)) for n in store.node_names()}
    assert ("thing", "Quill Press", ()) in names and ("thing", "T-1", ()) in names  # a key names a ticket
    assert ("kind", "wobbles", ("wobbling",)) in names
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
    kind = next(info for info in schema.labels if info.label == "Entity")
    assert "aliases (LIST<STRING>) e.g. ['spindle'], ['wobbling']" in text and kind.count == 2


class AxisEmbedder:
    """Every text on the first axis, so a chunk on that axis ranks first."""

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_kg_qa_answers_every_question_writes_the_answers_and_logs_what_code_can_score(driver, tmp_path):
    build(driver)
    out = tmp_path / "out"
    out.mkdir()
    (out / PLAN_FILE).write_text(PLAN.model_dump_json(), encoding="utf-8")
    gold = QAGold.model_validate(
        {
            "dataset": "test",
            "written_by": "claude",
            "date": "2026-09-30",
            "corpus": {
                "data_dir": "x", "chunk_max_chars": 1500, "chunk_min_chars": 200, "chunk_overlap_chars": 0
            },
            "questions": [
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
        }
    )  # fmt: skip
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(gold.model_dump_json(), encoding="utf-8")
    reply = ReaderAnswer(
        entities=["Quill Press"],
        citations=[ReaderCitation(chunk_id="notes.md#0", quote="the spindle wobbles")],
    )
    count = "MATCH (d:Document) WHERE d.doc_id = {} RETURN count(d) AS n"
    proposals = [  # the first quotes its value and is refused; the retry passes it as a parameter
        CypherProposal(cypher=count.format("'notes.md'"), answer_form="number"),
        CypherProposal(
            cypher=count.format("$doc"),
            parameters=[CypherParameter(name="doc", value="notes.md")],
            answer_form="number",
        ),
    ]

    def script(prompt, schema):
        if schema is RouteChoice:
            return RouteChoice(route="exact" if "How many" in prompt else "retrieval", reason="r")
        if schema is CypherProposal:
            return proposals.pop(0)
        return reply

    tracker = RecordingTracker()
    settings = Settings(qa_model="reader-model", qa_top_k=2, qa_link_neighbours=0, qa_workers=1)
    ctx = PipelineContext(
        settings=settings,
        driver=driver,
        out=out,
        llm=ScriptedLLM(script),
        embedder=AxisEmbedder(),
        tracker=tracker,
    )

    run_stages(ctx, PipelineState(gold=gold_file), [QAStage("graph")])

    run = tracker.run("qa_graph")
    assert run.logged_params["system"] == "graph" and run.logged_params["model"] == "reader-model"
    assert {
        "prompt_version", "router_prompt_version", "cypher_prompt_version", "qa_hops", "qa_link_fuzzy",
        "qa_cypher_limit", "qa_cypher_timeout_s", "gold_hash", "workers",
    } <= set(run.logged_params)  # fmt: skip
    metrics = run.logged_metrics
    # Q1 read from the text, Q2 counted by the retried query; Q3 is free text and waits for the judge
    assert metrics["answer_accuracy"] == 1.0 and metrics["answer_accuracy_n"] == 2
    assert metrics["answers_unjudged"] == 1 and metrics["route_accuracy"] == 1.0
    assert (metrics["routed_exact"], metrics["exact_answered"], metrics["exact_fallbacks"]) == (1, 1, 0)
    assert (metrics["cypher_proposals"], metrics["cypher_refused"]) == (2, 1)
    assert metrics["recall_at_k"] == 1.0  # Q1's and Q3's gold chunks are among the two shown
    assert metrics["citation_faithfulness"] == 1.0 and metrics["questions_unlinked"] == 0
    answers = load_system_answers(out / "answers_graph.jsonl")
    assert [a.question_id for a in answers] == ["Q1", "Q2", "Q3"]
    assert answers[0].retrieved[0] == "notes.md#0" and answers[0].trace.linked
    # the exact answer keeps both proposals: the refused one with its reason, the one that ran with a LIMIT
    refused, ran = answers[1].exact.attempts
    assert "parameter" in refused.issues[0] and not ran.issues and ran.cypher.endswith("LIMIT 100")
    assert answers[1].number == 1.0 and answers[1].retrieved == []
    assert any(path.endswith("answers_graph.jsonl") for path in run.artifacts)
    report = json.loads((out / "qa_report_graph.json").read_text(encoding="utf-8"))
    assert report["k"] == 2
