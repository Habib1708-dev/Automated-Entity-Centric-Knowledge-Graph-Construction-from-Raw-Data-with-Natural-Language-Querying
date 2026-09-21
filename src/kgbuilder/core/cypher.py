"""Cypher helpers that every graph-writing module needs.

Role in the pipeline: the single place where a dynamic identifier is made safe to splice into a query.
Design: values always travel as query parameters; only identifiers (which Neo4j cannot parameterise)
pass through `cypher_ident`.
"""


def cypher_ident(name: str) -> str:
    """Escape a label, relationship type or property key for use inside a Cypher query.

    Identifiers come from the LLM-proposed plan and schema, so they are untrusted. Backtick quoting with
    doubled inner backticks is Neo4j's escaping rule and makes injection through a name impossible.
    """
    return "`" + name.replace("`", "``") + "`"
