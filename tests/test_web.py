"""The web interface's API (R222, src/kgbuilder/web/) without Neo4j, a model or a network:
- the datasets file parses, and an id named twice is refused;
- a build's graph read from a small hand-made snapshot:
  - records with their source rows; table relationships with their rows;
  - text claims between the things their mentions refer to; a mention no assignment covers stands for itself;
  - the schema by type, the busiest part, a neighbourhood, name search, a thing's detail and a passage;
- the stream events: a tool call with its result cut, and each LLM request priced as the MLflow runs price it;
- the ask service: refusing a dataset whose graph is not the loaded one, Neo4j down, and the working
  folder made from the build (and not made by reading which graph is loaded);
- the routes: which tab is loaded, unknown ids, a streamed question, a refused variant, one question at a
  time, an unexpected error that still ends the stream;
- `qa_parts` carrying the observer to the systems;
- the committed OpenAPI file equal to the API's schema, and the built page served beside the API (R223);
- the load job (R222b): the cached recipe in order under the preset, a failed step stopping it, a wrong
  digest not ok, and the route streaming under the one lock."""

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable

from kgbuilder.agent.results import NodeHit, NodesResult
from kgbuilder.audit.inputs import Record, Relation
from kgbuilder.audit.snapshot import GraphSnapshot, SnapshotClaim, SnapshotMention
from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError
from kgbuilder.llm.base import LLMCallRecord
from kgbuilder.pipeline import PipelineContext, PipelineState
from kgbuilder.pipeline.qa_systems import qa_parts
from kgbuilder.query.answers import AgentCall, SystemAnswer
from kgbuilder.resolution.identity_graph import Assignment
from kgbuilder.structured.plan import ConstructionPlan, NodeRule, RelationshipRule
from kgbuilder.text.chunking import Chunk
from kgbuilder.tracking.base import ModelPrice
from kgbuilder.web import openapi
from kgbuilder.web.app import create_app
from kgbuilder.web.ask import AskService
from kgbuilder.web.datasets import Dataset, read_datasets
from kgbuilder.web.events import (
    RESULT_CHARS,
    AnswerEvent,
    CallEvent,
    ErrorEvent,
    ModelEvent,
    StreamListener,
    StreamObserver,
)
from kgbuilder.web.knowledge import KnowledgeGraph, PropertyRow, SourceRef

from .fakes import ScriptedLLM

# --- datasets ---------------------------------------------------------------------------------------------


def test_the_committed_datasets_file_names_the_five_benchmarked_datasets():
    datasets = read_datasets()
    assert [d.id for d in datasets] == ["furniture", "heldout", "generality", "lighthouses", "ntsb"]
    assert all(len(d.digest) == 12 for d in datasets)


def test_a_datasets_file_naming_an_id_twice_is_refused(tmp_path):
    entry = {"id": "a", "name": "A", "preset": "dev", "build": "out/a", "digest": "x"}
    path = tmp_path / "datasets.yaml"
    path.write_text(json.dumps({"datasets": [entry, entry]}), encoding="utf-8")  # JSON is YAML
    with pytest.raises(ConfigurationError, match="twice"):
        read_datasets(path)
    with pytest.raises(ConfigurationError, match="does not exist"):
        read_datasets(tmp_path / "none.yaml")


# --- the graph of a build ---------------------------------------------------------------------------------

PLAN = ConstructionPlan(
    nodes=[
        NodeRule(source_file="products.csv", label="Product", unique_column="id", properties=["price"],
                 description="A product.", name_column="name"),
        NodeRule(source_file="parts.csv", label="Part", unique_column="id", properties=[],
                 description="A part.", name_column="name"),
    ],
    relationships=[
        RelationshipRule(source_file="parts.csv", relationship_type="PART_OF", from_label="Part",
                         from_column="id", to_label="Product", to_column="product", properties=[]),
    ],
)  # fmt: skip
SENTENCE = "The Lark Kettle lid is loose."


def claim(claim_id: str, subject: str, obj: str) -> SnapshotClaim:
    return SnapshotClaim(id=claim_id, predicate="HAS_PART", subject=subject, object=obj, chunk_id="d.md#0",
                         evidence=SENTENCE, subject_name="", object_name="", derived=False)  # fmt: skip


def mention(mention_id: str, name: str, type_: str) -> SnapshotMention:
    return SnapshotMention(
        id=mention_id, name=name, type=type_, doc_id="d.md", chunks=["d.md#0"], derived=False
    )


