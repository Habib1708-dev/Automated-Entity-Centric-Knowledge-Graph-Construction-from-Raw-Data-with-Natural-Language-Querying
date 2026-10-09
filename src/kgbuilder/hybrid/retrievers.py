"""The hybrid retrievers (R120): each turns a question into one ranked list of chunk ids, its own way.

Role in the pipeline: the hybrid source (R120b) asks the retrievers its settings name, fuses their lists by
rank and hands the best chunks to the shared reader. Every list holds chunk ids, so only source text ever
reaches the reader: a card or a claim sentence only decides which chunks come first.
Design: Strategy, one class per kind of list, all given the question and its vector (embedded once by the
source) and a depth:
- `chunk_dense` / `chunk_lexical`: the chunks nearest the question's vector / scored highest by its words;
- `claim_dense` / `claim_lexical`: claims found by their sentences, each followed by the chunks of its
  opposite-truth siblings, so a denial and what it denies are read together; an optional `ClaimFilter`
  (for a caller that knows the question's polarity) is applied in the store;
- `card_dense` / `card_lexical`: node cards found by their text; each card's node starts the fixed
  traversal patterns (`GraphStore.reach`), its reached chunks are ranked by similarity to the question,
  and the cards then take turns (round-robin), so one hub's fifty chunks cannot crowd out the next card;
  the cards' nodes are the seeds;
- `card_fused` (R132): both card lists read `candidates` deep and fused by RRF, R131's chosen seeding; its
  fused nodes are the seeds and start the same traversal;
- `graph_route`: the name-linker route of the graph system (`GraphRetrieval`), with its links as seeds.
Not here: the queries (unit_store.py), fusion and the source (R120b).
"""

from typing import Literal, Protocol

from pydantic import BaseModel

from ..query.answers import ClaimHit, RetrievalTrace
from ..query.graph_store import GraphStore, StoredChunk
from ..query.ranking import rank
from .lucene import lucene_query
from .seed_fusion import SeedSetting, fuse
from .unit_store import ClaimFilter, UnitStore

Mode = Literal["dense", "lexical"]


class Retrieved(BaseModel):
    """One retriever's answer: ranked chunk ids, and the seeds and claims that led to them."""

    chunks: list[str]
    seeds: list[str] = []  # stable refs of the nodes it started from, best first
    claims: list[ClaimHit] = []


class Retriever(Protocol):
    """Ranks chunks for a question; `name` labels its list in the trace and in the settings."""

    name: str

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        """At most `depth` chunk ids, best first."""
        ...


class ChunkDense:
    """The chunks nearest the question in the chunks' vector index (the vector baseline's list)."""

    name = "chunk_dense"

    def __init__(self, store: GraphStore):
        self._store = store

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        return Retrieved(chunks=self._store.nearest_chunks(vector, depth))


class ChunkLexical:
    """The chunks the chunks' full-text index scores highest for the question's words (BM25)."""

    name = "chunk_lexical"

    def __init__(self, units: UnitStore):
        self._units = units

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        query = lucene_query(question)
        return Retrieved(chunks=self._units.search_chunks(query, depth) if query else [])


class ClaimRetriever:
    """Claims found by their sentences, by vector or by words; each claim's chunk, then its siblings'."""

    def __init__(self, units: UnitStore, mode: Mode, claim_filter: ClaimFilter | None = None):
        self.name = f"claim_{mode}"
        self._units = units
        self._mode = mode
        self._filter = claim_filter or ClaimFilter()

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        if self._mode == "dense":
            hits = self._units.nearest_claims(vector, depth, self._filter)
        else:
            query = lucene_query(question)
            hits = self._units.search_claims(query, depth, self._filter) if query else []
        chunks = _unique(c for hit in hits for c in [hit.chunk_id, *hit.sibling_chunks])
        return Retrieved(chunks=chunks[:depth], claims=hits)


