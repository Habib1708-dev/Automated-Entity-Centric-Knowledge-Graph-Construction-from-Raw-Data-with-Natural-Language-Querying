"""Similarity signals for entity resolution: how alike are two entity names?

Role in the pipeline: used by resolver.py to nominate candidate pairs of duplicate entities.
Design: Strategy. Each matcher implements `Matcher.score`; the resolver is written against the protocol,
so a new signal (phonetic, abbreviation-aware...) is a new class here and nothing else changes.
Not here: what to do with a score (resolver.py decides: merge, ask the LLM, or keep apart).
"""

from typing import Protocol

from pydantic import BaseModel

from ..core.similarity import dot, name_similarity, unit_vector
from ..llm.base import Embedder


class EntityRecord(BaseModel):
    """An `:Entity` node as entity resolution sees it."""

    id: str
    name: str
    type: str
    aliases: list[str]
    mentions: int  # number of chunks mentioning it; the most mentioned member of a group stays canonical
    # the tones ("positive", "negative") of the claims that have it as their object (R66); neutral claims
    # are left out, they say nothing about which way a kind points
    polarities: list[str] = []


class Matcher(Protocol):
    """Scores a pair of same-type entities from 0 (unrelated) to 100 (identical)."""

    name: str

    def score(self, a: EntityRecord, b: EntityRecord, cutoff: float = 0.0) -> float:
        """Similarity of `a` and `b`. May return 0 for any score below `cutoff` (lets matchers exit early)."""
        ...


class FuzzyNameMatcher:
    """Token-sort string similarity: robust to word order, plural s, typos ("Tables" vs "Table" = 91)."""

    name = "fuzzy"

    def score(self, a: EntityRecord, b: EntityRecord, cutoff: float = 0.0) -> float:
        # the cutoff makes rapidfuzz stop as soon as the pair cannot reach it, which is what keeps the
        # all-pairs comparison cheap: most pairs are hopeless and exit almost immediately
        return name_similarity(a.name, b.name, cutoff)


class EmbeddingMatcher:
    """Cosine similarity of name embeddings, scaled to 0..100: finds synonyms that share no characters
    ("sofa" / "couch"). Only ever used to nominate pairs for LLM adjudication, never to merge directly:
    short names of different things often have very similar embeddings."""

    name = "embedding"

    def __init__(self, embedder: Embedder, entities: list[EntityRecord]):
        # one batched embedding call for all names up front, instead of one call per pair
        vectors = embedder.embed([e.name for e in entities]) if entities else []
        self._unit_vectors = {e.id: unit_vector(v) for e, v in zip(entities, vectors, strict=True)}

    def score(self, a: EntityRecord, b: EntityRecord, cutoff: float = 0.0) -> float:
        return 100.0 * dot(self._unit_vectors[a.id], self._unit_vectors[b.id])
