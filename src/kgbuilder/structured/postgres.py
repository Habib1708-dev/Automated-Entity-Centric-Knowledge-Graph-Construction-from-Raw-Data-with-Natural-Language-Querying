"""PostgreSQL as a source of tables (R114): the tables and views of one schema, exported as CSV, and the
primary and foreign keys the schema declares (R115).

Role in the pipeline: `kg profile` (ProfileStage) hands it to `stage_structured`, which writes every table to
`out/staging/<table>.csv`. From there on a table from PostgreSQL looks exactly like a CSV file, so the
profiler, the plan, the importer and record documents work unchanged; the declared keys only add marks to
the profile.
Design: Adapter for the `TableSource` port of staging.py, and the only module that imports psycopg. It reads
in read-only transactions and never writes to the database. One connection per call, closed by its `with`.
Not here: what gets staged and the skip report (staging.py); column types (the profiler infers them from
the CSV with DuckDB, the same way it does for files).
"""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from ..core.errors import DataSourceError
from .staging import DeclaredForeignKey, DeclaredKeys

# Session settings that make the CSV text the same on every server: timestamps in UTC, dates as ISO 8601
# ("2024-01-05"), whatever the server's or the user's defaults are.
_SESSION = "-c TimeZone=UTC -c DateStyle=ISO"
# Seconds before an unreachable server is reported, instead of hanging `kg profile`.
_CONNECT_TIMEOUT_S = 10
# information_schema.columns.data_type of an array column. Postgres writes arrays as {a,b}, which is not
# JSON; to_json makes them ["a","b"], the form json_to_csv gives a JSON list, so a list reads the same
# whichever input it came from.
_ARRAY = "ARRAY"

# Every primary key ('p') and foreign key ('f') of the schema's tables, with its columns in key order and,
# for a foreign key, the table, schema and columns it refers to. pg_constraint rather than
# information_schema: the latter cannot pair the columns of a composite foreign key reliably.
_CONSTRAINTS = """
SELECT c.contype::text, c.conname, src.relname, ref.relname, refns.nspname,
       ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY AS k(num, pos)
             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num ORDER BY k.pos),
       ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY AS k(num, pos)
             JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num ORDER BY k.pos)
FROM pg_constraint c
JOIN pg_class src ON src.oid = c.conrelid
JOIN pg_namespace ns ON ns.oid = src.relnamespace
LEFT JOIN pg_class ref ON ref.oid = c.confrelid
LEFT JOIN pg_namespace refns ON refns.oid = ref.relnamespace
WHERE ns.nspname = %s AND c.contype IN ('p', 'f')
ORDER BY src.relname, c.conname
"""

# The primary key columns of one table, in key order; none for a view or a table without a key.
_PRIMARY_KEY = """
SELECT a.attname
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace ns ON ns.oid = t.relnamespace
JOIN unnest(c.conkey) WITH ORDINALITY AS k(num, pos) ON true
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num
WHERE c.contype = 'p' AND ns.nspname = %s AND t.relname = %s
ORDER BY k.pos
"""


class PostgresTables:
    """The base tables and views of one schema of a PostgreSQL database.

    Views are included on purpose: they let a user choose, filter or join what the graph sees in SQL,
    without copying data. Every method raises `DataSourceError` when the database cannot be read.
    """

    def __init__(self, url: str, schema: str):
        self._url = url
        self._schema = schema

    def describe(self) -> str:
        """Server, database and schema, without the user or the password of the URL."""
        info = conninfo_to_dict(self._url)
        server = f"{info.get('host', 'localhost')}:{info.get('port', '5432')}/{info.get('dbname', '')}"
        return f"postgres {server} schema {self._schema}"

    def tables(self) -> list[str]:
        """The schema's tables and views, sorted by name. A missing or empty schema is an error, because
        an empty profile would make the plan stage skip without a word."""
        rows = self._fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
            (self._schema,),
        )
        if not rows:
            raise DataSourceError(f"{self.describe()}: the schema does not exist or holds no tables or views")
        return [name for (name,) in rows]

    def write_csv(self, table: str, dest: Path) -> None:
        """Stream the table to `dest` as UTF-8 CSV with a header row, columns in table order.

        Rows are in primary key order (R115), so a rerun writes the same file; a view or a table without
        a primary key has no stable order, and its rows come as the database returns them.
        """
        columns = self._fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (self._schema, table),
        )
        key = [name for (name,) in self._fetch(_PRIMARY_KEY, (self._schema, table))]
        select = sql.SQL(", ").join(_column(name, data_type) for name, data_type in columns)
        order = sql.SQL("")
        if key:
            order = sql.SQL(" ORDER BY {}").format(sql.SQL(", ").join(map(sql.Identifier, key)))
        query = sql.SQL("COPY (SELECT {} FROM {}{}) TO STDOUT (FORMAT csv, HEADER)").format(
            select, sql.Identifier(self._schema, table), order
        )
        try:
            with self._connect() as conn, conn.cursor() as cur, cur.copy(query) as copy, dest.open("wb") as f:
                for block in copy:
                    f.write(block)
        except psycopg.Error as e:
            raise DataSourceError(f"{self.describe()}: cannot read table '{table}': {e}") from e

    def declared_keys(self) -> DeclaredKeys:
        """The schema's single-column primary keys and its single-column foreign keys within the schema.

        A composite key, or a foreign key to a table of another schema (which is not staged), cannot be
        marked on one column of a staged file; it is listed in `skipped` with the reason.
        """
        keys = DeclaredKeys()
        for kind, name, table, ref_table, ref_schema, columns, ref_columns in self._fetch(
            _CONSTRAINTS, (self._schema,)
        ):
            if len(columns) != 1:
                keys.skipped.append(f"{table}.{name}: {len(columns)} columns")
            elif kind == "p":
                keys.primary_keys[table] = columns[0]
            elif ref_schema != self._schema:
                keys.skipped.append(f"{table}.{name}: refers to schema {ref_schema}")
            else:
                fk = DeclaredForeignKey(
                    from_table=table, from_column=columns[0], to_table=ref_table, to_column=ref_columns[0]
                )
                keys.foreign_keys.append(fk)
        return keys

    def _fetch(self, query: str, params: tuple[str, ...]) -> list[tuple]:
        """The rows of one catalog query."""
        try:
            with self._connect() as conn:
                return conn.execute(query, params).fetchall()
        except psycopg.Error as e:
            # psycopg's messages name the host, port and user, never the password
            raise DataSourceError(f"{self.describe()}: {e}") from e

    def _connect(self) -> psycopg.Connection:
        """A read-only connection whose CSV output is UTF-8 and independent of the server's defaults."""
        conn = psycopg.connect(
            self._url, options=_SESSION, client_encoding="utf8", connect_timeout=_CONNECT_TIMEOUT_S
        )
        conn.read_only = True
        return conn


def _column(name: str, data_type: str) -> sql.Composable:
    """The select expression of one column: arrays as JSON text, everything else in Postgres' text form."""
    column = sql.Identifier(name)
    if data_type == _ARRAY:
        return sql.SQL("to_json({}) AS {}").format(column, column)
    return column
