"""The seam between a node's evidence and the text an index embeds for it (R118): node representations.

Role in the pipeline: `kg units --cards <name>` and `kg index --cards <name>` choose a representation here by
name and render every node's evidence with it; the hybrid systems compute its version to check the layer.
Design: Strategy, chosen by name from `REPRESENTATIONS`. A representation has a `name` (the label and index
names of its cards derive from it, R119), a `version` (a new version means new card texts), `render`, and
what decides its texts besides the evidence (`params`, `prompts`, logged by the stages). Each is built by a
factory from `RepresentationOptions` (the settings' values, passed in by the pipeline) and the model it
writes with, if any: the composition root injects the model, so a representation never builds a client.
Evidence gathering, the index writer, the embedding, the retrievers, fusion, the reader and the benchmark are
shared, so adding a representation is one class and one registry entry here, not a second query engine.
Not here: the evidence (evidence.py, unit_sources.py), the cards themselves (cards.py).
"""

from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ..core.errors import ConfigurationError
from ..llm.base import LLMClient
from .cards import TemplateCards
from .evidence import NodeEvidence, RenderedCard
from .summaries import SummaryCards, SummaryOptions


class NodeRepresentation(Protocol):
    """Turns nodes' evidence into the text an index embeds for each."""

    name: str
    version: str  # changes whenever the text it renders from the same evidence may change

    def render(self, evidence: list[NodeEvidence]) -> list[RenderedCard]:
        """One card per node, in the order given, each with the hash of its evidence."""
        ...

    def params(self) -> dict[str, object]:
        """What decides its texts besides the evidence and the evidence caps, as MLflow params."""
        ...

    def prompts(self) -> dict[str, str]:
        """The prompts it sends to a model, by short name, logged as `prompts/<name>.txt`; none without a
        model."""
        ...


class RepresentationOptions(BaseModel):
    """The settings' values a representation is built from (the pipeline reads them from the settings)."""

    model_config = ConfigDict(frozen=True)

    card_max_chars: int  # a template card's length cap in characters (also B's fallback cards)
    summary: SummaryOptions  # B's model, its settings and the summaries' length cap


# name -> the factory of the representation, given the options and the model it writes with (None: no model)
RepresentationFactory = Callable[[RepresentationOptions, LLMClient | None], NodeRepresentation]

REPRESENTATIONS: Mapping[str, RepresentationFactory] = {
    TemplateCards.name: lambda options, llm: TemplateCards(options.card_max_chars),
    # B (R123): a node whose summary fails the code check twice gets its template card
    SummaryCards.name: lambda options, llm: SummaryCards(
        options.summary, llm, TemplateCards(options.card_max_chars)
    ),
}


def check_representation(name: str) -> str:
    """`name` when a representation has it. Raises `ConfigurationError` for an unknown name, before anything
    is read."""
    if name not in REPRESENTATIONS:
        raise ConfigurationError(
            f"unknown node representation '{name}'; choose from {', '.join(REPRESENTATIONS)}"
        )
    return name


def representation(
    name: str, options: RepresentationOptions, llm: LLMClient | None = None
) -> NodeRepresentation:
    """The representation `name` built from `options`, writing with `llm` when it writes with a model.
    Raises `ConfigurationError` for an unknown name."""
    return REPRESENTATIONS[check_representation(name)](options, llm)
