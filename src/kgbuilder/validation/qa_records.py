"""Expected answers of record questions, computed from the source files by DuckDB (R73).

Role in the pipeline: a QA gold question over records carries a query (qa_gold.py `QAQuestion.sql`); the
gold-file tests run every query here and fail when the answer stored in the gold differs from the one
the data gives. Fixed decision 11 of the layered-model task: gold that code can compute is computed,
from the data, never from pipeline output.
Design: every structured source file of the corpus (`.csv`, `.json`, `.ndjson`, any folder depth) is a
DuckDB view named by its file stem, read as it lies on disk, not through the pipeline's staging, so a
staging bug cannot hide in the gold. A JSON file keeps its own shape (a wrapper object stays one row
whose list is unnested in the query). A query's first column is the answer: its distinct values for a
set, its one value for a number.
Not here: the gold model (qa_gold.py), scoring answers (qa.py).
"""

import math
from pathlib import Path

import duckdb

from ..core.errors import InvalidGoldError
from .qa_gold import QAGold, QAQuestion

STRUCTURED_SUFFIXES = (".csv", ".json", ".ndjson")
# a stored number must equal the computed one; the tolerance only absorbs floating-point dust
_NUMBER_TOLERANCE = 1e-9


def source_views(data_dir: Path) -> dict[str, Path]:
    """View name (the file stem) -> structured source file under `data_dir`.

    Raises `InvalidGoldError` when two files share a stem: a query could not say which one it means.
    """
    views: dict[str, Path] = {}
    clashes = []
    for path in sorted(p for p in Path(data_dir).rglob("*") if p.suffix in STRUCTURED_SUFFIXES):
        if path.stem in views:
            clashes.append(f"two source files are both the view {path.stem!r}: {views[path.stem]}, {path}")
        views[path.stem] = path
    if clashes:
        raise InvalidGoldError(clashes)
    return views


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection with one view per structured source file of `data_dir`."""
    con = duckdb.connect()
    for view, path in source_views(data_dir).items():
        relation = con.read_csv(str(path), header=True) if path.suffix == ".csv" else con.read_json(str(path))
        relation.create_view(view)
    return con


def computed_answer(con: duckdb.DuckDBPyConnection, question: QAQuestion) -> list[str] | float:
    """The answer the question's query gives: the distinct values of its first column as text for a set
    question (nulls dropped), or the one value of its one row for a number question.

    Raises `InvalidGoldError` for a question without a query, a query DuckDB refuses, or a number query
    that does not return exactly one numeric value.
    """
    if question.sql is None:
        raise InvalidGoldError([f"question {question.id}: has no query"])
    try:
        rows = con.sql(question.sql).fetchall()
    except duckdb.Error as e:
        raise InvalidGoldError([f"question {question.id}: the query failed: {e}"]) from e
    if question.expected.kind == "entities":
        return sorted({str(row[0]) for row in rows if row[0] is not None})
    if len(rows) != 1 or not isinstance(rows[0][0], int | float) or isinstance(rows[0][0], bool):
        raise InvalidGoldError([f"question {question.id}: a number query must return one number, got {rows}"])
    return float(rows[0][0])


def record_answer_issues(con: duckdb.DuckDBPyConnection, question: QAQuestion) -> list[str]:
    """Why the answer stored in the gold differs from the one its query computes; empty when they agree.

    A set agrees when the expected names (not their aliases: the gold names the record as the data does)
    are exactly the query's values, compared as text after stripping. A number agrees when it is equal.
    """
    try:
        computed = computed_answer(con, question)
    except InvalidGoldError as e:
        return e.issues
    expected = question.expected
    if isinstance(computed, list):
        stored = sorted(e.name.strip() for e in expected.entities or [])
        if stored != sorted(value.strip() for value in computed):
            return [f"question {question.id}: the gold names {stored}, the data gives {computed}"]
        return []
    if expected.number is None or not math.isclose(
        computed, expected.number, rel_tol=0.0, abs_tol=_NUMBER_TOLERANCE
    ):
        return [f"question {question.id}: the gold says {expected.number}, the data gives {computed}"]
    return []


def check_record_answers(gold: QAGold, data_dir: Path) -> None:
    """Raise `InvalidGoldError` unless every question with a query stores the answer its query computes
    over the source files in `data_dir`."""
    con = connect(data_dir)
    try:
        issues = [
            issue for q in gold.questions if q.sql is not None for issue in record_answer_issues(con, q)
        ]
    finally:
        con.close()
    if issues:
        raise InvalidGoldError(issues)