def assignment(mention_id: str, kind: str, canonical: str, name: str, type_: str) -> Assignment:
    return Assignment(mention=mention_id, said=name, kind=kind, canonical=canonical, name=name, type=type_,
                      reason="test")  # fmt: skip


def knowledge() -> KnowledgeGraph:
    """A product with a part from the tables, and three claims of one sentence: twice the product has the lid
    (an individual), once the handle (a mention no assignment covers)."""
    snapshot = GraphSnapshot(
        source="out/test",
        records=[
            Record(
                id="Product:P1", label="Product", key="P1", name="Lark Kettle", properties={"price": "20"}
            ),
            Record(id="Part:L1", label="Part", key="L1", name="Lid", properties={}),
        ],
        relations=[Relation(source="Part:L1", type="PART_OF", target="Product:P1")],
        chunks=[Chunk(chunk_id="d.md#0", doc_id="d.md", index=0, text=SENTENCE, context="Kettle review")],
        mentions=[
            mention("m1", "Lark Kettle", "Product"),
            mention("m2", "lid", "Component"),
            mention("m3", "handle", "Component"),
        ],  # fmt: skip
        claims=[claim("c1", "m1", "m2"), claim("c2", "m1", "m3"), claim("c3", "m1", "m2")],
        references=[
            assignment("m1", "record", "Product:P1", "Lark Kettle", "Product"),
            assignment("m2", "individual", "i1", "lid", "Component"),
        ],  # fmt: skip
        documents_about=[],
        sections_about=[],
        attachments=[],
        extracted_mentions_edges=3,
        derived_object_collisions=0,
    )
    return KnowledgeGraph(snapshot, PLAN)


def test_the_schema_counts_things_by_kind_and_type_and_facts_by_type_and_origin():
    view = knowledge().schema()
    assert {(t.kind, t.type, t.count) for t in view.things} == {
        ("record", "Product", 1), ("record", "Part", 1), ("individual", "Component", 1),
        ("mention", "Component", 1),
    }  # fmt: skip
    assert [(x.source_type, x.type, x.target_type, x.origin, x.count) for x in view.links] == [
        ("Product", "HAS_PART", "Component", "text", 3),
        ("Part", "PART_OF", "Product", "table", 1),
    ]
    assert (view.chunks, view.documents) == (1, 1)


def test_the_overview_is_the_busiest_things_with_the_facts_among_them_as_counted_links():
    view = knowledge().overview(2)
    assert [(t.id, t.degree) for t in view.things] == [("Product:P1", 4), ("i1", 2)]
    assert [(x.source, x.target, x.type, x.count) for x in view.links] == [
        ("Product:P1", "i1", "HAS_PART", 2)
    ]
    assert view.total == 4


def test_a_neighbourhood_puts_the_neighbours_sharing_most_facts_first():
    graph = knowledge()
    view = graph.neighbourhood("Product:P1", 3)
    assert {t.id for t in view.things} == {
        "Product:P1",
        "i1",
        "Part:L1",
    }  # m3 ties Part:L1 and sorts after it
    assert graph.neighbourhood("nothing", 3) is None


def test_search_puts_names_starting_with_the_text_first_then_the_busiest():
    assert [t.name for t in knowledge().search("L", 10)] == ["Lark Kettle", "lid", "Lid", "handle"]
    assert knowledge().search("  ", 10) == []


def test_a_things_detail_has_its_properties_with_their_row_and_its_facts_table_first():
    detail = knowledge().detail("Product:P1", 2)
    assert detail.properties == [PropertyRow(name="price", value="20", source="products.csv row id=P1")]
    assert detail.total_facts == 4 and len(detail.facts) == 2
    table, text = detail.facts
    assert (table.origin, table.row) == ("table", "parts.csv row id=L1 -> product=P1")
    assert (text.origin, text.evidence, text.chunk_id) == ("text", SENTENCE, "d.md#0")
    assert [t.id for t in detail.neighbours] == ["Part:L1", "i1"]
    assert knowledge().detail("nothing", 2) is None


def test_a_passage_is_found_by_its_id():
    chunk = knowledge().chunk("d.md#0")
    assert (chunk.doc_id, chunk.context, chunk.text) == ("d.md", "Kettle review", SENTENCE)
    assert knowledge().chunk("d.md#9") is None


# --- stream events ----------------------------------------------------------------------------------------


