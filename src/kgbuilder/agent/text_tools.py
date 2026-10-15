"""The agent's text tools: search every passage, and every claim, by meaning and by words (R133); read every
passage of one document, and find every passage holding some words (R141).

Role in the pipeline: the agent (R135) uses them when the question is about text no node leads to, or to
check what the graph walk found against the passages themselves.
Design: Strategy, one class per tool (tool.py), built on the hybrid retrievers R128 measured
(`chunk_dense` and `chunk_lexical`, `claim_dense` and `claim_lexical`), each list read `limit` deep and
the two fused by reciprocal rank fusion (`hybrid/fusion.py`); since R199 `search_text` reads each passage list
`depth` deep and has the passage reranker (hybrid/passage_rerank.py) order the fused pool, RRF kept as its
fallback (R198's `rerank_10x10`, frozen). A claim comes with its opposite-truth
siblings (the same triple stated and denied), as the claim retrievers read them, so a denial is never shown
without the statement it denies; the truth fields are the stored ones, never read from a sentence.
The two exhaustive tools (R141) exist for counting and listing: a ranked search shows the best few passages,
so a count over its results misses the rest (R136: five such counts wrong). They read through `TextReader`
(text_reader.py), cut to a cap with the total said in a note; `find_passages` shows each passage as the
sentences holding the words, so a list of thirty stays a few thousand characters. A word held by a
passage's context (its document's first heading or file name) counts for it, and an uncut list of two or
more passages from one document says how many of its passages they are and that `read_document` reads them
all (R149: a passage rarely repeats what its document is about). A word is searched as written or as its
other forms (R162: R157's FB38 searched "wobble" and never saw the passages saying "wobbling"): code cuts
one inflectional ending (`stem`) and matches the stem only as a whole word with one such ending
(`form_pattern`), so a cut word never matches inside an unrelated word ("signs" never finds "design"); a
different wording of the same idea is left to the searches by meaning. A space or a hyphen in a phrase
matches either (R169: R166's LC08 searched "fog bell" and never saw the passage saying "fog-bell"), in
the word as written and in its stem form alike, since the two are one phrase written two ways. A passage's
heading lines stay in its excerpt with the sentences holding the words (R170): a heading says what the
sentences under it are about, and cut away it hid what a passage was rated or titled (R166's FC05 saw
twenty passages holding "stars" and none of their "## Rating: ... (3/5)" lines, so it never searched the
form the ratings take); a heading is a markdown one, as the chunker reads a document's (`text/chunking`).
Not here: the graph-walk tools (graph_tools.py), the Cypher (node_reader.py, text_reader.py,
hybrid/unit_store.py).
"""

import logging
import re

from pydantic import BaseModel, Field, field_validator

from ..core.errors import LLMResponseError
from ..core.identity import document_of
from ..hybrid.fusion import rrf
from ..hybrid.passage_rerank import PassageReranker
from ..hybrid.retrievers import ChunkDense, ChunkLexical, ClaimRetriever
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder
from ..query.graph_store import GraphStore, StoredChunk
from .node_reader import NodeReader
from .results import ChunksResult, ChunkText, ClaimsResult, ranked_note
from .text_reader import TextReader

logger = logging.getLogger(__name__)

# a sentence ends at . ! or ? followed by space, or at a line break (a list item, a heading)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# a markdown heading, its own part after the split at line breaks: '#' marks, a space, then its text (R170)
_HEADING = re.compile(r"#+\s")
# The inflectional endings a searched word loses, one at most, the longer first so "rattles" loses "es", not
# "s": "rattle", "rattles", "rattled" and "rattling" all become "rattl". The same endings, or none, are what a
# stem may be followed by when it is matched (`form_pattern`). "-ly" is none of them: it is no inflection,
# and cut from a word that does not take it ("comply", "supply") it left a stem of many unrelated words.
_ENDINGS = ("ing", "ed", "es", "s", "e")
# The fewest letters a stem keeps: a shorter one is too often another word ("not" from "notes" would join
# "not" and "noting"), so such a word is matched as written ("lids") or loses a shorter ending ("notes" only
# "s").
_MIN_STEM = 4


class SearchArguments(BaseModel):
    query: str = Field(description="What the text should say, in words.")


