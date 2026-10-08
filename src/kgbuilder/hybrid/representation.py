"""The seam between a node's evidence and the text an index embeds for it (R118): node representations.

Role in the pipeline: `kg units --cards <name>` (and R119's `kg index --cards <name>`) choose a representation
here by name and render every node's evidence with it.
Design: Strategy, chosen by name from `REPRESENTATIONS`. A representation has a `name` (the label and index
names of its cards derive from it, R119), a `version` (a new version means new card texts) and `render`.
Evidence gathering, the index writer, the embedding, the retrievers, fusion, the reader and the benchmark
are shared, so adding LLM node summaries (B, R123) is one class and one registry entry here, not a second
query engine.
Not here: the evidence (evidence.py, unit_sources.py), the cards themselves (cards.py).
"""

from collections.abc import Callable, Mapping
from typing import Protocol

from ..core.errors import ConfigurationError
from .cards import TemplateCards
from .evidence import NodeEvidence, RenderedCard


class NodeRepresentation(Protocol):
    """Turns nodes' evidence into the text an index embeds for each."""

    name: str
    version: str  # changes whenever the text it renders from the same evidence may change

    def render(self, evidence: list[NodeEvidence]) -> list[RenderedCard]:
        """One card per node, in the order given, each with the hash of its evidence."""
        ...


# name -> the representation, built with a card's length cap in characters
REPRESENTATIONS: Mapping[str, Callable[[int], NodeRepresentation]] = {TemplateCards.name: TemplateCards}


def representation(name: str, max_chars: int) -> NodeRepresentation:
    """The representation `name` with the length cap `max_chars`. Raises `ConfigurationError` for an unknown
    name, before anything is read."""
    if name not in REPRESENTATIONS:
        raise ConfigurationError(
            f"unknown node representation '{name}'; choose from {', '.join(REPRESENTATIONS)}"
        )
    return REPRESENTATIONS[name](max_chars)
