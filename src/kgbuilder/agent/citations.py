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
copies the ref, as in R135's dev run).
Not here: sending an answer back (loop.py), turning chunks into the answer's shown list (system.py).
"""

from pydantic import BaseModel

from ..validation.qa import Citation
from .prompt import AgentCitation
from .results import (
    ChunksResult,
    ChunkText,
    ClaimsResult,
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


class Ledger:
    """The ids the tools returned in one question's loop, the claims' chunks, and the chunks read."""

    def __init__(self) -> None:
        self._ids: dict[str, None] = {}  # every returned id, in the order first returned
        self._claim_chunk: dict[str, str] = {}
        self._read: dict[str, ChunkText] = {}  # chunks whose text a tool returned, in the order first read
        self._names: dict[str, str] = {}  # node ref -> the display name a tool gave it

    def add(self, result: ToolResult) -> None:
        """Keep the ids `result` offers, the chunk of every claim in it, and the text of every chunk in it."""
        self._ids.update(dict.fromkeys(result.cites()))
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

    def read(self) -> list[ChunkText]:
        """The chunks whose text the agent read, in the order it first read them."""
        return list(self._read.values())


def _names(result: ToolResult) -> dict[str, str]:
    """Ref -> display name of every node a result names: found, read, related or listed by a query."""
    if isinstance(result, NodesResult):
        return {n.ref: n.title for n in result.nodes}
    if isinstance(result, NodeResult) and result.node is not None:
        return {result.node.ref: result.node.title}
    if isinstance(result, RelationsResult):
        return {r.ref: r.name for r in result.relations}
    if isinstance(result, QueryResult):
        return dict(zip(result.refs, result.names, strict=False))
    return {}
