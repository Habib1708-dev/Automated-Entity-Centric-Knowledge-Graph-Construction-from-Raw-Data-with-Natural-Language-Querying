"""Everything the query stage reads from the graph: node names, traversals, chunks, the vector index, the
nodes a chunk concerns and a claim joins (R126, the start nodes of chunk and claim retrieval), and for the
exact route the schema, the plan check (EXPLAIN) and the read-only run.

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
from ..core.identity import record_ref
from ..graph.canonical import canonical_id, canonical_kind, canonical_name
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


class NamedNode(BaseModel):
    """A node a chunk's mentions refer to: its stable ref, and the names those mentions are written with."""

    ref: str
    names: list[str]


class ChunkNodes(BaseModel):
    """What a chunk concerns by the graph's own navigation contract (anchor/navigation.py W3): the records it
    is about (its own ABOUT, else its document's), and the records, individuals and concepts its mentions
    refer to. Refs only: record refs and canonical ids, the ids the target gold is placed on."""

    about: list[str]  # sorted
    named: list[NamedNode]  # sorted by ref


class GraphStore(Protocol):
    """What the question-answering systems read from the graph."""

    def node_names(self) -> list[NodeName]:
        """Every thing (domain node) and kind (an individual or a concept, R75) a question could name."""
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

    def chunk_nodes(self, chunk_ids: list[str]) -> dict[str, ChunkNodes]:
        """Chunk id -> the nodes it concerns; unknown ids are left out."""
        ...

    def claim_nodes(self, claim_ids: list[str]) -> dict[str, list[str]]:
        """Observation id -> the refs of the nodes the claim joins: its subject's entity, its object's
        entity, then the things it is attached to (sorted), each once; unknown ids are left out."""
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
        # a record is also known by the names of the mentions that refer to it ("2019 Subaru Outback" for
        # the record named "OUTBACK"): a question may write it as the text does
        records, _, _ = self._driver.execute_query(
            "MATCH (m:Mention)-[r:REFERS_TO {kind: 'record'}]->(t) "
            "RETURN elementId(t) AS id, apoc.coll.sort(collect(DISTINCT m.name)) AS aliases"
        )
        aliases = {r["id"]: r["aliases"] for r in records}
        things = [
            NodeName(
                kind="thing",
                node_id=n.element_id,
                ref=record_ref(n.label, n.key),
                name=n.name,
                label=n.label,
                aliases=aliases.get(n.element_id, []),
            )
            for n in (read_domain_nodes(self._driver, self._plan) if self._plan else [])
        ]
        records, _, _ = self._driver.execute_query(
            # every canonical entity that is not a record, and a mention without an edge as itself
            f"MATCH (m:Mention) WHERE {canonical_kind('m')} <> 'record' "
            f"WITH {canonical_id('m')} AS id, {canonical_name('m')} AS name, m.type AS type, m.name AS said "
            "WITH id, min(name) AS name, min(type) AS type, "
            "apoc.coll.sort(collect(DISTINCT said)) AS aliases "
            "RETURN id, name, aliases, type ORDER BY id"
        )
        # a kind is addressed by its canonical id already, which a rebuild keeps: it is its own ref
        kinds = [
            NodeName(
                kind="kind",
                node_id=r["id"],
                ref=r["id"],
                name=r["name"],
                aliases=r["aliases"],
                label=r["type"],
            )
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

    def chunk_nodes(self, chunk_ids: list[str]) -> dict[str, ChunkNodes]:
        records, _, _ = self._driver.execute_query(_CHUNK_NODES, ids=chunk_ids, rules=self._key_rules())
        out = {}
        for r in records:
            names: dict[str, set[str]] = {}
            for hit in r["named"]:
                names.setdefault(hit["ref"], set()).add(hit["name"])
            out[r["chunk_id"]] = ChunkNodes(
                about=sorted({ref for ref in r["about"] if ref}),  # an ABOUT target no plan label keys: none
                named=[NamedNode(ref=ref, names=sorted(said)) for ref, said in sorted(names.items())],
            )
        return out

    def claim_nodes(self, claim_ids: list[str]) -> dict[str, list[str]]:
        records, _, _ = self._driver.execute_query(_CLAIM_NODES, ids=claim_ids, rules=self._key_rules())
        out = {}
        for r in records:
            attached = sorted(ref for ref in r["attached"] if ref)  # a holder no plan label keys: none
            out[r["id"]] = list(dict.fromkeys(ref for ref in [r["subject"], r["object"], *attached] if ref))
        return out

    def _key_rules(self) -> list[dict[str, str]]:
        """Each record label with its key column, so a query can write a record's ref (`_record_ref`)."""
        rules = self._plan.nodes if self._plan else []
        return [{"label": rule.label, "key": rule.unique_column} for rule in rules]


def _record_ref(node: str) -> str:
    """Cypher for `core.identity.record_ref` of the record bound to `node`, from the `$rules` parameter (label
    and key column per record label); null for a node of no plan label."""
    return f"head([r IN $rules WHERE r.label IN labels({node}) | r.label + ':' + toString({node}[r.key])])"


def _resolved(mention: str) -> str:
    """Cypher for the canonical id of the entity the mention bound to `mention` refers to; null when the
    variable is unbound or the mention has no REFERS_TO edge, since it then stands for no node a question
    could start from."""
    return (
        f"CASE WHEN {mention} IS NULL OR {canonical_kind(mention)} = 'mention' THEN null "
        f"ELSE {canonical_id(mention)} END"
    )


# R126, a chunk's start nodes by the navigation contract (anchor/navigation.py): W3's records it is about (the
# chunk's own ABOUT links, else its document's), and the entities its resolved mentions refer to
_CHUNK_NODES = (
    "UNWIND $ids AS id MATCH (c:Chunk {chunk_id: id}) "
    "WITH c, [(c)-[:ABOUT]->(t) | t] AS own "
    "WITH c, CASE WHEN size(own) > 0 THEN own "
    "ELSE [(c)-[:PART_OF]->(:Document)-[:ABOUT]->(t) | t] END AS about "
    "OPTIONAL MATCH (c)-[:MENTIONS]->(m:Mention) "
    f"WITH c, about, m, {_resolved('m')} AS ref "
    "WITH c, about, collect(CASE WHEN ref IS NULL THEN null ELSE {ref: ref, name: m.name} END) AS named "
    f"RETURN c.chunk_id AS chunk_id, [t IN about | {_record_ref('t')}] AS about, named"
)

# R126, a claim's start nodes by arm B of the navigation contract: the entities at its two ends, then the
# things it is attached to (a record by its ref, an individual by its canonical id)
_CLAIM_NODES = (
    "UNWIND $ids AS id MATCH (o:Observation {id: id}) "
    "OPTIONAL MATCH (o)-[:SUBJECT]->(s:Mention) OPTIONAL MATCH (o)-[:OBJECT]->(t:Mention) "
    f"RETURN o.id AS id, {_resolved('s')} AS subject, {_resolved('t')} AS object, "
    "[(h)-[:HAS_OBSERVATION]->(o) | "
    f"CASE WHEN h:Individual OR h:Concept THEN h.id ELSE {_record_ref('h')} END] AS attached"
)
