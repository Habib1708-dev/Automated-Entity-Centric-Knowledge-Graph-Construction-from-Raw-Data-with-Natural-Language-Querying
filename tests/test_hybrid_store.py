"""The unit store against Neo4j (R120a, hybrid/unit_store.py), on the shared hand-made graph with the claims
of test_hybrid_units.py, indexed by `kg index` with an embedder that puts every text naming "wobble" on one
axis and every other text on the other: cards and claims found by vector and by words, chunks found by
words, a query of stop words only answered with nothing, a claim's opposite-truth siblings (the statement and
its denial, a condition's claim among the stated ones), the claim filter with over-fetch, the start node of a
record's and a concept's card, the index state, and the card retriever end to end. Needs Neo4j."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.graph.index_layer import index_names
from kgbuilder.hybrid.cards import TemplateCards
from kgbuilder.hybrid.claims import CLAIM_VERSION
from kgbuilder.hybrid.lucene import lucene_query
from kgbuilder.hybrid.retrievers import CardRetriever
from kgbuilder.hybrid.unit_store import ClaimFilter, Neo4jUnitStore
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import IndexStage
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.graph_store import Neo4jGraphStore

from .fakes import RecordingTracker
from .test_hybrid_units import RELATED_PLAN, load

WOBBLE, OTHER = [1.0, 0.0], [0.0, 1.0]


class KeywordEmbedder:
    """Texts naming "wobble" on the first axis, every other text on the second."""

    def embed(self, texts):
        return [WOBBLE if "wobbl" in t.lower() else OTHER for t in texts]


@pytest.fixture
def units(driver, tmp_path):
    """The shared graph indexed; the layer's indexes dropped afterwards (the driver fixture keeps them)."""
    load(driver)
    out = tmp_path / "build"
    out.mkdir()
    (out / PLAN_FILE).write_text(RELATED_PLAN.model_dump_json(), encoding="utf-8")
    ctx = PipelineContext(settings=Settings(), driver=driver, out=out, embedder=KeywordEmbedder(),
                          tracker=RecordingTracker())  # fmt: skip
    run_stages(ctx, PipelineState(), [IndexStage("template")])
    yield Neo4jUnitStore(driver)
    for name in index_names(["template"]):
        driver.execute_query(f"DROP INDEX {name} IF EXISTS")


def test_cards_are_found_by_vector_and_by_words(units):
    # the press holds the wobble claims, the concept is "wobbles", Ada reports wobbles: three cards on the
    # axis
    assert set(units.nearest_cards("template", WOBBLE, 3)) == {"Press:P1", "k-wobble", "i-ada"}
    # "quill" stands in the press's card and in every card whose relation line names the Quill Press (its
    # four parts, the ticket), and in no other; BM25 ranks the short ones high, so only the set is fixed
    found = units.search_cards("template", lucene_query("quill"), 10)
    assert set(found) == {"Press:P1", "Part:S1", "Part:S2", "Part:S3", "Part:S4", "Ticket:T-1"}


def test_chunks_are_found_by_words_and_stop_words_alone_find_nothing(units):
    assert "notes.md#0" in units.search_chunks(lucene_query("spindle wobbles"), 5)
    assert units.search_chunks(lucene_query("the of it"), 5) == []


def test_a_claim_comes_with_its_opposite_truth_siblings(units):
    hits = {h.id: h for h in units.nearest_claims(WOBBLE, 4, ClaimFilter())}
    assert set(hits) == {"o1", "o2", "o3", "o4"}  # o5 (made by Norcast) is on the other axis
    # o1 (stated) and o2 (denied) oppose each other; o3, stated under a condition, also opposes the denial
    assert (hits["o1"].siblings, hits["o1"].sibling_chunks) == (["o2"], ["notes.md#1"])
    assert hits["o2"].siblings == ["o1", "o3"] and hits["o2"].triple_truth == "negated"
    assert (hits["o3"].modality, hits["o3"].condition, hits["o4"].siblings) == (
        "conditional",
        "when cold",
        [],
    )
    assert units.search_claims(lucene_query("norcast"), 3, ClaimFilter())[0].id == "o5"


def test_the_claim_filter_is_applied_after_the_search_and_still_fills_the_depth(units):
    # the denial is one claim of four on the axis: asking for one, the store fetches four and keeps it
    denied = units.nearest_claims(WOBBLE, 1, ClaimFilter(triple_truth="negated"))
    assert [h.id for h in denied] == ["o2"]
    conditional = units.nearest_claims(WOBBLE, 3, ClaimFilter(modality="conditional"))
    assert [h.id for h in conditional] == ["o3"]


def test_a_cards_start_is_a_thing_by_element_id_or_a_kind_by_canonical_id(units, driver):
    press, _, _ = driver.execute_query("MATCH (p:Press {press_id: 'P1'}) RETURN elementId(p) AS id")
    starts = units.card_starts(["Press:P1", "k-wobble", "Press:NONE"])
    assert set(starts) == {"Press:P1", "k-wobble"}
    assert (starts["Press:P1"].kind, starts["Press:P1"].node_id) == ("thing", press[0]["id"])
    assert (starts["k-wobble"].kind, starts["k-wobble"].node_id) == ("kind", "k-wobble")


def test_the_index_state_says_what_the_layer_holds(units):
    state = units.index_state("template")
    assert (state.cards, state.card_versions) == (10, [TemplateCards.version])
    assert (state.claims, state.claim_versions) == (5, [CLAIM_VERSION])
    assert state.embed_models == [Settings().embed_model] and set(state.online) == set(
        index_names(["template"])
    )
    assert units.index_state("other").cards == 0


def test_the_card_retriever_starts_from_the_cards_nodes_and_reads_what_they_reach(units, driver):
    store = Neo4jGraphStore(driver, RELATED_PLAN, hops=2)
    got = CardRetriever(units, store, "template", "dense").retrieve("Which press wobbles?", WOBBLE, 6)
    # as many cards as the depth: the three on the question's axis first, then three orthogonal ones
    assert set(got.seeds[:3]) == {"Press:P1", "k-wobble", "i-ada"} and len(got.seeds) == 6
    assert "notes.md#0" in got.chunks and len(got.chunks) == len(set(got.chunks)) <= 6
