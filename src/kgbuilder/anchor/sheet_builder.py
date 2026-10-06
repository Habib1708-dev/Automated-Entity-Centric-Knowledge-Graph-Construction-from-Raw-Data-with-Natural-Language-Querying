"""Builds the blind judging sheets of C3, C4 and C6 (anchor/sheets.py) from a build's offline snapshot (R93).

Role in the pipeline: called by `kg anchor-sheets` (pipeline/anchor_stages.py) with R87's snapshot, R87's
code checks of it (their flags and split groups) and the target nodes R90 placed.
  C3: every individual or concept node with more than one mention, and every R87 split group; records are
     left out, their wrong merges are C4's INCORRECT links (R93 decision 3);
  C4: every mention-to-record link, and every unlinked mention R87 flagged as named after a record's key
     (`label_mismatch` on a non-record reference), with that record; both asked the same question, so the
     judge cannot tell which exist (R93 decision 2);
  C6: every (start, chunk) pair W2 reaches from a placed node in arm A or arm B, a census (R93 decision 1),
     with two code flags: `same_label_about` (the chunk's section or document is ABOUT another record of
     the start's label, the direction's flag) and `scope_foreign` (the chunk's document has a scope without
     the start, R87's rule).
Design: one lookup index over the snapshot (`_Index`) renders mentions, records and nodes the same way on
every sheet; item ids are stable (`m:`, `s:`, `l:`, `p:` + the graph ids), so verdicts survive a rebuild of
the same snapshot.
Must not: decide a verdict, sample, or put a flag into what the judge sees.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable

from ..audit.checks import CodeChecks
from ..audit.scope import ScopeIndex
from ..audit.snapshot import GraphSnapshot
from ..core.text import contains_words, norm, split_sentences, squash
from .navigation import AnchorGraph, Arm
from .sheets import (
    C3Sheet,
    C4Sheet,
    C6Sheet,
    CodeItem,
    CodeSide,
    JudgingSheets,
    LinkItem,
    MentionView,
    MergeItem,
    NodeView,
    PurityItem,
    RecordView,
    SheetChunk,
    SplitItem,
)

# The questions the sheets ask; the rules files (tests/gold/r93/rules/) say what each label means.
MERGE_QUESTION = "Do all these mentions, read in their sentences, refer to one and the same thing?"
SPLIT_QUESTION = "Are these nodes, each read through its mentions, one and the same thing?"
LINK_QUESTION = "Does this mention, read in its chunk, refer to this record?"
PURITY_QUESTION = "Does this chunk concern this start node (speak of this very thing)?"
# Mentions shown to describe an individual or concept start of C6: enough to tell which thing it is
# without listing every mention of a frequent concept.
START_MENTIONS = 3


def snapshot_digest(s: GraphSnapshot) -> str:
    """Short content hash of a snapshot, named in the sheets and the verdict files."""
    return hashlib.sha256(s.model_dump_json().encode("utf-8")).hexdigest()[:12]


class _Index:
    """The snapshot's lookups the views need."""

    def __init__(self, s: GraphSnapshot) -> None:
        self.records = {r.id: r for r in s.records}
        self.chunks = {c.chunk_id: c for c in s.chunks}
        self.mentions = {m.id: m for m in s.mentions}
        self.refs = {a.mention: a for a in s.references}
        self.mentions_of: dict[str, list[str]] = defaultdict(list)
        for a in sorted(s.references, key=lambda a: (self.mentions[a.mention].doc_id, a.mention)):
            self.mentions_of[a.canonical].append(a.mention)
        self.relations: dict[str, list[str]] = defaultdict(list)
        for r in s.relations:
            self.relations[r.source].append(f"{r.type} -> {r.target} ({self._name(r.target)})")
            self.relations[r.target].append(f"{r.source} ({self._name(r.source)}) {r.type} -> this")

    def _name(self, ref: str) -> str:
        return self.records[ref].name or self.records[ref].key

    def mention(self, mid: str) -> MentionView:
        m = self.mentions[mid]
        sentences = (x for c in m.chunks if c in self.chunks for x in split_sentences(self.chunks[c].text))
        sentence = next((x for x in sentences if contains_words(x, m.name)), "")
        return MentionView(id=m.id, name=m.name, document=m.doc_id, chunks=m.chunks, sentence=sentence)

    def record(self, ref: str) -> RecordView:
        r = self.records[ref]
        return RecordView(
            ref=r.id, label=r.label, key=r.key, name=r.name, cells=r.properties, relations=self.relations[ref]
        )

    def node(self, node: str, mentions: Iterable[str]) -> NodeView:
        """A node with the mentions given; a record also shows its row."""
        first = self.refs[self.mentions_of[node][0]] if self.mentions_of[node] else None
        record = self.record(node) if node in self.records else None
        return NodeView(
            id=node,
            kind="record" if record else (first.kind if first else "unknown"),
            type=record.label if record else (first.type if first else ""),
            name=(record.name or record.key) if record else (first.name if first else node),
            record=record,
            mentions=[self.mention(m) for m in mentions],
        )

    def sheet_chunks(self, ids: Iterable[str]) -> dict[str, SheetChunk]:
        return {
            c: SheetChunk(
                document=self.chunks[c].doc_id, heading=self.chunks[c].context, text=self.chunks[c].text
            )
            for c in sorted(set(ids))
            if c in self.chunks
        }


