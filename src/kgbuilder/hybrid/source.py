"""The hybrid chunk source (R120b): the question embedded once, the named retrievers asked, their lists fused
by rank, the best chunks returned with a trace of where each came from.

Role in the pipeline: the `hybrid` system (pipeline/qa_systems.py) reads its best k chunks with the shared
reader, and `kg retrieve-eval` ranks with it; both build it here from the settings.
Design: a `ChunkSource` like the vector baseline and the graph route, so the reader, k and the benchmark are
the very ones the other systems use. Its lists hold chunk ids only: card and claim texts decide the order,
and only source chunks reach the reader. The seeds of the card retrievers and of the name-linker route are
fused by rank too; with `node_seeds` (R126) the chunk and claim retrievers give seeds as well (seeds.py), off
for every system but the single-technique ones. Before anything is asked, `build_hybrid` checks the
retrieval layer the retrievers need: cards of this representation's version, claim sentences of the current
version, vectors of this embedding model and their indexes online; a missing or stale layer is refused
instead of quietly ranking with old texts or another model's vectors.
Not here: the retrievers (retrievers.py), their seeds (seeds.py), fusion (fusion.py), the queries
(unit_store.py).
"""

from pydantic import BaseModel, Field, field_validator

from ..core.errors import ConfigurationError, MissingInputError
from ..graph.index_layer import CHUNK_FULLTEXT_INDEX, CLAIM_FULLTEXT_INDEX, CLAIM_VECTOR_INDEX, card_indexes
from ..llm.base import Embedder
from ..query.answers import ClaimHit, RetrievalTrace
from ..query.graph_store import GraphStore, StoredChunk
from .claims import CLAIM_VERSION
from .fusion import rrf
from .retrievers import (
    CardRetriever,
    ChunkDense,
    ChunkLexical,
    ClaimRetriever,
    FusedCardRetriever,
    LinkedRoute,
    Retriever,
    SourceRetriever,
)
from .seed_fusion import SeedSetting
from .seeds import with_node_seeds
from .unit_store import UnitStore

# every retriever the settings may name; the order is the trace's and the fusion's tie order
RETRIEVERS = (
    "chunk_dense",
    "chunk_lexical",
    "claim_dense",
    "claim_lexical",
    "card_dense",
    "card_lexical",
    "card_fused",
    "graph_route",
)


class HybridSettings(BaseModel):
    """Which retrievers a hybrid source asks, the fusion constant, how deep each list goes (and the fused
    one), which representation's cards it searches, and whether its chunk and claim retrievers give seeds."""

    retrievers: list[str] = Field(min_length=1)
    rrf_k: int = Field(ge=0)
    depth: int = Field(ge=1)
    cards: str
    node_seeds: bool = False  # R126: the nodes the chunks concern and the claims join, as start nodes
    seeding: SeedSetting | None = None  # R132: how `card_fused` fuses the two card lists; None without it

    @field_validator("retrievers")
    @classmethod
    def _known(cls, names: list[str]) -> list[str]:
        if unknown := sorted(set(names) - set(RETRIEVERS)):
            raise ValueError(f"unknown retrievers {unknown}; choose from {', '.join(RETRIEVERS)}")
        return [name for name in RETRIEVERS if name in names]  # one order, whatever the settings' order


class HybridSource:
    """A `ChunkSource`: the question embedded once, every retriever asked to its depth, the chunk lists and
    the seed lists fused by rank, the best `depth` chunks returned."""

    def __init__(
        self, retrievers: list[Retriever], embedder: Embedder, store: GraphStore, rrf_k: int, depth: int
    ):
        self._retrievers = retrievers
        self._embedder = embedder
        self._store = store
        self._rrf_k = rrf_k
        self._depth = depth

    def ranked(self, question: str) -> tuple[list[StoredChunk], RetrievalTrace | None]:
        vector = self._embedder.embed([question])[0]
        got = {r.name: r.retrieve(question, vector, self._depth) for r in self._retrievers}
        fused = rrf({name: g.chunks for name, g in got.items()}, self._rrf_k)[: self._depth]
        claims: dict[str, ClaimHit] = {}
        for g in got.values():
            claims.update({c.id: c for c in g.claims if c.id not in claims})
        trace = RetrievalTrace(
            candidates=fused,
            seeds=rrf({name: g.seeds for name, g in got.items() if g.seeds}, self._rrf_k),
            lists={name: g.chunks for name, g in got.items()},
            claims=list(claims.values()),
        )
        return self._store.chunks(fused), trace


