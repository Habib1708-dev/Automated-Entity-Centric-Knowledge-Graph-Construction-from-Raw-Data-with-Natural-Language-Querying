"""The record matching of a finished build, replayed offline under the current rules (R94, R95a).

Role in the pipeline: after a build, for measuring a change to `resolution/records.py` without a rebuild.
The build's graph is gone and `kg resolve` also asks an LLM to join individuals, but matching a keyed
mention to a record is pure code (`match_record`). This module feeds it what `resolution/particulars.py`
reads from the graph, restated over R87's snapshot, and applies the decisions that differ to the build's
REFERS_TO edges:
  - the keyed mentions, by the text schema's identity class;
  - each mention's candidates (the records of its type's plan labels, with their key attributes);
  - its scopes (the records within two hops of each thing its document is ABOUT, from the link stage only);
  - its sentences (those of its chunks that name it).
Design: the replay is checked against the build itself. A mention whose decision changes must have lost a
link the build made by matching, for a cause a rule change names (`RelinkCause`); any other change means
the replay is not the build's matching, and it is listed as unexplained so the caller can refuse it.
A mention that loses its record stands for itself, as `resolution/particulars.py` makes such a mention
before joining: the LLM's joining of individuals is not replayed.
Must not: call an LLM, read Neo4j, or change anything but the changed mentions' REFERS_TO.
"""

from collections import defaultdict
from typing import Literal, get_args

from pydantic import BaseModel

from ..core.identity import individual_id, record_ref
from ..core.text import sentences_naming
from ..resolution.attachment import TEXT_ABOUT
from ..resolution.identity_graph import Assignment
from ..resolution.records import LinkReason, RecordCandidate, RecordMatch, match_record, name_score
from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from .fidelity import LoggedCounts, snapshot_counts
from .inputs import Record, scopes
from .snapshot import GraphSnapshot, SnapshotMention

# The reason builds before R95a gave a link by containment, which no longer links
_CONTAINED = "contained"

# The reasons of a link made by matching, today's and the retired one; any other record assignment (an
# adjudicated join) is not one. Without `contained` here, a build's containment link would count as no link,
# and a replay that drops it would show no change at all.
_MATCH_REASONS = frozenset(get_args(LinkReason)) | {_CONTAINED}

# Why the replay may unlink what the build linked by matching, one cause per rule change:
#   - `left_scope` (R94): the record is outside the scope of the mention's document;
#   - `containment` (R95a): the record's name stood inside the mention's, which no longer links;
#   - `spelling` (R95a): the names were spelled alike, but not the same words up to their endings.
RelinkCause = Literal["left_scope", "containment", "spelling"]


class RelinkChange(BaseModel):
    """A keyed mention whose record decision differs from the build's."""

    mention: str
    name: str
    doc_id: str
    before: str | None  # the record the build's matching linked, None when it linked none
    after: str | None
    explained: bool  # a rule change (`cause`) explains it: the build's link is gone, nothing replaced it
    cause: RelinkCause | None = None  # None when unexplained, and in R94's reports, which predate the field


class Relink(BaseModel):
    keyed: int  # keyed mentions replayed
    changes: list[RelinkChange]
    references: list[Assignment]  # the build's REFERS_TO with the changes applied

    @property
    def unexplained(self) -> list[RelinkChange]:
        return [c for c in self.changes if not c.explained]


def relink(s: GraphSnapshot, plan: ConstructionPlan, schema: TextSchema, threshold: float) -> Relink:
    """Replay the record matching of every keyed mention of `s` and apply the decisions that changed."""
    replay = _Replay(s, plan, schema)
    keyed = [m for m in s.mentions if schema.identity_of(m.type) == "keyed"]
    matches = {m.id: replay.match(m, threshold) for m in keyed}
    built = {a.mention: a for a in s.references}
    changes = []
    for m in keyed:
        before, link = _linked(built.get(m.id)), matches[m.id].link
        after = record_ref(link.record.label, link.record.key) if link else None
        if before != after:
            # only a link lost, with nothing in its place, can be a rule change's doing
            unlinked = before is not None and after is None
            cause = _cause(built[m.id], replay.scope(m.doc_id), threshold) if unlinked else None
            changes.append(
                RelinkChange(
                    mention=m.id, name=m.name, doc_id=m.doc_id, before=before, after=after,
                    explained=cause is not None, cause=cause,
                )
            )  # fmt: skip
    changed = {c.mention for c in changes if c.after is None}
    references = [_alone(a, matches[a.mention]) if a.mention in changed else a for a in s.references]
    return Relink(keyed=len(keyed), changes=changes, references=references)


def _linked(a: Assignment | None) -> str | None:
    """The record a build's assignment linked by matching (not by an adjudicated join)."""
    return a.canonical if a is not None and a.kind == "record" and a.reason in _MATCH_REASONS else None


def _cause(built: Assignment, scope: set[str] | None, threshold: float) -> RelinkCause | None:
    """The rule change that explains why the replay unlinks a mention the build linked by matching (`built`,
    its assignment), or None when no rule change does. `scope` is the records the mention's document may
    link to (None: it has no scope), `threshold` the build's spelling threshold."""
    if scope is not None and built.canonical not in scope:
        return "left_scope"
    if built.reason == _CONTAINED:
        return "containment"
    # `built.name` is the record's name the build linked to: is the pair still one name for today's rule?
    if built.reason == "name" and name_score(built.said, built.name, threshold) is None:
        return "spelling"
    return None


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

    def scope(self, doc_id: str) -> set[str] | None:
        """The records a document's mentions may link to; None when it is about nothing (no scope)."""
        anchors = self._anchors.get(doc_id, [])
        return set().union(*(self._reach.get(a, {a}) for a in anchors)) if anchors else None

    def match(self, m: SnapshotMention, threshold: float) -> RecordMatch:
        etype = self._schema.entity_type(m.type)
        labels = set(etype.record_labels) if etype else set()
        wanted = set(etype.key_attributes) if etype else set()
        candidates = [self._candidate(r, wanted) for r in self._records if r.label in labels]
        anchors = sorted(self._anchors.get(m.doc_id, []))
        in_scope = [[c for c in candidates if c.element_id in self._reach.get(a, {a})] for a in anchors]
        ordered = sorted((self._chunks[c] for c in m.chunks if c in self._chunks), key=lambda c: c.index)
        sentences = [x for c in ordered for x in sentences_naming(c.text, [m.name])]
        return match_record(m.name, sentences, candidates, in_scope, threshold)

    def _candidate(self, r: Record, wanted: set[str]) -> RecordCandidate:
        """`records.read_records`: the key column is an attribute too; a missing value stays out."""
        key_column = self._key_column[r.label]
        attributes = {a: r.key if a == key_column else r.properties.get(a, "") for a in wanted}
        return RecordCandidate(
            element_id=r.id, label=r.label, name=r.name or r.key, key=r.key,
            attributes={a: v for a, v in attributes.items() if v},
        )  # fmt: skip


# Counts a build logs that rest on identity: the resolve stage's, and the attach stage's, which hangs claims
# and documents on what mentions refer to. A replay changes these and only these.
IDENTITY_COUNTS = ("resolve.", "attach.")


class RelinkReport(BaseModel):
    """What a replay changed: the mentions, and the logged counts as `[build, replay]`."""

    build: str
    changes: list[RelinkChange]
    counts: dict[str, list[int]]


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
