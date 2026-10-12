"""The agent's structured tools (R134): read the schema, and query the records or the source tables with a
typed query that code checks and compiles.

Role in the pipeline: the agent (R135) reads `schema` to learn the labels, properties, relationships, claim
patterns and tables, then asks `query_records` (the record layer of the graph) or `query_tables` (the
staged source tables) for lists, counts and measures; they answer what the text tools cannot: how many,
which is largest, every record that meets a condition.
Design: Strategy, one class per tool (tool.py). The LLM proposes a query; code checks every name against the
schema (a record label, a relationship between records, a table) and every value against its field's type
(structured_query.py), compiles it (record_query.py, table_query.py) and runs it read-only (a read
transaction with a timeout, or a SELECT over in-memory views). A refused query and a failed run are both
answered with their reasons, so the agent can correct its query; nothing is guessed. Each result is cut to
`limit` entries, keeps the total, and carries an id made from the statement it ran, the id an answer cites
for a count or a measure; listed records are cited by their refs. A cut list is paged (R148): the note says
which entries are shown of how many and the `offset` that shows the next ones, so the agent reads a long
list to its end instead of guessing past the cut; a page past the end says so, since it carries no total.
Not here: the checks and compilers, the table reader (tables.py).
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from ..query.graph_schema import GraphSchema
from ..query.graph_store import CypherStore, ReadResult
from ..structured.plan import ConstructionPlan, name_property
from .node_reader import NodeReader
from .record_query import compile_records
from .results import ToolResult
from .structured_query import MAX_HOPS, Compiled, RecordQuery, TableQuery, check_query
from .table_query import compile_table, total_column
from .tables import TableStore


class SchemaArguments(BaseModel):
    """`schema` takes no arguments."""


class SchemaResult(ToolResult):
    """`schema`: the record layer, the claim patterns and the source tables, as text."""

    text: str


class QueryResult(ToolResult):
    """`query_records` and `query_tables`: the rows of a checked, compiled query, or why it did not run."""

    query_id: str = ""  # "q-" and 10 hex characters of the statement and its parameters; empty if refused
    columns: list[str] = []
    rows: list[list[str]] = []
    refs: list[str] = []  # query_records listing records: each row's record ref, in row order
    names: list[str] = []  # and each one's display name (its name column, else its key), in row order
    total: int = 0  # the entries or groups before the cut; 1 for a single measured value
    issues: list[str] = []  # why the query was refused, or the error of its run

    def cites(self) -> list[str]:
        return [self.query_id, *self.refs] if self.query_id else []


class Schema:
    """`schema`: the record labels with their properties, the relationships between records, the claim
    patterns of the text and the source tables with their columns, each with types and example values."""

    name = "schema"
    description = (
        "Read what can be queried: the record labels and their properties, the relationships between "
        "records, the patterns of the claims read from the text, and the source tables and their columns, "
        "with types and example values."
    )
    arguments = SchemaArguments

    def __init__(self, schema: GraphSchema, plan: ConstructionPlan | None, tables: TableStore):
        records = schema.records_only({rule.label for rule in plan.nodes} if plan else set())
        layer = GraphSchema(labels=records.labels, relationships=records.relationships, claims=schema.claims)
        lines = [layer.text(), "Source tables (rows) and columns (type) with example values:"]
        for t in tables.tables():
            columns = "; ".join(f"{c.name} ({c.type}) e.g. {', '.join(c.examples)}" for c in t.columns)
            lines.append(f"- {t.name} ({t.rows}): {columns}")
        self._text = "\n".join(lines)

    def run(self, arguments: SchemaArguments) -> SchemaResult:
        return SchemaResult(text=self._text)


class QueryRecords:
    """`query_records`: a typed query over the records of one label, optionally those related to one record,
    checked against the schema, compiled to Cypher and run read-only."""

    name = "query_records"
    description = (
        "Query the records of one label: list them, count them, or measure a property (sum, average, min, "
        "max), optionally per value of a property, for the records that meet every condition. Read the "
        "schema first: names must be written exactly as it gives them."
    )
    arguments = RecordQuery

    def __init__(
        self, store: CypherStore, schema: GraphSchema, plan: ConstructionPlan | None, reader: NodeReader,
        limit: int, value_chars: int,
    ):  # fmt: skip
        record_labels = {rule.label for rule in plan.nodes} if plan else set()
        records = schema.records_only(record_labels)
        self._fields = {i.label: {p.name: p for p in i.properties} for i in records.labels}
        self._keys = {rule.label: rule.unique_column for rule in plan.nodes} if plan else {}
        self._names = {rule.label: name_property(rule) for rule in plan.nodes} if plan else {}
        self._relationships = {r.type for r in records.relationships}
        self._store = store
        self._reader = reader
        self._limit = limit
        self._value_chars = value_chars

    def run(self, arguments: RecordQuery) -> QueryResult:
        fields = self._fields.get(arguments.label)
        if fields is None:
            return _refused([f"no record label {arguments.label!r}; the labels are {_listed(self._fields)}"])
        issues, anchor = self._anchor(arguments)
        issues += check_query(arguments, fields)
        if issues:
            return _refused(issues)
        rules = [{"label": label, "key": key} for label, key in self._keys.items()]
        compiled = compile_records(arguments, fields, self._keys[arguments.label], rules, anchor, self._limit)
        read = self._store.run_read(compiled.statement, compiled.parameters)
        name = self._names[arguments.label]
        return _result(compiled, read, sorted(fields), self._value_chars, arguments.offset, name)

    def _anchor(self, query: RecordQuery) -> tuple[list[str], str | None]:
        """Why `related_to` and `relationship` (a type, or a path of types) cannot be used, and the related
        record's element id."""
        path = query.path()
        if (query.related_to is None) != (not path):
            return ["related_to and relationship go together: give both or neither"], None
        if query.related_to is None:
            return [], None
        issues = []
        if len(path) > MAX_HOPS:
            issues.append(f"a path follows at most {MAX_HOPS} relationships, got {len(path)}")
        issues += [
            f"no relationship {t!r} between records; the relationships are {_listed(self._relationships)}"
            for t in dict.fromkeys(path)
            if t not in self._relationships
        ]
        start = self._reader.start(query.related_to)
        if start is None or start.kind != "thing":
            issues.append(
                f"related_to {query.related_to!r} names no record; give a record ref a tool returned"
            )
            return issues, None
        return issues, start.node_id


