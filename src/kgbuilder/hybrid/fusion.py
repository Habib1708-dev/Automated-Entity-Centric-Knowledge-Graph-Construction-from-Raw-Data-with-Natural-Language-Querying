"""Reciprocal rank fusion (R120): several ranked lists made one, by rank alone.

Role in the pipeline: the hybrid source (source.py) fuses its retrievers' chunk lists, and their seed lists,
with it.
Design: an item's score is the sum, over the lists that hold it, of 1 / (k + its rank there), ranks counted
from 1 (Cormack, Clarke and Buettcher 2009). Only ranks count, never a retriever's own score: a cosine and a
BM25 score live on scales that cannot be compared, and no threshold has to be tuned per embedding model (the
rule of query/names.py). A larger `k` flattens the advantage of a first place. Ties go to the item with the
better single rank, then to the earlier list in the order given, then by the item itself, so the order never
depends on dictionary order or on the store.
Not here: producing the lists (retrievers.py).
"""

from collections.abc import Mapping, Sequence


def rrf(lists: Mapping[str, Sequence[str]], k: int) -> list[str]:
    """Every item of `lists` once, best fused score first. Raises `ValueError` for `k` below 0."""
    if k < 0:
        raise ValueError(f"the fusion constant k must be at least 0, got {k}")
    score: dict[str, float] = {}
    best: dict[str, tuple[int, int]] = {}  # item -> (its best rank, the index of the first list with it)
    for position, ranked in enumerate(lists.values()):
        for rank, item in enumerate(dict.fromkeys(ranked), start=1):  # an item counts once per list
            score[item] = score.get(item, 0.0) + 1.0 / (k + rank)
            best[item] = min(best.get(item, (rank, position)), (rank, position))
    return sorted(score, key=lambda item: (-score[item], *best[item], item))