class SearchText:
    """`search_text`: the chunks nearest the query and scored highest by its words, each list read `depth`
    deep, fused by RRF into one pool, ordered by the passage reranker, at most `limit` shown.

    R199 (frozen after R198): the reranker reads every pooled passage whole and orders the pool by what each
    states, so a passage saying the asked idea in other words is not pushed out by passages sharing the
    query's words (R196, R198: recall at 5 0.831 with RRF, 0.922 reranked from 10 + 10). RRF's order is the
    fallback: without a reranker, and when the reranker's call fails, which is logged and never breaks the
    search. Either way the result is the same `ChunksResult`, whole passages from the store.
    """

    name = "search_text"
    description = "Search every text passage by meaning and by words, best first."
    arguments = SearchArguments

    def __init__(
        self, store: GraphStore, units: UnitStore, embedder: Embedder, rrf_k: int, depth: int, limit: int,
        reranker: PassageReranker | None = None,
    ):  # fmt: skip
        self._lists = (ChunkDense(store), ChunkLexical(units))
        self._store = store
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._depth = depth
        self._limit = limit
        self._reranker = reranker

    def run(self, arguments: SearchArguments) -> ChunksResult:
        vector = self._embedder.embed([arguments.query])[0]
        lists = {r.name: r.retrieve(arguments.query, vector, self._depth).chunks for r in self._lists}
        fused = rrf(lists, self._rrf_k)
        pool = self._store.chunks(fused)
        shown = self._ordered(arguments.query, pool)[: self._limit]
        chunks = [ChunkText(chunk_id=c.chunk_id, context=c.context, text=c.text) for c in shown]
        note = ranked_note(len(chunks), len(fused), "passage") if chunks else "No passage found."
        return ChunksResult(chunks=chunks, total=len(fused), note=note)

    def _ordered(self, query: str, pool: list[StoredChunk]) -> list[StoredChunk]:
        """The pool in the reranker's order, or in RRF's when there is no reranker or its call fails."""
        if self._reranker is None or not pool:
            return pool
        try:
            order = self._reranker.rerank(query, pool).order
        except LLMResponseError as e:
            # the provider kept failing: the search still answers, in RRF's order (MLflow counts the failed
            # attempts as llm_failures)
            logger.warning("search_text reranking failed, RRF order kept: %s", e)
            return pool
        by_id = {c.chunk_id: c for c in pool}
        return [by_id[i] for i in order]


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
        claims = self._reader.claims(_whole_groups(fused, siblings, self._limit))
        note = ranked_note(len(claims), len(ordered), "claim") if claims else "No claim found."
        return ClaimsResult(claims=claims, total=len(ordered), note=note)


