"""Stage structured inputs: CSVs are copied, tabular JSON is converted to CSV, database tables are exported.

Role in the pipeline: first step of `kg profile`. Afterwards the profiler and the importer see one
uniform format (CSV files under `out/staging`), whatever the user supplied.
Design: nothing is dropped silently; every file that looked structured but could not be staged is
reported in `StagingReport.skipped` with the reason. A database is a `TableSource` (R114), a port that the
composition root fills with an adapter (structured/postgres.py), so this module never talks to a database.
Not here: text documents (text/documents.py) and profiling (profiler.py).
"""

import csv
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..core.errors import KgBuilderError

log = logging.getLogger(__name__)

_JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
# Written into every staging dir we create. Only a dir carrying it may be wiped on the next run, so a
# mistyped --out can never delete a user's folder.
_MARKER = ".kgbuilder-staging"
# A table name that is safe as a file name on every OS: letters, digits, "_", " ", "." and "-", starting with
# a letter, digit or "_" ("order_items", "Order Items 2024"). A database may name a table "a/b" or "..",
# which would write outside the staging dir.
_FILE_SAFE = re.compile(r"\w[\w .-]*")


class TableSource(Protocol):
    """Where the tables come from when they are not files in the data dir (R114): a database schema.

    Implemented by `PostgresTables` (structured/postgres.py); tests pass a fake. Every method may raise
    `DataSourceError`.
    """

    def describe(self) -> str:
        """A name for logs, params and skip reasons ("postgres localhost:5434/kgbuilder schema shop").
        Never contains a password."""
        ...

    def tables(self) -> list[str]:
        """The names of the tables to stage, sorted."""
        ...

    def write_csv(self, table: str, dest: Path) -> None:
        """Write one table to `dest` as UTF-8 CSV with a header row."""
        ...


class SkippedFile(BaseModel):
    file: str
    reason: str


class StagingReport(BaseModel):
    """What staging produced. `tables` and `skipped` are paths relative to the data dir, or the names of a
    `TableSource`'s tables."""

    staged_dir: Path
    tables: list[str]
    skipped: list[SkippedFile]


def _records(obj: object) -> list[dict] | None:
    """Find the list of records in a JSON value: a top-level list, or the single list inside an object."""

    def is_record_list(value: object) -> bool:
        return isinstance(value, list) and bool(value) and all(isinstance(x, dict) for x in value)

    if is_record_list(obj):
        return obj
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if is_record_list(v)]
        if len(lists) == 1:  # with two candidate lists we cannot know which one is "the table"
            return lists[0]
    return None


def _flatten(record: dict, prefix: str = "") -> dict:
    """Nested objects become dotted columns (`meta.a`); lists are kept as JSON text in one cell."""
    flat: dict = {}
    for key, value in record.items():
        column = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, column + "."))
        elif isinstance(value, (list, tuple)):
            flat[column] = json.dumps(value)
        else:
            flat[column] = value
    return flat


def json_to_csv(src: Path, dest: Path) -> int:
    """Convert a JSON or NDJSON file of records to CSV. Returns the row count.

    Raises `ValueError` when the file is not valid JSON/NDJSON or holds no list of records.
    """
    text = src.read_text(encoding="utf-8")
    try:
        records = _records(json.loads(text))
    except json.JSONDecodeError:
        # not one JSON document: try newline-delimited JSON (json.JSONDecodeError is a ValueError)
        parsed = [json.loads(line) for line in text.splitlines() if line.strip()]
        records = parsed if all(isinstance(r, dict) for r in parsed) else None
    if not records:
        raise ValueError("no list of records found")

    rows = [_flatten(r) for r in records]
    columns = list(dict.fromkeys(k for r in rows for k in r))  # union of keys, first-seen order
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _reset_dir(staging: Path) -> None:
    """Empty `staging` for a fresh run, refusing to delete a non-empty dir that we did not create."""
    if staging.exists():
        if any(staging.iterdir()) and not (staging / _MARKER).exists():
            raise KgBuilderError(
                f"refusing to clear '{staging}': it is not empty and was not created by kgbuilder"
            )
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    (staging / _MARKER).touch()


def stage_structured(data_dir: Path, staging: Path, source: TableSource | None = None) -> StagingReport:
    """Stage the tables as CSV under `staging`. Rebuilds `staging` from scratch every time.

    Without a `source`, the data dir's CSVs are copied and its JSON is converted to CSV. With one (R114),
    the tables come from it instead, and the data dir's CSV and JSON files are reported as skipped: a
    dataset kept in both places would otherwise be staged twice. Raises `DataSourceError` when the source
    cannot be read; the staging dir is then left as it was.
    """
    data_dir, staging = Path(data_dir), Path(staging)
    # Collect first: when `out/` lives inside the data dir, files staged below must not be picked up.
    files = [
        p for p in sorted(data_dir.rglob("*")) if p.is_file() and staging.resolve() not in p.resolve().parents
    ]
    if source is None:
        _reset_dir(staging)
        return _stage_files(data_dir, files, staging)
    names = source.tables()  # before the reset: an unreachable database must not cost the last staging
    _reset_dir(staging)
    return _stage_source(source, names, [p.relative_to(data_dir) for p in files], staging)


def _stage_source(source: TableSource, names: list[str], files: list[Path], staging: Path) -> StagingReport:
    """Write every table of `source` to `staging/<table>.csv`; report the data dir's tables as skipped."""
    reason = f"the tables come from {source.describe()}"
    skipped = [
        SkippedFile(file=rel.as_posix(), reason=reason)
        for rel in files
        if rel.suffix.lower() in {".csv", *_JSON_SUFFIXES}
    ]
    tables: list[str] = []
    for name in names:
        if not _FILE_SAFE.fullmatch(name):
            log.warning("skipped table %r: not usable as a file name", name)
            skipped.append(SkippedFile(file=name, reason="the table name is not usable as a file name"))
            continue
        source.write_csv(name, staging / f"{name}.csv")
        tables.append(name)
    return StagingReport(staged_dir=staging, tables=tables, skipped=skipped)


def _stage_files(data_dir: Path, files: list[Path], staging: Path) -> StagingReport:
    """Copy the CSVs among `files` and convert their JSON to CSV, under `staging`."""
    tables: list[str] = []
    skipped: list[SkippedFile] = []
    for path in files:
        rel = path.relative_to(data_dir)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, staging / rel)
            tables.append(rel.as_posix())
        elif suffix in _JSON_SUFFIXES:
            try:
                json_to_csv(path, staging / rel.with_suffix(".csv"))
                tables.append(rel.as_posix())
            except ValueError as e:
                # Not tabular, so not structured input. Reported, not fatal: a data dir may well
                # contain config or metadata JSON next to the tables.
                log.warning("skipped %s: %s", rel.as_posix(), e)
                skipped.append(SkippedFile(file=rel.as_posix(), reason=str(e)))
    return StagingReport(staged_dir=staging, tables=tables, skipped=skipped)
