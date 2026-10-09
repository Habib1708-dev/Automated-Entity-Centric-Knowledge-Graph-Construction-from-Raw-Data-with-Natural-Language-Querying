"""Deterministic CSV profiling: the facts an LLM should be given rather than asked to guess.

Role in the pipeline: `kg profile`. Reads the staged CSVs with DuckDB and computes, exactly, column
types, uniqueness and foreign key candidates (value inclusion between columns). The profile feeds the
plan proposer prompt and the plan validator. When the tables came from a database, the keys it declares
(staged as `DECLARED_KEYS_FILE`, R115) are marked in the profile; the measured facts stay as they are.
Not here: any decision about what becomes a node or a relationship.
"""

from pathlib import Path

import duckdb
from pydantic import BaseModel, Field

from .staging import DECLARED_KEYS_FILE, DeclaredForeignKey, DeclaredKeys

# types that can plausibly hold an identifier
_KEY_TYPES = ("VARCHAR", "BIGINT", "INTEGER", "SMALLINT", "HUGEINT", "UUID")

# A column is prose when its values read like running text rather than a name, a code or a tagline:
# long on average AND usually more than one sentence. Both bounds together keep a one-sentence product
# tagline out while a multi-sentence report summary passes. Language-level rules, nothing about a domain.
_PROSE_MIN_AVG_CHARS = 120
_PROSE_MIN_SENTENCE_RATIO = 0.5
# an end-of-sentence mark with more text after it ("... failed. The manufacturer ..."): a value that
# matches has at least two sentences. A single trailing period does not match.
_SENTENCE_BREAK = r"[.!?]\s+\S"


def _unset(value: object) -> bool:
    """A declared-key mark that is not set is left out of the JSON (R115): the profile of files, and so
    the plan prompt built from it, stays byte for byte what it was before declared keys existed."""
    return value is None


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_count: int
    distinct_count: int
    is_unique: bool  # no nulls and no duplicates: usable as a node key
    samples: list[str]
    avg_chars: float = 0.0  # average length of the non-null values; 0.0 for non-text columns
    multi_sentence_ratio: float = 0.0  # share of non-null values with more than one sentence
    is_prose: bool = False  # running text worth reading as a document, not only storing as a property
    # True when the database the table came from declares this column its primary key (R115); else unset
    primary_key: bool | None = Field(default=None, exclude_if=_unset)


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
    # True when the database the tables came from declares this foreign key (R115); else unset
    declared: bool | None = Field(default=None, exclude_if=_unset)


class DataProfile(BaseModel):
    files: list[FileProfile]
    foreign_keys: list[ForeignKeyCandidate]

    def file(self, name: str) -> FileProfile | None:
        return next((f for f in self.files if f.file == name), None)

    def has_declared_keys(self) -> bool:
        """Whether any key in the profile is declared by a database (R115); never for files."""
        return any(fk.declared for fk in self.foreign_keys) or any(
            c.primary_key for f in self.files for c in f.columns
        )


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
        avg_chars, sentence_ratio = 0.0, 0.0
        if dtype == "VARCHAR":  # only text can be prose; numbers and dates are stored typed anyway
            avg_chars, sentence_ratio = con.sql(
                f"SELECT coalesce(avg(length({col})), 0), "
                f"coalesce(avg(CASE WHEN regexp_matches({col}, '{_SENTENCE_BREAK}') "
                f"THEN 1.0 ELSE 0.0 END), 0) FROM {view} WHERE {col} IS NOT NULL"
            ).fetchone()
        columns.append(
            ColumnProfile(
                name=name,
                dtype=dtype,
                null_count=nulls,
                distinct_count=distinct,
                is_unique=row_count > 0 and nulls == 0 and distinct == row_count,
                samples=[s[0] for s in samples],
                avg_chars=round(float(avg_chars), 1),
                multi_sentence_ratio=round(float(sentence_ratio), 4),
                is_prose=avg_chars >= _PROSE_MIN_AVG_CHARS and sentence_ratio >= _PROSE_MIN_SENTENCE_RATIO,
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


def _with_declared(
    con: duckdb.DuckDBPyConnection,
    candidates: list[ForeignKeyCandidate],
    declared: list[DeclaredForeignKey],
    files: list[FileProfile],
    views: dict[str, str],
) -> list[ForeignKeyCandidate]:
    """`candidates` with the declared foreign keys marked, and added where the search missed them.

    The search misses a declared key whose values are not all present (a dangling reference), whose
    types were inferred differently, or whose target holds nulls; the database still declares it, so it
    is listed with the inclusion measured as text. Declared keys sort first. Without declared keys the
    candidates are returned as they came.
    """
    if not declared:
        return candidates
    found = {(c.from_file, c.from_column, c.to_file, c.to_column): c for c in candidates}
    columns = {(f.file, c.name): c for f in files for c in f.columns}
    for fk in declared:
        source = columns.get((fk.from_table, fk.from_column))
        if (fk.from_table, fk.from_column, fk.to_table, fk.to_column) in found:
            found[(fk.from_table, fk.from_column, fk.to_table, fk.to_column)].declared = True
        elif source is not None and (fk.to_table, fk.to_column) in columns:
            candidates.append(
                ForeignKeyCandidate(
                    from_file=fk.from_table,
                    from_column=fk.from_column,
                    to_file=fk.to_table,
                    to_column=fk.to_column,
                    inclusion=_text_inclusion(con, fk, views, source.distinct_count),
                    name_match=_name_match(fk.from_column, fk.to_column, fk.to_table),
                    declared=True,
                )
            )
    return sorted(candidates, key=lambda c: (not c.declared, not c.name_match, -c.inclusion))


def _text_inclusion(
    con: duckdb.DuckDBPyConnection, fk: DeclaredForeignKey, views: dict[str, str], distinct: int
) -> float:
    """Share of the distinct from-values found in the to-column, compared as text: the two columns of a
    declared key may have been inferred as different types ("7" read as BIGINT, "007" as VARCHAR)."""
    if not distinct:
        return 0.0
    f, t = quote_ident(fk.from_column), quote_ident(fk.to_column)
    hits = con.sql(
        f"SELECT count(DISTINCT a.{f}) FROM {views[fk.from_table]} a "
        f"WHERE CAST(a.{f} AS VARCHAR) IN (SELECT CAST({t} AS VARCHAR) FROM {views[fk.to_table]})"
    ).fetchone()[0]
    return round(hits / distinct, 4)


def _read_declared(data_dir: Path) -> DeclaredKeys:
    """The declared keys staging left next to the tables; none for files."""
    path = data_dir / DECLARED_KEYS_FILE
    return (
        DeclaredKeys.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else DeclaredKeys()
    )


def profile_directory(data_dir: Path, min_inclusion: float = 0.95) -> DataProfile:
    """Profile every CSV under `data_dir` and detect foreign key candidates between them.

    When staging left the keys a database declares (R115), their columns are marked `primary_key` and
    their foreign keys `declared`.
    """
    data_dir = Path(data_dir)
    declared = _read_declared(data_dir)
    con = duckdb.connect()
    files, views = [], {}
    for i, path in enumerate(sorted(data_dir.rglob("*.csv"))):
        profile = _profile_file(con, data_dir, path, f"t{i}")
        key = profile.column(declared.primary_keys.get(profile.file, ""))
        if key is not None:
            key.primary_key = True
        files.append(profile)
        views[profile.file] = f"t{i}"
    candidates = _foreign_keys(con, files, views, min_inclusion)
    return DataProfile(
        files=files, foreign_keys=_with_declared(con, candidates, declared.foreign_keys, files, views)
    )
