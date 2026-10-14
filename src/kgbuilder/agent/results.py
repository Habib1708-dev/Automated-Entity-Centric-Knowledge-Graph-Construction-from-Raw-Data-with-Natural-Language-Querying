"""What the agent's tools return (R133): nodes, their properties and relations, chunks and claims, each with
the id the agent may cite; R150: the things the text names with how many documents name them; R184: the
passages the semantic filter kept, with their documents.

Role in the pipeline: the tools (graph_tools.py, text_tools.py) build these from the node reader
(node_reader.py) and the stores; the agent loop (R135) shows them to the model and collects every id a
result offers, so an answer can cite only what a tool returned.
Design: plain data. Every result is a `ToolResult` whose `cites` lists the ids it offers (node refs, chunk
ids, claim ids) and whose `note` is what code tells the agent about it (nothing found, a cap reached), so
the agent never has to infer a cut from a count.
Not here: reading the graph (node_reader.py), choosing what to return (the tools).
"""

from typing import Literal

from pydantic import BaseModel

NodeKind = Literal["record", "individual", "concept"]


class NodeHit(BaseModel):
    """A node as a list shows it: its stable ref (a record's `Label:key`, an individual's or a concept's
    canonical id), what it is, and its display name."""

    ref: str
    kind: NodeKind
    label: str  # a record's label, an individual's or a concept's entity type
    title: str  # its display name (a record without a name: its key)


class Property(BaseModel):
    """One property of a record and where it comes from: the plan's source table and the record's key."""

    name: str
    value: str
    source: str  # "<source table> row <key column>=<key>"


class NodeFacts(NodeHit):
    """What the graph holds about one node itself: its other names and its properties."""

    aliases: list[str]  # the other names its mentions write, sorted
    properties: list[Property]  # a record's columns, sorted by name; none for an individual or a concept


class Relation(BaseModel):
    """One group of a node's relations to one other node: a record relationship, or the claims of one
    predicate and direction joining the two."""

    via: Literal["record", "claim"]
    type: str  # the relationship type, or the claims' predicate
    outgoing: bool  # the relation points away from the node
    ref: str  # the other end's ref
    name: str
    label: str
    count: int  # 1 for a relationship; the claims joining the two by this predicate and direction
    claims: list[str]  # up to three of those claims' ids, sorted; none for a relationship


class ClaimFact(BaseModel):
    """A claim with its sentence, its chunk and its truth fields as stored (never read from the sentence)."""

    id: str
    sentence: str
    chunk_id: str
    truth: str  # of the statement: "negated" when the text denies it
    triple_truth: str  # whether the stored triple itself is denied
    modality: str  # actual, possible or conditional
    hedge: str
    condition: str


class ChunkText(BaseModel):
    """A text passage: its id, its document's name, its text, and the claims read from it that a tool
    found relevant (for `node_evidence`: those the node holds or is an end of)."""

    chunk_id: str
    context: str
    text: str
    claims: list[ClaimFact] = []


class ToolResult(BaseModel):
    """The base of every tool's result: what code says about it, and the ids it offers for citation."""

    note: str = ""  # nothing found, a cap reached: said by code, not left to the agent to infer

    def cites(self) -> list[str]:
        """The ids this result offers for citation, each once, in the order shown."""
        return []


class NodesResult(ToolResult):
    """`find_nodes`: the nodes found, best first."""

    nodes: list[NodeHit]

    def cites(self) -> list[str]:
        return [n.ref for n in self.nodes]


class NodeResult(ToolResult):
    """`get_node`: one node, or None with a note when the ref names no node."""

    node: NodeFacts | None

    def cites(self) -> list[str]:
        return [self.node.ref] if self.node else []


class RelationsResult(ToolResult):
    """`neighbours`: a node's relation groups, capped, and how many there are in all."""

    ref: str
    relations: list[Relation]
    total: int

    def cites(self) -> list[str]:
        return _unique([self.ref] + [i for r in self.relations for i in (r.ref, *r.claims)])


class ChunksResult(ToolResult):
    """`node_evidence` and `search_text`: chunks, best first, with their relevant claims."""

    chunks: list[ChunkText]
    total: int  # the chunks there were to choose from, before the cap

    def cites(self) -> list[str]:
        return _unique(i for c in self.chunks for i in (c.chunk_id, *(f.id for f in c.claims)))


class FilteredResult(ChunksResult):
    """`filter_passages` (R184): the passages stating a statement, each shown as its verified quote, so a
    citation of it is checked against that quote; `total` counts them all, and their documents are counted
    by code, since a question often counts documents, not passages."""

    documents: list[str]  # the documents of the kept passages, each once, in the order found
    document_count: int  # how many documents: a number the answer's support check can find (R142)
    considered: int  # the candidate passages read
    unverified: int  # yeses left out because their quote is not in the passage


class ClaimsResult(ToolResult):
    """`search_claims`: claims, best first."""

    claims: list[ClaimFact]

    def cites(self) -> list[str]:
        return _unique(i for c in self.claims for i in (c.id, c.chunk_id))


class MentionCount(BaseModel):
    """A thing the text names, with how many documents name it and the names its mentions write. No count of
    mentions: one mention is one name of one type in one document, so it reads like an occurrence count
    but is not one (a document writing a name two ways holds two)."""

    ref: str
    kind: NodeKind
    label: str  # a record's label, an individual's or a concept's entity type
    name: str  # its display name
    documents: int  # the documents with a mention of it
    spellings: list[str]  # the names its mentions write, sorted, cut


class MentionCountsResult(ToolResult):
    """`rank_by_documents` (R150): the named things, most documents first, capped, and how many matched."""

    things: list[MentionCount]
    total: int

    def cites(self) -> list[str]:
        return [t.ref for t in self.things]


def _unique(ids) -> list[str]:
    """`ids` in order, each once."""
    return list(dict.fromkeys(ids))
