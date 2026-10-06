"""The graph audit's checks that code decides alone (R87 part a): provenance (M5) and the flags that point
the judge at likely errors in record links (M3), attachments (M4) and splits (M1b).

Role in the pipeline: after the snapshot passed the fidelity gate; its report is the cheapest useful audit
(no judge) and its flags seed the judged sheets of the later parts.
Design: each check reads the snapshot and says exactly what it counted. Provenance rates are bugs when below
1.0 (every claim must point at an existing chunk that contains its quote), so they are reported with their
misses. A flag is a suspicion, never a verdict: each carries the graph relation and the text that raised it,
so a reader can check it, and the judge decides in parts c-e.
  - `cross_scope_link`: a mention refers to a record outside its document's scope (`audit/scope.py`), the
    whole-domain fallback of `resolution/records.py`;
  - `label_mismatch`: a mention refers to a record of a label its type does not name, or a mention is
    named exactly like the key of a record of such a label (a recall key as a vehicle);
  - `fuzzy_name`: a mention reached its record by a spelling score below 100;
  - `cross_scope_attachment`: a claim hangs on a record outside its document's scope;
  - `compound_name`: a `key_in_sentence` attachment whose name stands in the claim's sentences only inside
    a longer name the document writes for something else ("drawer" inside "drawer rails");
  - `non_end` (counted, not listed): a `key_in_sentence` attachment to a thing neither end of the claim
    refers to: the co-mentions the judge must look at.
Must not: call an LLM, or turn a flag into a score.
"""

from collections import defaultdict

from pydantic import BaseModel

from ..core.text import claim_sentences, contains_words, norm, squash
from ..text.extraction import RawTriple, verify
from ..text.schema import TextSchema
from ..validation.interval import Proportion
from .reach import ReachReport
from .scope import ScopeIndex
from .snapshot import GraphSnapshot, SnapshotClaim


class Flag(BaseModel):
    """One suspicion, with what raised it."""

    kind: str
    item: str  # a mention id, or "<observation id> -> <thing>"
    graph: str  # the relation as a reader would draw it
    text: str  # the sentence, quote or rule that raised it


class SplitGroup(BaseModel):
    """Individuals of one type and one normalised name that are several canonical entities."""

    type: str
    name: str
    canonicals: int
    documents: list[str]


