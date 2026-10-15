"""What the agent's exhaustive text tools read (R141): every passage of one document, and every passage that
matches given patterns.

Role in the pipeline: `read_document` and `find_passages` (text_tools.py) depend on the `TextReader` protocol
and are tested with a fake; `Neo4jTextReader` runs the Cypher over the lexical graph (text/lexical.py): a
`Chunk` with its `context`, `text` and `index`, `PART_OF` its `Document`; tested once against Neo4j.
Design: Repository, read-only. Both reads are exhaustive and ordered (by document, then position), cut to
the limit in the database with the total kept, because a count needs every passage, not the best few the
ranked searches return (R136: five counts over text missed passages a top 5 left out). Every pattern must
match the same passage, in its lowercased text or in its lowercased context (the document's first heading
or file name): a word naming what the document is about counts for every passage of it (R149). The
patterns are `find_passages`'s (R162: a word as written, or its other forms as whole words); the reader
runs them and decides nothing about forms.
Not here: ranking passages (text_tools.py's searches), caps and notes for the agent (the tools).
"""

from typing import Protocol

from neo4j import Driver

from ..graph.connection import read_query
from .results import ChunkText


class TextReader(Protocol):
    """Passages read in full: one document's, or every one holding some words."""

    def document(self, doc_id: str, limit: int) -> tuple[list[ChunkText], int]:
        """The first `limit` passages of the document `doc_id`, in order, and how many it has (0: none)."""
        ...

    def matching(self, patterns: list[str], limit: int) -> tuple[list[ChunkText], int]:
        """The first `limit` passages each of `patterns` (regular expressions searched anywhere in the
        lowercased text, as `re.search` would) matches in their text or their context (the document's
        first heading or file name), by document and position, and how many there are."""
        ...


class Neo4jTextReader:
    """`TextReader` over the lexical graph in Neo4j."""

    def __init__(self, driver: Driver):
        self._driver = driver

    def document(self, doc_id: str, limit: int) -> tuple[list[ChunkText], int]:
        return self._page(
            "MATCH (c:Chunk)-[:PART_OF]->(d:Document {doc_id: $doc_id})", doc_id=doc_id, limit=limit
        )

    def matching(self, patterns: list[str], limit: int) -> tuple[list[ChunkText], int]:
        # every pattern found in the passage's text or in its context (the document's first heading or file
        # name, text/chunking.py `document_context`), lowercased: a later passage rarely repeats what its
        # document is about, so a search naming it missed those passages (R145, F12), and the extractor
        # reads names the same way (text/extraction.py `verify`). Each pattern matches one of the two, so
        # no phrase spans them; `coalesce` keeps a chunk without a context. Cypher's `=~` matches the whole
        # string, so each pattern is wrapped to be found anywhere, across line breaks (`(?s)`), as
        # `re.search` finds it in the tool's excerpt (R162).
        anywhere = [f"(?s).*(?:{p}).*" for p in patterns]
        where = (
            "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE all(p IN $patterns WHERE "
            "toLower(c.text) =~ p OR toLower(coalesce(c.context, '')) =~ p)"
        )
        return self._page(where, patterns=anywhere, limit=limit)

    def _page(self, match: str, **params: object) -> tuple[list[ChunkText], int]:
        """The passages `match` binds as `c` (in the document `d`), by document and position, cut to `$limit`
        with their total. The chunk id breaks ties, for a graph whose chunks carry no index."""
        records, _, _ = read_query(
            self._driver,
            f"{match} WITH c, d ORDER BY d.doc_id, c.index, c.chunk_id WITH collect(c) AS found "
            "RETURN size(found) AS total, [x IN found[0..$limit] | "
            "{chunk_id: x.chunk_id, context: coalesce(x.context, ''), text: x.text}] AS chunks",
            **params,
        )
        row = records[0]
        return [ChunkText(**c) for c in row["chunks"]], row["total"]
