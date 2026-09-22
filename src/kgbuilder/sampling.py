"""Small, key-consistent subsets of a dataset for cheap smoke and dev runs, built from a preset's config.

Role: `kg sample` writes the `data_dir` of each preset that has a `sample` block in presets.yaml. It is a
tool around the pipeline, not a stage: the pipeline then reads the subset like any other dataset.
Design: the tables follow the foreign keys the profiler detects, so no dataset-specific names live in code:
start from the root rows named in the config, walk down to every row that points at a kept row, then up to
every row a kept row points at. Documents are cut after their first sections, where the chunker splits.
Kept rows and documents are copied verbatim (same bytes per line), so a subset reads like the original.
Not here: which rows to choose (the config decides) and JSON sources (only CSV tables and listed documents).
"""

import csv
import io
import shutil
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .core.errors import ConfigurationError, MissingInputError
from .structured.profiler import ForeignKeyCandidate, profile_directory
from .text.chunking import SECTION_BREAK


class RootRows(BaseModel):
    """The rows a subset starts from: those of `file` whose `column` holds one of `values`."""

    file: str
    column: str
    values: list[str] = Field(min_length=1)


class SampleSpec(BaseModel):
    """The `sample` block of a preset in presets.yaml."""

    source: Path  # the full dataset the subset is taken from
    root: RootRows
    documents: list[str] = []  # paths relative to `source`
    sections: int = Field(default=3, ge=1)  # sections kept per document (reviews, in the corpus)


class SampleReport(BaseModel):
    """What was written: kept rows per table and kept sections per document."""

    rows: dict[str, int]
    sections: dict[str, int]


class _Table:
    """A CSV file as its header line, its raw data lines and the parsed rows (same order)."""

    def __init__(self, path: Path):
        text = path.read_bytes().decode("utf-8")
        lines = text.splitlines(keepends=True)
        self.header, self.lines = lines[0], lines[1:]
        self.columns = next(csv.reader(io.StringIO(self.header)))
        self.rows = list(csv.DictReader(io.StringIO(text, newline="")))
        if len(self.rows) != len(self.lines):
            # a quoted field with a line break: raw lines and rows no longer match one to one
            raise ConfigurationError(f"{path.name}: multi-line CSV fields are not supported by the sampler")

    def values(self, indices: set[int], column: str) -> set[str]:
        return {self.rows[i][column] for i in indices}

    def matching(self, column: str, values: set[str]) -> set[int]:
        return {i for i, row in enumerate(self.rows) if row[column] in values}


def select_rows(
    tables: dict[str, _Table], foreign_keys: list[ForeignKeyCandidate], root: RootRows
) -> dict[str, set[int]]:
    """Indices of the kept rows per table: the root rows, their descendants, then everything they reference.

    Downwards only through tables reached from the root (a child's rows that point at kept parent rows);
    upwards through every key (the parent rows a kept row points at), so no kept key dangles. Tables
    reached neither way keep no rows.
    """
    if root.file not in tables or root.column not in tables[root.file].columns:
        raise ConfigurationError(f"sample root {root.file}.{root.column} does not exist in the source")
    kept = {name: set() for name in tables}
    kept[root.file] = tables[root.file].matching(root.column, set(root.values))
    if not kept[root.file]:
        raise ConfigurationError(f"no row of {root.file} has {root.column} in {root.values}")

    # down: breadth first from the root, a table's children are the tables whose key points at it
    below, queue = {root.file}, deque([root.file])
    while queue:
        parent = queue.popleft()
        for fk in foreign_keys:
            if fk.to_file == parent and fk.from_file not in below:
                keys = tables[parent].values(kept[parent], fk.to_column)
                kept[fk.from_file] |= tables[fk.from_file].matching(fk.from_column, keys)
                below.add(fk.from_file)
                queue.append(fk.from_file)

    # up: repeat until nothing changes, because a referenced row can reference further rows
    changed = True
    while changed:
        changed = False
        for fk in foreign_keys:
            keys = tables[fk.from_file].values(kept[fk.from_file], fk.from_column)
            new = tables[fk.to_file].matching(fk.to_column, keys) - kept[fk.to_file]
            if new:
                kept[fk.to_file] |= new
                changed = True
    return kept


def cut_sections(text: str, sections: int) -> str:
    """`text` up to its `sections`-th section break (the chunker's), or all of it when it has fewer."""
    breaks = list(SECTION_BREAK.finditer(text))
    if len(breaks) < sections:
        return text
    return text[: breaks[sections - 1].start()].rstrip() + "\n"


def _clear(target: Path, source: Path) -> None:
    """Delete an old subset, refusing any path that holds the source or the working directory."""
    resolved = target.resolve()
    for protected in (source.resolve(), Path.cwd().resolve()):
        if resolved == protected or resolved in protected.parents:
            raise ConfigurationError(f"refusing to replace {target}: it contains {protected}")
    if target.exists():
        shutil.rmtree(target)


def write_sample(spec: SampleSpec, target: Path) -> SampleReport:
    """Replace `target` with the subset `spec` describes and report what it holds.

    Raises `MissingInputError` when the source or a listed document is missing, and `ConfigurationError`
    for a root that matches nothing or a target that would overwrite the source.
    """
    if not spec.source.is_dir():
        raise MissingInputError(f"sample source {spec.source} is not a directory")
    tables = {p.relative_to(spec.source).as_posix(): _Table(p) for p in sorted(spec.source.rglob("*.csv"))}
    kept = select_rows(tables, profile_directory(spec.source).foreign_keys, spec.root)
    _clear(target, spec.source)

    for name, table in tables.items():
        if kept[name]:
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(table.lines[i] for i in sorted(kept[name]))
            out.write_bytes((table.header + data).encode("utf-8"))

    sections: dict[str, int] = {}
    for doc in spec.documents:
        path = spec.source / doc
        if not path.is_file():
            raise MissingInputError(f"sample document {path} does not exist")
        text = cut_sections(path.read_bytes().decode("utf-8"), spec.sections)
        out = target / doc
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        sections[doc] = len(SECTION_BREAK.findall(text)) + 1
    return SampleReport(rows={name: len(ix) for name, ix in kept.items() if ix}, sections=sections)


def preset_samples(presets: dict[str, dict], names: list[str]) -> dict[str, tuple[SampleSpec, Path]]:
    """(spec, target directory) of each preset with a `sample` block, restricted to `names` if given.

    Raises `ConfigurationError` for an unknown name, a named preset without a `sample` block or
    `data_dir`, or a block that does not fit `SampleSpec`.
    """
    unknown = sorted(set(names) - set(presets))
    if unknown:
        raise ConfigurationError(f"unknown preset(s): {', '.join(unknown)}")
    chosen = names or [name for name, values in presets.items() if "sample" in values]
    result: dict[str, tuple[SampleSpec, Path]] = {}
    for name in chosen:
        values = presets[name]
        if "sample" not in values or "data_dir" not in values:
            raise ConfigurationError(f"preset '{name}' needs both a sample block and a data_dir")
        try:
            result[name] = (SampleSpec.model_validate(values["sample"]), Path(values["data_dir"]))
        except ValidationError as e:
            raise ConfigurationError(f"preset '{name}': invalid sample block: {e}") from e
    return result
