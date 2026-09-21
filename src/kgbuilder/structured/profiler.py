"""Deterministic CSV profiling: the facts an LLM should be given rather than asked to guess.

Role in the pipeline: `kg profile`. Reads the staged CSVs with DuckDB and computes, exactly, column
types, uniqueness and foreign key candidates (value inclusion between columns). The profile feeds the
plan proposer prompt and the plan validator.
Not here: any decision about what becomes a node or a relationship.
"""

from pathlib import Path

import duckdb
from pydantic import BaseModel

# types that can plausibly hold an identifier
_KEY_TYPES = ("VARCHAR", "BIGINT", "INTEGER", "SMALLINT", "HUGEINT", "UUID")


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int
    distinct_count: int
    is_unique: bool  # no nulls and no duplicates: usable as a node key
    samples: list[str]


class FileProfile(BaseModel):
    file: str
    row_count: int
    columns: list[ColumnProfile]

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


class ForeignKeyCandidate(BaseModel):
    from_file: str
    from_column: str
    to_file: str
    to_column: str
    inclusion: float  # share of distinct from-values that exist in the to-column
    name_match: bool


class DataProfile(BaseModel):
    files: list[FileProfile]
    foreign_keys: list[ForeignKeyCandidate]

    def file(self, name: str) -> FileProfile | None:
        return next((f for f in self.files if f.file == name), None)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_csv(con: duckdb.DuckDBPyConnection, path: Path) -> duckdb.DuckDBPyRelation:
    return con.read_csv(str(path), header=True)


def _profile_file(con: duckdb.DuckDBPyConnection, data_dir: Path, path: Path, view: str) -> FileProfile:
    read_csv(con, path).create_view(view)
    row_count = con.sql(f"SELECT count(*) FROM {view}").fetchone()[0]
    columns = []
    for name, dtype, *_ in con.sql(f"DESCRIBE {view}").fetchall():
        col = quote_ident(name)
        nulls, distinct = con.sql(
            f"SELECT count(*) - count({col}), count(DISTINCT {col}) FROM {view}"
        ).fetchone()
        samples = con.sql(
            f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM {view} WHERE {col} IS NOT NULL LIMIT 5"
        ).fetchall()
        columns.append(
            ColumnProfile(
                name=name,
                dtype=dtype,
                null_count=nulls,
                distinct_count=distinct,
                is_unique=row_count > 0 and nulls == 0 and distinct == row_count,
                samples=[s[0] for s in samples],
            )
        )
    return FileProfile(file=path.relative_to(data_dir).as_posix(), row_count=row_count, columns=columns)


def _name_match(from_column: str, to_column: str, to_file: str) -> bool:
    a, b = from_column.lower(), to_column.lower()
    stem = Path(to_file).stem.lower().rstrip("s")
    return a == b or stem in a


def _foreign_keys(
    con: duckdb.DuckDBPyConnection, files: list[FileProfile], views: dict[str, str], min_inclusion: float
) -> list[ForeignKeyCandidate]:
    candidates = []
    keys = [(f, c) for f in files for c in f.columns if c.is_unique and c.dtype in _KEY_TYPES]
    for to_file, to_col in keys:
        for from_file in files:
            for from_col in from_file.columns:
                if from_file.file == to_file.file and from_col.name == to_col.name:
                    continue
                if from_col.dtype != to_col.dtype or from_col.distinct_count == 0:
                    continue
                f, t = quote_ident(from_col.name), quote_ident(to_col.name)
                found = con.sql(
                    f"SELECT count(DISTINCT a.{f}) FROM {views[from_file.file]} a "
                    f"WHERE a.{f} IN (SELECT {t} FROM {views[to_file.file]})"
                ).fetchone()[0]
                inclusion = found / from_col.distinct_count
                if inclusion >= min_inclusion:
                    candidates.append(
                        ForeignKeyCandidate(
                            from_file=from_file.file,
                            from_column=from_col.name,
                            to_file=to_file.file,
                            to_column=to_col.name,
                            inclusion=round(inclusion, 4),
                            name_match=_name_match(from_col.name, to_col.name, to_file.file),
                        )
                    )
    # small integer columns (quantities, ratings) are often included in id ranges by accident
    candidates.sort(key=lambda c: (not c.name_match, -c.inclusion))
    return candidates


def profile_directory(data_dir: Path, min_inclusion: float = 0.95) -> DataProfile:
    """Profile every CSV under `data_dir` and detect foreign key candidates between them."""
    data_dir = Path(data_dir)
    con = duckdb.connect()
    files, views = [], {}
    for i, path in enumerate(sorted(data_dir.rglob("*.csv"))):
        profile = _profile_file(con, data_dir, path, f"t{i}")
        files.append(profile)
        views[profile.file] = f"t{i}"
    return DataProfile(files=files, foreign_keys=_foreign_keys(con, files, views, min_inclusion))
