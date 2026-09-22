"""Deterministic ids for nodes of the subject graph.

Role in the pipeline: `entity_id` names an `:Entity` from its type and normalised name, so that the same
name in two chunks is one node, and so that two writers (the subject-graph writer for extracted facts, the
derivation in the link stage for derived facts) agree on the id of the same entity without talking to
each other. Not here: anything that touches Neo4j or the LLM.
"""

import hashlib

from .text import norm


def entity_id(entity_type: str, name: str) -> str:
    """Deterministic id from type and normalised name: "Table" and "table " are the same entity."""
    # sha1 is used as a short stable hash, not for security
    return hashlib.sha1(f"{entity_type}|{norm(name)}".encode()).hexdigest()[:16]
