"""Chunk documents and write the lexical graph: Chunk -PART_OF-> Document, Chunk -NEXT_CHUNK-> Chunk."""

import re

from neo4j import Driver
from pydantic import BaseModel

from .config import settings
from .ingest import Document


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    index: int
    text: str


def chunk_document(doc: Document, max_chars: int | None = None, min_chars: int | None = None) -> list[Chunk]:
    """Split on rules, then pack: tiny sections join their neighbour, oversized ones are cut on paragraphs."""
    max_chars = max_chars or settings.chunk_max_chars
    min_chars = min_chars or settings.chunk_min_chars
    sections = [s.strip() for s in re.split(r"\n\s*(?:---+|\*\*\*+)\s*\n", doc.text) if s.strip()]

    pieces: list[str] = []
    for section in sections:
        if len(section) <= max_chars:
            pieces.append(section)
            continue
        current = ""
        for para in re.split(r"\n\s*\n", section):
            if current and len(current) + len(para) + 2 > max_chars:
                pieces.append(current)
                current = ""
            while len(para) > max_chars:  # single oversized paragraph: cut on whitespace
                cut = para.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                pieces.append(para[:cut].strip())
                para = para[cut:].strip()
            current = f"{current}\n\n{para}".strip()
        if current:
            pieces.append(current)

    packed: list[str] = []
    carry = ""
    for piece in pieces:
        piece = f"{carry}\n\n{piece}".strip() if carry else piece
        carry = ""
        if len(piece) < min_chars:
            carry = piece
        else:
            packed.append(piece)
    if carry:
        packed.append(f"{packed.pop()}\n\n{carry}" if packed else carry)

    return [Chunk(chunk_id=f"{doc.doc_id}#{i}", doc_id=doc.doc_id, index=i, text=t) for i, t in enumerate(packed)]


def write_lexical_graph(
    driver: Driver, docs: list[Document], chunks: list[Chunk], embeddings: dict[str, list[float]] | None = None
) -> None:
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE")
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE")
    driver.execute_query(
        "UNWIND $rows AS r MERGE (d:Document {doc_id: r.doc_id}) SET d.title = r.title",
        rows=[{"doc_id": d.doc_id, "title": d.title} for d in docs],
    )
    embeddings = embeddings or {}
    rows = [
        {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "index": c.index, "text": c.text, "embedding": embeddings.get(c.chunk_id)}
        for c in chunks
    ]
    for i in range(0, len(rows), 500):
        driver.execute_query(
            "UNWIND $rows AS r MATCH (d:Document {doc_id: r.doc_id}) "
            "MERGE (c:Chunk {chunk_id: r.chunk_id}) SET c.index = r.index, c.text = r.text, c.doc_id = r.doc_id "
            "FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END | SET c.embedding = r.embedding) "
            "MERGE (c)-[:PART_OF]->(d)",
            rows=rows[i : i + 500],
        )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (a:Chunk {chunk_id: r.a}), (b:Chunk {chunk_id: r.b}) MERGE (a)-[:NEXT_CHUNK]->(b)",
        rows=[{"a": a.chunk_id, "b": b.chunk_id} for a, b in zip(chunks, chunks[1:]) if a.doc_id == b.doc_id],
    )
    if embeddings:
        dim = len(next(iter(embeddings.values())))
        driver.execute_query(
            "CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}"
        )