class CardRetriever:
    """Node cards of one representation found by vector or by words; each card's node starts the traversal,
    its chunks are ranked by similarity to the question, and the cards take turns."""

    def __init__(self, units: UnitStore, store: GraphStore, representation: str, mode: Mode):
        self.name = f"card_{mode}"
        self._units = units
        self._store = store
        self._representation = representation
        self._mode = mode

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        return from_cards(self._cards(question, vector, depth), vector, depth, self._units, self._store)

    def _cards(self, question: str, vector: list[float], depth: int) -> list[str]:
        if self._mode == "dense":
            return self._units.nearest_cards(self._representation, vector, depth)
        query = lucene_query(question)
        return self._units.search_cards(self._representation, query, depth) if query else []


class FusedCardRetriever:
    """Both card lists of one representation, each read `candidates` deep and fused by RRF (R132: the
    seeding R131 chose, `hybrid/seed_fusion.py`); the fused nodes are the seeds, best first, and start the
    traversal as the cards of `CardRetriever` do."""

    name = "card_fused"

    def __init__(self, units: UnitStore, store: GraphStore, representation: str, setting: SeedSetting):
        if setting.method != "rrf":  # a reranker would need a model: it is no part of the live seeding
            raise ValueError(f"live card fusion is by RRF, not {setting.method}")
        self._units = units
        self._store = store
        self._representation = representation
        self._setting = setting

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        k = self._setting.candidates
        dense = self._units.nearest_cards(self._representation, vector, k)
        query = lucene_query(question)
        lexical = self._units.search_cards(self._representation, query, k) if query else []
        return from_cards(fuse(dense, lexical, self._setting), vector, depth, self._units, self._store)


def from_cards(
    refs: list[str], vector: list[float], depth: int, units: UnitStore, store: GraphStore
) -> Retrieved:
    """Cards' nodes, best first, made seeds and chunks: a card without a node is left out; each node starts
    the traversal, its reached chunks are ranked by similarity to the question, and the cards take turns."""
    starts = units.card_starts(refs)
    seeds = [ref for ref in refs if ref in starts]
    reached = {}
    for ref in seeds:  # one traversal per card: the turns need each card's own chunks
        start = starts[ref]
        patterns = store.reach(
            [start.node_id] if start.kind == "thing" else [],
            [start.node_id] if start.kind == "kind" else [],
        )
        reached[ref] = set().union(*patterns.values())
    stored = {c.chunk_id: c for c in store.chunks(sorted(set().union(*reached.values())))}
    per_card = [[c.chunk_id for c in rank(vector, [stored[i] for i in reached[ref] if i in stored])]
                for ref in seeds]  # fmt: skip
    return Retrieved(chunks=round_robin(per_card)[:depth], seeds=seeds)


class LinkedRoute(Protocol):
    """A chunk source that ranks from a question vector it is given: the graph system's name-linker route
    (`query.systems.GraphRetrieval.ranked_for`)."""

    def ranked_for(self, question: str, vector: list[float]) -> tuple[list[StoredChunk], RetrievalTrace]:
        """Ranked chunks and the trace, whose `seeds` are the linked nodes' refs."""
        ...


class SourceRetriever:
    """The graph system's name-linker route as one more list (`graph_route`), given the question's vector."""

    name = "graph_route"

    def __init__(self, route: LinkedRoute):
        self._route = route

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        ranked, trace = self._route.ranked_for(question, vector)
        return Retrieved(chunks=[c.chunk_id for c in ranked[:depth]], seeds=trace.seeds)


def round_robin(lists: list[list[str]]) -> list[str]:
    """The first item of each list, then the second of each, and so on; an item already taken is skipped."""
    rounds = max((len(items) for items in lists), default=0)
    return _unique(items[i] for i in range(rounds) for items in lists if i < len(items))


def _unique(items) -> list[str]:
    """`items` in order, each once."""
    return list(dict.fromkeys(items))
