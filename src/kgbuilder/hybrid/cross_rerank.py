"""The cross-encoder reranker of text passages (R200): a local model scores each (question, passage) pair of a
pool, and the pool is ordered by score.

Role in the pipeline: `kg passage-grid` (pipeline/passage_stages.py) asks it to order the pool of every
`cross` method of the passage grid (validation/passage_grid.py), beside the LLM reranker's `rerank` methods,
to see whether a dedicated reranking model shows as many gold passages in `search_text`'s first five as the
LLM does (plan R200-R201).
Design: Strategy, interchangeable with `PassageReranker` (passage_rerank.py): the same `rerank` and
`rerank_all` signatures and the same `Reranked` result, so the grid treats both alike. Over the `PairScorer`
port; the composition root injects the model. Each passage is scored as the LLM reads it, its document's
name then its text, whole. A score is a number per passage, so nothing can be dropped, repeated or left out:
the slips are always 0. Ties keep the pool's (RRF) order, so the order is deterministic. `version` names the
model and the text shown.
Not here: loading the model (llm/cross_encoder.py), the pools and the scores (validation/passage_grid.py).
"""

import time
from collections.abc import Mapping, Sequence

from ..llm.base import PairScorer, prompt_version
from ..query.graph_store import StoredChunk
from .seed_rerank import Reranked

TEXT = "{context}\n{text}"  # one passage as the model reads it: the LLM reranker's text without its id


class CrossEncoderReranker:
    """Orders a pool of passages, each read whole, by a cross-encoder's scores (Strategy)."""

    def __init__(self, scorer: PairScorer, model: str):
        self._scorer = scorer
        self.version = prompt_version(f"{model}\n{TEXT}")
        self.seconds = 0.0  # time spent scoring, summed over the pools (the latency the run logs)

    def rerank(self, question: str, pool: Sequence[StoredChunk]) -> Reranked:
        """The pool's chunk ids best first. One scoring call; an empty pool scores nothing."""
        if not pool:
            return Reranked(order=[], dropped=0, repeated=0, missing=0)
        started = time.perf_counter()
        scores = self._scorer.score(question, [TEXT.format(context=c.context, text=c.text) for c in pool])
        self.seconds += time.perf_counter() - started
        # sorted() is stable: equal scores keep the pool's order
        ranked = sorted(range(len(pool)), key=lambda i: -scores[i])
        return Reranked(order=[pool[i].chunk_id for i in ranked], dropped=0, repeated=0, missing=0)

    def rerank_all(self, pools: Mapping[str, tuple[str, Sequence[StoredChunk]]]) -> dict[str, Reranked]:
        """Key -> its pool ordered, from key -> (question, pool); one after another, as the model runs on one
        GPU and a thread pool would only queue the calls."""
        return {key: self.rerank(question, pool) for key, (question, pool) in pools.items()}
