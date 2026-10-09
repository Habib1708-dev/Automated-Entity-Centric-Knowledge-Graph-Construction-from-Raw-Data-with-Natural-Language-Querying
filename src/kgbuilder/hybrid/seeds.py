"""Start nodes for chunk and claim retrieval (R126): the nodes the ranked chunks concern, or the ranked claims
join, so every retrieval technique can be scored on finding graph starting nodes, not only the cards.

Role in the pipeline: `build_hybrid` (source.py) wraps the chunk and claim retrievers with these when a
system's settings ask for node seeds (`HybridSettings.node_seeds`, the single-technique systems of plan
R126-R128); `kg retrieve-eval` then scores their seeds like the cards'. The sealed `hybrid` asks for none.
Design: Decorator. `ChunkSeeds` and `ClaimSeeds` are retrievers around a retriever: its chunks, claims and
name pass through unchanged, and only `seeds` is added. The seeds follow the graph's own navigation contract
(anchor/navigation.py), as read by `GraphStore.chunk_nodes` / `claim_nodes`, in an order fixed before any
number (the plan's pre-registration): a chunk gives the records it is about, then the nodes its mentions
refer to in the order their names first occur in its text; a claim gives its subject's and object's
entities, then the things it is attached to. Ranked items are read best first and every node is kept once.
Must not: rank chunks, call a model, or read anything but the store.
"""

from collections.abc import Callable, Iterable

from ..query.graph_store import ChunkNodes, GraphStore
from .retrievers import Retrieved, Retriever


def chunk_order(text: str, nodes: ChunkNodes) -> list[str]:
    """The refs of what one chunk concerns, in reading order: the records it is about (sorted), then every
    node its mentions refer to by where one of its names first occurs in `text` (case-insensitive); a node
    whose names are not found comes after, and ties go by ref."""
    lowered = text.casefold()

    def first(names: list[str]) -> tuple[int, int]:
        found = [at for name in names if name and (at := lowered.find(name.casefold())) >= 0]
        return (0, min(found)) if found else (1, 0)

    named = sorted(nodes.named, key=lambda n: (*first(n.names), n.ref))
    return _unique([*nodes.about, *(n.ref for n in named)])


class ChunkSeeds:
    """A chunk retriever whose seeds are the nodes its ranked chunks concern, best chunk first (Decorator)."""

    def __init__(self, inner: Retriever, store: GraphStore):
        self.name = inner.name
        self._inner = inner
        self._store = store

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        got = self._inner.retrieve(question, vector, depth)
        nodes = self._store.chunk_nodes(got.chunks)
        texts = {c.chunk_id: c.text for c in self._store.chunks(got.chunks)}
        seeds = (ref for c in got.chunks if c in nodes for ref in chunk_order(texts.get(c, ""), nodes[c]))
        return got.model_copy(update={"seeds": _unique(seeds)})


class ClaimSeeds:
    """A claim retriever whose seeds are the nodes its ranked claims join, best claim first (Decorator).
    Siblings bring chunks only: a sibling joins the same canonical ends."""

    def __init__(self, inner: Retriever, store: GraphStore):
        self.name = inner.name
        self._inner = inner
        self._store = store

    def retrieve(self, question: str, vector: list[float], depth: int) -> Retrieved:
        got = self._inner.retrieve(question, vector, depth)
        joined = self._store.claim_nodes([hit.id for hit in got.claims])
        seeds = (ref for hit in got.claims for ref in joined.get(hit.id, []))
        return got.model_copy(update={"seeds": _unique(seeds)})


# which retrievers gain seeds, and how; the card retrievers and the name-linker route have their own
SEEDERS: dict[str, Callable[[Retriever, GraphStore], Retriever]] = {
    "chunk_dense": ChunkSeeds,
    "chunk_lexical": ChunkSeeds,
    "claim_dense": ClaimSeeds,
    "claim_lexical": ClaimSeeds,
}


def with_node_seeds(retriever: Retriever, store: GraphStore) -> Retriever:
    """`retriever` with node seeds when it is a chunk or claim retriever; any other retriever as it is."""
    seeder = SEEDERS.get(retriever.name)
    return seeder(retriever, store) if seeder else retriever


def _unique(items: Iterable[str]) -> list[str]:
    """`items` in order, each once."""
    return list(dict.fromkeys(items))
