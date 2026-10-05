"""The question-answering systems: the graph system, the vector-only baseline, and records plus vector RAG.

Role in the pipeline: `kg ask` and `kg qa` (pipeline/qa_stages.py) build them and ask them the questions.
Design: Strategy. The systems implement `QASystem.answer` and share the reader, the number of chunks shown
(k) and the embedder, so a difference in their scores comes only from how they choose:
- `VectorBaseline`: the k chunks nearest the question in the chunk vector index, nothing from the graph;
- `GraphRetrieval`, the graph's retrieval route: link the question's names to nodes (names.py), follow the
  fixed traversal patterns (traversal.py), rank the chunks they reach by similarity to the question, keep k.
  When nothing is linked or reached, the reader gets nothing: falling back to vector search would hide the
  graph's own failure;
- `RoutedGraph`, the graph system `kg qa` scores: the router (router.py) sends a question to the exact
  route (exact.py) or to `GraphRetrieval`, and an exact route that cannot answer falls back to retrieval;
- records plus vector RAG (R73, `build_records_vector`): the same router and exact route over the record
  layer alone (no claims, no documents), with `VectorBaseline` as its retrieval route. Against the graph
  system it shows what the extracted claims add; against the vector baseline, what the records add.
Not here: the prompts (reader.py, router.py, exact.py), Cypher (graph_store.py).
"""

import math
from typing import Protocol

from pydantic import BaseModel

from ..core.similarity import dot, unit_vector
from ..llm.base import Embedder, LLMClient
from ..validation.qa import Citation
from ..validation.qa_gold import Route
from .answers import RetrievalTrace, ShownChunk, SystemAnswer
from .exact import RECORDS_PROMPT, ExactRoute
from .graph_store import CypherStore, GraphStore, StoredChunk
from .names import NameLinker
from .reader import Reader
from .router import Router

# the records-plus-vector system's name: its answers file, its MLflow run (`qa_records_vector`)
RECORDS_VECTOR = "records_vector"


class QASystem(Protocol):
    """Answers one question; `name` labels its answers file and its MLflow run."""

    name: str

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        """The answer, with the chunks shown to the reader. Raises `LLMResponseError` if the model fails."""
        ...


class VectorBaseline:
    """The k chunks nearest the question in the chunk vector index, read by the shared reader."""

    name = "vector"

    def __init__(self, store: GraphStore, embedder: Embedder, reader: Reader, k: int):
        self._store = store
        self._embedder = embedder
        self._reader = reader
        self._k = k

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        vector = self._embedder.embed([question])[0]
        chosen = self._store.chunks(self._store.nearest_chunks(vector, self._k))
        return _answer(self.name, question_id, question, chosen, self._reader)


class GraphRetrieval:
    """The graph's retrieval route: linked names -> traversal patterns -> ranked chunks -> reader."""

    name = "graph"

    def __init__(self, store: GraphStore, embedder: Embedder, linker: NameLinker, reader: Reader, k: int):
        self._store = store
        self._embedder = embedder
        self._linker = linker
        self._reader = reader
        self._k = k

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        vector = self._embedder.embed([question])[0]
        linked = self._linker.link(question, vector)
        reached = self._store.reach(
            [n.node_id for n in linked if n.kind == "thing"], [n.node_id for n in linked if n.kind == "kind"]
        )
        ranked = rank(vector, self._store.chunks(sorted(set().union(*reached.values()))))
        trace = RetrievalTrace(
            linked=linked,
            candidates=[c.chunk_id for c in ranked],
            reached_by={pattern: sorted(ids) for pattern, ids in reached.items()},
        )
        return _answer(self.name, question_id, question, ranked[: self._k], self._reader, trace)


def build_graph_retrieval(
    store: GraphStore, embedder: Embedder, reader: Reader, k: int, fuzzy: float, neighbours: int
) -> GraphRetrieval:
    """The graph route over `store`, with its node names embedded once (one batched call)."""
    names = store.node_names()
    vectors = embedder.embed([n.name for n in names]) if names and neighbours else None
    return GraphRetrieval(store, embedder, NameLinker(names, vectors, fuzzy, neighbours), reader, k)


