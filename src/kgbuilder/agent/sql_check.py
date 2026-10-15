"""The check every SQL statement the model writes passes before it runs (R188): one SELECT, over the staged
tables and its own named subqueries, with no table function.

Role in the pipeline: `query_sql` (sql_tool.py) asks it first, in the experimental variant `agent_sql` of
plan R183-R191; a refused statement is answered with the reasons, as a refused typed query is.
Design: the LLM proposes, code decides, and code reads the statement with DuckDB's own parser, not with a
pattern: `json_serialize_sql` serialises only SELECT statements (COPY, ATTACH, INSTALL, PRAGMA, EXPLAIN and
every write are refused by the parser itself), gives one tree per statement, and marks each table a FROM
names (`BASE_TABLE`, with its schema) and each table function (`TABLE_FUNCTION`: `read_csv`, `glob`, the
settings functions). A name is allowed when it is a staged table or a named subquery (`WITH`) of the
statement, in no schema or in `main`; a file path written as a table name ('x.csv') is a table name no
staged table has, so it is refused too. The parse runs on its own empty in-memory connection, so checking
reads no data. The locked connection (sql_tables.py) is the second line of defence.
Not here: running the statement (sql_tables.py), the tool (sql_tool.py).
"""

import json
from collections.abc import Iterator

import duckdb

# a FROM entry the parser marks as a table, and one it marks as a table function
_TABLE = "BASE_TABLE"
_FUNCTION = "TABLE_FUNCTION"
# the schemas a staged table may be written in: none, or DuckDB's default
_SCHEMAS = ("", "main")
_NO_FUNCTION = "a table function (reading files or settings) is not allowed; read the source tables"


def check_sql(statement: str, tables: set[str]) -> list[str]:
    """Why `statement` may not run over `tables` (the staged table names), or [] when it may."""
    tree = json.loads(_parse(statement))
    if tree.get("error"):
        reason = tree.get("error_message", "it does not parse")
        return [f"the statement was refused ({reason}); give one read-only SELECT"]
    statements = tree.get("statements", [])
    if len(statements) != 1:
        return [f"give exactly one SELECT statement, not {len(statements)}"]
    named = {entry["key"] for entry in statements[0]["node"].get("cte_map", {}).get("map", [])}
    issues = []
    for node in _nodes(statements[0]):
        if node.get("type") == _FUNCTION:
            issues.append(_NO_FUNCTION)
        elif node.get("type") == _TABLE:
            issues += _table_issues(node, tables, named)
    return list(dict.fromkeys(issues))


def tables_read(statement: str, tables: set[str]) -> list[str]:
    """The staged tables of `tables` a checked `statement` reads, each once, in the order the parse tree
    names them (R208: the provenance of a `query_sql` run); its own subqueries are not tables."""
    tree = json.loads(_parse(statement))
    found = (n.get("table_name", "") for n in _nodes(tree.get("statements", [])) if n.get("type") == _TABLE)
    return list(dict.fromkeys(name for name in found if name in tables))


def _parse(statement: str) -> str:
    """DuckDB's parse tree of `statement` as JSON text, from an empty connection of its own."""
    with duckdb.connect() as con:
        return con.execute("SELECT json_serialize_sql($statement)", {"statement": statement}).fetchone()[0]


def _nodes(tree: object) -> Iterator[dict]:
    """Every object of a parse tree, depth first: a FROM entry can sit in a subquery, a join or a CTE."""
    if isinstance(tree, dict):
        yield tree
        for value in tree.values():
            yield from _nodes(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from _nodes(value)


def _table_issues(node: dict, tables: set[str], named: set[str]) -> list[str]:
    """Why a table the statement names may not be read: another schema or catalog, or no such table."""
    name = node.get("table_name", "")
    if node.get("schema_name", "") not in _SCHEMAS or node.get("catalog_name", ""):
        return [f"{name!r} is not a source table; read only the tables the schema lists"]
    if name not in tables and name not in named:
        return [f"no table {name!r}; the tables are {', '.join(sorted(tables))}"]
    return []
