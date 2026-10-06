"""The target gold (R89) placed on one build's graph: each target's nodes in the snapshot (R90).

Role in the pipeline: between the target gold (`validation/target_gold.py`) and the criteria
(`anchor/criteria.py`). The gold names what a target reaches without any graph id; this module finds those
nodes in one build, so the criteria can ask whether the walks get there.
Design: a record ref picks staged rows by their cells (`qa_gold.rows_matching`, as the gold's own check);
the build's plan says which node rule a file feeds, and the rule's label and key column give the
`record_ref`. A mention ref (a document and its names) finds the snapshot's mentions of that document
whose name equals one of the names after `norm`, and follows their REFERS_TO. Only when the document has no
such mention, it takes the mentions whose name holds one of the names as whole words: the extractor often
writes a longer name for the thing the gold names ("low-pressure fuel pump", "institute committee, 12 May
2025"), and the graph does hold that thing. A target placed this way is marked `loose`. What does not
resolve is kept as text (`missing`): a file without a node rule, a row the importer did not keep, a name the
extractor never wrote. The criteria count it; nothing is guessed.
Must not: decide what a target should reach (the gold's), or look a name up by similarity (that is W1's,
and it is what C2 measures).
"""

import csv
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

from ..audit.snapshot import GraphSnapshot
from ..core.identity import record_ref
from ..core.text import contains_words, norm
from ..structured.plan import ConstructionPlan
from ..validation.gold import MentionRef
from ..validation.qa_gold import RecordEvidence, rows_matching
from ..validation.target_gold import TargetGold

Rows = Mapping[str, Sequence[Mapping[str, str]]]


class PlacedTarget(BaseModel):
    """A gold target with the nodes it has in this build, and the refs that found none."""

    name: str
    aliases: list[str]
    nodes: list[str]  # record refs and canonical ids, sorted
    missing: list[str]  # one line per record or mention ref with no node in the snapshot
    loose: bool = False  # a mention ref was placed by whole words in a longer name, not by an equal one


def read_staged(staging_dir: Path, files: set[str]) -> dict[str, list[dict[str, str]]]:
    """The rows of each staged file in `files` that exists under `staging_dir`, as `csv` reads them."""
    out = {}
    for name in sorted(files):
        path = staging_dir / name
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as f:
                out[name] = list(csv.DictReader(f))
    return out


def record_nodes(ref: RecordEvidence, plan: ConstructionPlan, rows: Rows, known: set[str]) -> list[str]:
    """The records of the snapshot that the rows `ref` picks became, by every node rule fed by its file.
    Empty when the file feeds no node rule (a relationship table) or the importer kept none of the rows."""
    table = rows.get(ref.file, [])
    out = set()
    for rule in plan.nodes:
        if rule.source_file != ref.file:
            continue
        for row in rows_matching(ref, table):
            key = (row.get(rule.unique_column) or "").strip()
            if key and record_ref(rule.label, key) in known:
                out.add(record_ref(rule.label, key))
    return sorted(out)


class TargetPlacer:
    """Places gold targets on one snapshot under its build's plan and staged rows."""

    def __init__(self, s: GraphSnapshot, plan: ConstructionPlan, rows: Rows) -> None:
        self._plan, self._rows = plan, rows
        self._known = {r.id for r in s.records}
        canonical = {a.mention: a.canonical for a in s.references}
        self._of_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)  # doc -> (mention name, node)
        for m in s.mentions:
            if m.id in canonical:
                self._of_doc[m.doc_id].append((m.name, canonical[m.id]))

    def mention_nodes(self, ref: MentionRef) -> tuple[list[str], bool]:
        """What the mentions of `ref.doc_id` named by one of `ref.names` refer to, and whether only names
        holding one of them as whole words did (the second tier, used when no name is equal)."""
        mentions = self._of_doc.get(ref.doc_id, [])
        wanted = {norm(n) for n in ref.names}
        equal = {node for name, node in mentions if norm(name) in wanted}
        if equal:
            return sorted(equal), False
        within = {node for name, node in mentions if any(contains_words(name, n) for n in ref.names)}
        return sorted(within), bool(within)

    def records(self, ref: RecordEvidence) -> list[str]:
        return record_nodes(ref, self._plan, self._rows, self._known)

    def place(self, gold: TargetGold) -> dict[str, list[PlacedTarget]]:
        """Every question's targets with their nodes, by question id."""
        out = {}
        for entry in gold.questions:
            placed = []
            for t in entry.targets:
                nodes: set[str] = set()
                missing = []
                loose = False
                for ref in t.records:
                    found = self.records(ref)
                    nodes |= set(found)
                    if not found:
                        missing.append(f"record {ref.file} {ref.row}")
                for mention in t.mentions:
                    found, by_words = self.mention_nodes(mention)
                    nodes |= set(found)
                    loose |= by_words
                    if not found:
                        missing.append(f"mention {mention.doc_id} {mention.names}")
                placed.append(
                    PlacedTarget(
                        name=t.name, aliases=t.aliases, nodes=sorted(nodes), missing=missing, loose=loose
                    )
                )
            out[entry.id] = placed
        return out
