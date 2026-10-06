"""Traversal by code (R87, M6): missing paths against gold (thing, chunk) pairs, and foreign chunks.

Role in the pipeline: part of the graph audit's code checks (`audit/checks.py` carries the report). False
paths need meaning and are judged in part e; what code can decide is (1) whether each gold thing reaches
its gold chunk at all, per traversal pattern, and (2) which chunks a record reaches whose document is about
something outside the record's scope.
Design: the patterns are the query stage's (`query/traversal.py`, `_THING_TEXT`), restated over the snapshot:
  P1 thing -HAS_OBSERVATION-> claim -FROM-> chunk; P2 thing <-ABOUT- document <-PART_OF- chunk;
  P3 thing <-ABOUT- chunk; P4 thing <-REFERS_TO- mention <-SUBJECT|OBJECT- claim -FROM-> chunk;
  P5 thing <-REFERS_TO- mention <-MENTIONS- chunk.
The gold pairs are R68's blind claims (`tests/gold/r68/<dataset>_claims.json`, each naming the thing its
sentence is about, with the sentence's chunk in `<dataset>_sample.json`), written before any layered graph
existed. A pair counts only when the chunk holds at least one claim of the build, so an extraction miss is
not counted as a traversal miss. A gold name maps to a record by name or key, the document's scope first.
Must not: judge whether a reached chunk is relevant; that is the judge's (part e).
"""

import json
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from ..core.text import norm, squash
from ..validation.interval import Proportion
from .scope import ScopeIndex
from .snapshot import GraphSnapshot

PATTERNS = ("P1", "P2", "P3", "P4", "P5")


class ReachReport(BaseModel):
    """Reach of the gold pairs per pattern and in any pattern, the pairs left out, and foreign chunks."""

    pairs: int
    per_pattern: dict[str, Proportion]
    any_pattern: Proportion
    missed: list[str]  # "<thing> -> <chunk>" reached by no pattern
    unmapped: list[str]  # gold names that name no record
    no_claim_chunks: int  # gold pairs left out: their chunk holds no claim of the build
    foreign_chunks: dict[str, int]  # pattern -> (record, chunk) pairs whose document is outside its scope
    foreign_examples: dict[str, list[str]]

    def metrics(self) -> dict[str, float]:
        out = {f"reach_{p}": v.rate for p, v in self.per_pattern.items() if v.rate is not None}
        if self.any_pattern.rate is not None:
            out["reach_any"] = self.any_pattern.rate
        out |= {"reach_pairs": float(self.pairs), "reach_unmapped": float(len(self.unmapped))}
        return out | {f"foreign_chunks_{p}": float(n) for p, n in self.foreign_chunks.items()}


class Traversal:
    """The chunks each thing reaches, per pattern, over one snapshot."""

    def __init__(self, s: GraphSnapshot) -> None:
        chunk_of = {c.id: c.chunk_id for c in s.claims}
        chunks_of_doc: dict[str, set[str]] = defaultdict(set)
        for c in s.chunks:
            chunks_of_doc[c.doc_id].add(c.chunk_id)
        self.reach: dict[str, dict[str, set[str]]] = {p: defaultdict(set) for p in PATTERNS}
        for a in s.attachments:
            self.reach["P1"][a.thing].add(chunk_of[a.observation])
        for link in s.documents_about:
            self.reach["P2"][link.thing] |= chunks_of_doc[link.source]
        for link in s.sections_about:
            self.reach["P3"][link.thing].add(link.source)
        thing_of = {a.mention: a.canonical for a in s.references}
        for c in s.claims:
            for end in (c.subject, c.object):
                if end in thing_of:
                    self.reach["P4"][thing_of[end]].add(c.chunk_id)
        for m in s.mentions:
            if m.id in thing_of:
                self.reach["P5"][thing_of[m.id]] |= set(m.chunks)

    def chunks(self, pattern: str, thing: str) -> set[str]:
        return self.reach[pattern].get(thing, set())


def gold_pairs(claims_file: Path, sample_file: Path) -> list[tuple[str, str, str]]:
    """(gold thing name, chunk id, doc id) of every R68 blind claim with an `about`, deduplicated."""
    sentences = {x["id"]: x for x in json.loads(sample_file.read_text(encoding="utf-8"))["sentences"]}
    out: dict[tuple[str, str], str] = {}
    for sentence in json.loads(claims_file.read_text(encoding="utf-8"))["sentences"]:
        where = sentences[sentence["id"]]
        for claim in sentence["claims"]:
            if claim.get("about"):
                out.setdefault((claim["about"], where["chunk_id"]), where["doc_id"])
    return [(name, chunk, doc) for (name, chunk), doc in sorted(out.items())]


def compute_reach(s: GraphSnapshot, pairs: list[tuple[str, str, str]]) -> ReachReport:
    """Reach of the gold pairs, and the foreign chunks of every record of the snapshot."""
    scope = ScopeIndex(s)
    traversal = Traversal(s)
    with_claims = {c.chunk_id for c in s.claims}
    found: dict[str, list[bool]] = {p: [] for p in PATTERNS}
    any_found: list[bool] = []
    missed, unmapped = [], []
    skipped = 0
    for name, chunk, doc in pairs:
        if chunk not in with_claims:
            skipped += 1
            continue
        thing = _record_named(s, name, doc, scope)
        if thing is None:
            unmapped.append(name)
            continue
        hits = {p: chunk in traversal.chunks(p, thing) for p in PATTERNS}
        for p in PATTERNS:
            found[p].append(hits[p])
        any_found.append(any(hits.values()))
        if not any(hits.values()):
            missed.append(f"{thing} -> {chunk}")
    foreign, examples = _foreign(s, traversal, scope)
    return ReachReport(
        pairs=len(any_found),
        per_pattern={p: Proportion.of(sum(v), len(v)) for p, v in found.items()},
        any_pattern=Proportion.of(sum(any_found), len(any_found)),
        missed=missed,
        unmapped=sorted(set(unmapped)),
        no_claim_chunks=skipped,
        foreign_chunks=foreign,
        foreign_examples=examples,
    )


def _record_named(s: GraphSnapshot, name: str, doc: str, scope: ScopeIndex) -> str | None:
    """The record a gold name names, by display name or key; within the document's scope when one does."""
    hits = [r.id for r in s.records if norm(r.name or "") == norm(name) or squash(r.key) == squash(name)]
    in_scope = [h for h in hits if not scope.outside(doc, h)]
    chosen = in_scope or hits
    return chosen[0] if len(chosen) == 1 else None


def _foreign(
    s: GraphSnapshot, traversal: Traversal, scope: ScopeIndex
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Per claim pattern (P1, P4): the (record, chunk) pairs whose chunk's document has a scope without the
    record in it; up to five examples each."""
    doc_of = {c.chunk_id: c.doc_id for c in s.chunks}
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for p in ("P1", "P4"):
        pairs = sorted(
            (r.id, chunk)
            for r in s.records
            for chunk in traversal.chunks(p, r.id)
            if scope.outside(doc_of[chunk], r.id)
        )
        counts[p] = len(pairs)
        examples[p] = [f"{thing} -> {chunk}" for thing, chunk in pairs[:5]]
    return counts, examples
