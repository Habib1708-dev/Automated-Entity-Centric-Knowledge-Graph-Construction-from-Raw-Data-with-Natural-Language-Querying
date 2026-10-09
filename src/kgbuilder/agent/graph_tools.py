"""The agent's seeding and graph-walk tools (R133): find start nodes, read a node, list its relations, read
the text about it.

Role in the pipeline: the agent (R135) starts with `find_nodes`, then walks: `get_node` reads a node's own
facts, `neighbours` lists the nodes it relates to (a repeated call on a neighbour is the next hop), and
`node_evidence` reads the passages the graph links to it, with the claims from each that concern it.
Design: Strategy, one class per tool (tool.py). `find_nodes` is R132's live seeding, the fused card list
of `hybrid.retrievers.fused_cards`, so the agent starts where retrieve-eval measured it; `node_evidence`
reaches chunks by the fixed traversal patterns every graph system uses (`GraphStore.reach`) and ranks them
by `query.ranking.rank`. Every tool cuts its result to its cap and says so in the note.
Not here: the Cypher (node_reader.py), the text search tools (text_tools.py).
"""

from pydantic import BaseModel, Field

from ..hybrid.retrievers import fused_cards
from ..hybrid.seed_fusion import SeedSetting
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder
from ..query.graph_store import GraphStore
from ..query.ranking import rank
from .node_reader import NodeReader
from .results import ChunksResult, ChunkText, NodeFacts, NodeResult, NodesResult, RelationsResult


class FindNodesArguments(BaseModel):
    query: str = Field(
        description="What to look for: a name, or words saying what the wanted nodes are about."
    )


class NodeArguments(BaseModel):
    ref: str = Field(description="The ref of a node, exactly as a tool returned it.")


class NeighboursArguments(NodeArguments):
    relation: str | None = Field(
        default=None, description="Only relations of this type, exactly as a listed relation names it."
    )


class EvidenceArguments(NodeArguments):
    about: str = Field(description="What the passages should say: the passages closest to it come first.")


class FindNodes:
    """`find_nodes`: the nodes whose cards the query is nearest to and shares the most words with, by R132's
    seeding, the first `k` that name a node."""

    name = "find_nodes"
    description = (
        "Find the nodes of the graph that a text names or is about: records, named individuals and kinds of "
        "thing, best first. Start here: the other graph tools take the refs it returns."
    )
    arguments = FindNodesArguments

    def __init__(
        self,
        units: UnitStore,
        reader: NodeReader,
        embedder: Embedder,
        representation: str,
        setting: SeedSetting,
        k: int,
    ):
        self._units = units
        self._reader = reader
        self._embedder = embedder
        self._representation = representation
        self._setting = setting
        self._k = k

    def run(self, arguments: FindNodesArguments) -> NodesResult:
        vector = self._embedder.embed([arguments.query])[0]
        refs = fused_cards(self._units, self._representation, self._setting, arguments.query, vector)
        nodes = self._reader.nodes(refs)[: self._k]  # a card whose node is gone is left out, as in R132
        return NodesResult(nodes=nodes, note="" if nodes else "No node matches the query.")


class GetNode:
    """`get_node`: a node's label, name, other names and properties, each property with its source; values
    longer than `value_chars` and names beyond `names` are cut, and the note says so."""

    name = "get_node"
    description = (
        "Read one node: what it is, its name, its other names, and its properties, each with the source row "
        "it comes from."
    )
    arguments = NodeArguments

    def __init__(self, reader: NodeReader, value_chars: int, names: int):
        self._reader = reader
        self._value_chars = value_chars
        self._names = names

    def run(self, arguments: NodeArguments) -> NodeResult:
        node = self._reader.node(arguments.ref)
        if node is None:
            return NodeResult(node=None, note=_unknown(arguments.ref))
        capped, cuts = self._capped(node)
        return NodeResult(node=capped, note="; ".join(cuts))

    def _capped(self, node: NodeFacts) -> tuple[NodeFacts, list[str]]:
        """The node with long values and extra names cut, and what was cut."""
        cuts = []
        properties = []
        for p in node.properties:
            if len(p.value) > self._value_chars:
                p = p.model_copy(update={"value": p.value[: self._value_chars] + "..."})
                cuts.append(f"{p.name} cut at {self._value_chars} characters")
            properties.append(p)
        if len(node.aliases) > self._names:
            cuts.append(f"{self._names} of {len(node.aliases)} other names shown")
        aliases = node.aliases[: self._names]
        return node.model_copy(update={"properties": properties, "aliases": aliases}), cuts


class Neighbours:
    """`neighbours`: a node's record relationships and the claims it is an end of, grouped per other node,
    at most `limit` groups, with the total."""

    name = "neighbours"
    description = (
        "List the nodes one node relates to: each relation's type and direction, the other node's ref and "
        "name, and the ids of up to three claims stating it. To go a step further, call it again on a "
        "listed node."
    )
    arguments = NeighboursArguments

    def __init__(self, reader: NodeReader, limit: int):
        self._reader = reader
        self._limit = limit

    def run(self, arguments: NeighboursArguments) -> RelationsResult:
        ref = arguments.ref
        if not self._reader.nodes([ref]):  # an unknown ref and a node without relations both list nothing
            return RelationsResult(ref=ref, relations=[], total=0, note=_unknown(ref))
        page = self._reader.relations(ref, arguments.relation, self._limit)
        note = ""
        if page.total > len(page.relations):
            note = (
                f"{len(page.relations)} of {page.total} relations shown; give a relation type to see one type"
            )
        elif page.total == 0:
            note = "No relation found."
        return RelationsResult(ref=ref, relations=page.relations, total=page.total, note=note)


class NodeEvidence:
    """`node_evidence`: the chunks the fixed traversal reaches from a node, the `limit` nearest `about`
    first, each with the claims from it that the node holds or is an end of."""

    name = "node_evidence"
    description = (
        "Read the text passages the graph links to one node, those closest to `about` first, each with the "
        "claims read from it that concern the node."
    )
    arguments = EvidenceArguments

    def __init__(self, reader: NodeReader, store: GraphStore, embedder: Embedder, limit: int):
        self._reader = reader
        self._store = store
        self._embedder = embedder
        self._limit = limit

    def run(self, arguments: EvidenceArguments) -> ChunksResult:
        start = self._reader.start(arguments.ref)
        if start is None:
            return ChunksResult(chunks=[], total=0, note=_unknown(arguments.ref))
        thing = [start.node_id] if start.kind == "thing" else []
        patterns = self._store.reach(thing, [start.node_id] if start.kind == "kind" else [])
        reached = sorted(set().union(*patterns.values()))
        vector = self._embedder.embed([arguments.about])[0]
        ranked = rank(vector, self._store.chunks(reached))[: self._limit]
        claims = self._reader.node_claims(arguments.ref, [c.chunk_id for c in ranked])
        chunks = [
            ChunkText(chunk_id=c.chunk_id, context=c.context, text=c.text,
                      claims=[f for f in claims if f.chunk_id == c.chunk_id])
            for c in ranked
        ]  # fmt: skip
        return ChunksResult(chunks=chunks, total=len(reached), note=_shown(len(chunks), len(reached)))


def _unknown(ref: str) -> str:
    """The note for a ref that names no node."""
    return f"No node has the ref {ref!r}; use a ref a tool returned."


def _shown(shown: int, total: int) -> str:
    """The note for a list of passages: none found, or how many of how many are shown."""
    if total == 0:
        return "No passage found."
    return f"{shown} of {total} passages shown" if total > shown else ""
