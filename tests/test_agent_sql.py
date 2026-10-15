"""The SQL tool of the variant `agent_sql` (R188: agent/sql_check.py, sql_tables.py, sql_tool.py) over a real
in-memory DuckDB on small staged CSVs, without Neo4j or a model: a SELECT that joins, groups, counts, uses a
named subquery or extracts with a regular expression runs, with its first rows, its total and an id; a
trailing semicolon is dropped; DuckDB's parser refuses anything but one SELECT (two statements, COPY,
ATTACH, PRAGMA, a syntax error), and the check refuses a table function, a file read as a table, another
schema and an unknown table, each with its reason; the locked connection reads no file and keeps its
settings even for a statement that passed no check, and a query past the time limit is stopped; a cut list
says how to see more; what the model reads names no evaluated dataset. R207: a cut value is named in the
note. R208: a run keeps its statement, the tables it read and their staged files, out of the model's sight."""

from pathlib import Path

import pytest

from kgbuilder.agent.sql_check import check_sql
from kgbuilder.agent.sql_tables import LockedTables
from kgbuilder.agent.sql_tool import QuerySql, SqlArguments

from .test_agent_tools import DATASET_WORDS


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "kettles.csv").write_text(
        "id,name,maker,made\nK1,Lark Kettle,Ash,2019-03-01\nK2,Wren Kettle,Ash,03/15/2020\n"
        "K3,Tern Kettle,Elm,2021-07-09\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.csv").write_text(
        "kettle,note\nK1,call 555-0101 or 555-0102\nK3,no number given\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def tool(staging: Path) -> QuerySql:
    return QuerySql(LockedTables(staging, timeout_s=5.0), limit=2, value_chars=300)


def ask(tool: QuerySql, sql: str):
    return tool.run(SqlArguments(sql=sql))


def test_a_select_that_joins_and_counts_runs_with_its_total_and_an_id(tool):
    result = ask(tool, 'SELECT k.maker, count(*) AS n FROM "shop/kettles" k JOIN notes n ON n.kettle = k.id '
                       "GROUP BY k.maker ORDER BY k.maker")  # fmt: skip
    assert (result.columns, result.rows, result.total, result.issues) == (
        ["maker", "n"],
        [["Ash", "1"], ["Elm", "1"]],
        2,
        [],
    )
    assert result.query_id.startswith("q-") and result.cites() == [result.query_id]
    assert ask(tool, 'SELECT k.maker, count(*) AS n FROM "shop/kettles" k JOIN notes n ON n.kettle = k.id '
                     "GROUP BY k.maker ORDER BY k.maker;").query_id == result.query_id  # fmt: skip


def test_a_run_keeps_its_statement_the_tables_it_read_and_their_staged_files(tool, staging):
    """R208: the SQL, its tables and rows lived only in the prompt; kept now, out of the model's sight."""
    sql = 'SELECT k.id, n.note FROM "shop/kettles" k JOIN notes n ON n.kettle = k.id ORDER BY k.id'
    result = ask(tool, sql)
    record = result.record()
    assert (record.query_id, record.tool, record.statement) == (result.query_id, "query_sql", sql)
    assert sorted(record.sources) == ["notes", "shop/kettles"]
    assert sorted(record.files) == [
        (staging / "notes.csv").as_posix(),
        (staging / "shop/kettles.csv").as_posix(),
    ]
    assert record.rows == [["K1", "call 555-0101 or 555-0102"], ["K3", "no number given"]]
    assert "statement" not in result.model_dump_json()


def test_a_named_subquery_and_a_regular_expression_are_read_as_written(tool):
    sql = (
        "WITH numbers AS (SELECT unnest(regexp_extract_all(note, '\\d{3}-\\d{4}')) AS number FROM notes) "
        "SELECT count(DISTINCT number) AS n FROM numbers"
    )
    assert ask(tool, sql).rows == [["2"]]


def test_a_cut_list_says_which_rows_are_shown_and_how_to_see_or_count_more(tool):
    result = ask(tool, 'SELECT id FROM "shop/kettles" ORDER BY id')
    assert result.rows == [["K1"], ["K2"]] and result.total == 3
    assert result.note.startswith("1-2 of 3 rows shown; use LIMIT and OFFSET")
    assert ask(tool, "SELECT * FROM notes WHERE kettle = 'K9'").note == "No row."


def test_a_cut_value_is_named_in_the_note(staging):
    """R207: a value cut at `value_chars` ended in "..." with nothing in the note saying so."""
    short = QuerySql(LockedTables(staging, timeout_s=5.0), limit=5, value_chars=8)
    result = ask(short, "SELECT kettle, note FROM notes ORDER BY kettle")
    assert result.rows == [["K1", "call 555..."], ["K3", "no numbe..."]]
    assert result.note == "values of note cut at 8 characters, ending in '...'"


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT 1; SELECT 2", "exactly one SELECT statement, not 2"),
        ("COPY notes TO 'out.csv'", "give one read-only SELECT"),
        ("ATTACH 'other.db'", "give one read-only SELECT"),
        ("PRAGMA show_tables", "give one read-only SELECT"),
        ("SELEC id FROM notes", "give one read-only SELECT"),
        ("SELECT * FROM read_csv('notes.csv')", "a table function"),
        ("SELECT * FROM 'notes.csv'", "no table 'notes.csv'"),
        ("SELECT * FROM information_schema.tables", "'tables' is not a source table"),
        ("SELECT * FROM kettles", "no table 'kettles'; the tables are notes, shop/kettles"),
    ],
)
def test_anything_but_one_select_over_the_source_tables_is_refused_with_its_reason(tool, sql, reason):
    result = ask(tool, sql)
    assert result.query_id == "" and result.note.startswith("The query was refused")
    assert any(reason in issue for issue in result.issues), result.issues


def test_the_check_accepts_a_staged_table_in_the_default_schema_and_its_own_subqueries():
    tables = {"notes", "shop/kettles"}
    assert check_sql('SELECT * FROM main."shop/kettles"', tables) == []
    assert check_sql("WITH n AS (SELECT * FROM notes) SELECT * FROM n", tables) == []


def test_the_locked_tables_read_no_file_and_keep_their_settings_even_unchecked(staging):
    tables = LockedTables(staging, timeout_s=5.0)
    read = tables.select(f"SELECT * FROM read_csv('{(staging / 'notes.csv').as_posix()}')", 5)
    assert read.error is not None and "file system operations are disabled" in read.error.lower()
    setting = tables.select("SELECT current_setting('enable_external_access') AS on", 1)
    assert setting.rows == [[False]]


def test_a_query_past_the_time_limit_is_stopped_and_said_so(staging):
    tables = LockedTables(staging, timeout_s=0.05)
    read = tables.select("SELECT count(*) FROM range(1000000000) a, range(1000) b", 1)
    assert read.error == "the query ran longer than 0.05 s and was stopped"
    assert tables.select("SELECT 1 AS one", 1).rows == [[1]]  # the connection still answers after a stop


def test_what_the_model_reads_of_the_sql_tool_names_no_evaluated_dataset():
    texts = [QuerySql.description] + [f.description or "" for f in SqlArguments.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
