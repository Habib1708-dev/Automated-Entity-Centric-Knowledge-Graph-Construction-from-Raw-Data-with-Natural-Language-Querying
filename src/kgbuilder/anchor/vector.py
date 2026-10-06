"""Arm C of the anchor-graph evaluation (R92): vector retrieval, the baseline evidence reach (C5) is compared
with.

Role in the pipeline: in `kg anchor-compare` (pipeline/anchor_stages.py), after the graph arms' reports
exist. The direction (section 7.2) fixes the baseline: top-k cosine similarity between the question text
and the chunk embeddings of the build, under the build's embedding model and over the chunk text the
ingest stage embedded. The stage computes the vectors; this module only ranks and scores them.
Design: pure functions. A question's ranking keeps the best chunks with their cosine, so a reviewer sees
why each chunk came first; the C5 result has the graph arms' shape (`EvidenceReach`, mode "vector") over
the same questions, so the comparison pairs question by question.
Must not: call the embedder (the stage does), or choose another pool of questions than the graph arms'.
"""

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..validation.interval import Proportion
from ..validation.qa_gold import QAGold
from .criteria import EvidenceReach, QuestionReach

VECTOR_MODE = "vector"


class Ranked(BaseModel):
    chunk: str
    cosine: float


def cosine_rank(query: Sequence[float], chunks: Mapping[str, Sequence[float]], top: int) -> list[Ranked]:
    """The `top` chunks nearest `query` by cosine similarity, best first; ties go to the chunk id. A zero
    vector has no direction and scores 0."""
    q = _unit(query)
    scored = [
        Ranked(chunk=c, cosine=sum(a * b for a, b in zip(q, _unit(v), strict=True)))
        for c, v in chunks.items()
    ]
    return sorted(scored, key=lambda r: (-r.cosine, r.chunk))[:top]


def _unit(v: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else [0.0 for _ in v]


def vector_reach(
    qa: QAGold, pool: Sequence[str], rankings: Mapping[str, Sequence[Ranked]], budgets: Sequence[int]
) -> EvidenceReach:
    """C5 of vector retrieval over the questions of `pool` (the graph arms' C5 questions, in their order).
    Every chunk is reachable by similarity, so the unbudgeted reach is all gold chunks by definition."""
    by_id = {q.id: q for q in qa.questions}
    rows = []
    for qid in pool:
        q = by_id[qid]
        gold = sorted({c.chunk_id for c in q.chunks})
        ranked = [r.chunk for r in rankings[qid]]
        rows.append(
            QuestionReach(
                question=qid,
                type=q.type,
                gold=gold,
                starts=[],
                ranked=ranked[: max(budgets)],
                hits={k: len(set(ranked[:k]) & set(gold)) for k in budgets},
                reached=len(gold),
            )
        )
    chunks = sum(len(r.gold) for r in rows)
    return EvidenceReach(
        mode=VECTOR_MODE,
        questions=rows,
        no_start=[],
        recall_at={k: Proportion.of(sum(r.hits[k] for r in rows), chunks) for k in budgets},
        complete_at={k: Proportion.of(sum(r.complete(k) for r in rows), len(rows)) for k in budgets},
        reached=Proportion.of(chunks, chunks),
    )
