"""The identity stage: what every mention refers to, written as one `REFERS_TO` edge per mention (R75).

Role in the pipeline: `kg resolve`, after `kg link` (the scopes of record matching are the things the
documents are ABOUT) and before validation and question answering. Every reader finds a mention's
canonical entity through graph/canonical.py.
Design: the text schema's identity class of a mention's type decides what it may refer to (fixed decision 8
of the layered-model task: identity is an edge, not a merged node):
  - keyed: a record of the type's plan labels, by key, name or a telling attribute (records.py); a mention
    no record fits stands for one particular thing of its own, an individual;
  - individual: an `:Individual` of its own, so the same name in two documents stays two (joining
    individuals across documents with evidence is part b2 of R75);
  - concept: a `:Concept` per type and name across documents, joined by entity resolution (concepts.py).
Every decision is recomputed from scratch on each run and kept, with its reason, in the report
(`resolve.json`), then written by identity_graph.py, which replaces nothing but the identity layer itself.
Not here: matching records (records.py), the concept decisions (resolver.py, concepts.py), writing and
clearing the edges (identity_graph.py), reading them (graph/canonical.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import individual_id, record_ref
from ..core.text import sentences_naming
from ..graph.canonical import CanonicalKind
from ..llm.base import Embedder, LLMClient
from ..structured.plan import ConstructionPlan
from ..text.schema import IdentityClass, TextSchema
from .blocking import Blocking
from .concepts import ConceptResolution, resolve_concepts
from .identity_graph import Assignment, write_identity
from .matchers import EntityRecord
from .mentions import MentionRecord, read_mention_texts, read_mentions
from .records import RecordCandidate, RecordMatch, match_record, read_records, read_scopes
from .resolver import Decision, MergeGroup


class IdentitySettings(BaseModel):
    """The thresholds of the identity stage, from the settings (logged as params of the resolve run)."""

    auto_merge: float  # a concept pair spelled this alike merges without the LLM
    borderline: float  # a concept pair spelled at least this alike is asked about
    link_threshold: float  # the fuzzy score a mention's name needs to match a record's (records.py)


class AmbiguousMention(BaseModel):
    """A keyed mention that several records fit equally: not linked, so it stands for itself."""

    mention: str
    name: str
    doc_id: str
    records: list[str]  # the tied records, as `record_ref`


class IdentityReport(BaseModel):
    """Result and audit log of one identity run: every assignment, and the concept decisions behind them."""

    mentions: int
    ambiguous: list[AmbiguousMention]
    concepts_before: int  # concepts by type and name, before resolution joined any
    concepts_after: int
    merges: int  # concepts absorbed into another
    passes: int
    decisions: list[Decision]  # concept pairs: auto, llm_merge, llm_keep, skipped_borderline
    groups: list[MergeGroup]
    assignments: list[Assignment]

    def count(self, kind: CanonicalKind) -> int:
        return sum(a.kind == kind for a in self.assignments)


def resolve_identity(
    driver: Driver,
    schema: TextSchema | None,
    plan: ConstructionPlan | None,
    llm: LLMClient | None,
    model: str,
    settings: IdentitySettings,
    embedder: Embedder | None = None,
    blocking: Blocking | None = None,
) -> IdentityReport:
    """Decide what every mention refers to and write the identity layer. Without a schema every type is a
    concept (the rule before R75); without an LLM borderline concept pairs stay apart. The schema's
    identity classes must already have passed `validate_text_schema` against `plan`."""
    mentions = read_mentions(driver)
    classes = {m.type: (schema.identity_of(m.type) if schema else "concept") for m in mentions}
    keyed = [m for m in mentions if classes[m.type] == "keyed"]
    matches = _match_records(driver, keyed, schema, plan, settings.link_threshold) if keyed else {}
    assignments = [
        _record_assignment(m, match) if match.link else _individual(m, classes[m.type], match)
        for m in keyed
        for match in [matches[m.id]]
    ]
    assignments += [
        _individual(m, "individual", RecordMatch()) for m in mentions if classes[m.type] == "individual"
    ]
    concept_mentions = [m for m in mentions if classes[m.type] == "concept"]
    concepts = resolve_concepts(
        driver, concept_mentions, llm, model, (settings.auto_merge, settings.borderline), embedder, blocking
    )
    assignments += _concept_assignments(concept_mentions, concepts, model)
    write_identity(driver, assignments)
    return IdentityReport(
        mentions=len(mentions),
        ambiguous=_ambiguous(keyed, matches),
        concepts_before=len(concepts.concepts),
        concepts_after=len(concepts.concepts) - sum(len(g.absorbed) for g in concepts.groups),
        merges=sum(len(g.absorbed) for g in concepts.groups),
        passes=concepts.passes,
        decisions=concepts.decisions,
        groups=concepts.groups,
        assignments=sorted(assignments, key=lambda a: a.mention),
    )


def _ambiguous(keyed: list[MentionRecord], matches: dict[str, RecordMatch]) -> list[AmbiguousMention]:
    return [
        AmbiguousMention(
            mention=m.id, name=m.name, doc_id=m.doc_id, records=[_ref(r) for r in matches[m.id].tied]
        )
        for m in keyed
        if matches[m.id].link is None and matches[m.id].tied
    ]


def _ref(record: RecordCandidate) -> str:
    return record_ref(record.label, record.key)


def _match_records(
    driver: Driver,
    keyed: list[MentionRecord],
    schema: TextSchema | None,
    plan: ConstructionPlan | None,
    threshold: float,
) -> dict[str, RecordMatch]:
    """Mention id -> its record match, for every mention of a keyed type."""
    if schema is None or plan is None:  # unreachable after validation: a keyed type needs both
        return {m.id: RecordMatch() for m in keyed}
    types = {m.type: schema.entity_type(m.type) for m in keyed}
    labels = {label for t in types.values() if t for label in t.record_labels}
    attributes = {a for t in types.values() if t for a in t.key_attributes}
    records = read_records(driver, plan, labels, attributes)
    anchors = sorted({a for m in keyed for a in m.anchors})
    scope_ids = read_scopes(driver, anchors, [rule.label for rule in plan.nodes])
    sentences: dict[str, list[str]] = {}
    names = {m.id: m.name for m in keyed}
    for text in read_mention_texts(driver, sorted(names)):
        sentences.setdefault(text.mention, []).extend(sentences_naming(text.text, [names[text.mention]]))
    out = {}
    for m in keyed:
        entity_type = types[m.type]
        wanted = set(entity_type.record_labels) if entity_type else set()
        candidates = [r for r in records if r.label in wanted]
        scopes = [[r for r in candidates if r.element_id in scope_ids.get(a, set())] for a in m.anchors]
        out[m.id] = match_record(m.name, sentences.get(m.id, []), candidates, scopes, threshold)
    return out


def _record_assignment(mention: MentionRecord, match: RecordMatch) -> Assignment:
    link = match.link
    assert link is not None  # the caller checked: only a linked match comes here
    return Assignment(
        mention=mention.id,
        said=mention.name,
        kind="record",
        canonical=_ref(link.record),
        name=link.record.name,
        type=mention.type,
        target=link.record.element_id,
        reason=link.reason,
        score=link.score,
        evidence=link.evidence,
    )


def _individual(mention: MentionRecord, identity: IdentityClass, match: RecordMatch) -> Assignment:
    """A mention standing for one particular thing of its own. The reason says why: an individual type
    (`own_name`), a keyed mention no record fits (`no_record`) or several records fit (`ambiguous_record`)."""
    reason = "own_name" if identity == "individual" else "ambiguous_record" if match.tied else "no_record"
    return Assignment(
        mention=mention.id,
        said=mention.name,
        kind="individual",
        canonical=individual_id(mention.id),
        name=mention.name,
        type=mention.type,
        reason=reason,
        evidence=", ".join(_ref(r) for r in match.tied),
    )


def _concept_assignments(
    mentions: list[MentionRecord], concepts: ConceptResolution, model: str
) -> list[Assignment]:
    """One assignment per concept mention: to its own concept (`same_name`), or to the canonical concept of
    the group resolution joined it to, with the decision that joined it."""
    root = {i: g.canonical for g in concepts.groups for i in g.absorbed}
    by_id = {c.id: c for c in concepts.concepts}
    concept_of = {m: cid for cid, ids in concepts.members.items() for m in ids}
    out = []
    for m in mentions:
        own = concept_of[m.id]
        canonical = by_id[root.get(own, own)]
        # a member of the canonical concept itself shares its name; an absorbed one joined by a decision
        reason, score, evidence, by = (
            _concept_reason(own, concepts.decisions, by_id, model)
            if own in root
            else ("same_name", None, "", "code")
        )
        out.append(
            Assignment(
                mention=m.id,
                said=m.name,
                kind="concept",
                canonical=canonical.id,
                name=canonical.name,
                type=m.type,
                reason=reason,
                score=score,
                evidence=evidence,
                by=by,
            )  # fmt: skip
        )
    return out


def _concept_reason(
    concept: str, decisions: list[Decision], by_id: dict[str, EntityRecord], model: str
) -> tuple[str, float | None, str, str]:
    """Why a concept joined its group: its strongest merge decision, as (reason, score, evidence, by).
    "spelling" for an automatic merge, "adjudicated" for an LLM's yes."""
    merges = [d for d in decisions if d.action in ("auto", "llm_merge") and concept in (d.a_id, d.b_id)]
    best = max(merges, key=lambda d: d.score)
    other = by_id[best.b_id if best.a_id == concept else best.a_id].name
    if best.action == "auto":
        return "spelling", best.score, f"spelled like '{other}'", "code"
    return "adjudicated", best.score, f"the same kind as '{other}'", model