def test_a_run_call_is_emitted_with_its_result_cut_and_a_refused_one_without():
    events = []
    observer = StreamObserver(events.append)
    hits = [NodeHit(ref=f"k{i}", kind="concept", label="Thing", title="x" * 100) for i in range(60)]
    observer.call_done(AgentCall(turn=1, tool="find", arguments="{}"), NodesResult(nodes=hits, total=60))
    observer.call_done(AgentCall(turn=2, tool="nope", arguments="{}", refused="no tool"), None)
    observer.model_replied(2, 12.5)
    run, refused, model = events
    assert isinstance(run, CallEvent) and run.cut and len(run.result) == RESULT_CHARS
    assert (refused.result, refused.cut, refused.call.refused) == ("", False, "no tool")
    assert model == ModelEvent(turn=2, ms=12.5)


def test_each_llm_request_is_priced_and_the_question_costs_their_sum():
    events = []
    listener = StreamListener(events.append, {"m": ModelPrice(input=1.0, output=2.0)})
    live = LLMCallRecord(
        model="m", temperature=0, prompt="p", response="r", latency_s=0.5, cache_hit=False,
        prompt_tokens=1_000_000, completion_tokens=250_000, thinking_tokens=250_000,
    )  # fmt: skip
    cached = live.model_copy(update={"cache_hit": True, "prompt_tokens": None, "completion_tokens": None,
                                     "thinking_tokens": None})  # fmt: skip
    listener(live)
    listener(cached)
    assert [e.cost_usd for e in events] == [2.0, 0.0]  # 1M input at $1 + 0.5M output at $2; a hit is free
    assert listener.cost_usd() == 2.0


# --- the ask service --------------------------------------------------------------------------------------


