"""The retrieval units' stages: `kg units` (R118) writes the cards and claim sentences out for review, `kg
index` (R119) embeds them into Neo4j as the retrieval index layer.

Role in the pipeline: on a finished graph, after `kg attach`, not part of `kg run` (`FULL_PIPELINE`): the
units serve question answering, not the graph. Both read every node's evidence (hybrid/unit_sources.py),
render its card with one node representation, and take one sentence per claim. `kg units` only writes them
to `index/units.jsonl`; `kg index` also writes them into the graph as `:RetrievalUnit` nodes with their
vectors and creates the indexes R120's retrievers search, embedding only the units that are new or changed.
Design: wiring and logging only, like qa_stages.py. One MLflow run per stage: params name the representation
with its version and its own params, the evidence caps, the graph digest (which the index layer leaves
unchanged), and for `kg index` the embedding model and the full-text analyzer; metrics count the cards per
kind, the claim sentences, the claims the claims cap leaves out, the truncated cards and the text lengths, and
for `kg index` the units written, reused and removed and the embedding volume; the artifacts are the units
file and the representation's prompts, if it sends any. The representation is built here from the settings
(`card_representation`), with the context's model only for `kg index`: `kg units` never calls a model.
Not here: the evidence and its Cypher (hybrid/unit_sources.py), the cards (hybrid/cards.py), the index
layer's writes (hybrid/unit_graph.py) and names (graph/index_layer.py).
"""

from statistics import mean

from pydantic import BaseModel

from ..config import Settings
from ..core.errors import ConfigurationError, LLMUnavailableError
from ..graph.digest import graph_digest
from ..graph.index_layer import ANALYZER, RESERVED_LABELS, RESERVED_TYPES, card_label
from ..hybrid import (
    REPRESENTATIONS,
    ClaimSentence,
    EvidenceCaps,
    NodeEvidence,
    NodeRepresentation,
    RenderedCard,
    RepresentationOptions,
    SummaryOptions,
    check_representation,
    read_claim_sentences,
    read_evidence,
    read_targets,
    representation,
)
from ..hybrid.unit_graph import card_rows, claim_rows, ensure_indexes, write_units
from ..llm.base import LLMClient
from ..llm.counting import CountingEmbedder
from ..structured.plan import ConstructionPlan
from ..structured.profiler import DataProfile
from ..text.record_documents import prose_columns
from ..tracking.base import Run
from .stage import PipelineContext, PipelineState
from .stages import BaseStage

UNITS_FILE = "index/units.jsonl"


class Units(BaseModel):
    """Every node's evidence, its card in one representation, and every claim's sentence."""

    evidence: list[NodeEvidence]
    cards: list[RenderedCard]
    claims: list[ClaimSentence]


class UnitsStage(BaseStage):
    """Render every node's card with one representation and write the cards and the claim sentences
    (`kg units`)."""

    name = "units"
    UNITS_FILE = UNITS_FILE

    def __init__(self, cards: str):
        self.cards = check_representation(cards)  # an unknown name is refused before anything runs

    def params(self, ctx, state):
        return card_params(ctx, self.cards)

    def run(self, ctx, state, run):
        run.params(graph_digest=graph_digest(ctx.driver).value)
        rep = card_representation(ctx.settings, self.cards)  # no model: `kg units` never calls one
        log_prompts(run, rep)
        units = read_units(ctx, state, rep, state.load_plan(ctx, required=False))
        run.metrics(**unit_metrics(units))
        run.artifact(write_units_file(ctx, units))


class IndexStage(BaseStage):
    """Write one representation's cards and every claim sentence into the graph with their vectors, and the
    indexes over them (`kg index`)."""

    name = "index"

    def __init__(self, cards: str):
        self.cards = check_representation(cards)

    def params(self, ctx, state):
        return {**card_params(ctx, self.cards), "embed_model": ctx.settings.embed_model, "analyzer": ANALYZER}

    def run(self, ctx, state, run):
        if ctx.embedder is None:
            raise LLMUnavailableError("kg index embeds the units: set GEMINI_API_KEY")
        plan = state.load_plan(ctx, required=False)
        refuse_collisions(plan)
        run.params(graph_digest=graph_digest(ctx.driver).value)
        rep = card_representation(ctx.settings, self.cards, ctx.llm)
        log_prompts(run, rep)
        units = read_units(ctx, state, rep, plan)
        cards = card_rows(self.cards, rep.version, units.cards, read_targets(ctx.driver, plan))
        embedder = CountingEmbedder(ctx.embedder)
        written = write_units(
            ctx.driver, self.cards, cards, claim_rows(units.claims), embedder, ctx.settings.embed_model
        )
        recreated = ensure_indexes(ctx.driver, self.cards, written.dimensions) if written.dimensions else []
        run.metrics(
            **unit_metrics(units),
            units_written=written.written,
            units_reused=written.reused,
            stale_units_removed=written.stale_removed,
            embedded_texts=embedder.texts,
            embedded_chars=embedder.chars,
            indexes_recreated=len(recreated),
        )
        run.artifact(write_units_file(ctx, units))


