"""Blocking for entity resolution: which pairs of same-type entities a meaning-based matcher sends to the
LLM for a decision.

Role in the pipeline: `resolver.find_candidates` asks a `Blocking` which pairs to nominate on top of the
spelling candidates; the matcher (matchers.py) supplies the scores, the LLM makes every merge decision.
Design: Strategy. Two rules, chosen in the settings (`er_embedding_blocking`):
- `ScoreThreshold`: every pair at or above an absolute score. The scale of embedding similarity depends
  on the embedding model and on the domain, so the threshold has to be chosen per dataset (R36: 78 on
  the furniture reviews, read off `kg resolve --preview`).
- `MutualNearest`: a pair when each name is among the other's `k` most similar names of its type. It
  uses ranks only, so it needs no scale and fits any dataset and embedding model unchanged; it nominates
  at most k * n / 2 pairs per type of n entities, so the LLM cost is bounded by the entity count.
Not here: the scores (matchers.py) and what happens to a nominated pair (resolver.py).
"""

from collections.abc import Callable
from itertools import combinations
from typing import Literal, Protocol

from .matchers import EntityRecord

Score = Callable[[EntityRecord, EntityRecord], float]
PairKey = frozenset[str]  # the two entity ids: a pair has no direction
BlockingMode = Literal["off", "threshold", "mutual_nearest"]


class Blocking(Protocol):
    """Nominates pairs among the entities of one type."""

    name: str

    def pairs(self, members: list[EntityRecord], score: Score) -> dict[PairKey, float]:
        """The nominated pairs of `members`, each with its score."""
        ...


class ScoreThreshold:
    """Every pair whose score is at least `threshold`."""

    name = "threshold"

    def __init__(self, threshold: float):
        self.threshold = threshold

    def pairs(self, members: list[EntityRecord], score: Score) -> dict[PairKey, float]:
        scored = ((frozenset((a.id, b.id)), score(a, b)) for a, b in combinations(members, 2))
        return {key: s for key, s in scored if s >= self.threshold}


class MutualNearest:
    """A pair when each entity is among the other's `k` nearest neighbours of the same type.

    "Mutual" keeps a hub name (one close to many others, like "defective") from pulling in every
    neighbour: it must also be among *their* nearest. Ties are broken by entity id, so the same graph
    always gives the same pairs.
    """

    name = "mutual_nearest"

    def __init__(self, k: int):
        if k < 1:
            raise ValueError(f"k must be at least 1, got {k}")
        self.k = k

    def pairs(self, members: list[EntityRecord], score: Score) -> dict[PairKey, float]:
        scores = {frozenset((a.id, b.id)): score(a, b) for a, b in combinations(members, 2)}
        nearest = {e.id: self._nearest(e.id, members, scores) for e in members}

        def mutual(key: PairKey) -> bool:
            a, b = tuple(key)
            return b in nearest[a] and a in nearest[b]

        return {key: s for key, s in scores.items() if mutual(key)}

    def _nearest(self, entity: str, members: list[EntityRecord], scores: dict[PairKey, float]) -> set[str]:
        """Ids of the `k` members most similar to `entity`, highest score first, ties by id."""
        others = [m.id for m in members if m.id != entity]
        others.sort(key=lambda other: (-scores[frozenset((entity, other))], other))
        return set(others[: self.k])


def blocking_from(mode: BlockingMode, threshold: float, k: int) -> Blocking | None:
    """The blocking named by the settings, or None for "off" (spelling candidates only)."""
    if mode == "threshold":
        return ScoreThreshold(threshold)
    if mode == "mutual_nearest":
        return MutualNearest(k)
    return None
