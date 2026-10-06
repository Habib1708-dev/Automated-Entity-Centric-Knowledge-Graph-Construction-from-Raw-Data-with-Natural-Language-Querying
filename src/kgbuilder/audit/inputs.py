"""The structured side and the text of a build, read offline for the graph audit (R87).

Role in the pipeline: the first read of `audit/snapshot.py`. The records and their relationships come from
the staged tables the build imported (`out/<run>/staging`) under the build's plan, the chunks from the
dataset through the build's own loaders and chunker, so the snapshot holds what the graph held.
Design: a record is named by its `record_ref` (`<label>:<key>`), the id resolve.json and the verdict files
use, never by a Neo4j element id (they died with the graph). The importer's rules are followed where they
change the result: one record per key, the later row's non-empty properties winning (`SET n += props`), and
a relationship only when both its ends exist.
Must not: talk to Neo4j, or decide anything the build did not decide.
"""

import csv
from collections import defaultdict, deque
from pathlib import Path

from pydantic import BaseModel

from ..core.identity import record_ref
from ..structured.plan import ConstructionPlan, name_property
from ..structured.profiler import DataProfile
from ..text.chunking import Chunk, chunk_document
from ..text.documents import Document, load_documents
from ..text.record_documents import record_documents

# The importer's 2-hop scope (resolution/records.py `_SCOPE_HOPS`): the domain nodes a document's thing
# reaches in this many hops are the records its mentions may be matched to.
SCOPE_HOPS = 2


class Record(BaseModel):
    """One domain node as the importer wrote it."""

    id: str  # record_ref: "<label>:<key>"
    label: str
    key: str
    name: str | None  # the plan's name column; None when the row has none (such a node has no name)
    properties: dict[str, str]


class Relation(BaseModel):
    """One domain relationship between two records."""

    source: str  # record ids
    type: str
    target: str


class Corpus(BaseModel):
    """The documents and chunks the build ingested."""

    documents: list[Document]
    chunks: list[Chunk]


def read_records(staged_dir: Path, plan: ConstructionPlan) -> list[Record]:
    """Every record of the plan's node rules, in file order, one per key."""
    records: dict[str, Record] = {}
    for rule in plan.nodes:
        name_col = name_property(rule)
        for row in _rows(staged_dir / rule.source_file):
            key = row.get(rule.unique_column)
            if key is None:  # the importer skips a row with a null key
                continue
            ref = record_ref(rule.label, key)
            props = {p: row[p] for p in rule.properties if row.get(p) is not None}
            if ref in records:  # MERGE on the key: the later row's properties are added over the earlier's
                records[ref].properties.update(props)
                continue
            records[ref] = Record(id=ref, label=rule.label, key=key, name=None, properties=props)
        for record in records.values():
            if record.label == rule.label:
                record.name = (
                    record.key if name_col == rule.unique_column else record.properties.get(name_col)
                )
    return list(records.values())


def read_relations(staged_dir: Path, plan: ConstructionPlan, records: list[Record]) -> list[Relation]:
    """Every relationship the plan's rules give between two existing records, one per (ends, type)."""
    known = {r.id for r in records}
    out: dict[tuple[str, str, str], Relation] = {}
    for rule in plan.relationships:
        from_node, to_node = plan.node(rule.from_label), plan.node(rule.to_label)
        if from_node is None or to_node is None:
            continue
        for row in _rows(staged_dir / rule.source_file):
            a, b = row.get(rule.from_column), row.get(rule.to_column)
            if a is None or b is None:
                continue
            source, target = record_ref(from_node.label, a), record_ref(to_node.label, b)
            if source in known and target in known:  # the importer MATCHes both ends first
                out.setdefault(
                    (source, rule.relationship_type, target),
                    Relation(source=source, type=rule.relationship_type, target=target),
                )
    return list(out.values())


def scopes(records: list[Record], relations: list[Relation]) -> dict[str, set[str]]:
    """For every record, the records within `SCOPE_HOPS` undirected hops of it, itself included: the scope
    `resolution/records.read_scopes` reads from the graph."""
    neighbours: dict[str, set[str]] = defaultdict(set)
    for rel in relations:
        neighbours[rel.source].add(rel.target)
        neighbours[rel.target].add(rel.source)
    out: dict[str, set[str]] = {}
    for record in records:
        seen = {record.id: 0}
        queue = deque([record.id])
        while queue:
            node = queue.popleft()
            if seen[node] == SCOPE_HOPS:
                continue
            for nxt in neighbours[node]:
                if nxt not in seen:
                    seen[nxt] = seen[node] + 1
                    queue.append(nxt)
        out[record.id] = set(seen)
    return out


def read_corpus(
    data_dir: Path,
    staged_dir: Path,
    plan: ConstructionPlan | None,
    profile: DataProfile | None,
    chunking: tuple[int, int, int],
) -> Corpus:
    """The ingest stage's documents and chunks (`pipeline/stages.py`, IngestTextStage): the dataset's files,
    then one document per record with prose; `chunking` is (max, min, overlap) as the build logged them."""
    docs = load_documents(data_dir)
    if plan is not None and profile is not None and staged_dir.is_dir():
        docs = [*docs, *record_documents(staged_dir, plan, profile)]
    chunks = [c for d in docs for c in chunk_document(d, *chunking)]
    return Corpus(documents=docs, chunks=chunks)


def _rows(path: Path) -> list[dict[str, str | None]]:
    """The rows of a staged CSV, every cell stripped; an empty cell is None, as DuckDB reads it."""
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {k: ((v or "").strip() or None) for k, v in row.items() if k is not None}
            for row in csv.DictReader(f)
        ]
