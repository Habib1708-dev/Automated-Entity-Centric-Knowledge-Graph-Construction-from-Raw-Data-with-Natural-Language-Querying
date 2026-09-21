"""Load text documents (md, txt, pdf) from the data dir.

Role in the pipeline: first step of `kg ingest-text`; its output goes to the chunker.
Not here: structured files (structured/staging.py) and chunking (chunking.py).
"""

from pathlib import Path

from pydantic import BaseModel

TEXT_SUFFIXES = {".md", ".txt", ".pdf"}


class Document(BaseModel):
    doc_id: str  # path relative to the data dir, posix style: stable across machines and reruns
    title: str  # file name without suffix; the linker matches it against domain node names
    text: str


def read_pdf(path: Path) -> str:
    """Plain text of a PDF, pages separated by blank lines. Scanned pages yield empty text."""
    # imported here so that pypdf is only loaded when a PDF actually shows up
    from pypdf import PdfReader

    return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def load_documents(data_dir: Path, exclude: Path | None = None) -> list[Document]:
    """Every non-empty text document under `data_dir`, sorted by path so that reruns are deterministic.

    `exclude` is a directory to ignore, normally the output dir, in case it lives inside the data dir.
    """
    data_dir = Path(data_dir)
    excluded = exclude.resolve() if exclude else None
    docs = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if excluded and excluded in path.resolve().parents:
            continue
        if path.suffix.lower() == ".pdf":
            text = read_pdf(path)
        else:
            # errors="replace": one bad byte in a review must not abort the whole ingestion
            text = path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            docs.append(Document(doc_id=path.relative_to(data_dir).as_posix(), title=path.stem, text=text))
    return docs
