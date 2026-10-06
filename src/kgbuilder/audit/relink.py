"""The record matching of a finished build, replayed offline under the current rules (R94, R95a, R95b).

Role in the pipeline: after a build, for measuring a change to `resolution/records.py` without a rebuild.
The build's graph is gone and `kg resolve` also asks an LLM to join individuals, but matching a keyed
mention to a record is pure code (`match_record`). This module feeds it what `resolution/particulars.py`
reads from the graph, restated over R87's snapshot, and applies the decisions that differ to the build's
REFERS_TO edges:
  - the keyed mentions, by the text schema's identity class;
  - each mention's candidates (the records of its type's plan labels, with their key attributes);
  - its scopes (the records within two hops of each thing its document is ABOUT, from the link stage only);
  - its sentences (those of its chunks that name it), and for its near misses what the data holds about
    them (cells and one-hop relations, as `record_choice.read_candidate_views` reads them).
Design: the replay is checked against the build itself. A mention whose decision changes must have lost a
link the build made by matching, for a cause a rule change names (`RelinkCause`), or be linked by a choice
of the LLM among its near misses (R95b, only when an LLM is given); any other change means the replay is not
the build's matching, and it is listed as unexplained so the caller can refuse it.
A mention that loses its record stands for itself, as `resolution/particulars.py` makes such a mention
before joining; the LLM's joining of individuals is replayed apart, from `Relink.matches`
(audit/reidentify.py, R98).
Must not: read Neo4j, call an LLM other than the chooser it is given, or change anything but the changed
mentions' REFERS_TO.
"""

from collections import defaultdict
from typing import Literal, get_args

from pydantic import BaseModel

from ..core.identity import individual_id, record_ref
from ..core.text import sentences_naming
from ..llm.base import LLMClient
from ..resolution.attachment import TEXT_ABOUT
from ..resolution.identity_graph import Assignment
from ..resolution.names import name_score
from ..resolution.record_choice import (
    CandidateView,
    ChoiceDecision,
    ChoiceRequest,
    choice_lines,
    choose_records,
    chosen_links,
    near_misses,
    relation_line,
)
from ..resolution.records import LinkReason, RecordCandidate, RecordLink, RecordMatch, match_record
from ..structured.plan import ConstructionPlan
from ..text.chunking import Chunk
from ..text.schema import TextSchema
from .fidelity import LoggedCounts, snapshot_counts
from .inputs import Record, scopes
from .snapshot import GraphSnapshot, SnapshotMention

# The reason builds before R95a gave a link by containment, which no longer links
_CONTAINED = "contained"

# The reasons of a link made by matching, today's and the retired one; any other record assignment (an
# adjudicated join) is not one. Without `contained` here, a build's containment link would count as no link,
# and a replay that drops it would show no change at all. The individuals' replay reads the build's own
# matches by the same rule (reidentify.py).
MATCH_REASONS = frozenset(get_args(LinkReason)) | {_CONTAINED}

# Why the replay may decide otherwise than the build, one cause per rule change:
#   - `left_scope` (R94): the record is outside the scope of the mention's document;
#   - `containment` (R95a): the record's name stood inside the mention's, which no longer links;
#   - `spelling` (R95a): the names were spelled alike, but not the same words up to their endings;
#   - `chosen` (R95b): the LLM chose the record among the mention's near misses, and code verified it.
RelinkCause = Literal["left_scope", "containment", "spelling", "chosen"]


class RelinkChange(BaseModel):
    """A keyed mention whose record decision differs from the build's."""

    mention: str
    name: str
    doc_id: str
    before: str | None  # the record the build's matching linked, None when it linked none
    after: str | None
    explained: bool  # a rule change (`cause`) explains it
    cause: RelinkCause | None = None  # None when unexplained, and in R94's reports, which predate the field


class Relink(BaseModel):
    keyed: int  # keyed mentions replayed
    changes: list[RelinkChange]
    references: list[Assignment]  # the build's REFERS_TO with the changes applied
    choices: list[ChoiceDecision] = []  # the mentions with near misses, and the chooser's outcome for each
    # every keyed mention's replayed match, choices included: what the individuals' replay forms units from
    matches: dict[str, RecordMatch] = {}

    @property
    def unexplained(self) -> list[RelinkChange]:
        return [c for c in self.changes if not c.explained]


