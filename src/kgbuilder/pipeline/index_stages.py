"""The retrieval units' stages (R118): `kg units`, the cards and claim sentences written out for review.

Role in the pipeline: on a finished graph, after `kg attach`, not part of `kg run` (`FULL_PIPELINE`): the
units serve question answering, not the graph. `kg units` reads every node's evidence
(hybrid/unit_sources.py), renders its card with one node representation, and writes the cards and one
sentence per claim to `index/units.jsonl`; nothing is written to the graph and no model is called. R119's
`kg index` will embed the same units into Neo4j.
Design: wiring and logging only, like qa_stages.py. One MLflow run (`units`): params name the representation
with its version, the evidence caps and the graph digest; metrics count the cards per kind, the claim
sentences, the claims the claims cap leaves out, the truncated cards and the text lengths; the artifact is
the units file.
Not here: the evidence and its Cypher (hybrid/unit_sources.py), the cards (hybrid/cards.py).
"""

from statistics import mean

from ..graph.digest import graph_digest
from ..hybrid import (
    ClaimSentence,
    EvidenceCaps,
    NodeEvidence,
    RenderedCard,
    read_claim_sentences,
    read_evidence,
)
from ..hybrid import representation as node_representation
from ..structured.plan import ConstructionPlan
from ..structured.profiler import DataProfile
from ..text.record_documents import prose_columns
from .stages import BaseStage


class UnitsStage(BaseStage):
    """Render every node's card with one representation and write the cards and the claim sentences
    (`kg units`)."""

    name = "units"
    UNITS_FILE = "index/units.jsonl"

    def __init__(self, cards: str):
        node_representation(cards, 1)  # an unknown name is refused before anything runs
        self.cards = cards

    def params(self, ctx, state):
        s = ctx.settings
        return {
            "cards": self.cards,
            "representation_version": node_representation(self.cards, s.index_card_max_chars).version,
            "index_card_names": s.index_card_names,
            "index_card_claims": s.index_card_claims,
            "index_card_max_chars": s.index_card_max_chars,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        run.params(graph_digest=graph_digest(ctx.driver).value)
        plan = state.load_plan(ctx, required=False)  # without a plan (text only) there are no records
        caps = EvidenceCaps(names=s.index_card_names, claims=s.index_card_claims)
        evidence = read_evidence(ctx.driver, plan, caps, _prose(plan, state.load_profile(ctx)))
        cards = node_representation(self.cards, s.index_card_max_chars).render(evidence)
        claims = read_claim_sentences(ctx.driver)
        lines = [c.model_dump_json() for c in cards] + [c.model_dump_json() for c in claims]
        run.metrics(**unit_metrics(evidence, cards, claims))
        run.artifact(ctx.write(self.UNITS_FILE, "\n".join(lines) + "\n"))


def _prose(plan: ConstructionPlan | None, profile: DataProfile | None) -> dict[str, set[str]]:
    """Record label -> its prose columns (R67's record documents hold them as chunks already)."""
    if plan is None or profile is None:
        return {}
    return {
        rule.label: set(prose_columns({rule.unique_column, *rule.properties}, profile, rule.source_file))
        for rule in plan.nodes
    }


def unit_metrics(
    evidence: list[NodeEvidence], cards: list[RenderedCard], claims: list[ClaimSentence]
) -> dict[str, float | int | None]:
    """The cards per kind, the claim sentences, the claims the claims cap leaves out of the evidence, the
    cards the length cap truncated, and the characters of cards and claim sentences."""
    card_chars = [len(c.text) for c in cards]
    kinds = ("record", "individual", "concept")
    return {
        "cards": len(cards),
        **{f"cards_{kind}": sum(e.kind == kind for e in evidence) for kind in kinds},
        "claims": len(claims),
        "claims_skipped": sum(e.claims_total - len(e.claims) for e in evidence),
        "cards_truncated": sum(c.truncated for c in cards),
        "card_chars_mean": round(mean(card_chars), 1) if card_chars else None,
        "card_chars_max": max(card_chars, default=None),
        "claim_chars_mean": round(mean(len(c.text) for c in claims), 1) if claims else None,
    }
