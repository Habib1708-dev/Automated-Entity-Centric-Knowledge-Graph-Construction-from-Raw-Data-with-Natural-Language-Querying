"""The identity of a build's particular things (records and individuals), replayed offline (R98).

Role in the pipeline: after a build, for measuring a change to record linking or to the joining of
individuals without a rebuild (R99, R100). `audit/relink.py` replays the record matching; this module
replays the rest of `resolution/particulars.py` (the units, the pairs nominated, the LLM's adjudication of
each, the groups) through the same pure core, `particulars.assign_particulars`. Its inputs are what
`resolve_particulars` reads from the graph, restated over R87's snapshot:
  - the keyed and individual mentions, by the text schema's identity class, with their chunk counts
    (`mentions.read_mentions`);
  - every chunk naming them, by mention, document and position, the document named by its heading
    (`mentions.read_mention_texts`);
  - the record matches: the build's own (faithful mode) or the record replay's (measured mode);
  - the unit pairs near in meaning: the build's logged ones (faithful) or the embedder's (measured).
Design: faithful mode is the proof that the replay is the build's. With the build's matches and pairs,
today's adjudicator (answering from the LLM cache) must give resolve.json's individual decisions and
particular assignments field by field; `unfaithful` lists every difference. Measured mode explains each
mention whose canonical entity changed by a change of its group (`joined`: it is now one thing with a
mention it was not one with; `unjoined`: it only lost some); a mention whose record decision the record
replay changed carries that replay's cause instead, and anything else is unexplained, so the caller refuses
the replay.
Must not: read Neo4j, call any model but the adjudicator (and in measured mode the embedder) it is given, or
touch a concept mention.
"""

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from ..llm.base import LLMClient
from ..resolution.identity_graph import Assignment
from ..resolution.individuals import IndividualDecision
from ..resolution.mentions import MentionRecord, MentionText
from ..resolution.particulars import (
    AmbiguousMention,
    JoinSettings,
    Meaning,
    Particulars,
    assign_particulars,
)
from ..resolution.records import RecordCandidate, RecordLink, RecordMatch
from ..text.schema import TextSchema
from .inputs import Record
from .relink import MATCH_REASONS, snapshot_views
from .snapshot import GraphSnapshot


class BuiltIdentity(BaseModel):
    """What a build's resolve.json says about its particulars: every assignment, the keyed mentions tied
    between records, and the individuals' decisions (the rest of the file is the concepts')."""

    assignments: list[Assignment]
    ambiguous: list[AmbiguousMention] = []
    individual_decisions: list[IndividualDecision] = []


def particular_mentions(
    s: GraphSnapshot, schema: TextSchema
) -> tuple[list[MentionRecord], list[MentionRecord]]:
    """The keyed and the individual-class mentions, ordered by id as `read_mentions` returns them. Their
    scope anchors stay empty: only the record matching reads them, and it is not replayed here."""

    def record(m) -> MentionRecord:
        return MentionRecord(id=m.id, name=m.name, type=m.type, doc_id=m.doc_id, chunks=len(m.chunks))

    mentions = sorted(s.mentions, key=lambda m: m.id)
    keyed = [record(m) for m in mentions if schema.identity_of(m.type) == "keyed"]
    individual = [record(m) for m in mentions if schema.identity_of(m.type) == "individual"]
    return keyed, individual


def mention_texts(s: GraphSnapshot, ids: set[str]) -> list[MentionText]:
    """`read_mention_texts` over the snapshot: one row per chunk mentioning each mention of `ids`, ordered by
    mention, document and position. The order and the document names are the adjudicator's lines, so they
    decide its prompts and with them the LLM cache's answers."""
    chunks = {c.chunk_id: c for c in s.chunks}
    rows = []
    for m in sorted(s.mentions, key=lambda m: m.id):
        if m.id not in ids:
            continue
        for c in sorted((chunks[x] for x in m.chunks if x in chunks), key=lambda c: (c.doc_id, c.index)):
            # a chunk with an empty context is named by its document id, as the graph read names it
            rows.append(
                MentionText(mention=m.id, document=c.context or c.doc_id, chunk_id=c.chunk_id, text=c.text)
            )
    return rows


def built_matches(s: GraphSnapshot, built: BuiltIdentity, schema: TextSchema) -> dict[str, RecordMatch]:
    """The build's own record decision for every keyed mention: the record it linked by matching (with the
    build's reason, score, evidence and element id), else the records it could not choose between, else
    none. A record reached by an adjudicated join is no match: that join is what the replay decides again."""
    records = {r.id: r for r in s.records}
    by_mention = {a.mention: a for a in built.assignments}
    tied = {a.mention: a.records for a in built.ambiguous}
    out = {}
    for m in particular_mentions(s, schema)[0]:
        a = by_mention.get(m.id)
        if a is not None and a.kind == "record" and a.reason in MATCH_REASONS:
            out[m.id] = RecordMatch(link=_built_link(a, records[a.canonical]))
        else:
            out[m.id] = RecordMatch(tied=[_candidate(records[ref]) for ref in tied.get(m.id, [])])
    return out


def _built_link(a: Assignment, record: Record) -> RecordLink:
    """The link a build's edge records. `model_construct`: a build may carry a reason a later rule change
    retired (R95a's `contained`), which today's `LinkReason` refuses, and the replay must give it back as it
    was. `scoped` was never logged; nothing after the matching reads it."""
    candidate = RecordCandidate(
        element_id=a.target or a.canonical, label=record.label, name=a.name, key=record.key
    )
    return RecordLink.model_construct(
        record=candidate, reason=a.reason, score=a.score, evidence=a.evidence, scoped=False, by=a.by
    )


