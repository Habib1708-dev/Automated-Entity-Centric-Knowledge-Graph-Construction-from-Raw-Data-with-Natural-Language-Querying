"""The hybrid retrievers (R120a, hybrid/retrievers.py and lucene.py) over fake stores, without Neo4j: the
question as a quoted Lucene query, each list's order and depth, a claim followed by its opposite-truth
siblings' chunks (each chunk once), the claim filter passed to the store, a lexical retriever with no word
asking nothing, cards taking turns so a hub cannot crowd out the next card, a card's node started as a
thing or a kind, cards without a node left out of the seeds, the name-linker route given the question's
vector (nothing embedded again), and R132's fused cards: each list read `candidates` deep, fused by RRF, no
word search for a question without words, and no rerank setting."""

import pytest

from kgbuilder.hybrid.lucene import lucene_query
from kgbuilder.hybrid.retrievers import (
    CardRetriever,
    ChunkDense,
    ChunkLexical,
    ClaimRetriever,
    FusedCardRetriever,
    SourceRetriever,
    round_robin,
)
from kgbuilder.hybrid.seed_fusion import SeedSetting
from kgbuilder.hybrid.unit_store import CardStart, ClaimFilter
from kgbuilder.query.answers import ClaimHit
from kgbuilder.query.systems import build_graph_retrieval

from .test_query import DRESSER, FakeStore, FixedEmbedder, chunk

QUESTION = "Does the Quill Press wobble?"
VECTOR = [1.0, 0.0]


class FakeUnitStore:
    """`UnitStore` with fixed answers; records each call."""

    def __init__(self, cards=(), claims=(), chunks=(), starts=None, state=None):
        self._cards, self._claims, self._chunks = list(cards), list(claims), list(chunks)
        self._starts = starts or {}
        self._state = state
        self.calls: list[tuple] = []

    def nearest_cards(self, representation, vector, k):
        self.calls.append(("nearest_cards", representation, k))
        return self._cards[:k]

    def search_cards(self, representation, query, k):
        self.calls.append(("search_cards", representation, query, k))
        return self._cards[:k]

    def nearest_claims(self, vector, k, claim_filter):
        self.calls.append(("nearest_claims", k, claim_filter))
        return self._claims[:k]

    def search_claims(self, query, k, claim_filter):
        self.calls.append(("search_claims", query, k, claim_filter))
        return self._claims[:k]

    def search_chunks(self, query, k):
        self.calls.append(("search_chunks", query, k))
        return self._chunks[:k]

    def card_starts(self, refs):
        return {r: s for r, s in self._starts.items() if r in refs}

    def index_state(self, representation):
        return self._state


class ReachStore(FakeStore):
    """A graph store whose traversal reaches, from each start, its own chunks; records the starts."""

    def __init__(self, reached_from, chunks):
        super().__init__(chunks=chunks)
        self._from = reached_from
        self.chunk_calls = 0

    def reach(self, things, kinds):
        self.reach_calls.append((things, kinds))
        return {"pattern": set().union(*(self._from[i] for i in things + kinds))}

    def chunks(self, chunk_ids):
        self.chunk_calls += 1
        return super().chunks(chunk_ids)


def hit(obs: str, chunk_id: str, siblings: dict[str, str] | None = None) -> ClaimHit:
    siblings = siblings or {}
    return ClaimHit(
        id=obs, chunk_id=chunk_id, truth="affirmed",
        siblings=list(siblings), sibling_chunks=list(siblings.values()),
    )  # fmt: skip


def test_a_question_becomes_its_words_each_quoted_once():
    assert (
        lucene_query("Does the HP40-1183 pump wobble? The pump!")
        == '"does" "the" "hp40-1183" "pump" "wobble"'
    )
    assert lucene_query("?! --") is None


def test_the_chunk_retrievers_return_the_stores_order_cut_at_the_depth():
    store = FakeStore(nearest=["c1", "c2", "c3"])
    assert ChunkDense(store).retrieve(QUESTION, VECTOR, 2).chunks == ["c1", "c2"]
    units = FakeUnitStore(chunks=["c9", "c8"])
    assert ChunkLexical(units).retrieve(QUESTION, VECTOR, 5).chunks == ["c9", "c8"]
    assert units.calls == [("search_chunks", lucene_query(QUESTION), 5)]
    nothing = FakeUnitStore(chunks=["c9"])
    assert ChunkLexical(nothing).retrieve("?!", VECTOR, 5).chunks == [] and nothing.calls == []


def test_a_claim_is_followed_by_its_opposite_truth_siblings_and_every_chunk_comes_once():
    hits = [hit("o1", "c1", {"o2": "c2"}), hit("o3", "c2"), hit("o4", "c4", {"o5": "c5", "o6": "c1"})]
    units = FakeUnitStore(claims=hits)
    got = ClaimRetriever(units, "dense").retrieve(QUESTION, VECTOR, 10)
    # o1's denial (c2) comes right after it; o3's chunk is c2 already; o4's siblings follow it
    assert got.chunks == ["c1", "c2", "c4", "c5"] and got.claims == hits
    assert ClaimRetriever(FakeUnitStore(claims=hits), "dense").retrieve(QUESTION, VECTOR, 3).chunks == [
        "c1", "c2", "c4",
    ]  # fmt: skip


