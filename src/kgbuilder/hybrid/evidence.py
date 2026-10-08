"""The evidence of one node, read once and shared by every node representation (R118), and what a
representation renders from it.

Role in the pipeline: `kg units` (and R119's `kg index`) read the evidence of every record, individual and
concept from the graph (unit_sources.py) and hand it to a node representation (representation.py), which
turns it into the text an index embeds: deterministic cards (cards.py, A) now, LLM node summaries (B, R123)
later, from exactly the same evidence.
Design: plain data, capped where it is read, so every representation sees the same names, relations and
claims. `evidence_hash` identifies it, and every rendered card keeps the hash of the evidence it was made
from: an A-against-B comparison can then prove both were built from identical evidence (the fairness
contract of plan R116-R125).
Not here: reading the graph (unit_sources.py), rendering (cards.py).
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel

from ..text.extraction import Modality

NodeKind = Literal["record", "individual", "concept"]


class Neighbours(BaseModel):
    """One relation of a node, grouped by its type, its direction and the other ends' label: how many other
    ends it has, and the first of their names."""

    type: str  # a relationship type between records, or the predicate of a claim the node is an end of
    outgoing: bool  # the relation points away from the node
    label: str  # the other ends' record label or entity type
    count: int  # every other end, before the names cap
    names: list[str]  # the other ends' names, sorted, at most the names cap


class EvidenceClaim(BaseModel):
    """One claim the node holds (`HAS_OBSERVATION`), standing for every observation on the node of the same
    canonical triple with the same modality and condition: its sentence (claims.py), how many of those
    observations state the triple and how many deny it, and its qualifiers."""

    id: str  # the first observation's id
    predicate: str
    sentence: str
    stated: int
    denied: int  # by `triple_truth`: whether the stored triple itself is denied, not only its statement
    modality: Modality
    hedge: str  # the words that say it only may hold, as the first observation writes them
    condition: str
    chunk_id: str  # the first observation's chunk


class NodeEvidence(BaseModel):
    """What the graph holds about one node, capped where it is read (unit_sources.py)."""

    ref: str  # the node's stable id: a record's `record_ref`, an individual's or a concept's canonical id
    kind: NodeKind
    label: str  # a record's label, an individual's or a concept's entity type
    title: str  # its display name (a record without a name: its key)
    aliases: list[str]  # the other names its mentions write, sorted, at most the names cap
    properties: dict[str, str]  # a record's columns as text, but its name and its prose columns; sorted
    relations: list[Neighbours]
    claims: list[EvidenceClaim]  # each predicate's best supported first, at most the claims cap
    claims_total: int  # the claims the node holds before the cap


class RenderedCard(BaseModel):
    """A node's text as one representation renders it, with the hash of the evidence it came from: a line of
    `index/units.jsonl`, and the text R119 embeds for the node."""

    unit: Literal["card"] = "card"
    ref: str
    text: str
    evidence_hash: str
    truncated: bool  # the length cap dropped something of the evidence


def evidence_hash(evidence: NodeEvidence) -> str:
    """16 hex characters over the evidence's JSON with sorted keys: equal evidence, equal hash."""
    content = json.dumps(evidence.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
