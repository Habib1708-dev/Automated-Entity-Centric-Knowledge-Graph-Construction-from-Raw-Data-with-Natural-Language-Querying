"""The source tables locked for SQL the model writes (R188): loaded into memory, then cut off from the file
system and from any change of settings, with a time limit on every query.

Role in the pipeline: the store `query_sql` (sql_tool.py) runs a checked SELECT on, in the experimental
variant `agent_sql` of plan R183-R191; the agent's own `query_tables` keeps reading `DuckDBTables`.
Design: a subclass of `DuckDBTables` (Template Method: `_load` makes each staged CSV a table instead of a
view, since a view reads its file again at each query and a locked connection may read no file). After
loading, `enable_external_access` is turned off (no file, no network, no extension) and
`lock_configuration` on, so no statement can turn it back on (both checked when plan R183-R191 was
written: a file read is refused and the setting cannot be changed). This is the second line of defence:
sql_check.py refuses anything but one SELECT over the staged tables before a statement gets here. A query
past `timeout_s` is interrupted from a timer started under the lock, so it can only stop the query it was
started for.
Not here: deciding what may run (sql_check.py), the tool and its result (sql_tool.py).
"""

import threading
from pathlib import Path

import duckdb
from pydantic import BaseModel

from .tables import DuckDBTables


class SqlRead(BaseModel):
    """A SELECT's first rows, its column names and its total row count, or why it did not run."""

    keys: list[str] = []
    rows: list[list[object]] = []  # object: whatever a cell holds (a number, a text, a date, None)
    total: int = 0
    error: str | None = None


class LockedTables(DuckDBTables):
    """The staged CSVs as in-memory tables on a connection that reads no file and changes no setting."""

    def __init__(self, staging: Path, timeout_s: float):
        self._timeout_s = timeout_s
        super().__init__(staging)
        self._con.execute("SET enable_external_access = false")
        self._con.execute("SET lock_configuration = true")

    def _load(self, name: str, path: Path) -> None:
        """One staged CSV as a table, read once, so nothing is read from a file after the lock."""
        self._con.read_csv(str(path), header=True).create(name)

    def select(self, statement: str, limit: int) -> SqlRead:
        """The first `limit` rows of `statement` (a SELECT sql_check.py accepted) and how many rows it gives
        in all; `error` set instead when it fails or runs past the time limit."""
        with self._lock:
            timer = threading.Timer(self._timeout_s, self._con.interrupt)
            timer.start()
            try:
                total = self._con.execute(f"SELECT count(*) FROM ({statement}) AS counted").fetchone()[0]
                cursor = self._con.execute(statement)
                keys = [d[0] for d in cursor.description]
                rows = [list(row) for row in cursor.fetchmany(limit)]
                return SqlRead(keys=keys, rows=rows, total=total)
            except duckdb.InterruptException:  # the timer's interrupt: said so, not as a database error
                return SqlRead(error=f"the query ran longer than {self._timeout_s:g} s and was stopped")
            except duckdb.Error as e:  # a runtime error (a cast, a type, a name): the agent reads it
                return SqlRead(error=str(e))
            finally:
                timer.cancel()
