"""The agent's text tools: search every passage, and every claim, by meaning and by words (R133); read every
passage of one document, and find every passage holding some words (R141).

Role in the pipeline: the agent (R135) uses them when the question is about text no node leads to, or to
check what the graph walk found against the passages themselves.
Design: Strategy, one class per tool (tool.py), built on the hybrid retrievers R128 measured
(`chunk_dense` and `chunk_lexical`, `claim_dense` and `claim_lexical`), each list read `limit` deep and
the two fused by reciprocal rank fusion (`hybrid/fusion.py`). A claim comes with its opposite-truth
siblings (the same triple stated and denied), as the claim retrievers read them, so a denial is never shown
without the statement it denies; the truth fields are the stored ones, never read from a sentence.
The two exhaustive tools (R141) exist for counting and listing: a ranked search shows the best few passages,
so a count over its results misses the rest (R136: five such counts wrong). They read through `TextReader`
(text_reader.py), cut to a cap with the total said in a note; `find_passages` shows each passage as the
sentences holding the words, so a list of thirty stays a few thousand characters. A word held by a
passage's context (its document's first heading or file name) counts for it, and an uncut list of two or
more passages from one document says how many of its passages they are and that `read_document` reads them
all (R149: a passage rarely repeats what its document is about).
Not here: the graph-walk tools (graph_tools.py), the Cypher (node_reader.py, text_reader.py,
hybrid/unit_store.py).
"""

import re

from pydantic import BaseModel, Field, field_validator

from ..core.identity import document_of
from ..hybrid.fusion import rrf
from ..hybrid.retrievers import ChunkDense, ChunkLexical, ClaimRetriever
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder
from ..query.graph_store import GraphStore
from .node_reader import NodeReader
from .results import ChunksResult, ChunkText, ClaimsResult
from .text_reader import TextReader

# a sentence ends at . ! or ? followed by space, or at a line break (a list item, a heading)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class SearchArguments(BaseModel):
    query: str = Field(description="What the text should say, in words.")


class SearchText:
    """`search_text`: the chunks nearest the query and scored highest by its words, fused, at most `limit`."""

    name = "search_text"
    description = "Search every text passage by meaning and by words, best first."
    arguments = SearchArguments

    def __init__(self, store: GraphStore, units: UnitStore, embedder: Embedder, rrf_k: int, limit: int):
        self._lists = (ChunkDense(store), ChunkLexical(units))
        self._store = store
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._limit = limit

    def run(self, arguments: SearchArguments) -> ChunksResult:
        vector = self._embedder.embed([arguments.query])[0]
        lists = {r.name: r.retrieve(arguments.query, vector, self._limit).chunks for r in self._lists}
        fused = rrf(lists, self._rrf_k)
        chunks = [
            ChunkText(chunk_id=c.chunk_id, context=c.context, text=c.text)
            for c in self._store.chunks(fused[: self._limit])
        ]
        return ChunksResult(chunks=chunks, total=len(fused), note="" if chunks else "No passage found.")


class SearchClaims:
    """`search_claims`: the claims nearest the query and scored highest by its words, fused, each followed by
    its opposite-truth siblings, at most `limit` in all."""

    name = "search_claims"
    description = (
        "Search the claims read from the text by meaning and by words, best first. Each claim says whether "
        "the text states or denies it, whether it only may hold, and under what condition; a claim the text "
        "also denies, or states, comes with its opposite."
    )
    arguments = SearchArguments

    def __init__(self, units: UnitStore, reader: NodeReader, embedder: Embedder, rrf_k: int, limit: int):
        self._lists = (ClaimRetriever(units, "dense"), ClaimRetriever(units, "lexical"))
        self._reader = reader
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._limit = limit

    def run(self, arguments: SearchArguments) -> ClaimsResult:
        vector = self._embedder.embed([arguments.query])[0]
        hits = {r.name: r.retrieve(arguments.query, vector, self._limit).claims for r in self._lists}
        siblings = {h.id: h.siblings for found in hits.values() for h in found}
        fused = rrf({name: [h.id for h in found] for name, found in hits.items()}, self._rrf_k)
        ordered = list(dict.fromkeys(i for claim in fused for i in (claim, *siblings[claim])))
        claims = self._reader.claims(ordered[: self._limit])
        return ClaimsResult(claims=claims, note="" if claims else "No claim found.")


