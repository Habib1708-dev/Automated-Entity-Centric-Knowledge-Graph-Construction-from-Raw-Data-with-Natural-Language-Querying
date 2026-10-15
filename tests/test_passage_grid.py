"""The passage grid (R197): the passage reranker (whole passages under short ids, its reply checked by code,
an empty pool not asked), the grid's pools (RRF, dense, a rerank method over RRF's candidates), the scores
(recall at 5 per question, the pool's ceiling, the sign test against RRF), reading each question's lists
once, and the grid run end to end on fakes with its rerank calls keyed by method and question. No Neo4j: the
retrievers, the store and the model are fakes."""

import pytest

from kgbuilder.core.errors import ConfigurationError
from kgbuilder.hybrid.passage_rerank import PROMPT, PassageReranker, RankedPassages
from kgbuilder.hybrid.retrievers import Retrieved
from kgbuilder.hybrid.seed_rerank import RerankOptions
from kgbuilder.pipeline.passage_stages import PassageGridStage, read_lists, run_grid
from kgbuilder.query.graph_store import StoredChunk
from kgbuilder.validation.passage_grid import (
    BASELINE,
    DEPTH,
    GRID,
    SHOWN,
    PassageLists,
    PassageMethod,
    markdown,
    metrics,
    outcome,
    pool,
    score,
)
from kgbuilder.validation.qa_gold import QAQuestion

from .evaluation_corpora import quoted_four_grams
from .fakes import ScriptedLLM
from .test_query import FixedEmbedder

OPTIONS = RerankOptions(model="m", temperature=0.0, thinking="")
RRF_K = 10


def chunk(chunk_id: str, text: str = "text") -> StoredChunk:
    return StoredChunk(chunk_id=chunk_id, context=f"doc of {chunk_id}", text=text)


def method(name: str) -> PassageMethod:
    return next(m for m in GRID if m.name == name)


# the reranker


def test_the_reranker_shows_each_passage_whole_under_a_short_id_and_maps_the_reply_back():
    prompts = []
    llm = ScriptedLLM(lambda prompt, schema: prompts.append(prompt) or RankedPassages(order=["P2", "P1"]))
    long = "a sentence. " * 200  # far past any excerpt cut: the reranker reads the passage whole
    out = PassageReranker(llm, OPTIONS).rerank("which one?", [chunk("d.md#0", long), chunk("d.md#1")])
    assert out.order == ["d.md#1", "d.md#0"] and (out.dropped, out.missing) == (0, 0)
    prompt = prompts[0]
    assert (
        "which one?" in prompt and f"[P1] doc of d.md#0\n{long}" in prompt and "[P2] doc of d.md#1" in prompt
    )
    assert "d.md#0" not in prompt.replace("doc of d.md#0", "")  # the model copies short ids, never chunk ids
    assert llm.calls == [("RankedPassages", "m")]


def test_a_reply_off_the_pool_is_checked_by_code():
    llm = ScriptedLLM(lambda prompt, schema: RankedPassages(order=["P3", "P9", "P3"]))
    out = PassageReranker(llm, OPTIONS).rerank("q", [chunk("a"), chunk("b"), chunk("c")])
    assert out.order == ["c", "a", "b"] and (out.dropped, out.repeated, out.missing) == (1, 1, 2)


def test_an_empty_pool_asks_nothing():
    llm = ScriptedLLM(lambda prompt, schema: RankedPassages(order=[]))
    assert PassageReranker(llm, OPTIONS).rerank("q", []).order == [] and llm.calls == []


def test_the_rerank_prompt_quotes_no_evaluation_corpus():
    assert quoted_four_grams(PROMPT) == []


# the grid


LISTS = PassageLists(
    key="g/Q1",
    question="q",
    gold=["d6", "x1"],
    dense=["d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10"],
    lexical=["x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10"],
)


def test_the_grid_is_fixed_with_todays_search_text_as_the_baseline():
    assert [m.name for m in GRID] == ["rrf_5x5", "rrf_10x10", "dense_5", "rerank_5x5", "rerank_10x10"]
    assert (BASELINE, SHOWN, DEPTH) == ("rrf_5x5", 5, 10)


def test_rrf_and_rerank_methods_pool_both_lists_depth_deep_and_dense_takes_its_list_alone():
    assert pool(method("rrf_5x5"), LISTS, RRF_K)[:4] == ["d1", "x1", "d2", "x2"]
    assert (
        len(pool(method("rrf_5x5"), LISTS, RRF_K)) == 10
        and len(pool(method("rrf_10x10"), LISTS, RRF_K)) == 20
    )
    # a rerank method reads the very candidates RRF fuses, in RRF's order
    assert pool(method("rerank_5x5"), LISTS, RRF_K) == pool(method("rrf_5x5"), LISTS, RRF_K)
    assert pool(method("dense_5"), LISTS, RRF_K) == ["d1", "d2", "d3", "d4", "d5"]


