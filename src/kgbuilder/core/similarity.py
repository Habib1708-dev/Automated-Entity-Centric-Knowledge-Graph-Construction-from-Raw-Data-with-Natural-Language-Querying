"""How alike two names are, by spelling or by meaning: the two similarity primitives of the project.

Role in the pipeline: entity resolution scores candidate pairs with them (resolution/matchers.py), and the
query stage links the names in a question to graph nodes with them (query/names.py), so both agree on
what "a similar name" means.
Not here: what a score leads to (a merge, a link); that is decided by the caller.
"""

import math

from rapidfuzz import fuzz

from .text import norm


def name_similarity(a: str, b: str, cutoff: float = 0.0) -> float:
    """Token-sort similarity of `a` and `b` after `norm`, 0..100: robust to word order, a plural s and
    typos ("Tables" vs "Table" = 91). May return 0 below `cutoff`, which lets rapidfuzz stop early."""
    return fuzz.token_sort_ratio(norm(a), norm(b), score_cutoff=cutoff)


def unit_vector(vector: list[float]) -> list[float]:
    """`vector` scaled to length 1, so that cosine similarity becomes a plain dot product."""
    length = math.sqrt(sum(x * x for x in vector)) or 1.0  # an all-zero vector stays all-zero
    return [x / length for x in vector]


def dot(a: list[float], b: list[float]) -> float:
    """The dot product of two vectors of one length; the cosine similarity of two unit vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True))
