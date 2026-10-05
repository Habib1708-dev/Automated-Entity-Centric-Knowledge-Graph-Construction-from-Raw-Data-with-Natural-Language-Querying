"""Deterministic ids for the nodes of the subject graph and of its identity layer.

Role in the pipeline: `mention_id` names a `:Mention` (one name of one type in one document, R75), so that
two chunks of a document naming "the Linden Hive" give one node and two writers (the subject-graph writer
for extracted claims, the derivation in the link stage) agree on it without talking to each other.
`concept_id` and `individual_id` name the canonical entities the identity stage writes (resolution/), and
`record_ref` is the stable name of a record as a canonical entity. `observation_id` names an `:Observation`
(one claim from one chunk) and is also the fact id of the judge sheet, so a verdict written for a claim
finds it again in a rebuilt graph.
Not here: anything that touches Neo4j or the LLM.
"""

import hashlib

from .text import norm


def _digest(key: str, size: int) -> str:
    # sha1 is used as a short stable hash, not for security
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:size]


def document_of(chunk_id: str) -> str:
    """The document id of a chunk: chunk ids are `<doc_id>#<index>` (text/chunking.py)."""
    return chunk_id.rsplit("#", 1)[0]


def mention_id(entity_type: str, name: str, doc_id: str) -> str:
    """Deterministic id from type, normalised name and document: "Table" and "table " in one document are
    one mention, the same name in another document is another mention (R75)."""
    return _digest(f"{entity_type}|{norm(name)}|{doc_id}", 16)


def concept_id(entity_type: str, name: str) -> str:
    """Id of the concept of a type and normalised name, across documents. The same key as the `:Entity`
    ids before R75, so a concept that resolution leaves alone keeps the id its entity had."""
    return _digest(f"{entity_type}|{norm(name)}", 16)


def individual_id(founding_mention: str) -> str:
    """Id of an individual, from the mention it was founded on (the first of its group by the identity
    stage's order): an individual exists per particular thing, not per name, so its name cannot key it."""
    return _digest(f"individual|{founding_mention}", 16)


def record_ref(label: str, key: str) -> str:
    """The stable name of a record as a canonical entity, `<label>:<key>`: the database's element ids change
    when the graph is rebuilt, and a verdict file must find the record again."""
    return f"{label}:{key}"


def observation_id(chunk_id: str, predicate: str, subject: str, obj: str, time: str = "") -> str:
    """Deterministic id of one claim from the chunk and the claim's own wording, and its time if it has one.

    The wording, not the entity ids: identity decisions may later give a claim's subject the canonical
    name of another document's wording, and the id (with every judge verdict that refers to it) must
    survive that (R44). The key is the one the judge sheet has used for fact ids since R44, so verdicts
    written for the edge-based graph of R62 still apply to its observations. The time (R66) joins the key
    only when set: "slats crack" and "slats crack after two months" in one chunk are two claims, and a
    claim without a time keeps the id it had before R66.
    """
    parts = [chunk_id, predicate, norm(subject), norm(obj)]
    key = "|".join([*parts, norm(time)] if time else parts)
    return _digest(key, 12)