def _whole_groups(fused: list[str], siblings: dict[str, list[str]], limit: int) -> list[str]:
    """The claim ids in `fused` order, each followed by its opposite-truth siblings, at most `limit`, cut
    between groups (R207): cut inside one, a statement was shown without the denial the module promises
    beside it. The first group is kept whole even past `limit`, so a search never shows nothing."""
    kept: list[str] = []
    for claim in fused:
        group = [i for i in (claim, *siblings[claim]) if i not in kept]
        if kept and len(kept) + len(group) > limit:
            break
        kept += group
    return kept


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
        # The forms rule as the code applies it (R162), stated once, here where the argument is defined: what
        # joins (the endings, four letters kept) and what does not, so the model searches the forms the rule
        # misses itself instead of taking one search as complete. The example is `form_pattern`'s own output
        # (a test checks it and the limits), in words no evaluated dataset's text holds.
        description="Words or short phrases every passage must contain, in its text or its context, ignoring "
        "case: the fewest that pick out what is counted. A word also finds itself with -s, -es, -ed, -ing "
        "or -e added or removed when four letters stay ('brews' finds 'brew', 'brewed' and 'brewing'); a "
        "shorter word, a doubled letter or a y turned to i is not joined, so search those forms too. A "
        "phrase varies only its last word; a word with a digit or a sign is matched as written; a space "
        "or a hyphen in a phrase matches either ('lid-seal' finds 'lid seal').",
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
    document's first heading or file name) counts (R149); each word is searched as its `form_pattern`
    (R162)."""

    name = "find_passages"
    # The context sentence names the field the model sees (`context`) and says why a passage that never
    # repeats the name is found: R145's F12 searched a document's name with a topic word before it had seen
    # any document id, and the passages not naming it were missed. The words sentence points elsewhere for
    # what no form joins: R157's HB41 searched one wording ("notified") of an idea the text also words
    # otherwise ("made aware"); the searches by meaning find that. Which forms a word finds is said once, in
    # the `words` argument.
    description = (
        "Find every passage that contains all the given words, in every document, not only the best few: use "
        "it to count or list everything the text says about something. A word in a passage's context (its "
        "document's title) counts for every passage of that document, even one that does not repeat it. "
        f"It finds the words, not other words for the same idea: {SearchText.name} searches by meaning. Each "
        "passage shows its headings and its sentences that hold the words."
    )
    arguments = PassageArguments

    def __init__(self, reader: TextReader, limit: int, chars: int):
        self._reader = reader
        self._limit = limit
        self._chars = chars

    def run(self, arguments: PassageArguments) -> ChunksResult:
        # the patterns both pick the passages and the sentences shown of them, so every form found is shown
        patterns = [form_pattern(w.strip().lower()) for w in arguments.words]
        chunks, total = self._reader.matching(patterns, self._limit)
        shown = [c.model_copy(update={"text": excerpt(c.text, patterns, self._chars)}) for c in chunks]
        cut = sum(len(excerpted(c.text, patterns)) > self._chars for c in chunks)
        notes = (self._note(shown, total), self._cut(cut))
        return ChunksResult(chunks=shown, total=total, note=" ".join(n for n in notes if n))

    def _cut(self, cut: int) -> str:
        """The note on the passages cut at `chars` (R207), naming the tool that shows them whole."""
        if not cut:
            return ""
        whole = f"{ReadDocument.name} shows them whole"
        return f"{cut} of these passages are cut at {self._chars} characters, ending in '...'; {whole}."

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


def stem(term: str) -> str:
    """`term` (lowercase, a word or a phrase) with its last word cut to its stem: one ending of `_ENDINGS`
    dropped when at least `_MIN_STEM` letters stay ("boiling" -> "boil", "notes" -> "note"), else the term as
    given ("lids").

    A word holding anything but ASCII letters (a digit or a sign: "@handle", "kv12-0457", "45cm") is an id or
    a code and kept whole; so is a word of other letters, whose word edges `form_pattern` draws against ASCII
    letters only. In a phrase only the last word is cut: an inner word cut ("boil water" for "boiling water")
    would no longer be followed by the next word and the phrase would miss itself."""
    head, space, last = term.rpartition(" ")
    if not (last.isascii() and last.isalpha()):
        return term
    for ending in _ENDINGS:
        # a double "s" is no plural ending: "press" stays whole, so it finds "presses" and "pressed"
        if last.endswith(ending) and len(last) - len(ending) >= _MIN_STEM and not last.endswith("ss"):
            return head + space + last[: -len(ending)]
    return term


def form_pattern(term: str) -> str:
    """The regular expression a searched `term` (lowercase) is matched by: the term as written anywhere, as
    before R162, or, when `stem` cuts it, the stem as a whole word followed by one of `_ENDINGS` or by
    nothing ("wobble": "wobble" anywhere, or "wobbl", "wobbles", "wobbled", "wobbling" as whole words).

    The stem is anchored at both word edges because, matched anywhere, a cut word met unrelated words that
    hold its stem: R162's first replay found "sign" (from "signs") in "design", "vent" in "prevent", "star"
    (from "stars") in "started". An edge is drawn against an ASCII letter, not with the word-boundary escape,
    so Python (`excerpt`, the test fakes) and Neo4j's Java engine (text_reader.py), whose word characters
    differ, split words alike; the pattern uses only what both engines read the same way: escaped literals, a
    group, an alternation, fixed lookarounds."""
    literal = _phrase(term)
    cut = stem(term)
    if cut == term:  # nothing cut: every form a stem would add holds the term, which the literal finds
        return literal
    return rf"{literal}|(?<![a-z]){_phrase(cut)}(?:{'|'.join(_ENDINGS)})?(?![a-z])"


def _phrase(text: str) -> str:
    """`text` escaped for a regular expression, each space or hyphen in it matching either (R169): "lid seal"
    and "lid-seal" are one phrase written two ways, so a search for one finds the other. The class holds
    only the two characters, read alike by Python and by Neo4j's engine."""
    return re.escape(text).replace("\\ ", "[ -]").replace("\\-", "[ -]")


def excerpt(text: str, patterns: list[str], chars: int) -> str:
    """`excerpted` cut at `chars`, a cut one ending in "..." (R207: cut silently, mid-word, it read as the
    passage's end; R186's lighthouse answers kept 42 of their 64 passages at exactly 300 characters)."""
    kept = excerpted(text, patterns)
    return kept if len(kept) <= chars else kept[:chars] + "..."


def excerpted(text: str, patterns: list[str]) -> str:
    """The sentences of `text` matching one of `patterns` (`form_pattern`s) and every heading line of `text`
    (R170), in order, uncut; the text itself when no sentence matches (a phrase across a sentence end, or
    words held only by the passage's context)."""
    sentences = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
    matched = [sentence for sentence in sentences if any(re.search(p, sentence.lower()) for p in patterns)]
    if not matched:
        return text
    kept = [s for s in sentences if s in matched or _HEADING.match(s)]
    return " ... ".join(kept)
