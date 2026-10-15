"""What the agent may cite (R135): every id the tools returned, the chunks behind claims, and the passages
the agent read.

Role in the pipeline: the loop (loop.py) adds each tool result to a `Ledger`; when the model answers, the
ledger checks its citations, and the agent system (system.py) asks it which chunks the agent read and which
it cited, for the answer's `shown` and `retrieved`.
Design: the LLM proposes, code decides. A citation is valid only when a tool returned its id in this
question's loop; a cited claim brings its chunk along (a fact read from text cites its passage, the plan's
rule), computed here from the claim the tool returned, never asked of the model. A chunk citation keeps its
quote, which `kg qa` checks against the shown text as it does for every system. An entity the answer names
by a node's ref is given that node's name as a tool gave it (code knows the name; the model sometimes
copies the ref, as in R135's dev run). The ledger also says whether an answer is supported by what it cites
(R142): every entity it names, and its number, must appear in the results that offered the cited ids. A
chunk's quote is checked by `kg qa`; this check covers the node and query ids, which carry no quote. A
number the model counted itself (passages read) appears in no result: that check is read with care.
A thing `rank_by_documents` counted (R150) is named like any other node, so its name and its document count
are found in the result its ref cites. R210 adds a stricter check, recorded and never enforced: each kept
citation against what its own id stands for (a passage's whole text, a claim's sentence, a node's name,
other names and values, a query's rows and total), never against another row, a note or a column name, and
a chunk citation's quote against the whole passage.
Not here: sending an answer back (loop.py), turning chunks into the answer's shown list (system.py).
"""

import re

from pydantic import BaseModel

from ..core.text import norm
from ..query.answers import CitationCheck, CitationKind, QueryRecord
from ..validation.qa import Citation
from .prompt import AgentCitation
from .results import (
    ChunksResult,
    ChunkText,
    ClaimsResult,
    MentionCountsResult,
    NodeResult,
    NodesResult,
    RelationsResult,
    ToolResult,
)
from .structured_tools import QueryResult


class CheckedCitations(BaseModel):
    """An answer's citations after the check: the ids kept (cited claims' chunks added after them), the chunk
    citations with their quotes, and the ids no tool returned."""

    kept: list[str]
    chunks: list[Citation]
    unknown: list[str]


class Support(BaseModel):
    """Whether an answer's entities and number appear in what its cited ids' results hold (None: the answer
    names no entity, or gives no number), and the entities that do not."""

    entities_grounded: bool | None
    number_stated: bool | None
    ungrounded: list[str]


