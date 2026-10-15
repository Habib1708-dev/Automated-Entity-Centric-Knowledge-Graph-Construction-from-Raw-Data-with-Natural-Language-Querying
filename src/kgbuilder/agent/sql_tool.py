"""The agent's SQL tool (R188): one read-only SELECT the model writes over the staged source tables, checked
by DuckDB's parser and run on a locked connection.

Role in the pipeline: the experimental variant `agent_sql` of plan R183-R191 (pipeline/qa_systems.py) has it
in place of `query_tables`; the agent itself keeps the typed query. R180-R182 left counts the typed query
could not express (R182's LB11 counted 13 rows by eye, HE06 the phone numbers inside a text column), and
each earlier gap closed one operator at a time (R139-R175); SQL expresses joins, anti-joins, date parsing
and pattern extraction directly.
Design: Strategy, one class per tool (tool.py), as `query_tables`. The LLM proposes the statement; code
refuses it unless sql_check.py accepts it (one SELECT, staged tables and its own subqueries only), runs it
on `LockedTables` with a time limit, and returns the first `limit` rows with the total, values cut as the
typed tools cut them, and an id made from the statement, which an answer cites for a count. A refusal and a
failed run are answered with their reasons, as the typed tools answer theirs.
Not here: the check (sql_check.py), the locked store (sql_tables.py), the schema the model reads first
(structured_tools.py `schema`, unchanged).
"""

from pydantic import BaseModel, Field

from .sql_check import check_sql, tables_read
from .sql_tables import LockedTables, SqlRead
from .structured_query import Compiled
from .structured_tools import (  # the typed tools' rendering and provenance
    Origin,
    QueryResult,
    _query_id,
    _refused,
    cells,
    joined,
    traced,
)

# What the tool's text tells the model. "count in SQL" is R161's rule for the typed tools (a count read off
# listed rows was wrong in LB11), said here for the statement the model writes; quoting names is what a
# staged table in a folder ("site/staff") and a column with a dot ("product.productYear") need.
DESCRIPTION = (
    "Run one read-only SQL SELECT (DuckDB dialect) over the source tables the schema lists: filter, join "
    "tables, group, count, compare or parse dates, or extract values from text with regular expressions. "
    "Write a table or column name in double quotes when it holds anything but letters, digits and "
    "underscores. Take counts and sums in the SELECT itself, never by counting the rows shown."
)


class SqlArguments(BaseModel):
    sql: str = Field(description="One SELECT statement over the source tables, without a trailing semicolon.")


class QuerySql:
    """`query_sql`: one checked SELECT over the locked source tables, its first `limit` rows and its total."""

    name = "query_sql"
    description = DESCRIPTION
    arguments = SqlArguments

    def __init__(self, tables: LockedTables, limit: int, value_chars: int):
        self._tables = tables
        self._names = {t.name for t in tables.tables()}
        self._limit = limit
        self._value_chars = value_chars

    def run(self, arguments: SqlArguments) -> QueryResult:
        # a trailing semicolon ends the statement the count wraps, so it is dropped, not refused
        statement = arguments.sql.strip().rstrip(";").strip()
        if issues := check_sql(statement, self._names):
            return _refused(issues)
        read = self._tables.select(statement, self._limit)
        if read.error is not None:
            return QueryResult(issues=[read.error], note="The query failed to run.")
        rows, cut = cells(read.keys, read.rows, self._value_chars)
        compiled = Compiled(statement=statement, parameters={}, shape="rows")
        query_id = _query_id(compiled)
        result = QueryResult(
            query_id=query_id,
            columns=read.keys,
            rows=rows,
            total=read.total,
            note=joined(self._note(len(rows), read), cut),
        )
        # R208: the tables the statement reads, their staged files, the rows uncut; out of the model's sight
        read_tables = tables_read(statement, self._names)
        files = [self._tables.source_file(t) for t in read_tables]
        origin = Origin(tool=self.name, sources=read_tables, files=files)
        return result.traced(traced(query_id, compiled, origin, read.keys, read.rows, [], read.total))

    def _note(self, shown: int, read: SqlRead) -> str:
        """Said by code: nothing found, or which rows are shown of how many and how to see or count more."""
        if read.total == 0:
            return "No row."
        if shown == read.total:
            return ""
        more = "use LIMIT and OFFSET in the statement for the next ones, or count and aggregate in it"
        return f"1-{shown} of {read.total} rows shown; {more}"
