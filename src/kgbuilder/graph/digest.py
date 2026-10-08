"""The digest of a loaded graph: a short hash of what it holds, logged by every run that queries it (R117).

Role in the pipeline: `kg qa` and `kg retrieve-eval` log it as the param `graph_digest`, so two runs are
paired only when they asked the same graph (the fairness contract of plan R116-R125); its chunk ids let
those stages refuse a gold whose evidence the graph lacks before any model is called.
Design: content, not identity. Element ids change on every rebuild (R113's finding), so the digest hashes
what a rebuild of the same build keeps: how many nodes carry each label, how many relationships have each
type, and every chunk's id and text. Two builds of one recipe give one digest; a missing node, an extra
relationship or one changed chunk text gives another.
Not here: the gold check itself (pipeline/qa_systems.py), anything that writes.
"""

import hashlib
import json

from neo4j import Driver
from pydantic import BaseModel


class GraphDigest(BaseModel):
    """What identifies the content of the loaded graph: the hash, and the counts and chunk ids it covers."""

    value: str  # 12 hex characters, like a prompt version
    labels: dict[str, int]  # label -> nodes carrying it
    relationships: dict[str, int]  # relationship type -> relationships of it
    chunk_ids: frozenset[str]


def graph_digest(driver: Driver) -> GraphDigest:
    """Read the label and relationship counts and every chunk's id and text, and hash them. Read-only."""
    # a node with two labels counts under each: a label added to or removed from a node changes the digest
    labels, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label"
    )
    types, _, _ = driver.execute_query("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type")
    chunks, _, _ = driver.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.text AS text ORDER BY id")
    label_counts = {r["label"]: r["n"] for r in labels}
    type_counts = {r["type"]: r["n"] for r in types}
    content = {
        "labels": label_counts,
        "relationships": type_counts,
        "chunks": [[r["id"], r["text"]] for r in chunks],
    }
    value = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return GraphDigest(
        value=value,
        labels=label_counts,
        relationships=type_counts,
        chunk_ids=frozenset(r["id"] for r in chunks),
    )