class DocumentArguments(BaseModel):
    document: str = Field(
        description="The document: what a passage id has before '#', or a passage id of it."
    )


class ReadDocument:
    """`read_document`: every passage of one document in order, at most `limit`."""

    name = "read_document"
    description = (
        "Read every passage of one document, in order: use it to count or list what one document says, "
        "which a search, showing only the best few passages, cannot."
    )
    arguments = DocumentArguments

    def __init__(self, reader: TextReader, limit: int):
        self._reader = reader
        self._limit = limit

    def run(self, arguments: DocumentArguments) -> ChunksResult:
        # a passage id is the document's id, '#', and the passage's position: either names the document
        doc_id = arguments.document.split("#", 1)[0].strip()
        chunks, total = self._reader.document(doc_id, self._limit)
        if not chunks:
            note = f"No document {doc_id!r}; give what a passage id has before '#'."
        else:
            note = f"{len(chunks)} of {total} passages shown." if total > len(chunks) else ""
        return ChunksResult(chunks=chunks, total=total, note=note)


class PassageArguments(BaseModel):
    words: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Words or short phrases every passage must contain, in its text or its context, ignoring "
        "case: the fewest that pick out what is counted.",
    )

    @field_validator("words")
    @classmethod
    def _no_blank(cls, words: list[str]) -> list[str]:
        if any(not w.strip() for w in words):
            raise ValueError("a word must not be blank")
        return words


class FindPassages:
    """`find_passages`: every passage holding all the words, by document and position, at most `limit`, each
    shown as its sentences that hold a word, cut at `chars`. A word held by the passage's context (its
    document's first heading or file name) counts (R149)."""

    name = "find_passages"
    # The context sentence names the field the model sees (`context`) and says why a passage that never
    # repeats the name is found: R145's F12 searched a document's name with a topic word before it had seen
    # any document id, and the passages not naming it were missed.
    description = (
        "Find every passage that contains all the given words, in every document, not only the best few: use "
        "it to count or list everything the text says about something. A word in a passage's context (its "
        "document's title) counts for every passage of that document, even one that does not repeat it. "
        "Each passage shows its sentences that hold the words."
    )
    arguments = PassageArguments

    def __init__(self, reader: TextReader, limit: int, chars: int):
        self._reader = reader
        self._limit = limit
        self._chars = chars

    def run(self, arguments: PassageArguments) -> ChunksResult:
        words = [w.strip().lower() for w in arguments.words]
        chunks, total = self._reader.matching(words, self._limit)
        shown = [c.model_copy(update={"text": excerpt(c.text, words, self._chars)}) for c in chunks]
        return ChunksResult(chunks=shown, total=total, note=self._note(shown, total))

    def _note(self, shown: list[ChunkText], total: int) -> str:
        """That nothing was found; how many of the total are shown when cut; or, when every match is one
        document's and there are two or more, how many of its passages they are and that `read_document`
        reads them all (R149). Code says the last because the excerpts hide every line holding no word:
        R149's replay of FN04's search (a document's name and a topic word) found 3 of that document's 7
        passages; an answer over them misses the other 4. Only an uncut list is known to be one document's
        (the passages not shown may be another's), a single match is a lookup, not a count to finish, and a
        list holding the whole document leaves nothing to read."""
        if not shown:
            return "No passage holds all these words."
        if total > len(shown):
            return f"{len(shown)} of {total} passages shown; add a word to narrow them."
        documents = {document_of(c.chunk_id) for c in shown}
        if len(shown) < 2 or len(documents) > 1:
            return ""
        doc_id = documents.pop()
        _, passages = self._reader.document(doc_id, 1)  # only the document's total is needed
        if passages <= len(shown):  # the whole document is shown: nothing more to read
            return ""
        return (
            f"These are {len(shown)} of the {passages} passages of {doc_id}; "
            f"{ReadDocument.name} reads all {passages}."
        )


def excerpt(text: str, words: list[str], chars: int) -> str:
    """The sentences of `text` holding one of `words` (lowercase), in order, cut at `chars`; the text itself
    when none does (a phrase across a sentence end, or words held only by the passage's context)."""
    sentences = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
    kept = [sentence for sentence in sentences if any(w in sentence.lower() for w in words)]
    return (" ... ".join(kept) if kept else text)[:chars]
