"""The LLM reranker of text passages (R197): a model orders a question's pool of candidate passages, each read
whole, and code checks the order.

Role in the pipeline: `kg passage-grid` (pipeline/passage_stages.py) asks it to order the pool of every
`rerank` method of the passage grid (validation/passage_grid.py), beside the free RRF methods, to see whether
a model's order shows more of the gold passages in `search_text`'s first five than RRF's does (plan
R197-R198).
Design: the same contract as the start-node reranker (seed_rerank.py), over the `LLMClient` port wrapped in
`CachedLLM`: passages under short ids (P1, P2, ...), and the checked order (`check_order`): ids outside the
pool dropped, repeats counted once, the pool's passages the reply leaves out appended in the pool's own
order. A passage is shown whole with its document's name, never cut, as the user asked of the evidence
ledger (R193). `version` hashes the prompt and the reply schema.
Not here: the pools and the scores (validation/passage_grid.py), reading the passages (the graph store).
"""

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from ..llm.base import LLMClient, prompt_version
from ..query.graph_store import StoredChunk
from .seed_rerank import Reranked, RerankOptions, check_order

# The rerank prompt (R197). Intent: order text passages by whether each states what the question asks, read
# in any wording. What each rule guards against:
# - rule 1 asks for what the passage states, not for shared words: R196's never-read passages said the asked
#   idea in other words ("me and my roommate" for "two people") and lost to passages sharing the words;
# - rule 2: a question often has several answers in several passages (R192: lists of 3 to 7), so ranking
#   only the single best one first would push the second down;
# - rule 3: the passages only, no outside knowledge, so the order rests on what the corpus holds;
# - rule 4: every id once; code still drops ids outside the pool, de-duplicates and appends what is left out.
# No example: an example's words would pull the order towards its domain, and the rules need none. A test
# checks that the prompt holds no word of an evaluated dataset's question.
PROMPT = """You order text passages for a reader who must answer a question from them. Each passage is shown \
whole, under its document's name.

The question: {question}

The passages:
{passages}

Rules:
1. Order the passages by how directly each one states something the question asks for, in any wording: a \
passage that says it in other words counts as much as one that repeats the question's words.
2. A question can have several answers in several passages. Rank each of them high, not only the single \
best one.
3. Judge from the passages only.
4. In order, best first, give the id of every passage, such as "P3", each exactly once."""

PASSAGE = "[{id}] {context}\n{text}"  # one candidate as the prompt shows it; the ids are what `order` cites
_WORKERS = 8  # pools reranked in parallel: one request each, and CachedLLM writes atomically


class RankedPassages(BaseModel):
    """The model's reply: the pool's ids, best first."""

    order: list[str] = Field(description='The id of every passage, best first, such as "P3", each once.')


class PassageReranker:
    """Orders a pool of passages, each read whole, with a model, checked by code."""

    version = prompt_version(PROMPT + json.dumps(RankedPassages.model_json_schema(), sort_keys=True))

    def __init__(self, llm: LLMClient, options: RerankOptions):
        self._llm = llm
        self._options = options

    def rerank(self, question: str, pool: Sequence[StoredChunk]) -> Reranked:
        """The pool's chunk ids best first. One model call (cached); an empty pool asks nothing. Raises
        `LLMResponseError` when the provider keeps failing."""
        if not pool:
            return Reranked(order=[], dropped=0, repeated=0, missing=0)
        ids = {f"P{n}": c.chunk_id for n, c in enumerate(pool, start=1)}
        shown = "\n\n".join(
            PASSAGE.format(id=i, context=c.context, text=c.text) for i, c in zip(ids, pool, strict=True)
        )
        o = self._options
        reply = self._llm.generate(
            PROMPT.format(question=question, passages=shown),
            RankedPassages,
            model=o.model,
            temperature=o.temperature,
            thinking=o.thinking,
        )
        return check_order(reply.order, ids)

    def rerank_all(self, pools: Mapping[str, tuple[str, Sequence[StoredChunk]]]) -> dict[str, Reranked]:
        """Key -> its pool ordered, from key -> (question, pool); in parallel."""
        with ThreadPoolExecutor(max_workers=_WORKERS) as workers:
            done = workers.map(lambda item: self.rerank(*item), pools.values())
            return dict(zip(pools, done, strict=True))
