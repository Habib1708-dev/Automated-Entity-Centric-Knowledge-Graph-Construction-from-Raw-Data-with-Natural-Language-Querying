"""Extract subject-predicate-object facts from chunks. Every fact must carry a verbatim evidence quote,
verified in code against the chunk text; anything unverifiable is rejected, not stored."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

from neo4j import Driver
from pydantic import BaseModel

from .core.cypher import cypher_ident
from .core.text import norm
from .llm.base import LLMClient
from .text.chunking import Chunk
from .text.schema import TextSchema

PROMPT = """Extract facts from the text chunk as subject-predicate-object triples.

Allowed entity types:
{entity_types}

Allowed fact types (subject_type -[PREDICATE]-> object_type):
{fact_types}

Rules:
- Use only the entity types and fact types above. Skip anything that does not fit.
- `subject` and `object` are the entity names exactly as written in the text (or the proper name of the
  product/thing the text is about when it is named in the chunk). Never use pronouns.
- `evidence` must be ONE contiguous quote copied verbatim from the chunk that supports the fact.
- Extract only what the text states. Do not infer. Return an empty list when nothing qualifies.

<chunk id="{chunk_id}">
{text}
</chunk>"""


class RawTriple(BaseModel):
    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence: str


class ChunkExtraction(BaseModel):
    triples: list[RawTriple]


class Triple(RawTriple):
    chunk_id: str


class Rejected(BaseModel):
    triple: Triple
    reason: str


def entity_id(entity_type: str, name: str) -> str:
    return hashlib.sha1(f"{entity_type}|{norm(name)}".encode()).hexdigest()[:16]


def verify(t: RawTriple, chunk_text: str, schema: TextSchema) -> str | None:
    """Return a rejection reason, or None when the triple is grounded and schema-conformant."""
    text = norm(chunk_text)
    if not schema.allows(t.subject_type, t.predicate, t.object_type):
        return "fact type not in schema"
    if not norm(t.evidence) or norm(t.evidence) not in text:
        return "evidence is not a verbatim span of the chunk"
    if not norm(t.subject) or not norm(t.object):
        return "empty subject or object"
    if norm(t.subject) == norm(t.object):
        return "subject equals object"
    for role, name in (("subject", t.subject), ("object", t.object)):
        if norm(name) not in text:
            return f"{role} '{name}' does not appear in the chunk"
    return None


def extract_chunk(
    chunk: Chunk, schema: TextSchema, llm: LLMClient, model: str, temperature: float = 0.0
) -> tuple[list[Triple], list[Rejected]]:
    """Extract one chunk, then verify every triple in code. Returns (accepted, rejected)."""
    prompt = PROMPT.format(
        entity_types="\n".join(f"- {e.name}: {e.description}" for e in schema.entity_types),
        fact_types="\n".join(
            f"- {f.subject_type} -[{f.predicate}]-> {f.object_type}: {f.description}"
            for f in schema.fact_types
        ),
        chunk_id=chunk.chunk_id,
        text=chunk.text,
    )
    result = llm.generate(prompt, ChunkExtraction, model=model, temperature=temperature)
    accepted, rejected, seen = [], [], set()
    for raw in result.triples:
        t = Triple(**raw.model_dump(), chunk_id=chunk.chunk_id)
        reason = verify(raw, chunk.text, schema)
        if reason:
            rejected.append(Rejected(triple=t, reason=reason))
            continue
        key = (norm(t.subject), t.predicate, norm(t.object), norm(t.evidence))
        if key not in seen:
            seen.add(key)
            accepted.append(t)
    return accepted, rejected


def extract_all(
    chunks: list[Chunk],
    schema: TextSchema,
    llm: LLMClient,
    model: str,
    temperature: float = 0.0,
    workers: int = 8,
) -> tuple[list[Triple], list[Rejected]]:
    """Extract every chunk in parallel. The calls are I/O bound, so threads are enough."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: extract_chunk(c, schema, llm, model, temperature), chunks))
    return [t for a, _ in results for t in a], [r for _, rej in results for r in rej]


def write_subject_graph(driver: Driver, triples: list[Triple], extractor: str) -> dict[str, int]:
    """Entities are keyed by (type, normalized name); facts keep chunk + evidence as provenance."""
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")

    entities: dict[str, dict] = {}
    mentions = set()
    for t in triples:
        for name, etype in ((t.subject, t.subject_type), (t.object, t.object_type)):
            eid = entity_id(etype, name)
            entities.setdefault(eid, {"id": eid, "name": name.strip(), "type": etype})
            mentions.add((t.chunk_id, eid))

    # the type is a property, not a label: a `Product` label here would collide with the domain graph's
    driver.execute_query("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")
    driver.execute_query(
        "UNWIND $rows AS r MERGE (e:Entity {id: r.id}) "
        "ON CREATE SET e.name = r.name, e.type = r.type, e.aliases = [r.name]",
        rows=list(entities.values()),
    )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.c}), (e:Entity {id: r.e}) MERGE (c)-[:MENTIONS]->(e)",
        rows=[{"c": c, "e": e} for c, e in mentions],
    )

    by_pred: dict[str, list[dict]] = {}
    for t in triples:
        by_pred.setdefault(t.predicate, []).append(
            {
                "s": entity_id(t.subject_type, t.subject),
                "o": entity_id(t.object_type, t.object),
                "chunk_id": t.chunk_id,
                "evidence": t.evidence,
            }
        )
    for pred, rows in by_pred.items():
        driver.execute_query(
            "UNWIND $rows AS r MATCH (s:Entity {id: r.s}), (o:Entity {id: r.o}) "
            f"MERGE (s)-[f:{cypher_ident(pred)} {{chunk_id: r.chunk_id, evidence: r.evidence}}]->(o) "
            "SET f.extractor = $model",
            rows=rows,
            model=extractor,
        )
    return {"entities": len(entities), "facts": len(triples), "mentions": len(mentions)}
