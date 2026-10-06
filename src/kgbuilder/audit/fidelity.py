"""Is the snapshot the graph the build wrote? The fidelity gate of the graph audit (R87, M0).

Role in the pipeline: after `audit/snapshot.py`, before any check reads the snapshot. A check of a graph
that differs from the build would measure the rebuild, not the build.
Design: two kinds of evidence, both from the build itself. (1) The counts its stage runs logged to MLflow,
copied once with their run ids into a committed file (`tests/gold/r87/<dataset>_logged.json`), compared
one by one. (2) The judge sheet the build's eval run wrote, which keeps every fact of the gold documents
with its attachments: the snapshot must hold the same fact ids and, fact by fact, the same (thing, route)
pairs. Every comparison is reported; `passed` is true only when all hold.
Must not: tolerate a difference. A known, explained difference is stated by whoever reads the report.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel

from ..resolution.attachment import ROUTES, TEXT_ABOUT
from .snapshot import GraphSnapshot


class LoggedCounts(BaseModel):
    """The counts a build's stage runs logged, as `<stage>.<metric>` -> value, with the run ids."""

    dataset: str
    build: str
    git_sha: str
    runs: dict[str, str]  # stage -> MLflow run id
    counts: dict[str, int]
    # `<stage>.<metric>` cost and token usage of the same runs, copied from MLflow like the counts (R90, C9);
    # empty for a file written before R90. Not compared by the fidelity gate: usage is no graph count
    usage: dict[str, float] = {}


class Comparison(BaseModel):
    name: str
    expected: int
    found: int

    @property
    def ok(self) -> bool:
        return self.expected == self.found


class FidelityReport(BaseModel):
    """Every comparison, and the facts of the judge sheet whose attachments differ (with both sides)."""

    comparisons: list[Comparison]
    mention_ids_equal: bool  # the snapshot's mentions are exactly resolve.json's
    sheet_facts: int  # facts of the build's judge sheet; 0 when no sheet was given
    sheet_facts_missing: list[str]  # sheet fact ids the snapshot does not hold
    sheet_attachment_diffs: dict[str, dict[str, list[str]]]  # fact id -> {"sheet": [...], "snapshot": [...]}

    @property
    def passed(self) -> bool:
        return (
            all(c.ok for c in self.comparisons)
            and self.mention_ids_equal
            and not self.sheet_facts_missing
            and not self.sheet_attachment_diffs
        )

    def metrics(self) -> dict[str, float]:
        return {
            "fidelity_passed": float(self.passed),
            "fidelity_counts_failed": float(sum(not c.ok for c in self.comparisons)),
            "fidelity_sheet_facts": float(self.sheet_facts),
            "fidelity_sheet_diffs": float(len(self.sheet_facts_missing) + len(self.sheet_attachment_diffs)),
        }


def load_logged(path: Path) -> LoggedCounts:
    return LoggedCounts.model_validate_json(path.read_text(encoding="utf-8"))


def snapshot_counts(s: GraphSnapshot) -> dict[str, int]:
    """The snapshot's value of every count a build logs, under the logged names."""
    refs = Counter(a.kind for a in s.references)
    routes = Counter(a.how for a in s.attachments)
    documents = {c.doc_id for c in s.chunks}
    linked = [link for link in s.documents_about if link.how != TEXT_ABOUT]
    derived = [c for c in s.claims if c.derived]
    return {
        "ingest_text.chunks": len(s.chunks),
        "ingest_text.documents": len(documents),
        "ingest_text.record_documents": sum(d.startswith("record/") for d in documents),
        "extract.facts": len(s.claims) - len(derived),
        "extract.mention_nodes": sum(not m.derived for m in s.mentions),
        "extract.mentions": s.extracted_mentions_edges,
        "link.documents_linked": len({link.source for link in linked}),
        "link.record_documents_linked": len({link.source for link in linked if link.how == "record"}),
        "link.chunks_linked": len({link.source for link in s.sections_about}),
        "link.facts_derived": len(derived),
        "link.mentions_created": sum(m.derived for m in s.mentions),
        "resolve.mentions": len(s.references),
        "resolve.mentions_to_records": refs["record"],
        "resolve.mentions_to_individuals": refs["individual"],
        "resolve.mentions_to_concepts": refs["concept"],
        "attach.observations_total": len(s.claims),
        "attach.observations_attached": len({a.observation for a in s.attachments}),
        "attach.attachments": len(s.attachments),
        **{f"attach.attached_{how}": routes[how] for how in ROUTES},
        "attach.documents_about_by_text": sum(link.how == TEXT_ABOUT for link in s.documents_about),
        "attach.documents_about_nothing": len(documents - {link.source for link in s.documents_about}),
    }


def check_fidelity(s: GraphSnapshot, logged: LoggedCounts, sheet: Path | None) -> FidelityReport:
    """Compare the snapshot with the build's logged counts and, when given, its judge sheet."""
    found = snapshot_counts(s)
    comparisons = [
        Comparison(name=k, expected=v, found=found.get(k, -1)) for k, v in sorted(logged.counts.items())
    ]
    missing: list[str] = []
    diffs: dict[str, dict[str, list[str]]] = {}
    facts = 0
    if sheet is not None:
        snapshot_edges: dict[str, set[str]] = defaultdict(set)
        for a in s.attachments:
            snapshot_edges[a.observation].add(f"{a.name} / {a.how}")
        claim_ids = {c.id for c in s.claims}
        sheet_facts = json.loads(sheet.read_text(encoding="utf-8"))["facts"]
        facts = len(sheet_facts)
        for f in sheet_facts:
            if f["id"] not in claim_ids:
                missing.append(f["id"])
                continue
            # the sheet names things by display name, so names are compared, not ids
            on_sheet = {f"{a['thing']} / {a['how']}" for a in f.get("attachments") or []}
            if on_sheet != snapshot_edges[f["id"]]:
                diffs[f["id"]] = {"sheet": sorted(on_sheet), "snapshot": sorted(snapshot_edges[f["id"]])}
    return FidelityReport(
        comparisons=comparisons,
        mention_ids_equal={m.id for m in s.mentions} == {a.mention for a in s.references},
        sheet_facts=facts,
        sheet_facts_missing=missing,
        sheet_attachment_diffs=diffs,
    )