class CodeChecks(BaseModel):
    """The report of part a."""

    provenance: dict[str, Proportion]  # "<extracted|derived>.<check>" -> share passing
    provenance_misses: dict[str, list[str]]  # same keys -> ids that failed
    flags: list[Flag]
    flag_counts: dict[str, int]
    record_links: int
    record_links_without_scope: int  # links in documents without anchors: no scope, never cross-scope
    non_end_attachments: int
    key_in_sentence_attachments: int
    splits: list[SplitGroup]
    reach: ReachReport | None = None

    def metrics(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, p in self.provenance.items():
            if p.rate is not None:
                out[f"provenance_{name.replace('.', '_')}"] = p.rate
        out |= {f"flags_{kind}": float(n) for kind, n in self.flag_counts.items()}
        out |= {
            "record_links": float(self.record_links),
            "record_links_without_scope": float(self.record_links_without_scope),
            "attachments_non_end": float(self.non_end_attachments),
            "attachments_key_in_sentence": float(self.key_in_sentence_attachments),
            "split_groups": float(len(self.splits)),
            "split_extra_canonicals": float(sum(g.canonicals - 1 for g in self.splits)),
        }
        if self.reach is not None:
            out |= self.reach.metrics()
        return out


# Every flag kind, so a kind with no flag still reports 0 (a count of 0 is a result).
FLAG_KINDS = (
    "cross_scope_link",
    "label_mismatch",
    "fuzzy_name",
    "cross_scope_attachment",
    "compound_name",
)


def run_checks(s: GraphSnapshot, schema: TextSchema, reach: ReachReport | None = None) -> CodeChecks:
    """Provenance, the flags and the split census of one snapshot; `reach` (audit/reach.py) is carried."""
    scope = ScopeIndex(s)
    provenance, misses = _provenance(s, schema)
    flags = [*_link_flags(s, schema, scope), *_attachment_flags(s, scope)]
    counts = {kind: 0 for kind in FLAG_KINDS}
    for f in flags:
        counts[f.kind] += 1
    record_refs = [a for a in s.references if a.kind == "record"]
    doc_of = {m.id: m.doc_id for m in s.mentions}
    non_end, kis = _non_end(s)
    return CodeChecks(
        provenance=provenance,
        provenance_misses=misses,
        flags=flags,
        flag_counts=counts,
        record_links=len(record_refs),
        record_links_without_scope=sum(not scope.has_scope(doc_of.get(a.mention, "")) for a in record_refs),
        non_end_attachments=non_end,
        key_in_sentence_attachments=kis,
        splits=_splits(s),
        reach=reach,
    )


def _provenance(s: GraphSnapshot, schema: TextSchema) -> tuple[dict[str, Proportion], dict[str, list[str]]]:
    """M5: every claim's chunk exists and is of its document, its quote is in the chunk, and (extracted
    claims) it passes the extractor's own `verify`; every extracted mention's name is in a chunk it is
    MENTIONED in (or that chunk's document context). Derived mentions are named after plan nodes, by design
    not after the text, so they are left out of the last check."""
    chunks = {c.chunk_id: c for c in s.chunks}
    type_of = {m.id: m.type for m in s.mentions}
    results: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for c in s.claims:
        group = "derived" if c.derived else "extracted"
        chunk = chunks.get(c.chunk_id)
        results[f"{group}.chunk_exists"].append((c.id, chunk is not None))
        results[f"{group}.doc_consistent"].append((c.id, chunk is not None and chunk.doc_id == c.doc_id))
        results[f"{group}.evidence_in_chunk"].append(
            (c.id, chunk is not None and bool(c.evidence) and norm(c.evidence) in norm(chunk.text))
        )
        if not c.derived:
            ok = chunk is not None and verify(_triple(c, type_of), chunk.text, schema, chunk.context) is None
            results["extracted.ends_grounded"].append((c.id, ok))
    for m in s.mentions:
        if not m.derived:
            texts = [chunks[c].text + " " + chunks[c].context for c in m.chunks if c in chunks]
            group = "pass" if m.found_by_pass else "extracted"  # the mention pass's own row (R101)
            results[f"{group}.mention_in_chunk"].append((m.id, any(contains_words(t, m.name) for t in texts)))
    rates = {k: Proportion.of(sum(ok for _, ok in v), len(v)) for k, v in results.items()}
    return rates, {k: [i for i, ok in v if not ok] for k, v in results.items()}


def _triple(c: SnapshotClaim, type_of: dict[str, str]) -> RawTriple:
    return RawTriple(
        subject=c.subject_name,
        subject_type=type_of.get(c.subject, ""),
        predicate=c.predicate,
        object=c.object_name,
        object_type=type_of.get(c.object, ""),
        evidence=c.evidence,
        polarity=c.polarity,
        time=c.time,
        truth=c.truth,
        negation=c.negation,
        modality=c.modality,
        hedge=c.hedge,
        condition=c.condition,
    )


def _link_flags(s: GraphSnapshot, schema: TextSchema, scope: ScopeIndex) -> list[Flag]:
    mentions = {m.id: m for m in s.mentions}
    label_of = {r.id: r.label for r in s.records}
    keys_by_label: dict[str, set[str]] = defaultdict(set)
    for r in s.records:
        keys_by_label[r.label].add(squash(r.key))
    flags = []
    for a in s.references:
        m = mentions.get(a.mention)
        if m is None:
            continue
        etype = schema.entity_type(m.type)
        labels = set(etype.record_labels) if etype is not None else set()
        edge = f"[:REFERS_TO {{{a.reason}, {a.score}}}]"
        graph = f'(Mention "{m.name}" @{m.doc_id})-{edge}->({a.canonical} "{a.name}")'
        if a.kind == "record":
            if scope.outside(m.doc_id, a.canonical):
                flags.append(
                    Flag(
                        kind="cross_scope_link",
                        item=m.id,
                        graph=graph,
                        text=f"document anchors: {', '.join(scope.anchor_names(m.doc_id))}",
                    )
                )
            if label_of.get(a.canonical) not in labels:
                flags.append(
                    Flag(
                        kind="label_mismatch",
                        item=m.id,
                        graph=graph,
                        text=f"type {m.type} names labels {sorted(labels)}",
                    )
                )
            if a.reason == "name" and (a.score or 0) < 100:
                flags.append(
                    Flag(kind="fuzzy_name", item=m.id, graph=graph, text=f"spelling score {a.score}")
                )
        elif any(squash(m.name) in keys for label, keys in keys_by_label.items() if label not in labels):
            other = [
                label
                for label, keys in keys_by_label.items()
                if label not in labels and squash(m.name) in keys
            ]
            flags.append(
                Flag(
                    kind="label_mismatch",
                    item=m.id,
                    graph=graph,
                    text=f"named like a key of {other}, a label type {m.type} does not name",
                )
            )
    return flags


def _attachment_flags(s: GraphSnapshot, scope: ScopeIndex) -> list[Flag]:
    claims = {c.id: c for c in s.claims}
    refers = {a.mention: a.canonical for a in s.references}
    names_by_doc: dict[str, list[tuple[str, str]]] = defaultdict(list)  # doc -> (mention name, its canonical)
    for m in s.mentions:
        names_by_doc[m.doc_id].append((m.name, refers.get(m.id, m.id)))
    flags = []
    for a in s.attachments:
        c = claims[a.observation]
        claim = f"({c.subject_name} {c.predicate} {c.object_name})"
        graph = f'({a.thing} "{a.name}")-[:HAS_OBSERVATION {{{a.how}}}]->{claim}'
        if a.kind.startswith("record:") and scope.outside(c.doc_id, a.thing):
            flags.append(
                Flag(kind="cross_scope_attachment", item=f"{c.id} -> {a.thing}", graph=graph, text=c.evidence)
            )
        if a.how == "key_in_sentence" and _only_inside_longer(c, a.evidence, a.thing, names_by_doc[c.doc_id]):
            flags.append(
                Flag(kind="compound_name", item=f"{c.id} -> {a.thing}", graph=graph, text=c.evidence)
            )
    return flags


def _only_inside_longer(c: SnapshotClaim, name: str, thing: str, doc_names: list[tuple[str, str]]) -> bool:
    """True when every sentence of the claim that names `name` names it only as part of a longer name the
    document writes for another thing."""
    sentences = [
        x for x in claim_sentences(c.evidence, [c.subject_name, c.object_name]) if contains_words(x, name)
    ]
    longer = [
        n
        for n, canonical in doc_names
        if canonical != thing and norm(n) != norm(name) and contains_words(n, name)
    ]
    return bool(sentences) and all(any(contains_words(x, n) for n in longer) for x in sentences)


def _non_end(s: GraphSnapshot) -> tuple[int, int]:
    """(key_in_sentence attachments to a thing neither end refers to, all key_in_sentence attachments)."""
    refers = {a.mention: a.canonical for a in s.references}
    ends = {c.id: {refers.get(c.subject), refers.get(c.object)} for c in s.claims}
    kis = [a for a in s.attachments if a.how == "key_in_sentence"]
    return sum(a.thing not in ends[a.observation] for a in kis), len(kis)


def _splits(s: GraphSnapshot) -> list[SplitGroup]:
    """M1b census: individuals sharing a type and a normalised name but not a canonical entity."""
    doc_of = {m.id: m.doc_id for m in s.mentions}
    groups: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for a in s.references:
        if a.kind == "individual":
            groups[(a.type, norm(a.name))][a.canonical].add(doc_of.get(a.mention, ""))
    return [
        SplitGroup(type=t, name=n, canonicals=len(by), documents=sorted(set().union(*by.values())))
        for (t, n), by in sorted(groups.items())
        if len(by) > 1
    ]