class QASettings(BaseModel):
    """The settings a graph system is built with (from config.Settings, passed down by the stage)."""

    model: str
    temperature: float
    top_k: int
    link_fuzzy: float
    link_neighbours: int
    cypher_limit: int


class RoutedGraph:
    """A routed system: the router chooses a route; the exact route answers from the rows of a checked
    query, and when it cannot (both proposals refused or failing), the question falls back to retrieval.
    The answer keeps the router's label (scored as route accuracy) and the exact route's trace, and carries
    this system's `name` whichever route answered. Template shared by the graph system (`GraphRetrieval`)
    and records plus vector RAG (`VectorBaseline`)."""

    def __init__(self, router: Router, exact: ExactRoute, retrieval: QASystem, name: str = "graph"):
        self._router = router
        self._exact = exact
        self._retrieval = retrieval
        self.name = name

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        route = self._router.route(question)
        if route != Route.EXACT:
            read = self._retrieval.answer(question_id, question)
            return read.model_copy(update={"route": route, "system": self.name})
        outcome = self._exact.answer(question)
        if not outcome.trace.answered:
            fallback = self._retrieval.answer(question_id, question)
            return fallback.model_copy(update={"route": route, "exact": outcome.trace, "system": self.name})
        return SystemAnswer(
            question_id=question_id,
            system=self.name,
            route=route,
            entities=outcome.entities,
            number=outcome.number,
            exact=outcome.trace,
        )


class QueryStore(GraphStore, CypherStore, Protocol):
    """A store the whole graph system can use: retrieval reads and the exact route's reads."""


def build_routed_graph(
    store: QueryStore, embedder: Embedder, llm: LLMClient, reader: Reader, settings: QASettings
) -> RoutedGraph:
    """The graph system over `store`: the schema read once for the router and text2cypher, the node names
    embedded once for the retrieval route."""
    exact = ExactRoute(store, llm, settings.model, settings.cypher_limit, settings.temperature)
    router = Router(llm, settings.model, exact.schema_text, settings.temperature)
    retrieval = build_graph_retrieval(
        store, embedder, reader, settings.top_k, settings.link_fuzzy, settings.link_neighbours
    )
    return RoutedGraph(router, exact, retrieval)


def build_records_vector(
    store: QueryStore,
    embedder: Embedder,
    llm: LLMClient,
    reader: Reader,
    settings: QASettings,
    record_labels: set[str],
) -> RoutedGraph:
    """Records plus vector RAG over `store` (R73): the router and the exact route see only the record layer
    (`record_labels`, the plan's labels), code refuses a query naming anything outside it, and the vector
    baseline answers what the exact route does not."""
    full = store.schema()
    exact = ExactRoute(
        store,
        llm,
        settings.model,
        settings.cypher_limit,
        settings.temperature,
        schema=full.records_only(record_labels),
        prompt=RECORDS_PROMPT,
        excluded=full.names_outside(record_labels),
    )
    router = Router(llm, settings.model, exact.schema_text, settings.temperature)
    vector = VectorBaseline(store, embedder, reader, settings.top_k)
    return RoutedGraph(router, exact, vector, name=RECORDS_VECTOR)


def rank(vector: list[float], chunks: list[StoredChunk]) -> list[StoredChunk]:
    """`chunks` by cosine similarity to `vector`, most similar first; chunks without a vector come last,
    and ties go by chunk id, so the order never depends on how the graph returned them."""
    query = unit_vector(vector)

    def similarity(chunk: StoredChunk) -> float:
        return dot(query, unit_vector(chunk.embedding)) if chunk.embedding else -math.inf

    return sorted(chunks, key=lambda c: (-similarity(c), c.chunk_id))


def _answer(
    system: str,
    question_id: str,
    question: str,
    chosen: list[StoredChunk],
    reader: Reader,
    trace: RetrievalTrace | None = None,
) -> SystemAnswer:
    shown = [ShownChunk(chunk_id=c.chunk_id, context=c.context, text=c.text) for c in chosen]
    reply = reader.read(question, shown)
    return SystemAnswer(
        question_id=question_id,
        system=system,
        retrieved=[c.chunk_id for c in shown],
        entities=reply.entities,
        number=reply.number,
        text=reply.text,
        citations=[Citation(chunk_id=c.chunk_id, quote=c.quote) for c in reply.citations],
        shown=shown,
        trace=trace,
    )
