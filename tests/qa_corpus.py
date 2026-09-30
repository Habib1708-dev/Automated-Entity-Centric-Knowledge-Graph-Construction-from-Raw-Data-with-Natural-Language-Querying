"""Rebuild the chunks and staged rows a question-answer gold file cites, with the pipeline's own code (R70).

Used by the gold-file tests (test_qa_gold_files.py): the chunks come from the loader, the record-document
builder and the chunker that `kg ingest-text` runs, under the gold's pinned chunk settings, so a quote
that is verbatim here is verbatim in the graph's chunks. No Neo4j, no LLM: staging and profiling are file
and DuckDB work.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured
from kgbuilder.text.chunking import chunk_document
from kgbuilder.text.documents import load_documents
from kgbuilder.text.record_documents import record_documents
from kgbuilder.validation.qa_gold import CorpusSpec

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Corpus:
    chunks: dict[str, str]  # chunk id -> text
    rows: dict[str, list[dict[str, str]]]  # staged file (relative to the staging dir) -> its rows


def rebuild_corpus(spec: CorpusSpec, staging: Path) -> Corpus:
    """The corpus of `spec`: its file documents and, with a plan, its record documents, chunked as the
    pipeline chunks them; and every staged table. `staging` is an empty scratch directory."""
    data_dir = REPO / spec.data_dir
    staged = stage_structured(data_dir, staging).staged_dir
    documents = load_documents(data_dir)
    if spec.plan is not None:
        plan = ConstructionPlan.model_validate_json((REPO / spec.plan).read_text(encoding="utf-8"))
        documents += record_documents(staged, plan, profile_directory(staged))
    chunks = {
        c.chunk_id: c.text
        for d in documents
        for c in chunk_document(d, spec.chunk_max_chars, spec.chunk_min_chars, spec.chunk_overlap_chars)
    }
    rows = {}
    for path in sorted(staged.rglob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows[path.relative_to(staged).as_posix()] = list(csv.DictReader(f))
    return Corpus(chunks=chunks, rows=rows)
