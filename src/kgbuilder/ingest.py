"""Heterogeneous inputs: JSON becomes staged CSV tables; md/txt/pdf become documents."""

import csv
import json
import shutil
from pathlib import Path

from pydantic import BaseModel

TEXT_SUFFIXES = {".md", ".txt", ".pdf"}


class Document(BaseModel):
    doc_id: str  # path relative to the data dir
    title: str
    text: str


def _records(obj) -> list[dict] | None:
    """Find the list of records in a JSON value."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        return obj
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(lists) == 1:
            return lists[0]
    return None


def _flatten(rec: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in rec.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, (list, tuple)):
            out[key] = json.dumps(v)
        else:
            out[key] = v
    return out


def json_to_csv(src: Path, dest: Path) -> int:
    text = src.read_text(encoding="utf-8")
    try:
        records = _records(json.loads(text))
    except json.JSONDecodeError:  # ndjson
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not records:
        raise ValueError(f"{src}: no list of records found")
    rows = [_flatten(r) for r in records]
    columns = list(dict.fromkeys(k for r in rows for k in r))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def stage_structured(data_dir: Path, staging: Path) -> Path:
    """Copy CSVs and convert JSON to CSV under `staging`, so profiling sees one uniform format."""
    data_dir = Path(data_dir)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_dir)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, staging / rel)
        elif suffix in (".json", ".jsonl", ".ndjson"):
            try:
                json_to_csv(path, staging / rel.with_suffix(".csv"))
            except ValueError:
                pass  # not tabular, so not structured input
    return staging


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def load_documents(data_dir: Path) -> list[Document]:
    data_dir = Path(data_dir)
    docs = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = (
            read_pdf(path)
            if path.suffix.lower() == ".pdf"
            else path.read_text(encoding="utf-8", errors="replace")
        )
        if text.strip():
            docs.append(Document(doc_id=path.relative_to(data_dir).as_posix(), title=path.stem, text=text))
    return docs