def build_hybrid(
    settings: HybridSettings,
    store: GraphStore,
    units: UnitStore,
    embedder: Embedder,
    route: LinkedRoute | None,
    card_version: str,
    embed_model: str,
) -> HybridSource:
    """The hybrid source over `settings`, after checking the layer its retrievers need. `route` is the
    name-linker route, needed only when `graph_route` is listed. Raises `MissingInputError` when the layer is
    missing, stale (another card or claim version) or holds another embedding model's vectors."""
    check_layer(settings, units, card_version, embed_model)
    build = {
        "chunk_dense": lambda: ChunkDense(store),
        "chunk_lexical": lambda: ChunkLexical(units),
        "claim_dense": lambda: ClaimRetriever(units, "dense"),
        "claim_lexical": lambda: ClaimRetriever(units, "lexical"),
        "card_dense": lambda: CardRetriever(units, store, settings.cards, "dense"),
        "card_lexical": lambda: CardRetriever(units, store, settings.cards, "lexical"),
        "card_fused": lambda: FusedCardRetriever(units, store, settings.cards, _seeding(settings)),
        "graph_route": lambda: SourceRetriever(_route(route)),
    }
    retrievers = [build[name]() for name in settings.retrievers]
    if settings.node_seeds:
        retrievers = [with_node_seeds(r, store) for r in retrievers]
    return HybridSource(retrievers, embedder, store, settings.rrf_k, settings.depth)


def check_layer(settings: HybridSettings, units: UnitStore, card_version: str, embed_model: str) -> None:
    """Refuse a retrieval layer that does not fit the listed retrievers (see `build_hybrid`)."""
    state = units.index_state(settings.cards)
    names = card_indexes(settings.cards)
    issues = []
    wants_cards = any(n.startswith("card_") for n in settings.retrievers)
    wants_claims = any(n.startswith("claim_") for n in settings.retrievers)
    if wants_cards and (state.cards == 0 or state.card_versions != [card_version]):
        issues.append(f"the {settings.cards} cards are missing or of another version ({state.card_versions})")
    if wants_claims and (state.claims == 0 or state.claim_versions != [CLAIM_VERSION]):
        issues.append(f"the claim sentences are missing or of another version ({state.claim_versions})")
    if (wants_cards or wants_claims) and state.embed_models != [embed_model]:
        issues.append(f"the layer's vectors come from {state.embed_models}, not {embed_model}")
    needed = {
        "chunk_lexical": [CHUNK_FULLTEXT_INDEX],
        "claim_dense": [CLAIM_VECTOR_INDEX],
        "claim_lexical": [CLAIM_FULLTEXT_INDEX],
        "card_dense": [names.vector],
        "card_lexical": [names.fulltext],
        "card_fused": [names.vector, names.fulltext],
    }
    if offline := sorted(
        {i for n in settings.retrievers for i in needed.get(n, []) if i not in state.online}
    ):
        issues.append(f"indexes not online: {', '.join(offline)}")
    if issues:
        raise MissingInputError(
            f"the retrieval layer does not fit: {'; '.join(issues)}; run kg index --cards {settings.cards}"
        )


def _seeding(settings: HybridSettings) -> SeedSetting:
    if settings.seeding is None:
        raise ConfigurationError(
            "card_fused needs a seeding setting (seed_candidates, seed_rrf_k); none given"
        )
    return settings.seeding


def _route(route: LinkedRoute | None) -> LinkedRoute:
    if route is None:
        raise MissingInputError("graph_route needs the name-linker route; none was built")
    return route
