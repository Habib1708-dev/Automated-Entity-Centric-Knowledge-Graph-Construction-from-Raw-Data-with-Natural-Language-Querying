"""The lexical graph: `(Chunk)-[:PART_OF]->(Document)` and `(Chunk)-[:NEXT_CHUNK]->(Chunk)`.

Role in the pipeline: `kg ingest-text` writes it; `kg text-schema` and `kg extract` read the chunks back
with `read_chunks`, so every later stage works on exactly the chunks (and chunk ids) that are stored,
even when the chunk settings changed in between.
Design: idempotent MERGE writes. Re-ingesting a document replaces its chunks: chunks left over from an
earlier run with other settings are deleted, because their ids would otherwise linger as ghost evidence.
Naming: PLAN.md called the relationship FROM_DOCUMENT; the code settled on PART_OF, which reads naturally
in both directions of a traversal.
Not here: chunking decisions (chunking.py).
"""

from neo4j import Driver

from .chunking import Chunk
from .documents import Document

_BATCH_SIZE = 500  # chunk rows carry text and an embedding vector, so batches are kept moderate


def write_lexical_graph(
    driver: Driver,
    docs: list[Document],
    chunks: list[Chunk],
    embeddings: dict[str, list[float]] | None = None,
) -> int:
    """Write documents and chunks. Returns the number of stale chunks removed from earlier runs."""
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE")
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE")
    driver.execute_query(
        "UNWIND $rows AS r MERGE (d:Document {doc_id: r.doc_id}) SET d.title = r.title",
        rows=[{"doc_id": d.doc_id, "title": d.title} for d in docs],
    )

    # Chunks of these documents that the current chunking no longer produces. DETACH also drops their
    # MENTIONS; entities that lose their last mention are then reported by the provenance check.
    removed, _, _ = driver.execute_query(
        "MATCH (c:Chunk) WHERE c.doc_id IN $doc_ids AND NOT c.chunk_id IN $keep "
        "DETACH DELETE c RETURN count(c) AS n",
        doc_ids=[d.doc_id for d in docs],
        keep=[c.chunk_id for c in chunks],
    )

    embeddings = embeddings or {}
    rows = [{**c.model_dump(), "embedding": embeddings.get(c.chunk_id)} for c in chunks]
    for start in range(0, len(rows), _BATCH_SIZE):
        driver.execute_query(
            "UNWIND $rows AS r MATCH (d:Document {doc_id: r.doc_id}) "
            "MERGE (c:Chunk {chunk_id: r.chunk_id}) "
            "SET c.index = r.index, c.text = r.text, c.doc_id = r.doc_id "
            # FOREACH-as-IF: only set the vector when we have one, so a run without embeddings does
            # not erase vectors from an earlier run
            "FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END | "
            "  SET c.embedding = r.embedding) "
            "MERGE (c)-[:PART_OF]->(d)",
            rows=rows[start : start + _BATCH_SIZE],
        )

    # consecutive chunks of the same document; `chunks` is ordered by document, then index
    pairs = [
        {"a": a.chunk_id, "b": b.chunk_id}
        for a, b in zip(chunks, chunks[1:], strict=False)
        if a.doc_id == b.doc_id
    ]
    driver.execute_query(
        "UNWIND $rows AS r MATCH (a:Chunk {chunk_id: r.a}), (b:Chunk {chunk_id: r.b}) "
        "MERGE (a)-[:NEXT_CHUNK]->(b)",
        rows=pairs,
    )

    if embeddings:
        dimensions = len(next(iter(embeddings.values())))
        # index options cannot be parameters; `dimensions` is an int computed here, never user text
        driver.execute_query(
            "CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dimensions}, "
            "`vector.similarity_function`: 'cosine'}}"
        )
    return removed[0]["n"]


def read_chunks(driver: Driver) -> list[Chunk]:
    """All stored chunks, ordered by document and position: the single source of truth after ingestion."""
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk) RETURN c.chunk_id AS chunk_id, c.doc_id AS doc_id, c.index AS index, c.text AS text "
        "ORDER BY doc_id, index"
    )
    return [Chunk(**r.data()) for r in records]
