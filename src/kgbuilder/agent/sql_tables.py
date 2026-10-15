"""The source tables for SQL the model writes (R188): the locked tables of `DuckDBTables`, with a time limit
on every query and a count of the rows a query gives in all.

Role in the pipeline: the store `query_sql` (sql_tool.py) runs a checked SELECT on, in the experimental
variant `agent_sql` of plan R183-R191; the agent's own `query_tables` reads `DuckDBTables`.
Design: a subclass of `DuckDBTables`, which loads the staged CSVs as in-memory tables, then turns
`enable_external_access` off and `lock_configuration` on (R188 here; moved into `DuckDBTables` in R203, so
`query_tables` is locked too): a file read is refused and the setting cannot be changed. This is the second
line of defence: sql_check.py refuses anything but one SELECT over the staged tables before a statement gets
here. A query past `timeout_s` is interrupted from a timer started under the lock, so it can only stop the
query it was started for.
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
    """The locked staged tables, with a time limit on every query the model writes."""

    def __init__(self, staging: Path, timeout_s: float):
        self._timeout_s = timeout_s
        super().__init__(staging)

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
