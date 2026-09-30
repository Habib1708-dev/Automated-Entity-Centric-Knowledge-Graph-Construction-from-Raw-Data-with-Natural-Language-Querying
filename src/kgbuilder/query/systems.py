"""The question-answering systems: the graph's retrieval route and the vector-only baseline.

Role in the pipeline: `kg ask` and `kg qa` (pipeline/qa_stages.py) build them and ask them the questions.
Design: Strategy. Both implement `QASystem.answer` and share the reader, the number of chunks shown (k) and
the embedder, so a difference in their scores comes only from how they choose the chunks:
- `VectorBaseline`: the k chunks nearest the question in the chunk vector index, nothing from the graph;
- `GraphRetrieval`: link the question's names to nodes (names.py), follow the fixed traversal patterns
  (traversal.py), rank the chunks they reach by similarity to the question, keep k. When nothing is linked
  or reached, the reader gets nothing: falling back to vector search would hide the graph's own failure.
Not here: the exact route and the router (part b), the reader's prompt (reader.py), Cypher (graph_store.py).
"""

import math
from typing import Protocol

from ..core.similarity import dot, unit_vector
from ..llm.base import Embedder
from ..validation.qa import Citation
from .answers import RetrievalTrace, ShownChunk, SystemAnswer
from .graph_store import GraphStore, StoredChunk
from .names import NameLinker
from .reader import Reader


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
