"""Read the mentions of the subject graph as the identity stage sees them: names, types, the class the
mention pass stated, documents, the things their documents are about, and the chunk texts they appear in.

Role in the pipeline: first step of `kg resolve` (identity.py); the record matching (records.py) and the
concept resolution (concepts.py) decide on these models, without the database.
Design: Repository; read-only. Ordered by id, document and chunk position, so the same graph always gives
the same models, the same prompts (the LLM cache) and the same decisions.
Not here: any decision about what a mention refers to.
"""

from neo4j import Driver
from pydantic import BaseModel

from ..text.schema import MentionClass
from .attachment import TEXT_ABOUT


class MentionRecord(BaseModel):
    """A `:Mention`: one name of one type in one document (R75)."""

    id: str
    name: str
    type: str
    doc_id: str
    chunks: int  # chunks of its document that mention it: a group's most mentioned member names it
    # the tones ("positive", "negative") of the claims that have it as their object (R66); neutral claims
    # say nothing about which way a kind points, so they are left out
    polarities: list[str] = []
    anchors: list[str] = []  # element ids of the things its document is ABOUT: its scope for records
    # the class the mention pass stated (R104): a stated kind no record fits is a concept (R107); None for a
    # claim's or derivation's mention, whose type alone decides
    stated_class: MentionClass | None = None


class MentionText(BaseModel):
    """One chunk that mentions a mention, with the document name a reader of the chunk sees."""

    mention: str  # mention id
    document: str  # the chunk's context (its document heading), else its document id
    chunk_id: str
    text: str


def read_mentions(driver: Driver) -> list[MentionRecord]:
    """Every mention with its chunk count, the tones of the claims ending in it and its document's things."""
    records, _, _ = driver.execute_query(
        "MATCH (m:Mention) OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(m) WITH m, count(c) AS chunks "
        # sorted, so the same graph gives the same record
        "RETURN m.id AS id, m.name AS name, m.type AS type, m.doc_id AS doc_id, chunks, "
        "m.stated_class AS stated_class, "
        "apoc.coll.sort(apoc.coll.toSet([(m)<-[:OBJECT]-(o:Observation) "
        "WHERE o.polarity IN ['positive', 'negative'] | o.polarity])) AS polarities, "
        # the document's ABOUT links come from the link stage, which runs before identity (R75); those the
        # attach stage writes from the text (R76) rest on identity itself, so they are no scope
        "apoc.coll.sort(apoc.coll.toSet([(m)<-[:MENTIONS]-(:Chunk)-[:PART_OF]->(:Document)-[l:ABOUT]->(a) "
        "WHERE coalesce(l.how, '') <> $text | elementId(a)])) AS anchors "
        "ORDER BY id",
        text=TEXT_ABOUT,
    )
    return [MentionRecord(**r.data()) for r in records]


def read_mention_texts(driver: Driver, ids: list[str] | None = None) -> list[MentionText]:
    """The chunks mentioning each mention (all mentions when `ids` is None), ordered by mention, document
    and position in the document."""
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[:MENTIONS]->(m:Mention) WHERE $ids IS NULL OR m.id IN $ids "
        # chunks written before R26 have no context, or an empty one: name them by their document id
        "RETURN m.id AS mention, CASE WHEN coalesce(c.context, '') = '' THEN coalesce(c.doc_id, m.doc_id) "
        "ELSE c.context END AS document, c.chunk_id AS chunk_id, coalesce(c.text, '') AS text "
        "ORDER BY mention, c.doc_id, c.index",
        ids=ids,
    )
    return [MentionText.model_validate(dict(r)) for r in records]