def a_build(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    (build / "staging").mkdir(parents=True)
    for name in ("plan.json", "text_schema.json", "profile.json"):
        (build / name).write_text("{}", encoding="utf-8")
    (build / "staging" / "kettles.csv").write_text("id\nK1\n", encoding="utf-8")
    return build


def service(tmp_path: Path, digest) -> AskService:
    @contextmanager
    def contexts(preset, folder, listener):
        yield SimpleNamespace(driver=object())

    return AskService(contexts, None, tmp_path / "web", read_digest=digest)


def test_a_question_to_a_dataset_whose_graph_is_not_loaded_is_an_error_event(tmp_path):
    dataset = Dataset(id="kettles", name="Kettles", preset="dev", build=a_build(tmp_path), digest="abc")
    events = []
    service(tmp_path, lambda driver: "other").ask(dataset, "Which kettle?", "agent", events.append)
    [event] = events
    assert isinstance(event, ErrorEvent) and "load this dataset first" in event.message
    folder = tmp_path / "web" / "kettles"  # made from the build, as the reload recipe lays it out
    assert (folder / "plan.json").exists() and (folder / "staging" / "kettles.csv").exists()


def test_neo4j_down_is_no_loaded_digest_and_an_error_event_on_a_question(tmp_path):
    dataset = Dataset(id="kettles", name="Kettles", preset="dev", build=a_build(tmp_path), digest="abc")

    def down(driver):
        raise ServiceUnavailable("refused")

    asker = service(tmp_path, down)
    assert asker.loaded_digest(dataset) is None
    assert not (tmp_path / "web" / "kettles").exists()  # reading what is loaded makes no working folder
    events = []
    asker.ask(dataset, "Which kettle?", "agent", events.append)
    assert "Neo4j cannot be reached" in events[0].message


# --- the routes -------------------------------------------------------------------------------------------

DATASETS = [
    Dataset(id="kettles", name="Kettles", preset="dev", build=Path("out/k"), digest="aaa"),
    Dataset(id="lamps", name="Lamps", preset="dev", build=Path("out/l"), digest="bbb"),
]


class FakeAsker:
    """Neo4j holds the lamps; a question emits a model reply and an answer, or what `script` emits."""

    def __init__(self, script=None):
        self.script = script
        self.asked: list[tuple[str, str, str]] = []

    def loaded_digest(self, any_dataset):
        return "bbb"

    def ask(self, dataset, question, variant, emit):
        self.asked.append((dataset.id, question, variant))
        if self.script:
            self.script(emit)
            return
        emit(ModelEvent(turn=1, ms=5.0))
        answer = SystemAnswer(question_id="ask", system=variant, text="The Wren lamp.")
        emit(AnswerEvent(answer=answer, seconds=1.0, cost_usd=0.01))


def client(asker=None) -> TestClient:
    return TestClient(
        create_app(DATASETS, lambda d: knowledge(), asker or FakeAsker(), ["agent", "agent_sql"])
    )


def events_of(response) -> list[dict]:
    return [
        json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def test_the_datasets_route_names_the_loaded_tab_and_the_variants():
    body = client().get("/api/datasets").json()
    assert [(d["id"], d["loaded"]) for d in body["datasets"]] == [("kettles", False), ("lamps", True)]
    assert (body["neo4j"], body["loaded"], body["variants"]) == (True, "lamps", ["agent", "agent_sql"])


def test_the_graph_routes_answer_from_the_builds_graph_and_404_on_unknown_ids():
    c = client()
    assert c.get("/api/datasets/kettles/graph", params={"limit": 2}).json()["total"] == 4
    assert c.get("/api/datasets/kettles/thing", params={"id": "Product:P1"}).json()["total_facts"] == 4
    assert c.get("/api/datasets/kettles/chunk", params={"id": "d.md#0"}).json()["text"] == SENTENCE
    assert c.get("/api/datasets/kettles/search", params={"q": "lid"}).json()[0]["name"] == "lid"
    assert c.get("/api/datasets/kettles/schema").json()["chunks"] == 1
    assert c.get("/api/datasets/nope/schema").status_code == 404
    assert c.get("/api/datasets/kettles/thing", params={"id": "nothing"}).status_code == 404
    assert c.get("/api/datasets/kettles/graph", params={"limit": 1000}).status_code == 422


def test_a_question_streams_its_events_in_order():
    asker = FakeAsker()
    response = client(asker).post(
        "/api/datasets/lamps/ask", json={"question": "Which lamp?", "variant": "agent"}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [e["event"] for e in events_of(response)] == ["model", "answer"]
    assert asker.asked == [("lamps", "Which lamp?", "agent")]


def test_a_variant_not_offered_is_refused_before_asking():
    asker = FakeAsker()
    response = client(asker).post("/api/datasets/lamps/ask", json={"question": "Q?", "variant": "graph"})
    assert response.status_code == 422 and asker.asked == []


def test_one_question_at_a_time():
    started, release = threading.Event(), threading.Event()

    def slow(emit):
        started.set()
        release.wait(5)

    c = client(FakeAsker(slow))
    first = threading.Thread(
        target=lambda: c.post("/api/datasets/lamps/ask", json={"question": "One?", "variant": "agent"})
    )
    first.start()
    assert started.wait(5)
    busy = c.post("/api/datasets/lamps/ask", json={"question": "Two?", "variant": "agent"})
    release.set()
    first.join(5)
    assert busy.status_code == 409


def test_an_unexpected_error_still_ends_the_stream_with_an_error_event():
    def broken(emit):
        raise ValueError("boom")

    response = client(FakeAsker(broken)).post(
        "/api/datasets/lamps/ask", json={"question": "Q?", "variant": "agent"}
    )
    assert events_of(response) == [{"event": "error", "message": "unexpected error: boom"}]


# --- the observer reaches the systems ---------------------------------------------------------------------


def test_the_qa_parts_carry_the_observer_the_agent_is_built_with(tmp_path):
    observer = StreamObserver(lambda event: None)
    ctx = PipelineContext(settings=Settings(), driver=object(), out=tmp_path,
                          llm=ScriptedLLM(lambda p, s: None), embedder=object())  # fmt: skip
    assert qa_parts(ctx, PipelineState(), observer=observer).observer is observer


# --- the page (R223) --------------------------------------------------------------------------------------


def test_the_committed_openapi_file_is_the_apis_schema():
    """frontend/openapi.json is what the page's types are generated from (`npm run api`); a changed model
    without a regenerated file would let the page and the API drift apart."""
    committed = json.loads(Path("frontend/openapi.json").read_text(encoding="utf-8"))
    assert committed == openapi.schema()
    assert "StreamEvent" in committed["components"]["schemas"]


def test_the_built_page_is_served_at_the_root_and_the_api_still_wins(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    c = TestClient(create_app(DATASETS, lambda d: knowledge(), FakeAsker(), ["agent"], tmp_path))
    assert "<title>page</title>" in c.get("/").text
    assert c.get("/api/datasets").json()["loaded"] == "lamps"


def test_an_answers_sources_are_placed_on_the_graph():
    """R223: a passage at the things its claims join, a table query's rows at their records by the table's key
    column, a node at itself; unknown ids and claims are placed nowhere."""
    graph = knowledge()
    assert graph.locate(SourceRef(kind="chunk", id="d.md#0")) == ["Product:P1", "i1", "m3"]
    rows = SourceRef(
        kind="query", id="q-1", sources=["products"], columns=["id", "name"], rows=[["P1", "x"], ["P9", "y"]]
    )
    assert graph.locate(rows) == ["Product:P1"]
    assert graph.locate(SourceRef(kind="node", id="Part:L1")) == ["Part:L1"]
    assert graph.locate(SourceRef(kind="node", id="nothing")) == []
    assert graph.locate(SourceRef(kind="claim", id="c1")) == []
    body = client().post("/api/datasets/kettles/locate", json=[{"kind": "chunk", "id": "d.md#0"}]).json()
    assert body == {"d.md#0": ["Product:P1", "i1", "m3"]}


# --- loading a graph (R222b) ------------------------------------------------------------------------------


class FakeRun:
    """A `kg` runner keeping every command; the step named in `fails` exits 1."""

    def __init__(self, fails: str = ""):
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fails = fails

    def __call__(self, args, env):
        self.calls.append((args, env))
        return (1, "Traceback: boom") if args[2] == self.fails else (0, "ok")


def loader(tmp_path, run, digest="abc"):
    from kgbuilder.web.load import GraphLoader

    return GraphLoader(run, lambda d: Path("data/kettles"), lambda d: digest, tmp_path / "web")


def test_a_load_runs_the_cached_recipe_in_order_under_the_datasets_preset(tmp_path):
    """R222b: the reload recipe of tests/gold/r178/runs.json, every step with the preset and the working
    folder; only the mention pass gets invalid keys, so a cache miss there fails instead of paying."""
    dataset = Dataset(id="kettles", name="Kettles", preset="dev", build=a_build(tmp_path), digest="abc")
    run, events = FakeRun(), []
    loader(tmp_path, run).load(dataset, events.append)
    folder = str(tmp_path / "web" / "kettles")
    assert [a[2] for a, _ in run.calls] == [
        "reset", "build", "ingest-text", "extract", "link", "mention-pass", "resolve", "attach", "index",
    ]  # fmt: skip
    assert all(a[:2] == ["--preset", "dev"] and a[-2:] == ["--out", folder] for a, _ in run.calls)
    assert run.calls[3][0][3:5] == ["--from-build", str(dataset.build)]
    assert run.calls[8][0][3:5] == ["--cards", "template"]
    keys = {a[2]: env for a, env in run.calls}
    assert keys["mention-pass"] == {"GEMINI_API_KEY": "invalid", "GEMINI_FREE_API_KEY": "invalid"}
    assert all(env == {} for name, env in keys.items() if name != "mention-pass")
    assert [(e.name, e.status) for e in events[:2]] == [("reset", "running"), ("reset", "done")]
    assert (events[-1].ok, events[-1].message) == (True, "Kettles is loaded")
    assert (tmp_path / "web" / "kettles" / "staging" / "kettles.csv").exists()


def test_a_failed_step_stops_the_load_and_a_wrong_digest_is_not_ok(tmp_path):
    dataset = Dataset(id="kettles", name="Kettles", preset="dev", build=a_build(tmp_path), digest="abc")
    run, events = FakeRun(fails="resolve"), []
    loader(tmp_path, run).load(dataset, events.append)
    assert [a[2] for a, _ in run.calls][-1] == "resolve"
    failed = events[-2]
    assert (failed.name, failed.status, failed.output) == ("resolve", "failed", "Traceback: boom")
    assert not events[-1].ok and "partial graph" in events[-1].message
    events = []
    loader(tmp_path, FakeRun(), digest="other").load(dataset, events.append)
    assert not events[-1].ok and "not Kettles's abc" in events[-1].message


def test_the_load_route_streams_and_shares_the_one_lock_with_questions(tmp_path):
    from kgbuilder.web.load import LoadedEvent

    class FakeLoader:
        def load(self, dataset, emit):
            emit(LoadedEvent(ok=True, digest="aaa", expected="aaa", seconds=1.0, message="Kettles is loaded"))

    app = create_app(DATASETS, lambda d: knowledge(), FakeAsker(), ["agent"], None, FakeLoader())
    response = TestClient(app).post("/api/datasets/kettles/load")
    assert [e["event"] for e in events_of(response)] == ["loaded"]
    assert client().post("/api/datasets/kettles/load").status_code == 501  # a server without a loader