class QueryTables:
    """`query_tables`: a typed query over one staged source table, checked against its columns, compiled to
    SQL and run on in-memory views."""

    name = "query_tables"
    description = (
        "Query one source table: list its rows, count them, or measure a column (sum, average, min, max), "
        "optionally per value of a column, for the rows that meet every condition. Read the schema first: "
        "names must be written exactly as it gives them."
    )
    arguments = TableQuery

    def __init__(self, tables: TableStore, limit: int, value_chars: int):
        self._tables = tables
        self._fields = {t.name: {c.name: c for c in t.columns} for t in tables.tables()}
        self._limit = limit
        self._value_chars = value_chars

    def run(self, arguments: TableQuery) -> QueryResult:
        fields = self._fields.get(arguments.table)
        if fields is None:
            return _refused([f"no table {arguments.table!r}; the tables are {_listed(self._fields)}"])
        if issues := check_query(arguments, fields):
            return _refused(issues)
        compiled = compile_table(arguments, fields, self._limit)
        read = self._tables.run(compiled.statement, compiled.parameters)
        return _result(compiled, read, list(fields), self._value_chars, arguments.offset)


def _listed(names) -> str:
    """Names for a refusal: sorted, comma-separated."""
    return ", ".join(sorted(names))


def _refused(issues: list[str]) -> QueryResult:
    return QueryResult(issues=issues, note="The query was refused; correct it and ask again.")


def _result(
    compiled: Compiled,
    read: ReadResult,
    columns: list[str],
    value_chars: int,
    offset: int,
    name_column: str = "",
) -> QueryResult:
    """A run's rows as the agent reads them: values as text and cut, the total, the paging note (`offset`:
    the entries the page skipped) and the statement's id; listed records with their refs and their names
    (`name_column`, else the key the ref ends in)."""
    if read.error is not None:
        return QueryResult(issues=[read.error], note="The query failed to run.")
    query_id = _query_id(compiled)
    rows = [dict(zip(read.keys, row, strict=True)) for row in read.rows]
    if compiled.shape == "value":
        value = rows[0]["value"] if rows else None
        return QueryResult(query_id=query_id, columns=["value"], rows=[[_text(value, value_chars)]], total=1)
    total = int(rows[0].get("total", rows[0].get(total_column(), 0))) if rows else 0
    note = _note(len(rows), total, offset)
    if compiled.shape == "groups":
        values = [[_text(r["bucket"], value_chars), _text(r["value"], value_chars)] for r in rows]
        return QueryResult(query_id=query_id, columns=["group", "value"], rows=values, total=total, note=note)
    if "props" in read.keys:  # records: each row's properties and its ref
        values = [[_text(r["props"].get(c), value_chars) for c in columns] for r in rows]
        refs = [r["ref"] for r in rows]
        names = [str(r["props"].get(name_column) or r["ref"].partition(":")[2]) for r in rows]
        return QueryResult(
            query_id=query_id, columns=columns, rows=values, refs=refs, names=names, total=total, note=note
        )
    values = [[_text(r[c], value_chars) for c in columns] for r in rows]
    return QueryResult(query_id=query_id, columns=columns, rows=values, total=total, note=note)


def _note(shown: int, total: int, offset: int) -> str:
    """What the agent is told of a list's cut: which entries it sees of how many, and the offset that shows
    the next ones; nothing when the whole list is shown at once. A page past the end has no rows to read the
    total from, so its note says only that nothing is left."""
    if shown == 0:
        return f"Nothing past the first {offset}." if offset > 0 else "Nothing matches."
    last = offset + shown
    if total > last:
        return f"{offset + 1}-{last} of {total} shown; offset {last} shows the next ones"
    return f"{offset + 1}-{last} of {total} shown" if offset > 0 else ""


def _query_id(compiled: Compiled) -> str:
    """The id of a run: a hash of its statement and parameters, equal for an equal query."""
    content = json.dumps([compiled.statement, compiled.parameters], sort_keys=True, default=str)
    return "q-" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]


def _text(value: Any, chars: int) -> str:  # Any: whatever a database cell holds
    """A cell as text, cut at `chars`; an empty cell as ''."""
    text = "" if value is None else str(value)
    return text if len(text) <= chars else text[:chars] + "..."
