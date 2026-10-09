"""Fusion and the hybrid source (R120b, hybrid/fusion.py and source.py, the `hybrid` system), over fakes and
without Neo4j: reciprocal rank fusion's order, its ties and what its constant k changes, the question
embedded once for every retriever, only the listed retrievers asked, the fused chunks read from the store
with a trace of each list, the fused seeds and the claims, the settings' retrievers checked and put in one
order, a retrieval layer that is missing, stale, of another embedding model or offline refused before any
question, and the `hybrid` system's reader given exactly k chunks."""

import pytest
from pydantic import ValidationError

from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError, MissingInputError
from kgbuilder.graph.index_layer import index_names
from kgbuilder.hybrid.cards import TemplateCards
from kgbuilder.hybrid.claims import CLAIM_VERSION
from kgbuilder.hybrid.fusion import rrf
from kgbuilder.hybrid.retrievers import Retrieved
from kgbuilder.hybrid.source import HybridSettings, HybridSource, build_hybrid
from kgbuilder.hybrid.unit_store import IndexState
from kgbuilder.pipeline.qa_systems import SYSTEMS, QAParts

from .test_hybrid_retrievers import FakeUnitStore, hit
from .test_query import FakeStore, FixedEmbedder, chunk, citing_reader

MODEL = Settings().embed_model
FITTING = IndexState(
    cards=3, card_versions=[TemplateCards.version], claims=2, claim_versions=[CLAIM_VERSION],
    embed_models=[MODEL], online=index_names(["template"]),
)  # fmt: skip


def test_fusion_adds_reciprocal_ranks_and_breaks_ties_by_best_rank_then_list_order():
    # x and y each hold a first and a second place: a tie, which x wins by holding its first place earlier
    assert rrf({"a": ["x", "y", "z"], "b": ["y", "x"]}, 60) == ["x", "y", "z"]
    assert rrf({"a": ["x", "x", "y"]}, 60) == ["x", "y"]  # an item counts once per list
    with pytest.raises(ValueError):
        rrf({"a": ["x"]}, -1)


def test_a_small_k_rewards_one_first_place_and_a_large_k_agreement():
    # "solo" is first in one list; "both" is third in two
    lists = {"a": ["solo", "p", "both"], "b": ["q", "r", "both"]}
    assert rrf(lists, 0)[0] == "solo"  # 1/1 against 1/3 + 1/3
    assert rrf(lists, 60)[0] == "both"  # 1/61 against 2/63


class Fixed:
    """A retriever with a fixed answer; records the vectors it is given."""

    def __init__(self, name, retrieved):
        self.name = name
        self._retrieved = retrieved
        self.vectors = []

    def retrieve(self, question, vector, depth):
        self.vectors.append(vector)
        return Retrieved(chunks=self._retrieved.chunks[:depth], seeds=self._retrieved.seeds,
                         claims=self._retrieved.claims)  # fmt: skip


def test_the_source_embeds_once_fuses_the_lists_and_traces_each_one():
    claim = hit("o1", "c2")
    dense = Fixed("chunk_dense", Retrieved(chunks=["c1", "c2"]))
    cards = Fixed("card_dense", Retrieved(chunks=["c2", "c3"], seeds=["Press:P1", "k-x"]))
    claims = Fixed("claim_dense", Retrieved(chunks=["c2"], claims=[claim]))
    embedder = FixedEmbedder()
    store = FakeStore(chunks=[chunk(c, None) for c in ("c1", "c2", "c3")])
    ranked, trace = HybridSource([dense, cards, claims], embedder, store, rrf_k=60, depth=2).ranked("Why?")
    assert embedder.batches == [["Why?"]] and dense.vectors == cards.vectors == claims.vectors == [[1.0, 0.0]]
    # c2 is in all three lists; the fused list is cut at the depth
    assert [c.chunk_id for c in ranked] == ["c2", "c1"] and trace.candidates == ["c2", "c1"]
    assert trace.lists == {"chunk_dense": ["c1", "c2"], "card_dense": ["c2", "c3"], "claim_dense": ["c2"]}
    assert trace.seeds == ["Press:P1", "k-x"] and trace.claims == [claim]


