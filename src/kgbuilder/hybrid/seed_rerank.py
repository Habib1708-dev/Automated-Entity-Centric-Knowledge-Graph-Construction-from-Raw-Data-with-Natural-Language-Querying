"""The LLM reranker of start nodes (R130): a model orders a question's pool of candidate nodes by their cards,
and code checks the order.

Role in the pipeline: `kg seed-grid --rerank` (pipeline/seed_stages.py) asks it to order the pool every
`rerank` setting fuses (seed_fusion.py), beside the free RRF settings, in one table (plan R130-R136); later
the agent's `find_nodes` may use it if R131 keeps it.
Design: over the `LLMClient` port; the composition root injects the model wrapped in `CachedLLM`, so a rerun
costs nothing. The cards are shown under short ids (N1, N2, ...) rather than node refs, so the model copies
two characters, not a long key. The LLM proposes, code decides: an id outside the pool is dropped, a repeated
id counts once, and the pool's nodes the reply leaves out follow in the pool's own (RRF) order; each is
counted, so a run shows how far the reply was off. `version` hashes the prompt and the reply schema.
Not here: the pool (seed_fusion.py), the scores (validation/seed_grid.py), the card texts (the units file).
"""

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from ..core.errors import MissingInputError
from ..llm.base import LLMClient, ThinkingLevel, prompt_version

# The rerank prompt (R130). Intent: order the nodes of a knowledge graph as starting points for a question,
# judged from their cards alone. What each rule guards against:
# - rule 1 ranks a node the question names or asks about first, then a node the answer is connected to: the
#   targets of R89 are both kinds, and a node merely similar in words is not a start;
# - rule 2: a question often concerns two or three nodes (213 targets over 141 questions), so ranking only the
#   single best one first would push the second target down;
# - rule 3: the cards only, no outside knowledge, so the order rests on what the graph holds;
# - rule 4: every id once; code still drops ids outside the pool, de-duplicates and appends what is left out.
# No example: an example's words would pull the order towards its domain, and the rules need none. A test
# checks that no four consecutive words of the prompt occur in an evaluation corpus.
PROMPT = """You choose where to start searching a knowledge graph to answer a question. Each candidate node \
is shown as a card: its name and kind, its properties, its relations to other nodes and some claims about it.

The question: {question}

The candidate nodes:
{cards}

Rules:
1. Order the nodes by how useful each one is as a starting point for answering the question. A node the \
question names or asks about comes first; then a node the answer is likely connected to.
2. A question can concern several nodes. Rank each of them high, not only the single best one.
3. Judge from the cards only.
4. In order, best first, give the id of every node, such as "N3", each exactly once."""

CARD = "[{id}]\n{text}"  # one candidate as the prompt shows it; the ids are what `order` cites
_WORKERS = 8  # questions reranked in parallel: one request each, and CachedLLM writes atomically


class RankedNodes(BaseModel):
    """The model's reply: the pool's ids, best first."""

    order: list[str] = Field(description='The id of every node, best first, such as "N3", each once.')


class RerankOptions(BaseModel):
    """What decides an order besides the pool (the settings' values)."""

    model: str
    temperature: float
    thinking: ThinkingLevel


class Reranked(BaseModel):
    """One pool ordered: the refs best first, and how far the reply was off."""

    order: list[str]
    dropped: int  # ids the reply gave that are not in the pool
    repeated: int  # ids the reply gave more than once (counted once)
    missing: int  # pool nodes the reply left out, appended in the pool's order


class SeedReranker:
    """Orders a pool of start nodes by their cards with a model, checked by code."""

    version = prompt_version(PROMPT + json.dumps(RankedNodes.model_json_schema(), sort_keys=True))

    def __init__(self, llm: LLMClient, options: RerankOptions, cards: Mapping[str, str]):
        self._llm = llm
        self._options = options
        self._cards = cards  # node ref -> its card text

    def rerank(self, question: str, pool: Sequence[str]) -> Reranked:
        """The pool best first. One model call (cached). Raises `MissingInputError` for a node without a card
        and `LLMResponseError` when the provider keeps failing."""
        if missing := [ref for ref in pool if ref not in self._cards]:
            raise MissingInputError(f"no card for {len(missing)} pool node(s), first {missing[0]}")
        ids = {f"N{n}": ref for n, ref in enumerate(pool, start=1)}
        cards = "\n\n".join(CARD.format(id=i, text=self._cards[ref]) for i, ref in ids.items())
        o = self._options
        reply = self._llm.generate(
            PROMPT.format(question=question, cards=cards),
            RankedNodes,
            model=o.model,
            temperature=o.temperature,
            thinking=o.thinking,
        )
        return check_order(reply.order, ids)

    def rerank_all(self, pools: Mapping[str, tuple[str, Sequence[str]]]) -> dict[str, Reranked]:
        """Question id -> its pool ordered, from question id -> (question, pool); in parallel."""
        with ThreadPoolExecutor(max_workers=_WORKERS) as workers:
            done = workers.map(lambda item: self.rerank(*item), pools.values())
            return dict(zip(pools, done, strict=True))


def check_order(reply: Sequence[str], ids: Mapping[str, str]) -> Reranked:
    """The refs in the reply's order: ids outside `ids` dropped, repeats counted once, the ids left out after,
    in the order of `ids` (the pool's)."""
    stated = [i.strip() for i in reply]
    known = [i for i in stated if i in ids]
    kept = list(dict.fromkeys(known))
    left = [i for i in ids if i not in kept]
    return Reranked(
        order=[ids[i] for i in kept + left],
        dropped=len(stated) - len(known),
        repeated=len(known) - len(kept),
        missing=len(left),
    )
