"""The names of the retrieval index layer (R119): its labels, edges and indexes, written down once.

Role in the pipeline: `kg index` (hybrid/unit_graph.py) writes the layer; R120's retrievers search it; the
graph digest (digest.py) and the planner's schema (query/graph_schema.py) leave it out, so adding or dropping
the layer never changes a Part 1 measurement or a planner prompt.
Design: the layer is additive and deletable. Every unit is a `:RetrievalUnit` (one constraint, one label to
drop it all by); a node card also carries `:NodeCard` and its representation's own label (`TemplateCard`,
`SummaryCard` since R123), because a Neo4j 5 vector index covers one label and cannot filter, so two
representations need two labels to be searched apart on one graph; a claim sentence carries `:ClaimSentence`.
Units point at what they stand for (`CARD_OF` a record, individual or concept; `SENTENCE_OF` an
observation); nothing in Part 1 points at a unit. Dropping the layer:
    MATCH (u:RetrievalUnit) DETACH DELETE u
and the indexes `index_names()` lists.
Not here: writing the units (hybrid/unit_graph.py), their text (hybrid/cards.py, hybrid/claims.py).
"""

import re

from pydantic import BaseModel

from ..core.errors import ConfigurationError

RETRIEVAL_UNIT = "RetrievalUnit"
NODE_CARD = "NodeCard"
CLAIM_SENTENCE = "ClaimSentence"
CARD_OF = "CARD_OF"
SENTENCE_OF = "SENTENCE_OF"

# the full-text analyzer of every index of the layer: Lucene's English analyzer stems ("wobbling" finds
# "wobbles") and drops stop words, "not" among them, which is why truth never lives in a unit's text alone
ANALYZER = "english"

CLAIM_VECTOR_INDEX = "claim_sentence_embeddings"
CLAIM_FULLTEXT_INDEX = "claim_sentence_text"
# the chunks' own full-text index (R120's lexical chunk retriever); the chunks' vector index is ingest's
CHUNK_FULLTEXT_INDEX = "chunk_text"

# the layer's own labels and edge types: a plan whose labels or relationship types use one would mix the
# domain graph with the index layer, so `kg index` refuses it
RESERVED_LABELS = frozenset({RETRIEVAL_UNIT, NODE_CARD, CLAIM_SENTENCE})
RESERVED_TYPES = frozenset({CARD_OF, SENTENCE_OF})

# a representation's name becomes part of a label and of index names, so it is plain lowercase letters
_REPRESENTATION_NAME = re.compile(r"[a-z]+")


class CardIndexes(BaseModel):
    """The two indexes over one representation's cards."""

    vector: str
    fulltext: str


def card_label(representation: str) -> str:
    """The label of one representation's cards: "template" -> "TemplateCard". Raises `ConfigurationError`
    for a name that is not plain lowercase letters."""
    if not _REPRESENTATION_NAME.fullmatch(representation):
        raise ConfigurationError(f"a representation's name must be lowercase letters, got '{representation}'")
    return f"{representation.capitalize()}Card"


def card_indexes(representation: str) -> CardIndexes:
    """The vector and full-text index names of one representation's cards."""
    card_label(representation)  # the same check
    return CardIndexes(vector=f"card_{representation}_embeddings", fulltext=f"card_{representation}_text")


def index_names(representations: list[str]) -> list[str]:
    """Every index the layer creates for these representations, the chunk full-text index included."""
    cards = [name for rep in representations for name in card_indexes(rep).model_dump().values()]
    return [*cards, CLAIM_VECTOR_INDEX, CLAIM_FULLTEXT_INDEX, CHUNK_FULLTEXT_INDEX]
