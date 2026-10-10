"""What the agent's exhaustive text tools read (R141): every passage of one document, and every passage that
contains given words.

Role in the pipeline: `read_document` and `find_passages` (text_tools.py) depend on the `TextReader` protocol
and are tested with a fake; `Neo4jTextReader` runs the Cypher over the lexical graph (text/lexical.py): a
`Chunk` with its `context`, `text` and `index`, `PART_OF` its `Document`; tested once against Neo4j.
Design: Repository, read-only. Both reads are exhaustive and ordered (by document, then position), cut to
the limit in the database with the total kept, because a count needs every passage, not the best few the
ranked searches return (R136: five counts over text missed passages a top 5 left out). Words are matched
as substrings ignoring case, every word in the same passage.
Not here: ranking passages (text_tools.py's searches), caps and notes for the agent (the tools).
"""

from typing import Protocol

from neo4j import Driver

from .results import ChunkText


class TextReader(Protocol):
    """Passages read in full: one document's, or every one holding some words."""

    def document(self, doc_id: str, limit: int) -> tuple[list[ChunkText], int]:
        """The first `limit` passages of the document `doc_id`, in order, and how many it has (0: none)."""
        ...

    def matching(self, words: list[str], limit: int) -> tuple[list[ChunkText], int]:
        """The first `limit` passages holding every one of `words` (lowercase), by document and position, and
        how many there are."""
        ...


class Neo4jTextReader:
    """`TextReader` over the lexical graph in Neo4j."""

    def __init__(self, driver: Driver):
        self._driver = driver

    def document(self, doc_id: str, limit: int) -> tuple[list[ChunkText], int]:
        return self._page(
            "MATCH (c:Chunk)-[:PART_OF]->(d:Document {doc_id: $doc_id})", doc_id=doc_id, limit=limit
        )

    def matching(self, words: list[str], limit: int) -> tuple[list[ChunkText], int]:
        # every word a substring of the passage ignoring case; `words` arrive lowercase from the tool
        where = (
            "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE all(w IN $words WHERE toLower(c.text) CONTAINS w)"
        )
        return self._page(where, words=words, limit=limit)

    def _page(self, match: str, **params: object) -> tuple[list[ChunkText], int]:
        """The passages `match` binds as `c` (in the document `d`), by document and position, cut to `$limit`
        with their total. The chunk id breaks ties, for a graph whose chunks carry no index."""
        records, _, _ = self._driver.execute_query(
            f"{match} WITH c, d ORDER BY d.doc_id, c.index, c.chunk_id WITH collect(c) AS found "
            "RETURN size(found) AS total, [x IN found[0..$limit] | "
            "{chunk_id: x.chunk_id, context: coalesce(x.context, ''), text: x.text}] AS chunks",
            **params,
        )
        row = records[0]
        return [ChunkText(**c) for c in row["chunks"]], row["total"]