def build_sheets(s: GraphSnapshot, checks: CodeChecks, starts: Iterable[str], dataset: str) -> JudgingSheets:
    """The three sheets of one build and their code sides. `checks` are R87's code checks of `s` (the flags
    and split groups); `starts` the target nodes R90 placed (C6 walks W2 from each, in both arms)."""
    index = _Index(s)
    header = {"dataset": dataset, "build": s.source, "snapshot_hash": snapshot_digest(s)}
    c3, code3 = _identity(index, checks, header)
    c4, code4 = _links(index, s, checks, header)
    c6, code6 = _purity(index, s, sorted(set(starts)), header)
    return JudgingSheets(c3=c3, c4=c4, c6=c6, code={"C3": code3, "C4": code4, "C6": code6})


def _identity(index: _Index, checks: CodeChecks, header: dict) -> tuple[C3Sheet, CodeSide]:
    """C3: individual and concept nodes with more than one mention; the R87 split groups. Records are
    left out: their wrong merges are C4's INCORRECT links (R93 decision 3)."""
    merges, code = [], []
    for node, mids in sorted(index.mentions_of.items()):
        kind = index.refs[mids[0]].kind
        if len(mids) > 1 and kind in ("individual", "concept"):
            merges.append(MergeItem(id=f"m:{node}", node=index.node(node, mids)))
            code.append(CodeItem(id=f"m:{node}", node=node, kind=kind))
    splits = []
    for g in checks.splits:
        # the group's nodes, read by its own key (type and normalised name), as audit/checks.py made it
        nodes = sorted(
            {
                a.canonical
                for a in index.refs.values()
                if a.kind == "individual" and a.type == g.type and norm(a.name) == g.name
            }
        )
        item_id = f"s:{g.type}:{g.name}"
        splits.append(
            SplitItem(
                id=item_id,
                type=g.type,
                name=g.name,
                nodes=[index.node(n, index.mentions_of[n]) for n in nodes],
            )
        )
        code.append(CodeItem(id=item_id, node=",".join(nodes), kind="split"))
    mentions = [m for item in merges for m in item.node.mentions]
    mentions += [m for item in splits for n in item.nodes for m in n.mentions]
    sheet = C3Sheet(
        **header,
        question=MERGE_QUESTION,
        split_question=SPLIT_QUESTION,
        merges=merges,
        splits=splits,
        chunks=index.sheet_chunks(c for m in mentions for c in m.chunks),
    )
    return sheet, CodeSide(criterion="C3", items=code)


