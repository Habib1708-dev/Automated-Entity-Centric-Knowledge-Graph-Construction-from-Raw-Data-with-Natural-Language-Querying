"""The source tables the agent's `query_tables` reads (R134): their columns, and a read of one compiled query.

Role in the pipeline: `kg build` stages every structured source of a dataset as CSV in the build's staging
folder (structured/staging.py: files, JSON flattened, PostgreSQL tables written out), so one reader serves
every source. The composition root (R135) opens `DuckDBTables` on that folder; `schema` and `query_tables`
(structured_tools.py) depend on the `TableStore` protocol and are tested with it on small CSVs.
Design: Adapter over an in-memory DuckDB with one view per staged CSV, named by its file stem, the name the
profile and the plan use. Column types are mapped to R74's type names (INTEGER, FLOAT, STRING, DATE,
BOOLEAN), so one value check (structured_query.py) serves records and tables. Only statements compiled by
code (table_query.py) are run, all of them SELECTs over views, so nothing can write; a failed run is
reported, not raised, for the agent to read. The connection is in memory and lives as long as the system
that reads it (one `kg qa` or `kg ask`); a lock lets the parallel questions of `kg qa` share it.
Not here: compiling (table_query.py), the record layer (record_query.py).
"""

import threading
from pathlib import Path
from typing import Any, Protocol

import duckdb
from pydantic import BaseModel

from ..query.graph_schema import PropertyInfo
from ..query.graph_store import ReadResult
from ..structured.profiler import quote_ident

_EXAMPLES = 3  # example values per column: enough to show a format, as the graph schema shows
_EXAMPLE_CHARS = 60  # an example longer than this is cut: it shows the format, not the content


class TableInfo(BaseModel):
    """A source table: its name, its row count and its columns with their types and examples."""

    name: str
    rows: int
    columns: list[PropertyInfo]


class TableStore(Protocol):
    """What the structured tools read from the source tables."""

    def tables(self) -> list[TableInfo]:
        """Every table, by name."""
        ...

    def run(self, statement: str, parameters: dict[str, Any]) -> ReadResult:
        """The rows of a compiled SELECT; `error` set instead when it fails."""
        ...


class DuckDBTables:
    """`TableStore` over the staged CSVs of one build, read once into in-memory views."""

    def __init__(self, staging: Path):
        self._con = duckdb.connect()
        self._lock = threading.Lock()  # `kg qa` answers questions in parallel; one connection runs one query
        for path in sorted(Path(staging).glob("*.csv")):
            self._con.read_csv(str(path), header=True).create_view(path.stem)
        self._tables = [self._describe(path.stem) for path in sorted(Path(staging).glob("*.csv"))]

    def tables(self) -> list[TableInfo]:
        return self._tables

    def run(self, statement: str, parameters: dict[str, Any]) -> ReadResult:
        try:
            with self._lock:
                result = self._con.execute(statement, parameters)
                keys = [d[0] for d in result.description]
                rows = [list(row) for row in result.fetchall()]
            return ReadResult(rows=rows, keys=keys)
        except duckdb.Error as e:  # a runtime error (a cast, a type): the agent reads it as the result
            return ReadResult(error=str(e))

    def close(self) -> None:
        self._con.close()

    def _describe(self, table: str) -> TableInfo:
        """A view's row count and columns, each with its type and a few distinct values."""
        name = quote_ident(table)
        rows = self._con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        columns = []
        for column, kind, *_ in self._con.execute(f"DESCRIBE {name}").fetchall():
            col = quote_ident(column)
            values = self._con.execute(
                f"SELECT DISTINCT {col} FROM {name} WHERE {col} IS NOT NULL ORDER BY {col} LIMIT {_EXAMPLES}"
            ).fetchall()
            examples = [_literal(v[0]) for v in values]
            columns.append(PropertyInfo(name=column, type=_type_name(kind), examples=examples))
        return TableInfo(name=table, rows=rows, columns=columns)


def _type_name(duckdb_type: str) -> str:
    """R74's name for a DuckDB column type: the integer and decimal families, a date, a timestamp, a
    boolean, and a text for everything else."""
    kind = duckdb_type.upper()
    if kind in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                "UBIGINT"):  # fmt: skip
        return "INTEGER"
    if kind in ("FLOAT", "DOUBLE", "REAL") or kind.startswith("DECIMAL"):
        return "FLOAT"
    if kind == "DATE":
        return "DATE"
    if kind.startswith("TIMESTAMP"):
        return "LOCAL DATETIME"
    if kind == "BOOLEAN":
        return "BOOLEAN"
    return "STRING"


def _literal(value: object) -> str:
    """An example value as the schema shows it: a text quoted and cut, anything else as written."""
    if isinstance(value, str):
        text = value if len(value) <= _EXAMPLE_CHARS else value[:_EXAMPLE_CHARS] + "..."
        return "'" + text.replace("'", "\\'") + "'"
    return str(value)