def _candidate(record: Record) -> RecordCandidate:
    return RecordCandidate(
        element_id=record.id, label=record.label, name=record.name or record.key, key=record.key
    )


def logged_meaning(built: BuiltIdentity) -> Meaning:
    """The unit pairs the build nominated by meaning, from its decisions: units are named by their founding
    mention, so the same units give the same pairs without the embedder."""
    near = {frozenset((d.a, d.b)) for d in built.individual_decisions if d.signal == "meaning"}
    return lambda units: near


def reidentify(
    s: GraphSnapshot,
    schema: TextSchema,
    matches: dict[str, RecordMatch],
    meaning: Meaning,
    llm: LLMClient | None,
    settings: JoinSettings,
) -> Particulars:
    """The particulars of `s` decided again: `assign_particulars` on the snapshot's mentions and chunks,
    with the records' data as the graph read gives it (`relink.snapshot_views`)."""
    keyed, individual = particular_mentions(s, schema)
    texts = mention_texts(s, {m.id for m in (*keyed, *individual)})
    views = snapshot_views(s) if llm is not None else {}
    return assign_particulars(keyed, individual, matches, texts, meaning, llm, settings, views)


def unfaithful(built: BuiltIdentity, replay: Particulars) -> list[str]:
    """Every difference between the build's particulars and a faithful replay's, field by field: the
    individual decisions in their order, and the assignment of each keyed or individual mention."""
    issues = []
    if len(built.individual_decisions) != len(replay.decisions):
        issues.append(
            f"{len(replay.decisions)} individual decisions; the build logged "
            f"{len(built.individual_decisions)}"
        )
    for i, (before, after) in enumerate(zip(built.individual_decisions, replay.decisions, strict=False)):
        if diff := _diff(before, after):
            issues.append(f"individual decision {i} ({before.a_name!r} / {before.b_name!r}): {diff}")
    by_mention = {a.mention: a for a in built.assignments}
    replayed = {a.mention for a in replay.assignments}
    for a in replay.assignments:
        if diff := _diff(by_mention.get(a.mention), a):
            issues.append(f"assignment of {a.mention} {a.said!r}: {diff}")
    issues += [
        f"assignment of {a.mention} {a.said!r}: in the build, not in the replay"
        for a in built.assignments
        if a.kind != "concept" and a.mention not in replayed
    ]
    return issues


def _diff(before: BaseModel | None, after: BaseModel) -> str:
    if before is None:
        return "not in the build"
    fields = [k for k in type(after).model_fields if getattr(before, k) != getattr(after, k)]
    return ", ".join(f"{k} {getattr(before, k)!r} -> {getattr(after, k)!r}" for k in fields)


# Why a mention's canonical entity differs from the build's when only the joining explains it
JoinCause = Literal["joined", "unjoined"]


class IdentityChange(BaseModel):
    """A particular mention whose canonical entity a measured replay changed, not by its own record."""

    mention: str
    name: str
    doc_id: str
    before: str  # its canonical entity in the build
    after: str
    cause: JoinCause | None  # None: no change of its group explains it

    @property
    def explained(self) -> bool:
        return self.cause is not None


def identity_changes(
    s: GraphSnapshot, replayed: list[Assignment], relinked: set[str]
) -> list[IdentityChange]:
    """The particular mentions of `replayed` whose canonical entity differs from the build's (`s.references`),
    except those whose own record decision the record replay changed (`relinked`): those carry its cause."""
    before = {a.mention: a.canonical for a in s.references if a.kind != "concept"}
    after = {a.mention: a.canonical for a in replayed}
    doc = {m.id: m.doc_id for m in s.mentions}
    group_before, group_after = _groups(before), _groups(after)
    out = []
    for a in sorted(replayed, key=lambda a: a.mention):
        m = a.mention
        if m in relinked or before.get(m) == after[m]:
            continue
        gained, lost = (
            group_after[m] - group_before.get(m, set()),
            group_before.get(m, set()) - group_after[m],
        )
        cause: JoinCause | None = "joined" if gained else "unjoined" if lost else None
        out.append(
            IdentityChange(
                mention=m, name=a.said, doc_id=doc[m], before=before.get(m, ""), after=after[m], cause=cause
            )
        )
    return out


def _groups(canonical: dict[str, str]) -> dict[str, set[str]]:
    """Each mention -> the mentions sharing its canonical entity (itself included)."""
    members: dict[str, set[str]] = defaultdict(set)
    for mention, entity in canonical.items():
        members[entity].add(mention)
    return {mention: members[entity] for mention, entity in canonical.items()}


def with_build_targets(s: GraphSnapshot, replayed: list[Assignment]) -> list[Assignment]:
    """The replay's record edges with the element id the build's graph gave their record, where the build
    linked it (a replay names records by ref; a record the build never linked keeps none)."""
    targets = {a.canonical: a.target for a in s.references if a.kind == "record"}
    return [
        a.model_copy(update={"target": targets.get(a.canonical)}) if a.kind == "record" else a
        for a in replayed
    ]


class ReidentifyReport(BaseModel):
    """What a replay of the individuals gave: in faithful mode every difference from the build (none when it
    is the build's), in measured mode the changed mentions with their causes; and the replayed decisions."""

    build: str
    faithful: bool  # the mode: the build's own matches and pairs, which the replay must reproduce
    issues: list[str] = []  # faithful mode: every difference from the build
    changes: list[IdentityChange] = []  # measured mode
    decisions: list[IndividualDecision]
