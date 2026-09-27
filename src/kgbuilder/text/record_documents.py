"""Documents built from the prose columns of structured records (R67 part 1).

Role in the pipeline: `kg ingest-text`, next to loading file documents: every record whose source file
has prose columns (`ColumnProfile.is_prose`) becomes one synthetic document carrying a `RecordRef`, so
that linking ties it to its record by key (never by name) and its claims become observations with the
record as their thing. On a table whose text is tagline-length (the furniture `description`) no column
is prose and nothing here fires, so that graph stays unchanged.
Not here: the prose rule itself (structured/profiler.py) and the ABOUT link (resolution/linking.py).
"""

import csv
from pathlib import Path

from ..structured.plan import ConstructionPlan, name_property
from ..structured.profiler import DataProfile
from .documents import Document, RecordRef

# Synthetic doc_ids live under this prefix so they can never collide with a file path relative to the
# data dir ("record/Recall/16V074000" vs "product_reviews/table.md").
_DOC_ID_PREFIX = "record"


def prose_columns(rule_columns: set[str], profile: DataProfile, source_file: str) -> list[str]:
    """The prose columns of `source_file` that the plan imports, in file order.

    Restricted to imported columns on purpose: a prose column the reviewed plan left out was left out
    by a human decision, and a document from it would carry text the graph knows nothing about.
    """
    file_profile = profile.file(source_file)
    if file_profile is None:
        return []
    return [c.name for c in file_profile.columns if c.is_prose and c.name in rule_columns]


def record_documents(staged_dir: Path, plan: ConstructionPlan, profile: DataProfile) -> list[Document]:
    """One document per record that has prose to read, in file order, so reruns are deterministic.

    The document repeats the record's display name as its first heading: the chunker takes that heading
    as the context every chunk carries, so the extractor can name the record when the text says "it".
    Records without a key or whose prose cells are all empty yield no document. Failure modes: a missing
    staged file raises (the profile named it, so its absence is a broken run, not an empty table).
    """
    docs: list[Document] = []
    for rule in plan.nodes:
        columns = prose_columns({rule.unique_column, *rule.properties}, profile, rule.source_file)
        if not columns:
            continue
        name_col = name_property(rule)
        with (Path(staged_dir) / rule.source_file).open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                key = (row.get(rule.unique_column) or "").strip()
                sections = [(c, (row.get(c) or "").strip()) for c in columns]
                sections = [(c, text) for c, text in sections if text]
                if not key or not sections:
                    continue
                title = (row.get(name_col) or "").strip() or key
                # one section per column, separated like the review corpus, so the chunker never lets
                # extraction overlap leak from one column's text into the next
                body = "\n\n---\n\n".join(f"## {column}\n\n{text}" for column, text in sections)
                docs.append(
                    Document(
                        doc_id=f"{_DOC_ID_PREFIX}/{rule.label}/{key}",
                        title=title,
                        text=f"# {title}\n\n{body}",
                        record=RecordRef(label=rule.label, key_property=rule.unique_column, key=key),
                    )
                )
    return docs
