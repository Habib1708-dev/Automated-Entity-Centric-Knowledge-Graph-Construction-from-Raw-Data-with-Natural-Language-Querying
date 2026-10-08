"""Hybrid retrieval (plan R116-R125): the text a node is found by, and later the retrievers that search it.

R118: every record, individual and concept gets its evidence read from the graph (unit_sources.py, into
evidence.py's models), and a node representation (representation.py) turns the evidence into the text an
index embeds: deterministic cards (cards.py, A) and, since R123, LLM node summaries checked by code
(summaries.py, B) over the same evidence. Every claim also gets a sentence of its own (claims.py).
R119: unit_graph.py writes the cards and claim sentences into Neo4j as the retrieval index layer, with their
vectors and indexes, re-embedding only what changed.
R120a: unit_store.py searches that layer (and the chunks' full-text index) by vector and by words
(lucene.py), and retrievers.py turns a question into one ranked list of chunk ids per retriever: chunks,
claims with their opposite-truth siblings, cards taking turns, and the name-linker route.
R120b: source.py embeds a question once, asks the retrievers the settings name, fuses their lists by rank
(fusion.py) and returns the best chunks: the `hybrid` system's chunk source (pipeline/qa_systems.py).
Dependencies: pipeline -> hybrid -> query's public models, graph, llm.base, core; nothing imports hybrid but
the pipeline.
"""

from .claims import ClaimSentence, claim_text, predicate_words
from .evidence import EvidenceClaim, Neighbours, NodeEvidence, RejectedSummary, RenderedCard, evidence_hash
from .representation import (
    REPRESENTATIONS,
    NodeRepresentation,
    RepresentationOptions,
    check_representation,
    representation,
)
from .summaries import SummaryOptions
from .unit_sources import EvidenceCaps, read_claim_sentences, read_evidence, read_targets

__all__ = [
    "REPRESENTATIONS",
    "ClaimSentence",
    "EvidenceCaps",
    "EvidenceClaim",
    "Neighbours",
    "NodeEvidence",
    "NodeRepresentation",
    "RejectedSummary",
    "RenderedCard",
    "RepresentationOptions",
    "SummaryOptions",
    "check_representation",
    "claim_text",
    "evidence_hash",
    "predicate_words",
    "read_claim_sentences",
    "read_evidence",
    "read_targets",
    "representation",
]
