"""Start nodes for chunk and claim retrieval (R126, hybrid/seeds.py) over fake stores, without Neo4j: a
chunk's nodes in reading order (the records it is about, then its named nodes where their names first occur,
unfound names last), a chunk retriever's and a claim retriever's seeds in rank order with every node once and
their chunks unchanged, only chunk and claim retrievers wrapped, and the single-technique systems giving
seeds while the sealed `hybrid` gives none. The store's two reads are tested against Neo4j in
test_query_graph.py."""

from kgbuilder.config import Settings
from kgbuilder.graph.index_layer import index_names
from kgbuilder.hybrid.claims import CLAIM_VERSION
from kgbuilder.hybrid.retrievers import CardRetriever, ChunkDense, ClaimRetriever, SourceRetriever
from kgbuilder.hybrid.seeds import ChunkSeeds, ClaimSeeds, chunk_order, with_node_seeds
from kgbuilder.hybrid.unit_store import IndexState
from kgbuilder.pipeline.index_stages import card_representation
from kgbuilder.pipeline.qa_systems import SourceParts, check_source
from kgbuilder.query.graph_store import ChunkNodes, NamedNode

from .test_hybrid_retrievers import FakeUnitStore, hit
from .test_query import FakeStore, FixedEmbedder, chunk

VECTOR = [1.0, 0.0]


class SeedStore(FakeStore):
    """A graph store that also answers what each chunk concerns and what each claim joins."""

    def __init__(self, chunk_nodes=None, claim_nodes=None, **kwargs):
        super().__init__(**kwargs)
        self._chunk_nodes = chunk_nodes or {}
        self._claim_nodes = claim_nodes or {}

    def chunk_nodes(self, chunk_ids):
        return {c: self._chunk_nodes[c] for c in chunk_ids if c in self._chunk_nodes}

    def claim_nodes(self, claim_ids):
        return {c: self._claim_nodes[c] for c in claim_ids if c in self._claim_nodes}


def named(ref: str, *names: str) -> NamedNode:
    return NamedNode(ref=ref, names=list(names))


def test_a_chunk_gives_the_records_it_is_about_then_its_named_nodes_in_reading_order():
    nodes = ChunkNodes(
        about=["Press:P1"],
        named=[named("k-wobble", "wobbles"), named("Part:S1", "spindle", "Spindle Assembly"),
               named("k-noise", "rattle"), named("Maker:M1", "Norcast"), named("Press:P1", "quill press")],
    )  # fmt: skip
    text = "The SPINDLE of the Quill Press wobbles; Norcast was told."
    # case-insensitive, a node's earliest name counts; a node found nowhere comes last; a record already
    # given as the subject of the chunk is not repeated
    assert chunk_order(text, nodes) == ["Press:P1", "Part:S1", "k-wobble", "Maker:M1", "k-noise"]


def test_names_found_nowhere_keep_their_ref_order():
    nodes = ChunkNodes(about=[], named=[named("k-b", "bend"), named("k-a", "arc")])
    assert chunk_order("Nothing named here.", nodes) == ["k-a", "k-b"]


def test_a_chunk_retriever_seeds_from_its_chunks_in_rank_order_each_node_once():
    store = SeedStore(
        nearest=["c2", "c1", "c3"],
        chunks=[
            chunk("c1", None, "The spindle wobbles."),
            chunk("c2", None, "Wobbles at the gear."),
            chunk("c3", None),
        ],
        chunk_nodes={
            "c1": ChunkNodes(
                about=["Press:P1"], named=[named("Part:S1", "spindle"), named("k-w", "wobbles")]
            ),
            "c2": ChunkNodes(about=[], named=[named("Part:S2", "gear"), named("k-w", "wobbles")]),
        },  # c3 concerns nothing
    )
    got = ChunkSeeds(ChunkDense(store), store).retrieve("q", VECTOR, 3)
    assert got.chunks == ["c2", "c1", "c3"]  # the list is the wrapped retriever's, unchanged
    assert got.seeds == ["k-w", "Part:S2", "Press:P1", "Part:S1"]


def test_a_claim_retriever_seeds_from_its_claims_in_rank_order_and_keeps_the_siblings_chunks():
    units = FakeUnitStore(claims=[hit("o2", "c2", {"o9": "c9"}), hit("o1", "c1")])
    store = SeedStore(claim_nodes={"o2": ["Part:S1", "k-w", "Press:P1"], "o1": ["Part:S1", "k-n"]})
    got = ClaimSeeds(ClaimRetriever(units, "dense"), store).retrieve("q", VECTOR, 5)
    assert got.chunks == ["c2", "c9", "c1"] and [c.id for c in got.claims] == ["o2", "o1"]
    assert got.seeds == ["Part:S1", "k-w", "Press:P1", "k-n"]  # a sibling joins the same ends: no seed


def test_only_chunk_and_claim_retrievers_gain_seeds_and_keep_their_names():
    store, units = SeedStore(), FakeUnitStore()
    chunk_r, claim_r = ChunkDense(store), ClaimRetriever(units, "lexical")
    card_r, route_r = CardRetriever(units, store, "template", "dense"), SourceRetriever(None)
    assert isinstance(with_node_seeds(chunk_r, store), ChunkSeeds)
    assert isinstance(with_node_seeds(claim_r, store), ClaimSeeds)
    assert with_node_seeds(claim_r, store).name == "claim_lexical"
    assert with_node_seeds(card_r, store) is card_r and with_node_seeds(route_r, store) is route_r


def claim_layer(s: Settings) -> IndexState:
    return IndexState(
        cards=1, card_versions=[card_representation(s, "template").version], claims=2,
        claim_versions=[CLAIM_VERSION], embed_models=[s.embed_model], online=index_names(["template"]),
    )  # fmt: skip


def test_a_single_technique_system_gives_its_seeds_and_the_sealed_hybrid_none():
    """The registered `claim_dense` asks the claims as deep as it is asked and returns their nodes as seeds;
    the sealed `hybrid` lists the same claim retriever but asks for no node seeds (R126 changes no existing
    system)."""
    s = Settings()
    units = FakeUnitStore(claims=[hit("o1", "c1")], state=claim_layer(s))
    store = SeedStore(chunks=[chunk("c1", None)], claim_nodes={"o1": ["Part:S1", "k-w"]})
    parts = SourceParts(settings=s, store=store, embedder=FixedEmbedder(), plan=None, units=units)
    ranked, trace = check_source("claim_dense")(parts, 50).ranked("Which part wobbles?")
    assert [c.chunk_id for c in ranked] == ["c1"] and trace.seeds == ["Part:S1", "k-w"]
    assert units.calls[0][:2] == ("nearest_claims", 50)
    _, sealed = check_source("hybrid")(parts, 20).ranked("Which part wobbles?")
    assert sealed.seeds == []