def test_an_outcome_shows_the_first_five_of_each_method_and_the_pools_ceiling():
    orders = {"rerank_5x5": ["x1", "d1"], "rerank_10x10": ["d6", "x1", "d1", "d2", "d3", "d4"]}
    o = outcome(LISTS, RRF_K, orders, GRID)
    assert o.shown["rrf_5x5"] == ["d1", "x1", "d2", "x2", "d3"] and o.recall("rrf_5x5") == 0.5
    assert o.shown["rerank_10x10"] == ["d6", "x1", "d1", "d2", "d3"] and o.recall("rerank_10x10") == 1.0
    # d6 is dense rank 6: outside the 5x5 pool, inside the 10x10 one
    assert (o.pool_recall["rerank_5x5"], o.pool_recall["rerank_10x10"]) == (0.5, 1.0)
    assert (o.pool_size["rrf_5x5"], o.pool_size["dense_5"]) == (10, 5)


def test_scores_pair_each_method_with_the_baseline_question_by_question():
    second = LISTS.model_copy(update={"key": "g/Q2", "gold": ["d1"]})
    orders = {"rerank_5x5": ["d2"], "rerank_10x10": ["d6", "x1"]}
    outcomes = [outcome(q, RRF_K, orders, GRID) for q in (LISTS, second)]
    scores = {s.method: s for s in score(outcomes, GRID)}
    base = scores[BASELINE]
    assert (base.questions, base.recall, base.any_found, base.better, base.worse, base.p_value) == (
        2,
        0.75,
        2,
        0,
        0,
        1.0,
    )
    # the 10x10 rerank shows both of Q1's gold (better) and loses Q2's d1 (worse)
    r = scores["rerank_10x10"]
    assert (r.recall, r.better, r.worse, r.any_found) == (0.5, 1, 1, 1)
    flat = metrics(list(scores.values()))
    assert (
        flat["rerank_10x10_recall"] == 0.5 and flat["rrf_5x5_p_value"] == 1.0 and len(flat) == 8 * len(GRID)
    )
    assert markdown(list(scores.values())).count("\n") == 2 + len(GRID)


def test_nothing_to_score_is_refused():
    with pytest.raises(ValueError):
        score([], GRID)


# reading and running


class FakeRetriever:
    def __init__(self, name: str, chunks: list[str]):
        self.name = name
        self._chunks = chunks
        self.depths: list[int] = []

    def retrieve(self, question, vector, depth):
        self.depths.append(depth)
        return Retrieved(chunks=self._chunks[:depth])


class FakeStore:
    def chunks(self, chunk_ids):
        return [chunk(i) for i in chunk_ids]


def question(qid: str, text: str, chunks: list[str]) -> QAQuestion:
    return QAQuestion(
        id=qid, type="lookup", question=text, expected={"text": "x"}, route="retrieval",
        chunks=[{"chunk_id": c, "quote": "q"} for c in chunks],
    )  # fmt: skip


def test_each_distinct_question_is_embedded_once_and_read_depth_deep():
    dense, lexical = FakeRetriever("chunk_dense", LISTS.dense), FakeRetriever("chunk_lexical", LISTS.lexical)
    embedder = FixedEmbedder()
    asked = [("a/Q1", question("Q1", "same?", ["d1"])), ("b/Q7", question("Q7", "same?", ["d1"]))]
    asked.append(("a/Q2", question("Q2", "other?", ["x2", "x1"])))
    lists = read_lists(asked, dense, lexical, embedder)
    assert [x.key for x in lists] == ["a/Q1", "a/Q2"] and lists[1].gold == ["x1", "x2"]
    assert embedder.batches == [["same?"], ["other?"]] and dense.depths == lexical.depths == [DEPTH, DEPTH]
    assert lists[0].dense == LISTS.dense and lists[0].lexical == LISTS.lexical


def test_the_grid_reranks_one_pool_per_rerank_method_and_question_and_scores_every_method():
    prompts = []

    def reply(prompt, schema):
        prompts.append(prompt)
        return RankedPassages(order=["P2"])  # the rest follows in RRF order

    report, orders = run_grid([LISTS], FakeStore(), PassageReranker(ScriptedLLM(reply), OPTIONS), RRF_K)
    assert sorted(orders) == ["rerank_10x10|g/Q1", "rerank_5x5|g/Q1"] and len(prompts) == 2
    assert orders["rerank_5x5|g/Q1"].order[:2] == ["x1", "d1"]  # RRF's second moved first
    assert [s.method for s in report.scores] == [m.name for m in GRID] and len(report.outcomes) == 1
    assert prompts[0].count("[P") in (10, 20)


def test_the_stage_needs_a_gold_file():
    with pytest.raises(ConfigurationError):
        PassageGridStage([])