def test_only_the_listed_retrievers_are_asked_in_one_order():
    settings = HybridSettings(
        retrievers=["claim_lexical", "chunk_dense"], rrf_k=60, depth=5, cards="template"
    )
    assert settings.retrievers == ["chunk_dense", "claim_lexical"]
    with pytest.raises(ValidationError, match="unknown retrievers"):
        HybridSettings(retrievers=["chunk_dense", "telepathy"], rrf_k=60, depth=5, cards="template")
    units = FakeUnitStore(claims=[hit("o1", "c9")], cards=["Press:P1"], state=FITTING)
    store = FakeStore(nearest=["c1"], chunks=[chunk("c1", None), chunk("c9", None)])
    source = build_hybrid(settings, store, units, FixedEmbedder(), None, TemplateCards.version, MODEL)
    _, trace = source.ranked("What does the press do?")
    assert list(trace.lists) == ["chunk_dense", "claim_lexical"]
    assert [call[0] for call in units.calls] == ["search_claims"]  # no card was looked up


@pytest.mark.parametrize(
    "state, message",
    [
        (FITTING.model_copy(update={"cards": 0, "card_versions": []}), "cards are missing"),
        (FITTING.model_copy(update={"card_versions": ["old"]}), "of another version"),
        (FITTING.model_copy(update={"claim_versions": ["old"]}), "claim sentences are missing"),
        (FITTING.model_copy(update={"embed_models": ["another-model"]}), "vectors come from"),
        (FITTING.model_copy(update={"online": []}), "indexes not online"),
    ],
)
def test_a_retrieval_layer_that_does_not_fit_is_refused_before_any_question(state, message):
    settings = HybridSettings(
        retrievers=["chunk_lexical", "claim_dense", "card_dense"], rrf_k=60, depth=5, cards="template"
    )
    embedder = FixedEmbedder()
    with pytest.raises(MissingInputError, match=message):
        build_hybrid(
            settings, FakeStore(), FakeUnitStore(state=state), embedder, None, TemplateCards.version, MODEL
        )
    assert embedder.batches == []


def test_graph_route_without_a_route_is_refused():
    settings = HybridSettings(retrievers=["graph_route"], rrf_k=60, depth=5, cards="template")
    with pytest.raises(MissingInputError, match="name-linker route"):
        build_hybrid(settings, FakeStore(), FakeUnitStore(state=FITTING), FixedEmbedder(), None, "v", MODEL)


def test_the_hybrid_systems_reader_is_given_exactly_k_chunks():
    """Fusion keeps `hybrid_depth` chunks; the shared reader reads the first k of them, like every system."""
    ids = [f"c{i}" for i in range(8)]
    store = FakeStore(nearest=ids, chunks=[chunk(c, None, f"text {c}") for c in ids])
    units = FakeUnitStore(state=FITTING)
    reader, _ = citing_reader()
    settings = Settings(qa_top_k=3, hybrid_retrievers=["chunk_dense", "chunk_lexical"], hybrid_depth=6)
    parts = QAParts(settings=settings, store=store, embedder=FixedEmbedder(), plan=None, units=units,
                    llm=None, reader=reader)  # fmt: skip
    answer = SYSTEMS["hybrid"].build(parts).answer("Q1", "Which press?")
    assert answer.retrieved == ["c0", "c1", "c2"] and store.nearest_calls == [6]
    assert answer.trace.candidates == ids[:6]


def test_card_fusion_without_a_seeding_setting_is_refused():
    settings = HybridSettings(retrievers=["card_fused"], rrf_k=60, depth=5, cards="template")
    with pytest.raises(ConfigurationError, match="seeding"):
        build_hybrid(settings, FakeStore(), FakeUnitStore(state=FITTING), FixedEmbedder(), None,
                     TemplateCards.version, MODEL)  # fmt: skip
