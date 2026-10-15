"""The passage grid (R197): which passages each method would show in `search_text`'s first five, scored
against the gold's evidence, each method paired with today's RRF question by question.

Role in the pipeline: `kg passage-grid` (pipeline/passage_stages.py) reads each gold question's dense and
lexical chunk lists from the loaded graph, has the reranker (hybrid/passage_rerank.py) order the `rerank`
methods' pools, and scores here. Plan R197-R198 asks whether a model's order shows more gold passages than
RRF's (R196: two never-read passages were retrieved, then cut by the fused first five); plan R200-R201 adds a
local cross-encoder ordering the same pools, to compare it with the LLM.
Design: pure functions over pydantic rows. The grid is fixed before any run, so no method is chosen after
seeing the scores (R200 added the `cross` methods before their first run). A `rerank` or `cross` method's
pool is its RRF fusion of the same lists, in RRF order: the model
reads the candidates RRF would choose from, and any passage its reply leaves out keeps its RRF place, so
`rerank_5x5` differs from `rrf_5x5` only by the order. Recall is per question (the share of its gold
passages shown), so a question with seven gold passages and one with one count alike.
Not here: reading the graph, calling the model, MLflow (the stage).
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from ..hybrid.fusion import rrf
from .paired import mcnemar_exact

Kind = Literal["rrf", "dense", "rerank", "cross"]
# the kinds whose shown passages are a reranker's order of the pool: `rerank` the LLM's, `cross` the
# cross-encoder's (R200)
RERANKED: tuple[Kind, ...] = ("rerank", "cross")

SHOWN = 5  # passages search_text shows the model (`agent_chunks`, R135): the budget every method is scored at
BASELINE = "rrf_5x5"


class PassageMethod(BaseModel):
    """One way to choose the shown passages: fuse by RRF, take the dense list, or have the LLM (`rerank`) or
    the cross-encoder (`cross`) order the RRF pool; `depth` is how deep each list is read."""

    name: str
    kind: Kind
    depth: int = Field(ge=1)


# The grid (plan R197-R198), fixed before the run. rrf_5x5 is search_text as it is; rrf_10x10 reads the lists
# deeper with the same fusion (R196: deeper fusion buried dense-only passages); dense_5 is the meaning list
# alone; rerank_5x5 orders the very candidates rrf_5x5 fuses; rerank_10x10 orders twice as deep a pool.
# bge_5x5 and bge_10x10 (R200) order the very pools of rerank_5x5 and rerank_10x10 with the cross-encoder,
# so the two rerankers differ only by the model.
GRID: tuple[PassageMethod, ...] = (
    PassageMethod(name="rrf_5x5", kind="rrf", depth=5),
    PassageMethod(name="rrf_10x10", kind="rrf", depth=10),
    PassageMethod(name="dense_5", kind="dense", depth=5),
    PassageMethod(name="rerank_5x5", kind="rerank", depth=5),
    PassageMethod(name="rerank_10x10", kind="rerank", depth=10),
    PassageMethod(name="bge_5x5", kind="cross", depth=5),
    PassageMethod(name="bge_10x10", kind="cross", depth=10),
)
DEPTH = max(m.depth for m in GRID)  # how deep the stage reads each list once, for every method


class PassageLists(BaseModel):
    """One gold question's lists, read `DEPTH` deep: its key (gold file and id), wording and evidence."""

    key: str
    question: str
    gold: list[str]  # the chunk ids the gold cites as evidence
    dense: list[str]
    lexical: list[str]


def pool(method: PassageMethod, lists: PassageLists, rrf_k: int) -> list[str]:
    """The method's candidates in their free order: RRF over both lists read `depth` deep (an `rrf` or
    reranked method), or the dense list alone."""
    if method.kind == "dense":
        return lists.dense[: method.depth]
    return rrf({"dense": lists.dense[: method.depth], "lexical": lists.lexical[: method.depth]}, rrf_k)


class PassageOutcome(BaseModel):
    """One question under every method: the passages shown, and each pool's size and recall."""

    key: str
    gold: list[str]
    shown: dict[str, list[str]]
    pool_size: dict[str, int]
    pool_recall: dict[str, float]

    def recall(self, method: str) -> float:
        """The share of the gold passages among the method's shown ones."""
        return _recall(self.shown[method], self.gold)


def outcome(
    lists: PassageLists, rrf_k: int, orders: Mapping[str, Sequence[str]], methods: Sequence[PassageMethod]
) -> PassageOutcome:
    """The question under every method; `orders` holds the reranker's order of each reranked method's pool.
    Raises `KeyError` for a reranked method without its order."""
    pools = {m.name: pool(m, lists, rrf_k) for m in methods}
    shown = {m.name: list(orders[m.name] if m.kind in RERANKED else pools[m.name])[:SHOWN] for m in methods}
    return PassageOutcome(
        key=lists.key,
        gold=lists.gold,
        shown=shown,
        pool_size={n: len(p) for n, p in pools.items()},
        pool_recall={n: _recall(p, lists.gold) for n, p in pools.items()},
    )


class MethodScore(BaseModel):
    """One method over the questions: mean recall at SHOWN, questions with a gold passage shown, the pool's
    mean recall and size, and the pairing with the baseline on per-question recall."""

    method: str
    questions: int
    recall: float
    any_found: int
    pool_recall: float
    pool_size: float
    better: int  # questions where this method shows more of the gold than the baseline
    worse: int
    p_value: float  # exact sign test on better against worse (validation/paired.py)


def score(outcomes: Sequence[PassageOutcome], methods: Sequence[PassageMethod]) -> list[MethodScore]:
    """Every method's score over `outcomes`, the baseline paired with itself (0, 0, p = 1). Raises
    `ValueError` for no outcome."""
    if not outcomes:
        raise ValueError("no question to score")
    n = len(outcomes)
    out = []
    for m in methods:
        better = sum(o.recall(m.name) > o.recall(BASELINE) for o in outcomes)
        worse = sum(o.recall(m.name) < o.recall(BASELINE) for o in outcomes)
        out.append(
            MethodScore(
                method=m.name,
                questions=n,
                recall=sum(o.recall(m.name) for o in outcomes) / n,
                any_found=sum(o.recall(m.name) > 0 for o in outcomes),
                pool_recall=sum(o.pool_recall[m.name] for o in outcomes) / n,
                pool_size=sum(o.pool_size[m.name] for o in outcomes) / n,
                better=better,
                worse=worse,
                p_value=mcnemar_exact(better, worse),
            )
        )
    return out


def metrics(scores: Sequence[MethodScore]) -> dict[str, float | int]:
    """Flat MLflow metrics, `<method>_<score>`, stable across runs."""
    fields = ("questions", "recall", "any_found", "pool_recall", "pool_size", "better", "worse", "p_value")
    return {f"{s.method}_{f}": getattr(s, f) for s in scores for f in fields}


def markdown(scores: Sequence[MethodScore]) -> str:
    """The scores as one table, a row per method."""
    lines = [
        f"| method | n | recall@{SHOWN} | any gold shown | pool recall | pool size | better | worse | p |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {s.method} | {s.questions} | {s.recall:.3f} | {s.any_found} | {s.pool_recall:.3f} | "
        f"{s.pool_size:.1f} | {s.better} | {s.worse} | {s.p_value:.3g} |"
        for s in scores
    ]
    return "\n".join(lines) + "\n"


def _recall(chosen: Sequence[str], gold: Sequence[str]) -> float:
    # a question without evidence never reaches here: the stage keeps only questions citing chunks
    return len(set(chosen) & set(gold)) / len(set(gold))