def test_the_claim_filter_goes_to_the_store_and_lexical_search_uses_the_words():
    denied = ClaimFilter(triple_truth="negated")
    units = FakeUnitStore(claims=[hit("o2", "c2")])
    ClaimRetriever(units, "dense", denied).retrieve(QUESTION, VECTOR, 4)
    ClaimRetriever(units, "lexical").retrieve(QUESTION, VECTOR, 4)
    assert units.calls == [
        ("nearest_claims", 4, denied),
        ("search_claims", lucene_query(QUESTION), 4, ClaimFilter()),
    ]


def test_cards_take_turns_so_a_hub_cannot_crowd_out_the_next_card():
    # the hub reaches five chunks, the second card two, a third card's node is gone from the graph
    chunks = [chunk(f"h{i}", [1.0, i / 10]) for i in range(5)] + [
        chunk("b0", [1.0, 0.0]),
        chunk("b1", [0.0, 1.0]),
    ]
    store = ReachStore({"4:hub": {f"h{i}" for i in range(5)}, "k-b": {"b0", "b1"}}, chunks)
    units = FakeUnitStore(
        cards=["Press:HUB", "k-b", "Press:GONE"],
        starts={
            "Press:HUB": CardStart(kind="thing", node_id="4:hub"),
            "k-b": CardStart(kind="kind", node_id="k-b"),
        },
    )
    got = CardRetriever(units, store, "template", "dense").retrieve(QUESTION, VECTOR, 5)
    # within a card by similarity to the question (h0 nearest), then the cards in turn
    assert got.chunks == ["h0", "b0", "h1", "b1", "h2"]
    assert got.seeds == ["Press:HUB", "k-b"]  # a card without a node is no seed
    # one traversal per card, a thing by element id and a kind by canonical id; one read of the chunks
    assert store.reach_calls == [(["4:hub"], []), ([], ["k-b"])] and store.chunk_calls == 1
    assert units.calls == [("nearest_cards", "template", 5)]


def test_round_robin_skips_what_an_earlier_list_gave():
    assert round_robin([["a", "b", "c"], ["b", "d"], []]) == ["a", "b", "d", "c"]
    assert round_robin([]) == []


def test_the_name_linker_route_ranks_from_the_given_vector_without_embedding_again():
    embedder = FixedEmbedder()
    store = FakeStore(
        names=[DRESSER], reached={"thing_observations": {"c1"}}, chunks=[chunk("c1", [1.0, 0.0])]
    )
    route = build_graph_retrieval(store, embedder, 90.0, neighbours=0)
    got = SourceRetriever(route).retrieve("Is the Quill Press stable?", VECTOR, 5)
    assert (got.chunks, got.seeds) == (["c1"], ["Press:P1"]) and embedder.batches == []


class SplitUnitStore(FakeUnitStore):
    """`UnitStore` whose card vector search and card word search give different lists."""

    def __init__(self, dense, lexical, starts):
        super().__init__(starts=starts)
        self._dense, self._lexical = list(dense), list(lexical)

    def nearest_cards(self, representation, vector, k):
        self.calls.append(("nearest_cards", representation, k))
        return self._dense[:k]

    def search_cards(self, representation, query, k):
        self.calls.append(("search_cards", representation, query, k))
        return self._lexical[:k]


def _fused_setup():
    refs = ["Press:A", "Press:B", "Press:C", "Press:D"]
    starts = {r: CardStart(kind="thing", node_id=f"4:{r[-1]}") for r in refs}
    chunks = [chunk(f"x{r[-1]}", [1.0, 0.0]) for r in refs]
    store = ReachStore({f"4:{r[-1]}": {f"x{r[-1]}"} for r in refs}, chunks)
    units = SplitUnitStore(["Press:A", "Press:B", "Press:C"], ["Press:C", "Press:A", "Press:D"], starts)
    return units, store


def test_fused_cards_read_each_list_candidates_deep_and_fuse_them_by_rrf():
    units, store = _fused_setup()
    setting = SeedSetting(name="s", representation="template", method="rrf", candidates=2, rrf_k=60)
    got = FusedCardRetriever(units, store, "template", setting).retrieve(QUESTION, VECTOR, 5)
    # A is first in one list and second in the other; C first in one; B second in one; D is beyond 2 deep
    assert got.seeds == ["Press:A", "Press:C", "Press:B"]
    assert got.chunks == ["xA", "xC", "xB"]  # each fused node's chunks, the nodes in turn
    assert units.calls == [
        ("nearest_cards", "template", 2),
        ("search_cards", "template", lucene_query(QUESTION), 2),
    ]


def test_fused_cards_ask_no_word_search_for_a_question_without_words():
    units, store = _fused_setup()
    setting = SeedSetting(name="s", representation="template", method="rrf", candidates=2, rrf_k=60)
    got = FusedCardRetriever(units, store, "template", setting).retrieve("?!", VECTOR, 5)
    assert got.seeds == ["Press:A", "Press:B"] and [c[0] for c in units.calls] == ["nearest_cards"]


def test_live_card_fusion_refuses_a_rerank_setting():
    units, store = _fused_setup()
    setting = SeedSetting(
        name="s", representation="template", method="rerank", candidates=2, rrf_k=60, pool=5
    )
    with pytest.raises(ValueError, match="by RRF"):
        FusedCardRetriever(units, store, "template", setting)