def relink(
    s: GraphSnapshot,
    plan: ConstructionPlan,
    schema: TextSchema,
    thresholds: tuple[float, float],
    llm: LLMClient | None = None,
    model: str = "",
) -> Relink:
    """Replay the record matching of every keyed mention of `s` and apply the decisions that changed.

    `thresholds` are the build's spelling scores for a link and for a near miss. With `llm`, the mentions with
    near misses are offered to it (`model`) as `kg resolve` offers them; without, that tier abstains.
    """
    threshold, borderline = thresholds
    replay = _Replay(s, plan, schema)
    keyed = [m for m in s.mentions if schema.identity_of(m.type) == "keyed"]
    matches = {m.id: replay.match(m, threshold) for m in keyed}
    requests = [
        replay.request(m, near)
        for m in keyed
        if (
            near := near_misses(
                m.name, matches[m.id], replay.scopes_of(m), borderline, domain=replay.candidates_of(m)
            )
        )
    ]
    views = {c.element_id: replay.view(c.element_id) for r in requests for c in r.candidates}
    choices = choose_records(requests, views, llm, model)
    matches |= {mention: RecordMatch(link=link) for mention, link in chosen_links(choices, requests).items()}
    built = {a.mention: a for a in s.references}
    changes = [c for m in keyed if (c := _change(m, built.get(m.id), matches[m.id], replay, threshold))]
    replaced = {c.mention: matches[c.mention] for c in changes}
    references = [_replayed(a, replaced[a.mention]) if a.mention in replaced else a for a in s.references]
    return Relink(keyed=len(keyed), changes=changes, references=references, choices=choices, matches=matches)


def _change(
    m: SnapshotMention, built: Assignment | None, match: RecordMatch, replay: "_Replay", threshold: float
) -> RelinkChange | None:
    """How the replay's decision for `m` differs from the build's (`built`, its assignment), or None.

    A different record is a change; so is the same record reached by a choice the build did not make (the
    build linked it by containment, or joined it by adjudication): its edge must say why it holds now.
    """
    link = match.link
    before, after = _linked(built), _ref(link.record) if link else None
    newly_chosen = (
        link is not None and link.reason == "chosen" and (built is None or built.reason != "chosen")
    )
    if before == after and not newly_chosen:
        return None
    cause = _cause(built, link, replay.scope(m.doc_id), threshold)
    return RelinkChange(
        mention=m.id, name=m.name, doc_id=m.doc_id, before=before, after=after,
        explained=cause is not None, cause=cause,
    )  # fmt: skip


def _ref(record: RecordCandidate) -> str:
    return record_ref(record.label, record.key)


def _linked(a: Assignment | None) -> str | None:
    """The record a build's assignment linked by matching (not by an adjudicated join)."""
    return a.canonical if a is not None and a.kind == "record" and a.reason in MATCH_REASONS else None


def _cause(
    built: Assignment | None, link: RecordLink | None, scope: set[str] | None, threshold: float
) -> RelinkCause | None:
    """The rule change that explains why the replay's decision (`link`) differs from the build's (`built`, its
    assignment), or None when no rule change does. A new link is explained only as the chooser's; a lost one
    by the rule that made it. `scope` is the records the mention's document may link to (None: it has no
    scope), `threshold` the build's spelling threshold."""
    if link is not None:
        return "chosen" if link.reason == "chosen" else None
    if built is None or _linked(built) is None:  # nothing was lost, so there is nothing a rule took away
        return None
    if scope is not None and built.canonical not in scope:
        return "left_scope"
    if built.reason == _CONTAINED:
        return "containment"
    # `built.name` is the record's name the build linked to: is the pair still one name for today's rule?
    if built.reason == "name" and name_score(built.said, built.name, threshold) is None:
        return "spelling"
    return None


def _replayed(a: Assignment, match: RecordMatch) -> Assignment:
    """The replay's edge of a changed mention: the record it links (by the chooser, when explained), or
    itself when it lost its record."""
    return _record_edge(a, match.link) if match.link is not None else _alone(a, match)


def _record_edge(a: Assignment, link: RecordLink) -> Assignment:
    """A mention the replay links: `particulars._record_assignment`. Without a target: the build's graph,
    where the record's element id lived, is gone, and the snapshot names records by ref."""
    return Assignment(
        mention=a.mention, said=a.said, kind="record", canonical=_ref(link.record), name=link.record.name,
        type=a.type, reason=link.reason, score=link.score, evidence=link.evidence, by=link.by,
    )  # fmt: skip


def _alone(a: Assignment, match: RecordMatch) -> Assignment:
    """A mention no record fits, standing for itself: `particulars._individual` with itself as founder."""
    tied = ", ".join(record_ref(r.label, r.key) for r in match.tied)
    return Assignment(
        mention=a.mention, said=a.said, kind="individual", canonical=individual_id(a.mention), name=a.said,
        type=a.type, reason="ambiguous_record" if match.tied else "no_record", evidence=tied,
    )  # fmt: skip


