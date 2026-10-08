"""Test helpers for the PostgreSQL source (R114): a test schema, and CSV files loaded into it as TEXT tables.

Every column is TEXT and the file is copied in as it is, so staging the schema back must give the CSV the
profiler would have read from the file; the equivalence tests in test_postgres_source.py rest on that.
Needs the tests' own PostgreSQL (`postgres-test` in docker-compose.yml); the `pg_schema` fixture in
conftest.py hands out the schema.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

# the `postgres-test` service of docker-compose.yml: port 5435, its own fixed credentials; 127.0.0.1, not
# localhost, so a down server costs one connect timeout, not one per address (IPv6 and IPv4)
TEST_POSTGRES_URL = "postgresql://kgbuilder:password123@127.0.0.1:5435/kgbuilder"


@dataclass(frozen=True)
class PgSchema:
    """A schema of the test PostgreSQL that exists only for one test."""

    conn: psycopg.Connection  # autocommit, so every statement is visible to the adapter's own connections
    name: str
    url: str = TEST_POSTGRES_URL


def load_csv_dir(conn: psycopg.Connection, schema: str, data_dir: Path) -> list[str]:
    """Create one TEXT table per top-level CSV of `data_dir`, named by its file name, and copy the rows in.

    Returns the table names. An empty unquoted cell becomes NULL, the way COPY reads CSV.
    """
    names = []
    for path in sorted(data_dir.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        table = sql.Identifier(schema, path.stem)
        columns = sql.SQL(", ").join(sql.SQL("{} text").format(sql.Identifier(c)) for c in header)
        conn.execute(sql.SQL("CREATE TABLE {} ({})").format(table, columns))
        with (
            conn.cursor() as cur,
            cur.copy(sql.SQL("COPY {} FROM STDIN (FORMAT csv, HEADER)").format(table)) as copy,
        ):
            copy.write(path.read_bytes())
        names.append(path.stem)
    return names
