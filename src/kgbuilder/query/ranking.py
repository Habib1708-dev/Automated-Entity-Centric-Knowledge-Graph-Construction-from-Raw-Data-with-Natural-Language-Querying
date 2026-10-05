"""Ranking chunks by similarity to a question: the one order every question-answering system uses.

Role in the pipeline: systems.py ranks the chunks the graph route reaches; plan_run.py ranks a candidate's
chunks for read_check and the input's chunks for the final reader (R74).
Design: cosine similarity over the stored chunk vectors, ties by chunk id, so the order never depends on how
the graph returned the chunks. Moved here from systems.py (R74) so plan_run.py and systems.py share it
without importing each other.
Not here: choosing which chunks are candidates (systems.py, plan_run.py).
"""

import math

from ..core.similarity import dot, unit_vector
from .graph_store import StoredChunk


def rank(vector: list[float], chunks: list[StoredChunk]) -> list[StoredChunk]:
    """`chunks` by cosine similarity to `vector`, most similar first; chunks without a vector come last,
    and ties go by chunk id, so the order never depends on how the graph returned them."""
    query = unit_vector(vector)

    def similarity(chunk: StoredChunk) -> float:
        return dot(query, unit_vector(chunk.embedding)) if chunk.embedding else -math.inf

    return sorted(chunks, key=lambda c: (-similarity(c), c.chunk_id))
