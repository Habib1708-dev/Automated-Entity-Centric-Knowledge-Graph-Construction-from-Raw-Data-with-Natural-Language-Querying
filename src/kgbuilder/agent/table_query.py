"""A checked `TableQuery` compiled to one parameterised DuckDB SELECT over a staged source table (R134).

Role in the pipeline: `query_tables` (structured_tools.py) checks a query (structured_query.py), compiles it
here and runs it on the `TableStore` (tables.py).
Design: a pure function returning the statement, its parameters and the shape of its rows, like
record_query.py, with the same comparison rules written in SQL: a text equal ignoring case or containing
words, a text holding a number ('$1,289') compared and measured as that number, a date compared as a date
or by its year, and the year of a text that holds a date found as a whole number among its digits. Table
and column names are quoted by `quote_ident`, every value is a named parameter. Rows without an order are
sorted by every column, so the cut never depends on how the file was scanned; each list keeps its total. A
group over a text split at the query's separator counts each part (trimmed, the empty ones dropped, each
once per row) as its own group (R139); a staged table holds no list column. An `in` condition is the `=`
comparison once per value, joined by OR (R140).
Not here: the check (structured_query.py), running and capping (structured_tools.py).
"""

import re
from typing import Any

from ..query.graph_schema import PropertyInfo
from ..query.plan import Operator, as_number
from ..structured.profiler import quote_ident
from .structured_query import Compiled, TableQuery, shape_of

_COMPARISONS = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_AGGREGATES = {"count": "count", "sum": "sum", "average": "avg", "min": "min", "max": "max"}
# what is no part of a number, removed from a text before it is read as one, as in R74's Cypher
_NON_NUMERIC = r"[^0-9.\-]"
_TOTAL = "__total"  # the window column that keeps a list's size before the cut; dropped from the rows
_BUCKET = "__bucket"  # one part of a split text, the column a split group is grouped by (R139)


def compile_table(query: TableQuery, fields: dict[str, PropertyInfo], limit: int) -> Compiled:
    """The SQL of a checked query over `query.table` (`fields`: its columns, in the table's order)."""
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "number_pattern": _NON_NUMERIC}
    for i, c in enumerate(query.where):
        col = quote_ident(c.field)
        if c.operator == "in" and isinstance(c.value, list):
            parts = [_condition(col, fields[c.field], "=", v, f"v{i}_{j}") for j, v in enumerate(c.value)]
            where.append(f"({' OR '.join(text for text, _ in parts)})")
            params |= {f"v{i}_{j}": value for j, (_, value) in enumerate(parts)}
            continue
        text, value = _condition(col, fields[c.field], c.operator, c.value, f"v{i}")
        where.append(text)
        params[f"v{i}"] = value
    table = quote_ident(query.table)
    shape = shape_of(query)
    if shape == "rows":
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        order = [quote_ident(c) for c in fields]
        if query.order_by is not None:
            order.insert(0, f"{quote_ident(query.order_by)}{' DESC' if query.descending else ''} NULLS LAST")
        statement = (
            f"SELECT *, count(*) OVER () AS {_TOTAL} FROM {table}{clause} ORDER BY {', '.join(order)} "
            "LIMIT $limit"
        )
        return Compiled(statement=statement, parameters=_used(statement, params), shape=shape)
    measure = _measure(query, fields)
    if shape == "value":
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        statement = f"SELECT {measure} AS value FROM {table}{clause}"
        return Compiled(statement=statement, parameters=_used(statement, params), shape=shape)
    group = quote_ident(query.group_by or "")
    clause = f" WHERE {' AND '.join([*where, f'{group} IS NOT NULL'])}"
    if query.group_split is not None:
        params["group_split"] = query.group_split
        # each row once per distinct part, the parts a column of their own the outer query groups by
        split = f"string_split(CAST({group} AS VARCHAR), $group_split)"
        parts = f"list_distinct(list_transform({split}, p -> trim(p)))"
        table = f"(SELECT *, unnest({parts}) AS {_BUCKET} FROM {table}{clause}) WHERE {_BUCKET} <> ''"
        group, clause = _BUCKET, ""
    statement = (
        f"SELECT {group} AS bucket, {measure} AS value, count(*) OVER () AS {_TOTAL} FROM {table}{clause} "
        f"GROUP BY {group} ORDER BY value DESC, bucket LIMIT $limit"
    )
    return Compiled(statement=statement, parameters=_used(statement, params), shape=shape)


def total_column() -> str:
    """The name of the column that holds a list's size before the cut."""
    return _TOTAL


def _condition(col: str, field: PropertyInfo, operator: Operator, value: object, key: str) -> tuple[str, Any]:
    """One condition and the value of its parameter `$key`, compared as the column's type demands."""
    temporal = field.type.startswith(("DATE", "LOCAL", "ZONED"))
    if operator == "year":
        year = int(float(str(value)))
        if temporal:
            return f"year({col}) = ${key}", year
        # RE2 has no look-behind: the year bounded by a non-digit or an end on each side
        return f"regexp_matches(CAST({col} AS VARCHAR), ${key})", f"(^|[^0-9]){year}([^0-9]|$)"
    if operator == "contains":
        return f"contains(lower(CAST({col} AS VARCHAR)), lower(${key}))", str(value)
    cmp = _COMPARISONS[operator]
    if temporal:
        return f"{col} {cmp} CAST(${key} AS DATE)", str(value)
    if field.type in ("INTEGER", "FLOAT"):
        return f"{col} {cmp} ${key}", _number(value)
    if field.type == "BOOLEAN":
        return f"{col} {cmp} ${key}", value if isinstance(value, bool) else str(value).lower() == "true"
    if operator in ("<", "<=", ">", ">="):
        return f"{_text_number(col)} {cmp} ${key}", _number(value)
    return f"lower(CAST({col} AS VARCHAR)) {cmp} lower(${key})", str(value)


def _measure(query: TableQuery, fields: dict[str, PropertyInfo]) -> str:
    """The aggregate expression: a row count, or the measured column (a text column by its number)."""
    if query.measure == "count":
        return "count(*)"
    col = quote_ident(query.field or "")
    expression = _text_number(col) if fields[query.field or ""].type == "STRING" else col
    return f"{_AGGREGATES[query.measure]}({expression})"


def _text_number(col: str) -> str:
    """The number a text column holds ('$1,289' -> 1289.0), null for a text without one."""
    text = f"replace(CAST({col} AS VARCHAR), ',', '')"
    return f"TRY_CAST(regexp_replace({text}, $number_pattern, '', 'g') AS DOUBLE)"


def _number(value: object) -> float:
    """A checked value as a number. Raises `ValueError` for a value the check (`value_issues`) would have
    refused: compiling an unchecked query is a bug, not a result."""
    number = as_number(value)
    if number is None:
        raise ValueError(f"not a number: {value!r}; check the query before compiling it")
    return number


def _used(statement: str, params: dict[str, Any]) -> dict[str, Any]:
    """The parameters the statement names: DuckDB refuses a named parameter it is given but does not use."""
    return {k: v for k, v in params.items() if re.search(rf"\${re.escape(k)}\b", statement)}