class _Replay:
    """`particulars._match_records`' reads, restated over the snapshot and computed once."""

    def __init__(self, s: GraphSnapshot, plan: ConstructionPlan, schema: TextSchema) -> None:
        self._schema = schema
        key_column = {rule.label: rule.unique_column for rule in plan.nodes}
        self._records = s.records
        self._key_column = key_column
        self._reach = scopes(s.records, s.relations)
        # what each document is ABOUT from the link stage: `mentions.read_mentions` leaves out text links
        self._anchors: dict[str, list[str]] = defaultdict(list)
        for link in s.documents_about:
            if link.how != TEXT_ABOUT:
                self._anchors[link.source].append(link.thing)
        self._chunks = {c.chunk_id: c for c in s.chunks}
        self._views = snapshot_views(s)

    def scope(self, doc_id: str) -> set[str] | None:
        """The records a document's mentions may link to; None when it is about nothing (no scope)."""
        anchors = self._anchors.get(doc_id, [])
        return set().union(*(self._reach.get(a, {a}) for a in anchors)) if anchors else None

    def match(self, m: SnapshotMention, threshold: float) -> RecordMatch:
        sentences = [x for c in self._chunks_of(m) for x in sentences_naming(c.text, [m.name])]
        return match_record(m.name, sentences, self.candidates_of(m), self.scopes_of(m), threshold)

    def candidates_of(self, m: SnapshotMention) -> list[RecordCandidate]:
        """The records of the mention's type's labels, as `records.read_records` gives them."""
        etype = self._schema.entity_type(m.type)
        labels = set(etype.record_labels) if etype else set()
        wanted = set(etype.key_attributes) if etype else set()
        return [self._candidate(r, wanted) for r in self._records if r.label in labels]

    def scopes_of(self, m: SnapshotMention) -> list[list[RecordCandidate]]:
        """The candidates near each thing the mention's document is about, one list per thing."""
        candidates = self.candidates_of(m)
        anchors = sorted(self._anchors.get(m.doc_id, []))
        return [[c for c in candidates if c.element_id in self._reach.get(a, {a})] for a in anchors]

    def _chunks_of(self, m: SnapshotMention) -> list[Chunk]:
        return sorted((self._chunks[c] for c in m.chunks if c in self._chunks), key=lambda c: c.index)

    def request(self, m: SnapshotMention, near: list[RecordCandidate]) -> ChoiceRequest:
        """What `particulars._choose_records` asks about `m`. A chunk's document is its heading, else its
        document id, as `mentions.read_mention_texts` names it."""
        chunks = self._chunks_of(m)
        return ChoiceRequest(
            mention=m.id,
            name=m.name,
            lines=choice_lines(m.name, [(c.context or c.doc_id, c.text) for c in chunks]),
            texts=[c.text for c in chunks],
            candidates=near,
            scoped=self.scope(m.doc_id) is not None,
        )

    def view(self, ref: str) -> CandidateView:
        """`record_choice.read_candidate_views` over the snapshot (`snapshot_views`)."""
        return self._views[ref]

    def _candidate(self, r: Record, wanted: set[str]) -> RecordCandidate:
        """`records.read_records`: the key column is an attribute too; a missing value stays out."""
        key_column = self._key_column[r.label]
        attributes = {a: r.key if a == key_column else r.properties.get(a, "") for a in wanted}
        return RecordCandidate(
            element_id=r.id, label=r.label, name=r.name or r.key, key=r.key,
            attributes={a: v for a, v in attributes.items() if v},
        )  # fmt: skip


def snapshot_views(s: GraphSnapshot) -> dict[str, CandidateView]:
    """Record ref -> `record_choice.read_candidate_views` over the snapshot: the record's plan properties
    and its sorted one-hop relations, each relation seen from both ends, as the graph read sees it."""
    by_id = {r.id: r for r in s.records}

    def name(ref: str) -> str:
        return by_id[ref].name or by_id[ref].key

    relations: dict[str, list[str]] = defaultdict(list)
    for rel in s.relations:
        relations[rel.source].append(relation_line(rel.type, True, rel.target, name(rel.target)))
        relations[rel.target].append(relation_line(rel.type, False, rel.source, name(rel.source)))
    return {
        r.id: CandidateView(cells=dict(r.properties), relations=sorted(relations[r.id])) for r in s.records
    }


# Counts a build logs that rest on identity: the resolve stage's, and the attach stage's, which hangs claims
# and documents on what mentions refer to. A replay changes these and only these.
IDENTITY_COUNTS = ("resolve.", "attach.")


class RelinkReport(BaseModel):
    """What a replay changed: the mentions, the logged counts as `[build, replay]`, and the chooser's
    decisions (R95b; absent from the reports before it)."""

    build: str
    changes: list[RelinkChange]
    counts: dict[str, list[int]]
    choices: list[ChoiceDecision] = []


def relinked_counts(
    logged: LoggedCounts, replayed: GraphSnapshot
) -> tuple[LoggedCounts, dict[str, list[int]]]:
    """The logged counts of the replayed build: the build's own, except those resting on identity, which are
    the replayed snapshot's. Returns them and every count that changed, as `[build, replay]`. The fidelity
    gate of the replayed build then checks the others (ingest, extract, link) against the build; the
    replaced ones it can only confirm by construction."""
    counts = dict(logged.counts)
    for name, value in snapshot_counts(replayed).items():
        if name.startswith(IDENTITY_COUNTS) and name in counts:
            counts[name] = value
    changed = {k: [logged.counts[k], v] for k, v in counts.items() if logged.counts[k] != v}
    return logged.model_copy(update={"counts": counts}), changed
