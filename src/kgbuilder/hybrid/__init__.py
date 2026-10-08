"""Hybrid retrieval (plan R116-R125): the text a node is found by, and later the retrievers that search it.

R118: every record, individual and concept gets its evidence read from the graph (unit_sources.py, into
evidence.py's models), and a node representation (representation.py) turns the evidence into the text an
index embeds: today deterministic cards (cards.py, A); LLM node summaries (B, R123) will be one more
representation over the same evidence. Every claim also gets a sentence of its own (claims.py).
R119: unit_graph.py writes the cards and claim sentences into Neo4j as the retrieval index layer, with their
vectors and indexes, re-embedding only what changed.
Dependencies: pipeline -> hybrid -> query's public models, graph, llm.base, core; nothing imports hybrid but
the pipeline.
"""

from .claims import ClaimSentence, claim_text, predicate_words
from .evidence import EvidenceClaim, Neighbours, NodeEvidence, RenderedCard, evidence_hash
from .representation import REPRESENTATIONS, NodeRepresentation, representation
from .unit_sources import EvidenceCaps, read_claim_sentences, read_evidence, read_targets

__all__ = [
    "REPRESENTATIONS",
    "ClaimSentence",
    "EvidenceCaps",
    "EvidenceClaim",
    "Neighbours",
    "NodeEvidence",
    "NodeRepresentation",
    "RenderedCard",
    "claim_text",
    "evidence_hash",
    "predicate_words",
    "read_claim_sentences",
    "read_evidence",
    "read_targets",
    "representation",
]
