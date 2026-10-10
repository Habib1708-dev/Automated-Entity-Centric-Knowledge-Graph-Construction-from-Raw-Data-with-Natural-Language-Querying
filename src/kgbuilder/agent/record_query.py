"""A checked `RecordQuery` compiled to one parameterised, read-only Cypher statement (R134).

Role in the pipeline: `query_records` (structured_tools.py) checks a query (structured_query.py), compiles it
here and runs it with `CypherStore.run_read`, in a read transaction the server cancels at the timeout.
Design: a pure function returning the statement, its parameters and the shape of its rows, so the Cypher is
tested as text and once against Neo4j. Labels, types and keys go through `cypher_ident`, every value is a
parameter, and every comparison is R74's own (`query.plan_cypher.condition`): a text equal ignoring case, a
text holding a number compared as that number, a date by `date()` or its year. A text field is measured by
the number it holds (`plan_cypher.text_number`). The matching records are made distinct before anything is
counted, so a record related to the anchor twice counts once; lists keep their total before the cut. A group
over a list, or over a text split at the query's separator, unwinds the values (trimmed, the empty ones
dropped) and keeps each record once per value, so a record naming a value twice counts once (R139). An `in`
condition is R74's `=` once per value, joined by OR; a path of relationships from the related record is
written from the queried records' side, its types reversed, through unnamed nodes of any label (R140).
Not here: the check (structured_query.py), running and capping (structured_tools.py).
"""

from typing import Any

from ..core.cypher import cypher_ident
from ..graph.canonical import record_ref_of
from ..query.graph_schema import PropertyInfo
from ..query.plan_cypher import condition, text_number
from .structured_query import Compiled, RecordQuery, StructuredQuery, shape_of

_AGGREGATES = {"count": "count", "sum": "sum", "average": "avg", "min": "min", "max": "max"}


def compile_records(
    query: RecordQuery, fields: dict[str, PropertyInfo], key: str, rules: list[dict[str, str]],
    anchor: str | None, limit: int,
) -> Compiled:  # fmt: skip
    """The Cypher of a checked query over the records of `query.label` (`fields`: its properties; `key`: its
    key column, the order of rows without `order_by`; `rules`: each record label with its key, for the refs;
    `anchor`: the element id of the `related_to` record, when given)."""
    where, params = _conditions(query, fields)
    rel = ""
    if anchor is not None and query.path():
        hops = [f"-[:{cypher_ident(t)}]-" for t in reversed(query.path())]
        rel = "()".join(hops) + "(a)"
        where.insert(0, "elementId(a) = $anchor")
        params["anchor"] = anchor
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    body = f"MATCH (n:{cypher_ident(query.label)}){rel}{clause} WITH DISTINCT n"
    params["limit"] = limit
    shape = shape_of(query)
    if shape == "rows":
        # the key breaks ties, so the cut never depends on the order the database found the records in
        order = [f"n.{cypher_ident(key)}"]
        if query.order_by is not None and query.order_by != key:
            order.insert(0, f"n.{cypher_ident(query.order_by)}{' DESC' if query.descending else ''}")
        elif query.descending:
            order = [f"n.{cypher_ident(key)} DESC"]
        statement = (
            f"{body} ORDER BY {', '.join(order)} "
            "WITH collect(n) AS found UNWIND found[0..$limit] AS n "
            f"RETURN size(found) AS total, {record_ref_of('n')} AS ref, properties(n) AS props"
        )
        return Compiled(statement=statement, parameters=params | {"rules": rules}, shape=shape)
    measure, extra = _measure(query, fields)
    params |= extra
    if shape == "value":
        return Compiled(statement=f"{body} RETURN {measure} AS value", parameters=params, shape=shape)
    group = f"n.{cypher_ident(query.group_by or '')}"
    unwind, bucket, extra = _buckets(group, fields[query.group_by or ""], query.group_split)
    statement = (
        f"{body} WHERE {group} IS NOT NULL {unwind}WITH {bucket} AS bucket, {measure} AS value "
        "ORDER BY value DESC, bucket WITH collect([bucket, value]) AS found UNWIND found[0..$limit] AS row "
        "RETURN size(found) AS total, row[0] AS bucket, row[1] AS value"
    )
    return Compiled(statement=statement, parameters=params | extra, shape=shape)


def _buckets(group: str, field: PropertyInfo, split: str | None) -> tuple[str, str, dict[str, Any]]:
    """What a record is grouped by: the field itself, or (after an unwinding clause that keeps each record
    once per value) each element of a list or each part of a text split at `split`."""
    if split is not None:
        unwind = f"UNWIND split({group}, $group_split) AS part WITH n, trim(part) AS part WHERE part <> '' "
        return f"{unwind}WITH DISTINCT n, part AS bucket ", "bucket", {"group_split": split}
    if field.type.startswith("LIST"):
        return f"UNWIND {group} AS bucket WITH DISTINCT n, bucket ", "bucket", {}
    return "", group, {}


def _conditions(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> tuple[list[str], dict[str, Any]]:
    """Each condition as R74 compares it, its value a parameter of its own (`$v0`, `$v1`, ...)."""
    where: list[str] = []
    params: dict[str, Any] = {}
    for i, c in enumerate(query.where):
        if c.operator == "in" and isinstance(c.value, list):
            parts = [condition("n", fields[c.field], "=", v, f"v{i}_{j}") for j, v in enumerate(c.value)]
            where.append(f"({' OR '.join(text for text, _ in parts)})")
            for _, extra in parts:
                params |= extra
            continue
        text, extra = condition("n", fields[c.field], c.operator, c.value, f"v{i}")
        where.append(text)
        params |= extra
    return where, params


def _measure(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> tuple[str, dict[str, Any]]:
    """The aggregate expression of a measure: a count of records, or the measured field (a text field by the
    number it holds)."""
    if query.measure == "count":
        return "count(n)", {}
    ref = f"n.{cypher_ident(query.field or '')}"
    if fields[query.field or ""].type == "STRING":
        number, extra = text_number(ref, "measure_pattern")
        return f"{_AGGREGATES[query.measure]}({number})", extra
    return f"{_AGGREGATES[query.measure]}({ref})", {}