class Ledger:
    """The ids the tools returned in one question's loop, the claims' chunks, and the chunks read."""

    def __init__(self) -> None:
        self._ids: dict[str, None] = {}  # every returned id, in the order first returned
        self._claim_chunk: dict[str, str] = {}
        self._read: dict[str, ChunkText] = {}  # chunks whose text a tool returned, in the order first read
        self._names: dict[str, str] = {}  # node ref -> the display name a tool gave it
        self._evidence: dict[str, list[str]] = {}  # id -> the results that offered it, as JSON (R142)
        self._queries: dict[str, QueryRecord] = {}  # query id -> its run's provenance, first run kept (R208)
        self._content: dict[str, list[str]] = {}  # id -> what results state of it, notes left out (R210)

    def add(self, result: ToolResult) -> None:
        """Keep the ids `result` offers, the chunk of every claim in it, and the text of every chunk in it."""
        self._ids.update(dict.fromkeys(result.cites()))
        shown = result.model_dump_json()
        for i in result.cites():
            self._evidence.setdefault(i, []).append(shown)
        if isinstance(result, ChunksResult):
            for chunk in result.chunks:
                # the longer text wins, its place kept: find_passages shows a passage's sentences only, and
                # a quote copied from a later full read must be checked against the text it came from (R141)
                known = self._read.get(chunk.chunk_id)
                if known is None or len(chunk.text) > len(known.text):
                    self._read[chunk.chunk_id] = chunk
                self._claim_chunk.update({c.id: c.chunk_id for c in chunk.claims})
        if isinstance(result, ClaimsResult):
            self._claim_chunk.update({c.id: c.chunk_id for c in result.claims})
        if isinstance(result, QueryResult) and (record := result.record()) is not None:
            # an equal query has an equal id, so a repeated run adds nothing new
            self._queries.setdefault(record.query_id, record)
        for i, texts in _contents(result).items():
            self._content.setdefault(i, []).extend(texts)
        self._names.update(_names(result))

    def check(self, citations: list[AgentCitation]) -> CheckedCitations:
        """Which cited ids a tool returned, with each cited claim's chunk added; which ones none did."""
        known = [c for c in citations if c.id in self._ids]
        unknown = list(dict.fromkeys(c.id for c in citations if c.id not in self._ids))
        cited = list(dict.fromkeys(c.id for c in known))
        chunks_of_claims = [self._claim_chunk[i] for i in cited if i in self._claim_chunk]
        kept = list(dict.fromkeys(cited + chunks_of_claims))
        quotes = [Citation(chunk_id=c.id, quote=c.quote) for c in known if c.quote and self.is_chunk(c.id)]
        return CheckedCitations(kept=kept, chunks=quotes, unknown=unknown)

    def is_chunk(self, ref: str) -> bool:
        """Whether `ref` is a chunk id a tool returned (read, or named as a claim's chunk)."""
        return ref in self._read or ref in self._claim_chunk.values()

    def name_of(self, entity: str) -> str:
        """The display name of the node `entity` is the ref of, as a tool gave it; `entity` itself when it
        is no ref a tool returned (then it is a name already)."""
        return self._names.get(entity, entity)

    def support(self, entities: list[str] | None, number: float | None, cited: list[str]) -> Support:
        """Whether each of `entities` (names, not refs) and `number` appear in the results that offered the
        `cited` ids, compared by `norm`; a number as a whole token ("10" not inside "2010")."""
        held = norm(" ".join(text for i in cited for text in self._evidence.get(i, [])))
        missing = [e for e in entities or [] if norm(e) not in held]
        stated = None if number is None else _holds_number(held, number)
        grounded = None if not entities else not missing
        return Support(entities_grounded=grounded, number_stated=stated, ungrounded=missing)

    def check_each(
        self, citations: list[AgentCitation], kept: list[str], entities: list[str] | None,
        number: float | None, whole: dict[str, str],
    ) -> list[CitationCheck]:  # fmt: skip
        """Each `kept` id checked against its own content (R210): which of `entities` and whether `number`
        it holds, and for a chunk its quote, against `whole` (chunk id -> the whole passage, R209) or the
        text read. Recorded only: the answer is never sent back for it."""
        quotes = {c.id: c.quote for c in reversed(citations) if c.quote}  # the first quote of an id wins
        checks = []
        for i in kept:
            kind = self._kind(i)
            # a chunk's whole passage when known, else the text read of it; any other id its own content
            read = self._read[i].text if i in self._read else " ".join(self._content.get(i, []))
            held = norm(whole.get(i) or read)
            quote = quotes.get(i, "") if kind == "chunk" else ""
            checks.append(CitationCheck(
                id=i, kind=kind, quote_found=(norm(quote) in held) if quote else None,
                entities_found=[e for e in entities or [] if norm(e) in held],
                number_found=None if number is None else _holds_number(held, number),
            ))  # fmt: skip
        return checks

    def _kind(self, ref: str) -> CitationKind:
        """What a returned id names: a claim, a chunk, a query run, or else a node."""
        if ref in self._claim_chunk:
            return "claim"
        if self.is_chunk(ref):
            return "chunk"
        return "query" if ref in self._queries else "node"

    def read(self) -> list[ChunkText]:
        """The chunks whose text the agent read, in the order it first read them."""
        return list(self._read.values())

    def queries(self) -> list[QueryRecord]:
        """Every structured query that ran, with its provenance, in the order first run (R208)."""
        return list(self._queries.values())


def _holds_number(held: str, number: float) -> bool:
    """Whether `held` writes `number` as a whole token ("10" not inside "2010")."""
    token = str(int(number)) if float(number).is_integer() else str(number)
    return re.search(rf"(?<![0-9.]){re.escape(token)}(?![0-9])", held) is not None


def _contents(result: ToolResult) -> dict[str, list[str]]:
    """Id -> what `result` states of that id itself (R210): a passage's text, a claim's sentence, a node's
    name, other names and values, a counted thing's spellings and document count, a query's cells and total
    and a listed record's own row. Never its note or a column name: those say how a result was cut or
    shaped, not what the cited item holds."""
    if isinstance(result, ChunksResult):  # filter_passages' too: each passage shown as its verified quote
        texts = {c.chunk_id: [c.text] for c in result.chunks}
        return texts | {f.id: [f.sentence] for c in result.chunks for f in c.claims}
    if isinstance(result, ClaimsResult):
        return {c.id: [c.sentence] for c in result.claims}
    if isinstance(result, NodesResult):
        return {n.ref: [n.title] for n in result.nodes}
    if isinstance(result, NodeResult) and result.node is not None:
        node = result.node
        return {node.ref: [node.title, *node.aliases, *(p.value for p in node.properties)]}
    if isinstance(result, RelationsResult):
        return {r.ref: [r.name] for r in result.relations}
    if isinstance(result, MentionCountsResult):
        return {t.ref: [t.name, *t.spellings, str(t.documents)] for t in result.things}
    if isinstance(result, QueryResult) and (record := result.record()) is not None:
        rows = {ref: row for ref, row in zip(record.refs, record.rows, strict=False)}
        return {record.query_id: [*(v for row in record.rows for v in row), str(record.total)]} | rows
    return {}


def _names(result: ToolResult) -> dict[str, str]:
    """Ref -> display name of every node a result names: found, read, related, listed or counted."""
    if isinstance(result, NodesResult):
        return {n.ref: n.title for n in result.nodes}
    if isinstance(result, NodeResult) and result.node is not None:
        return {result.node.ref: result.node.title}
    if isinstance(result, RelationsResult):
        return {r.ref: r.name for r in result.relations}
    if isinstance(result, QueryResult):
        return dict(zip(result.refs, result.names, strict=False))
    if isinstance(result, MentionCountsResult):
        return {t.ref: t.name for t in result.things}
    return {}
