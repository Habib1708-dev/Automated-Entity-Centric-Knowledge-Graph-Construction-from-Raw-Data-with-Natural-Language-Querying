"""Load text documents (md, txt, pdf) from the data dir."""

from pathlib import Path

from pydantic import BaseModel

TEXT_SUFFIXES = {".md", ".txt", ".pdf"}


class Document(BaseModel):
    doc_id: str  # path relative to the data dir
    title: str
    text: str


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
