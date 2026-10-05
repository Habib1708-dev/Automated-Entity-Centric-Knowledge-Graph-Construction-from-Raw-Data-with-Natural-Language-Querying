"""Everything the query stage reads from the graph: node names, traversals, chunks, the vector index, and for
the exact route the schema, the plan check (EXPLAIN) and the read-only run.

Role in the pipeline: the one reader of Neo4j for question answering; systems.py and exact.py depend on the
`GraphStore` and `CypherStore` protocols, so they are tested with fakes and the Cypher is tested once,
against Neo4j.
Design: Repository. Read-only: nothing here writes to the graph, and the model's Cypher runs only in a read
transaction, which the server refuses to write in.
Not here: deciding which names link (names.py), which chunks to keep (systems.py), the text check of a
model's query (cypher_check.py).
"""

from typing import Any, Protocol

from neo4j import READ_ACCESS, Driver, RoutingControl
from neo4j.exceptions import ClientError, Neo4jError
from pydantic import BaseModel

from ..core.errors import MissingInputError
from ..resolution.linking import read_domain_nodes
from ..structured.plan import ConstructionPlan
from ..text.lexical import CHUNK_VECTOR_INDEX
from .graph_schema import GraphSchema, read_graph_schema
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


class ReadResult(BaseModel):
    """The rows a read query returned, or why it failed (a runtime error, the timeout)."""

    rows: list[list[Any]] = []  # Any: whatever Neo4j returns (text, numbers, lists, dates, nodes)
    keys: list[str] = []  # the column names, in the order of each row (R74: plan_run.py reads rows by name)
    error: str | None = None


class CypherStore(Protocol):
    """What the exact route needs from the graph: its schema, a plan check and a read-only run."""

    def schema(self) -> GraphSchema:
        """The labels, relationships and claim patterns of the graph (graph_schema.py)."""
        ...

    def explain(self, cypher: str, parameters: dict[str, object]) -> list[str]:
        """Why the database refuses to plan `cypher` as a read over known names; [] when it plans it."""
        ...

    def run_read(self, cypher: str, parameters: dict[str, object]) -> ReadResult:
        """The rows of `cypher`, run in a read transaction that the database cancels at the timeout."""
        ...


# GQL status codes of EXPLAIN's warnings about names the database does not hold (Neo4j 5.26)
_UNKNOWN_NAMES = {"01N50", "01N51", "01N52"}  # label, relationship type, property key


class Neo4jGraphStore:
    """`GraphStore` and `CypherStore` over the pipeline's Neo4j graph. `plan` gives the domain labels and
    display names (None for a text-only dataset: then there are no things); `hops` bounds
    `related_records`; `cypher_timeout_s` bounds every read of the exact route."""

    def __init__(
        self, driver: Driver, plan: ConstructionPlan | None, hops: int, cypher_timeout_s: float = 10.0
    ):
        self._driver = driver
        self._plan = plan
        self._hops = hops
        self._timeout = cypher_timeout_s

    def schema(self) -> GraphSchema:
        return read_graph_schema(self._driver)

    def explain(self, cypher: str, parameters: dict[str, object]) -> list[str]:
        try:
            _, summary, _ = self._driver.execute_query(
                "EXPLAIN " + cypher, parameters, routing_=RoutingControl.READ
            )
        except Neo4jError as e:  # a syntax error or a missing parameter: a reason for the retry, not a crash
            return [f"the database cannot plan it: {e.message}"]
        issues = []
        # "r" is read-only; "rw", "w" and "s" (schema) all change something
        if summary.query_type != "r":
            issues.append(f"the database plans it as query type '{summary.query_type}', not read-only")
        issues += [s.status_description for s in summary.gql_status_objects if s.gql_status in _UNKNOWN_NAMES]
        return issues

    def run_read(self, cypher: str, parameters: dict[str, object]) -> ReadResult:
        try:
            # a read session and transaction: the server itself refuses any write ("AccessMode" error)
            with self._driver.session(default_access_mode=READ_ACCESS) as session:
                tx = session.begin_transaction(timeout=self._timeout)
                try:
                    result = tx.run(cypher, parameters)
                    rows = [list(record.values()) for record in result]
                    return ReadResult(rows=rows, keys=list(result.keys()))
                finally:
                    tx.close()  # rolls back: a read has nothing to commit
        except Neo4jError as e:  # the timeout or a runtime error: reported to the route, which may retry
            return ReadResult(error=e.message or str(e))

    def node_names(self) -> list[NodeName]:
        things = [
            NodeName(kind="thing", node_id=n.element_id, name=n.name, label=n.label)
            for n in (read_domain_nodes(self._driver, self._plan) if self._plan else [])
        ]
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity) "
            "RETURN e.id AS id, e.name AS name, coalesce(e.aliases, []) AS aliases, e.type AS type"
        )
        kinds = [
            NodeName(kind="kind", node_id=r["id"], name=r["name"], aliases=r["aliases"], label=r["type"])
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