def _links(index: _Index, s: GraphSnapshot, checks: CodeChecks, header: dict) -> tuple[C4Sheet, CodeSide]:
    """C4: every mention-to-record link, then every unlinked mention R87 flagged as named after the key of
    a record (`label_mismatch` on a non-record reference), with that record."""
    flags: dict[str, list[str]] = defaultdict(list)
    for f in checks.flags:
        flags[f.item].append(f.kind)
    by_name: dict[str, list[str]] = defaultdict(list)
    by_key: dict[str, list[str]] = defaultdict(list)
    for r in s.records:
        by_name[norm(r.name or "")].append(r.id)
        by_key[squash(r.key)].append(r.id)
    pairs = [
        (a.mention, a.canonical, "link", f"{a.reason} {a.score}") for a in s.references if a.kind == "record"
    ]
    for a in s.references:
        if a.kind != "record" and "label_mismatch" in flags[a.mention]:
            name = index.mentions[a.mention].name
            pairs += [(a.mention, ref, "unlinked", f"{a.reason}") for ref in by_key[squash(name)]]
    items, code = [], []
    for mid, ref, kind, edge in sorted(pairs, key=lambda p: (index.mentions[p[0]].doc_id, p[0], p[1])):
        same = [
            r for r in by_name[norm(index.records[ref].name or "")] if r != ref and index.records[ref].name
        ]
        item_id = f"l:{mid}:{ref}"
        items.append(
            LinkItem(
                id=item_id,
                mention=index.mention(mid),
                record=index.record(ref),
                same_name=[index.record(r) for r in same],
            )
        )
        code.append(CodeItem(id=item_id, node=ref, kind=kind, flags=flags[mid], edge=edge))
    sheet = C4Sheet(
        **header,
        question=LINK_QUESTION,
        links=items,
        chunks=index.sheet_chunks(c for i in items for c in i.mention.chunks),
    )
    return sheet, CodeSide(criterion="C4", items=code)


def _purity(index: _Index, s: GraphSnapshot, starts: list[str], header: dict) -> tuple[C6Sheet, CodeSide]:
    """C6: every (start, chunk) pair W2 reaches in arm A or arm B, with the arms and the two flags:
    `same_label_about` (the chunk's section or document is ABOUT another record of the start's label, the
    direction's flag) and `scope_foreign` (the chunk's document has a scope without the start, R87's)."""
    arms = {arm: AnchorGraph(s, arm) for arm in Arm}
    scope = ScopeIndex(s)
    about: dict[str, set[str]] = defaultdict(set)
    for link in [*s.documents_about, *s.sections_about]:
        about[link.source].add(link.thing)
    items, code = [], []
    for start in starts:
        reached = {
            c: [a.value for a in Arm if c in arms[a].chunks_of(start)]
            for c in arms[Arm.LAYERED].chunks_of(start) | arms[Arm.ANCHOR].chunks_of(start)
        }
        for chunk in sorted(reached):
            doc = index.chunks[chunk].doc_id
            flags = []
            if start in index.records:
                label = index.records[start].label
                others = {t for t in about[chunk] | about[doc] if t != start and t in index.records}
                if any(index.records[t].label == label for t in others):
                    flags.append("same_label_about")
                if scope.outside(doc, start):
                    flags.append("scope_foreign")
            shown = [m for m in index.mentions_of[start] if chunk not in index.mentions[m].chunks]
            shown = (shown or index.mentions_of[start])[:START_MENTIONS]
            view = index.node(start, [] if start in index.records else shown)
            item_id = f"p:{start}|{chunk}"
            items.append(PurityItem(id=item_id, start=view, chunk=chunk))
            code.append(CodeItem(id=item_id, node=start, kind=view.kind, flags=flags, arms=reached[chunk]))
    shown_chunks = [i.chunk for i in items] + [c for i in items for m in i.start.mentions for c in m.chunks]
    sheet = C6Sheet(**header, question=PURITY_QUESTION, pairs=items, chunks=index.sheet_chunks(shown_chunks))
    return sheet, CodeSide(criterion="C6", items=code)
