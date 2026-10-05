"""The fixed traversal patterns of the retrieval route: from the nodes a question names to chunks to read.

Role in the pipeline: the second step of the graph route; graph_store.py runs them, systems.py ranks what
they reach.
Design: three patterns, each a few parameterised Cypher queries written here, never by a model, and at most
a few relationship hops long:
- `thing_observations`: a thing -> its observations -> their chunks (the kinds those claims mention come
  along), plus the text about the thing: documents about it, sections about it, and the claims and chunks
  of the mentions that refer to it (R75: "the spindle" of a review, linked to its record);
- `kind_observations`: a kind (an individual or a concept, R75) -> the claims whose mentions refer to it ->
  their chunks (the things those claims hang on come along), plus the chunks that mention it;
- `related_records`: a thing -> the domain nodes within `hops` relationships (a vehicle's recalls, a
  product's parts) -> their text, as for a thing.
Until R75 a fourth pattern, `referred_records`, went from a kind to the record it named; a mention that names
a record now refers to the record itself, so the thing's own text holds those chunks.
Every pattern returns chunk ids and is reported on its own, so a missed chunk can be traced to a pattern.
Not here: which nodes a question names (names.py), which chunks are kept (systems.py).
"""

from neo4j import Driver

from ..graph.canonical import canonical_id

# The text of a thing: the chunks its observations come from, the chunks of documents about it (a product's
# reviews, a record's own prose), the sections about it (a complaint's section of a larger file), and the
# claims and chunks of the mentions that refer to it.
_THING_TEXT = [
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH (t)-[:HAS_OBSERVATION]->(:Observation)-[:FROM]->(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH (t)<-[:ABOUT]-(:Document)<-[:PART_OF]-(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH (t)<-[:ABOUT]-(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH (t)<-[:REFERS_TO]-(:Mention)<-[:SUBJECT|OBJECT]-(:Observation)-[:FROM]->(c:Chunk) "
    "RETURN DISTINCT c.chunk_id AS chunk_id",
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH (t)<-[:REFERS_TO]-(:Mention)<-[:MENTIONS]-(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
]

# The text of a kind: the chunks of the claims about it or pointing at it, and the chunks that mention it
# (a mention without a claim still says the chunk talks about the kind). A kind is addressed by its canonical
# id, which its mentions carry on their identity edge (graph/canonical.py).
_KIND_TEXT = [
    f"MATCH (m:Mention) WHERE {canonical_id('m')} IN $ids "
    "MATCH (m)<-[:SUBJECT|OBJECT]-(:Observation)-[:FROM]->(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
    f"MATCH (m:Mention) WHERE {canonical_id('m')} IN $ids "
    "MATCH (m)<-[:MENTIONS]-(c:Chunk) RETURN DISTINCT c.chunk_id AS chunk_id",
]

# Domain nodes near a thing. Every node on the path must carry a domain label ($labels, from the plan), so
# the walk stays in the structured graph and cannot wander through documents, chunks or kinds, which would
# reach half the corpus in two hops. `{hops}` is an int the caller checked: a length cannot be a parameter.
_RELATED = (
    "MATCH (t) WHERE elementId(t) IN $ids "
    "MATCH p = (t)-[*1..{hops}]-(r) "
    "WHERE NOT elementId(r) IN $ids AND all(n IN nodes(p) WHERE any(l IN labels(n) WHERE l IN $labels)) "
    "RETURN DISTINCT elementId(r) AS id"
)

PATTERNS = ("thing_observations", "kind_observations", "related_records")


def reach(
    driver: Driver, things: list[str], kinds: list[str], domain_labels: list[str], hops: int
) -> dict[str, set[str]]:
    """Pattern name -> the chunk ids it reaches from the linked `things` (element ids) and `kinds` (canonical
    ids). Every pattern is present, empty when it reaches nothing.

    Raises `ValueError` for `hops` below 1: the hop count is written into the query text.
    """
    if not isinstance(hops, int) or hops < 1:
        raise ValueError(f"hops must be a positive int, got {hops!r}")
    related = _node_ids(driver, _RELATED.format(hops=hops), things, labels=domain_labels) if things else []
    return {
        "thing_observations": _chunk_ids(driver, _THING_TEXT, things),
        "kind_observations": _chunk_ids(driver, _KIND_TEXT, kinds),
        "related_records": _chunk_ids(driver, _THING_TEXT, related),
    }


def _chunk_ids(driver: Driver, queries: list[str], ids: list[str]) -> set[str]:
    if not ids:
        return set()
    found: set[str] = set()
    for query in queries:
        records, _, _ = driver.execute_query(query, ids=ids)
        found.update(r["chunk_id"] for r in records)
    return found


def _node_ids(driver: Driver, query: str, ids: list[str], **params: object) -> list[str]:
    records, _, _ = driver.execute_query(query, ids=ids, **params)
    return [r["id"] for r in records]
