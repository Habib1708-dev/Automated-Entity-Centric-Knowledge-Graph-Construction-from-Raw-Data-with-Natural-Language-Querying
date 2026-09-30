"""Everything the query stage reads from the graph: node names, traversals, chunks and the vector index.

Role in the pipeline: the one reader of Neo4j for question answering; systems.py depends on the
`GraphStore` protocol, so it is tested with a fake and the Cypher is tested once, against Neo4j.
Design: Repository. Read-only: nothing here writes to the graph.
Not here: deciding which names link (names.py), which chunks to keep (systems.py).
"""

from typing import Protocol

from neo4j import Driver
from neo4j.exceptions import ClientError
from pydantic import BaseModel

from ..core.errors import MissingInputError
from ..resolution.linking import read_domain_nodes
from ..structured.plan import ConstructionPlan
from ..text.lexical import CHUNK_VECTOR_INDEX
from .names import NodeName
from .traversal import reach


class StoredChunk(BaseModel):
    """A chunk with what ranking and reading need: its document's name, its text and its vector."""

    chunk_id: str
    context: str
    text: str
    embedding: list[float] | None = None  # None when ingest ran without an embedder


class GraphStore(Protocol):
    """What the question-answering systems read from the graph."""

    def node_names(self) -> list[NodeName]:
        """Every thing (domain node) and kind (`:Entity`) a question could name."""
        ...

    def reach(self, things: list[str], kinds: list[str]) -> dict[str, set[str]]:
        """Traversal pattern -> chunk ids reached from the linked things and kinds (traversal.py)."""
        ...

    def chunks(self, chunk_ids: list[str]) -> list[StoredChunk]:
        """The chunks with these ids, in the order given; unknown ids are left out."""
        ...

    def nearest_chunks(self, vector: list[float], k: int) -> list[str]:
        """The ids of the `k` chunks nearest `vector` in the chunk vector index, nearest first."""
        ...


class Neo4jGraphStore:
    """`GraphStore` over the pipeline's Neo4j graph. `plan` gives the domain labels and display names
    (None for a text-only dataset: then there are no things); `hops` bounds `related_records`."""

    def __init__(self, driver: Driver, plan: ConstructionPlan | None, hops: int):
        self._driver = driver
        self._plan = plan
        self._hops = hops

    def node_names(self) -> list[NodeName]:
        things = [
            NodeName(kind="thing", node_id=n.element_id, name=n.name)
            for n in (read_domain_nodes(self._driver, self._plan) if self._plan else [])
        ]
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity) RETURN e.id AS id, e.name AS name, coalesce(e.aliases, []) AS aliases"
        )
        kinds = [
            NodeName(kind="kind", node_id=r["id"], name=r["name"], aliases=r["aliases"])
            for r in records
            if r["name"]
        ]
        return things + kinds

    def reach(self, things: list[str], kinds: list[str]) -> dict[str, set[str]]:
        labels = [rule.label for rule in self._plan.nodes] if self._plan else []
        return reach(self._driver, things, kinds, labels, self._hops)

    def chunks(self, chunk_ids: list[str]) -> list[StoredChunk]:
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Chunk) WHERE c.chunk_id IN $ids "
            "RETURN c.chunk_id AS chunk_id, coalesce(c.context, '') AS context, c.text AS text, "
            "c.embedding AS embedding",
            ids=chunk_ids,
        )
        found = {r["chunk_id"]: StoredChunk(**r.data()) for r in records}
        return [found[i] for i in chunk_ids if i in found]

    def nearest_chunks(self, vector: list[float], k: int) -> list[str]:
        try:
            records, _, _ = self._driver.execute_query(
                "CALL db.index.vector.queryNodes($index, $k, $vector) YIELD node, score "
                "RETURN node.chunk_id AS chunk_id ORDER BY score DESC",
                index=CHUNK_VECTOR_INDEX,
                k=k,
                vector=vector,
            )
        except ClientError as e:
            # the index exists only after an ingest with an embedder; say so instead of a Cypher error
            raise MissingInputError(
                f"the chunk vector index '{CHUNK_VECTOR_INDEX}' cannot be searched ({e.code}); "
                "run `kg ingest-text` with an embedding model first"
            ) from e
        return [r["chunk_id"] for r in records]
