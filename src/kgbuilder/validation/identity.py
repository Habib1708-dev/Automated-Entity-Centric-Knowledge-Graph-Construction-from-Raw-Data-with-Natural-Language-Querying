"""Identity scoring against the gold's mention pairs: precision, recall and the different pairs kept apart
(R75, layered-model Step 5).

Role in the pipeline: one section of `kg eval` (evaluate.py reads the mentions; this module scores them).
A gold pair names two mentions by document and name and says whether they are one canonical entity (one
individual, one record or one kind). The graph says "same" when both mentions refer to one canonical
entity (graph/canonical.py). A pair whose mention the extractor never wrote is not an identity error: it is
counted apart as "not extracted", as for the ER pairs (er.py, R33).
Measures (task file, Step 5): identity precision (of the scored pairs the graph calls the same, the share
the gold calls the same), recall (of the gold's same pairs, the share the graph calls the same) and the
apart rate (of the gold's different pairs, the share kept apart; the bar is 1.0: a wrong join answers
questions about the wrong thing, a missed one only leaves two names apart).
Design: pure, so it is tested without Neo4j; exact lookup only (the mention's name under `norm`, any
spelling the gold side lists). Not here: graph access (evaluate.py), the ER pairs by name (er.py).
"""

from pydantic import BaseModel

from ..core.text import norm
from .gold import IdentityPair, MentionRef


class SheetMention(BaseModel):
    """A mention of the graph with the canonical entity it refers to (itself when it has no edge)."""

    id: str
    doc_id: str
    name: str
    type: str
    canonical: str


class IdentityOutcome(BaseModel):
    """One gold pair: the canonical entities each side's mentions refer to, and what the graph says."""

    index: int
    same: bool  # the gold
    a_entities: list[str]
    b_entities: list[str]

    @property
    def extracted(self) -> bool:
        return bool(self.a_entities and self.b_entities)

    @property
    def joined(self) -> bool:
        """The graph's answer: some mention of each side refers to one entity."""
        return bool(set(self.a_entities) & set(self.b_entities))


class IdentityScore(BaseModel):
    """Identity precision, recall and apart rate with their counts; a rate is None without pairs to score."""

    precision: float | None
    recall: float | None
    apart: float | None
    scored: int  # pairs whose mentions both exist
    joined: int  # scored pairs the graph calls the same
    same_scored: int  # scored pairs the gold calls the same
    different_scored: int
    not_extracted: int
    outcomes: list[IdentityOutcome]

    def metrics(self) -> dict[str, float]:
        """Flat MLflow metrics; a rate without pairs is left out, its counts always logged."""
        out: dict[str, float] = {
            "identity_pairs_scored": self.scored,
            "identity_same_scored": self.same_scored,
            "identity_different_scored": self.different_scored,
            "identity_not_extracted": self.not_extracted,
        }
        for name, value in (
            ("precision", self.precision),
            ("recall", self.recall),
            ("apart_rate", self.apart),
        ):
            if value is not None:
                out[f"identity_{name}"] = value
        return out


def _entities(side: MentionRef, mentions: list[SheetMention]) -> list[str]:
    wanted = {norm(n) for n in side.names}
    return sorted({m.canonical for m in mentions if m.doc_id == side.doc_id and norm(m.name) in wanted})


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def score_identity(mentions: list[SheetMention], pairs: list[IdentityPair]) -> IdentityScore:
    """Score every gold pair against the graph's mentions (each with its canonical id). Pure."""
    outcomes = [
        IdentityOutcome(
            index=i, same=p.same, a_entities=_entities(p.a, mentions), b_entities=_entities(p.b, mentions)
        )
        for i, p in enumerate(pairs)
    ]
    scored = [o for o in outcomes if o.extracted]
    joined = [o for o in scored if o.joined]
    same = [o for o in scored if o.same]
    different = [o for o in scored if not o.same]
    return IdentityScore(
        precision=_rate(sum(o.same for o in joined), len(joined)),
        recall=_rate(sum(o.joined for o in same), len(same)),
        apart=_rate(sum(not o.joined for o in different), len(different)),
        scored=len(scored),
        joined=len(joined),
        same_scored=len(same),
        different_scored=len(different),
        not_extracted=len(outcomes) - len(scored),
        outcomes=outcomes,
    )
