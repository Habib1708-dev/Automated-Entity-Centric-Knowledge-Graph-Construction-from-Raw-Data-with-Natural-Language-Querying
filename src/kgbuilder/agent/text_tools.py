"""The agent's text tools (R133): search every passage, and every claim, by meaning and by words.

Role in the pipeline: the agent (R135) uses them when the question is about text no node leads to, or to
check what the graph walk found against the passages themselves.
Design: Strategy, one class per tool (tool.py), built on the hybrid retrievers R128 measured
(`chunk_dense` and `chunk_lexical`, `claim_dense` and `claim_lexical`), each list read `limit` deep and
the two fused by reciprocal rank fusion (`hybrid/fusion.py`). A claim comes with its opposite-truth
siblings (the same triple stated and denied), as the claim retrievers read them, so a denial is never shown
without the statement it denies; the truth fields are the stored ones, never read from a sentence.
Not here: the graph-walk tools (graph_tools.py), the Cypher (node_reader.py, hybrid/unit_store.py).
"""

from pydantic import BaseModel, Field

from ..hybrid.fusion import rrf
from ..hybrid.retrievers import ChunkDense, ChunkLexical, ClaimRetriever
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder
from ..query.graph_store import GraphStore
from .node_reader import NodeReader
from .results import ChunksResult, ChunkText, ClaimsResult


class SearchArguments(BaseModel):
    query: str = Field(description="What the text should say, in words.")


class SearchText:
    """`search_text`: the chunks nearest the query and scored highest by its words, fused, at most `limit`."""

    name = "search_text"
    description = "Search every text passage by meaning and by words, best first."
    arguments = SearchArguments

    def __init__(self, store: GraphStore, units: UnitStore, embedder: Embedder, rrf_k: int, limit: int):
        self._lists = (ChunkDense(store), ChunkLexical(units))
        self._store = store
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._limit = limit

    def run(self, arguments: SearchArguments) -> ChunksResult:
        vector = self._embedder.embed([arguments.query])[0]
        lists = {r.name: r.retrieve(arguments.query, vector, self._limit).chunks for r in self._lists}
        fused = rrf(lists, self._rrf_k)
        chunks = [
            ChunkText(chunk_id=c.chunk_id, context=c.context, text=c.text)
            for c in self._store.chunks(fused[: self._limit])
        ]
        return ChunksResult(chunks=chunks, total=len(fused), note="" if chunks else "No passage found.")


class SearchClaims:
    """`search_claims`: the claims nearest the query and scored highest by its words, fused, each followed by
    its opposite-truth siblings, at most `limit` in all."""

    name = "search_claims"
    description = (
        "Search the claims read from the text by meaning and by words, best first. Each claim says whether "
        "the text states or denies it, whether it only may hold, and under what condition; a claim the text "
        "also denies, or states, comes with its opposite."
    )
    arguments = SearchArguments

    def __init__(self, units: UnitStore, reader: NodeReader, embedder: Embedder, rrf_k: int, limit: int):
        self._lists = (ClaimRetriever(units, "dense"), ClaimRetriever(units, "lexical"))
        self._reader = reader
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._limit = limit

    def run(self, arguments: SearchArguments) -> ClaimsResult:
        vector = self._embedder.embed([arguments.query])[0]
        hits = {r.name: r.retrieve(arguments.query, vector, self._limit).claims for r in self._lists}
        siblings = {h.id: h.siblings for found in hits.values() for h in found}
        fused = rrf({name: [h.id for h in found] for name, found in hits.items()}, self._rrf_k)
        ordered = list(dict.fromkeys(i for claim in fused for i in (claim, *siblings[claim])))
        claims = self._reader.claims(ordered[: self._limit])
        return ClaimsResult(claims=claims, note="" if claims else "No claim found.")