def card_representation(settings: Settings, cards: str, llm: LLMClient | None = None) -> NodeRepresentation:
    """The representation `cards` built from the settings, writing with `llm` if it writes with a model:
    every stage and system builds it here, so all compute the same version from the same settings."""
    s = settings
    summary = SummaryOptions(
        model=s.index_summary_model,
        temperature=s.llm_temperature,
        thinking=s.index_summary_thinking,
        max_chars=s.index_summary_max_chars,
    )
    options = RepresentationOptions(card_max_chars=s.index_card_max_chars, summary=summary)
    return representation(cards, options, llm)


def card_params(ctx: PipelineContext, cards: str) -> dict[str, object]:
    """What decides the cards' texts: the representation, its version and its own params, and the evidence
    caps."""
    s = ctx.settings
    rep = card_representation(s, cards)
    return {
        "cards": cards,
        "representation_version": rep.version,
        "index_card_names": s.index_card_names,
        "index_card_claims": s.index_card_claims,
        "index_card_max_chars": s.index_card_max_chars,
        **rep.params(),
    }


def log_prompts(run: Run, rep: NodeRepresentation) -> None:
    """Log the prompts the representation sends to a model as `prompts/<name>.txt` artifacts of `run`."""
    for name, text in rep.prompts().items():
        run.text(text, f"prompts/{name}.txt")


def read_units(
    ctx: PipelineContext, state: PipelineState, rep: NodeRepresentation, plan: ConstructionPlan | None
) -> Units:
    """Every node's evidence (records only with a plan), its card in the representation `rep`, and every
    claim's sentence, read from the graph."""
    s = ctx.settings
    caps = EvidenceCaps(names=s.index_card_names, claims=s.index_card_claims)
    evidence = read_evidence(ctx.driver, plan, caps, _prose(plan, state.load_profile(ctx)))
    return Units(evidence=evidence, cards=rep.render(evidence), claims=read_claim_sentences(ctx.driver))


def write_units_file(ctx: PipelineContext, units: Units):
    """`index/units.jsonl`: the cards, then the claim sentences, one JSON line each. Fields a representation
    leaves unset (a template card's summary fields) are left out."""
    lines = [c.model_dump_json(exclude_none=True) for c in units.cards]
    lines += [c.model_dump_json() for c in units.claims]
    return ctx.write(UNITS_FILE, "\n".join(lines) + "\n")


def refuse_collisions(plan: ConstructionPlan | None) -> None:
    """Raise `ConfigurationError` when the plan's labels or relationship types use a name of the index
    layer: the domain graph and the layer would mix, and dropping the layer would drop domain nodes."""
    if plan is None:
        return
    reserved = RESERVED_LABELS | {card_label(name) for name in REPRESENTATIONS}
    clashes = sorted({rule.label for rule in plan.nodes} & reserved)
    clashes += sorted({rel.relationship_type for rel in plan.relationships} & RESERVED_TYPES)
    if clashes:
        raise ConfigurationError(f"the plan uses names of the retrieval index layer: {', '.join(clashes)}")


def _prose(plan: ConstructionPlan | None, profile: DataProfile | None) -> dict[str, set[str]]:
    """Record label -> its prose columns (R67's record documents hold them as chunks already)."""
    if plan is None or profile is None:
        return {}
    return {
        rule.label: set(prose_columns({rule.unique_column, *rule.properties}, profile, rule.source_file))
        for rule in plan.nodes
    }


def unit_metrics(units: Units) -> dict[str, float | int | None]:
    """The cards per kind, the claim sentences, the claims the claims cap leaves out of the evidence, the
    cards the length cap truncated, and the characters of cards and claim sentences; for cards a model wrote
    (B), the nodes whose first summary the code check refused and those left with their template card (None,
    so not logged, for the template)."""
    card_chars = [len(c.text) for c in units.cards]
    kinds = ("record", "individual", "concept")
    written = [c for c in units.cards if c.fallback is not None]  # the cards a model was asked for
    return {
        "summaries_rejected": sum(bool(c.rejected) for c in written) if written else None,
        "summaries_fallback": sum(bool(c.fallback) for c in written) if written else None,
        "cards": len(units.cards),
        **{f"cards_{kind}": sum(e.kind == kind for e in units.evidence) for kind in kinds},
        "claims": len(units.claims),
        "claims_skipped": sum(e.claims_total - len(e.claims) for e in units.evidence),
        "cards_truncated": sum(c.truncated for c in units.cards),
        "card_chars_mean": round(mean(card_chars), 1) if card_chars else None,
        "card_chars_max": max(card_chars, default=None),
        "claim_chars_mean": round(mean(len(c.text) for c in units.claims), 1) if units.claims else None,
    }
