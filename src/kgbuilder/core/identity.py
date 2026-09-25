"""Deterministic ids for nodes of the subject graph.

Role in the pipeline: `entity_id` names an `:Entity` from its type and normalised name, so that the same
name in two chunks is one node, and so that two writers (the subject-graph writer for extracted facts, the
derivation in the link stage for derived facts) agree on the id of the same entity without talking to
each other. `observation_id` names an `:Observation` (one claim from one chunk) the same way, and is also
the fact id of the judge sheet, so a verdict written for a claim finds it again in a rebuilt graph.
Not here: anything that touches Neo4j or the LLM.
"""

import hashlib

from .text import norm


def entity_id(entity_type: str, name: str) -> str:
    """Deterministic id from type and normalised name: "Table" and "table " are the same entity."""
    # sha1 is used as a short stable hash, not for security
    return hashlib.sha1(f"{entity_type}|{norm(name)}".encode()).hexdigest()[:16]


def observation_id(chunk_id: str, predicate: str, subject: str, obj: str) -> str:
    """Deterministic id of one claim from the chunk and the claim's own wording.

    The wording, not the entity ids: entity resolution may later merge a claim's subject into a node named
    after another review, and the id (with every judge verdict that refers to it) must survive that
    (R44). The key is the one the judge sheet has used for fact ids since R44, so verdicts written for the
    edge-based graph of R62 still apply to its observations.
    """
    key = "|".join([chunk_id, predicate, norm(subject), norm(obj)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
