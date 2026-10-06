"""Particular things: what the mentions of keyed and individual types refer to (R75, layered-model Step 5).

Role in the pipeline: the part of `kg resolve` (identity.py) before concepts. Records first: every keyed
mention is matched against its type's records (records.py); a mention no rule links but whose scope holds
near misses is offered to an LLM, whose choice code checks (record_choice.py). Then individuals: a record's
mentions are one unit, every other keyed or individual-class mention a unit of its own, and units are joined
across documents only with evidence (individuals.py). Each group becomes one canonical entity: its record
when it holds one, else an `:Individual` named after its fullest name.
Design: reads the graph (records, scopes, the mentions' chunks, the near misses' data), asks the LLM only
through the record chooser and the adjudicator, writes nothing; the outcome is a list of assignments (one
edge per mention) and the decisions behind them.
Not here: the matching rules (records.py, record_choice.py), the joining rules (individuals.py), concepts
(concepts.py), writing (identity_graph.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import individual_id, record_ref
from ..core.text import sentences_naming
from ..llm.base import Embedder, LLMClient
from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from .blocking import Blocking
from .identity_graph import Assignment
from .individuals import (
    IndividualDecision,
    Unit,
    display_name,
    embedding_for,
    join,
    llm_adjudicator,
    nominate,
)
from .mentions import MentionRecord, MentionText, read_mention_texts
from .record_choice import (
    ChoiceDecision,
    ChoiceRequest,
    choice_lines,
    choose_records,
    chosen_links,
    near_misses,
    read_candidate_views,
)
from .records import RecordCandidate, RecordLink, RecordMatch, match_record, read_records, read_scopes


class AmbiguousMention(BaseModel):
    """A keyed mention that several records fit equally: not linked to any of them."""

    mention: str
    name: str
    doc_id: str
    records: list[str]  # the tied records, as `record_ref`


class Particulars(BaseModel):
    """The assignments of the keyed and individual mentions, and the decisions behind them."""

    assignments: list[Assignment]
    ambiguous: list[AmbiguousMention]
    decisions: list[IndividualDecision]
    choices: list[ChoiceDecision] = []  # keyed mentions with near misses, and what the chooser made of them


class JoinSettings(BaseModel):
    """How the LLM is asked: which pairs of individuals and which near misses of a record are nominated, and
    the model that decides."""

    borderline: float  # a pair of names (or a name and a record's) spelled at least this alike is nominated
    model: str  # the adjudicating and choosing model, logged on each joined or chosen edge


class _Matched(BaseModel):
    """Every keyed mention's record match, and the near misses of those no rule decided (tier 2)."""

    matches: dict[str, RecordMatch]
    near: dict[str, list[RecordCandidate]]  # mention id -> its near misses, for the mentions that have any


def resolve_particulars(
    driver: Driver,
    keyed: list[MentionRecord],
    individuals: list[MentionRecord],
    schema: TextSchema | None,
    plan: ConstructionPlan | None,
    link_threshold: float,
    llm: LLMClient | None,
    settings: JoinSettings,
    embedder: Embedder | None = None,
    blocking: Blocking | None = None,
) -> Particulars:
    """Records for the keyed mentions (an LLM choosing among near misses), then individuals joined with
    evidence; without an LLM no near miss is chosen and no pair of individuals is joined (each is logged as
    skipped)."""
    texts = read_mention_texts(driver, sorted(m.id for m in (*keyed, *individuals)))
    matched = (
        _match_records(driver, keyed, schema, plan, (link_threshold, settings.borderline), texts)
        if keyed
        else _Matched(matches={}, near={})
    )
    choices, links = _choose_records(driver, plan, keyed, matched.near, texts, llm, settings.model)
    matches = matched.matches | {mention: RecordMatch(link=link) for mention, link in links.items()}
    units = _units(keyed, individuals, matches)
    by_unit = {m: u for u in units for m in u.mentions}
    chunk_texts: dict[str, list[str]] = {}
    for t in texts:
        chunk_texts.setdefault(by_unit[t.mention].id, []).append(t.text)
    pairs = nominate(units, settings.borderline, embedding_for(units, embedder, blocking), blocking)
    adjudicate = llm_adjudicator(llm, settings.model, texts, units) if llm is not None else None
    joining = join(units, pairs, adjudicate, chunk_texts, settings.model)
    mentions = {m.id: m for m in (*keyed, *individuals)}
    assignments = [
        a
        for group in joining.groups
        for a in _group_assignments([u for u in units if u.id in group], mentions, matches, joining.decisions)
    ]
    return Particulars(
        assignments=assignments,
        ambiguous=_ambiguous(keyed, matches),
        decisions=joining.decisions,
        choices=choices,
    )


def _ref(record: RecordCandidate) -> str:
    return record_ref(record.label, record.key)


def _match_records(
    driver: Driver,
    keyed: list[MentionRecord],
    schema: TextSchema | None,
    plan: ConstructionPlan | None,
    thresholds: tuple[float, float],
    texts: list[MentionText],
) -> _Matched:
    """The record match of every mention of a keyed type, and its near misses where no rule decided.
    `thresholds` are the spelling scores a name needs to link a record and to be a near miss of one."""
    if schema is None or plan is None:  # unreachable after validation: a keyed type needs both
        return _Matched(matches={m.id: RecordMatch() for m in keyed}, near={})
    threshold, borderline = thresholds
    types = {m.type: schema.entity_type(m.type) for m in keyed}
    labels = {label for t in types.values() if t for label in t.record_labels}
    attributes = {a for t in types.values() if t for a in t.key_attributes}
    records = read_records(driver, plan, labels, attributes)
    anchors = sorted({a for m in keyed for a in m.anchors})
    scope_ids = read_scopes(driver, anchors, [rule.label for rule in plan.nodes])
    names = {m.id: m.name for m in keyed}
    sentences: dict[str, list[str]] = {}
    for text in texts:
        if text.mention in names:
            sentences.setdefault(text.mention, []).extend(sentences_naming(text.text, [names[text.mention]]))
    out = _Matched(matches={}, near={})
    for m in keyed:
        entity_type = types[m.type]
        wanted = set(entity_type.record_labels) if entity_type else set()
        candidates = [r for r in records if r.label in wanted]
        scopes = [[r for r in candidates if r.element_id in scope_ids.get(a, set())] for a in m.anchors]
        match = match_record(m.name, sentences.get(m.id, []), candidates, scopes, threshold)
        out.matches[m.id] = match
        if near := near_misses(m.name, match, scopes, borderline):
            out.near[m.id] = near
    return out


def _choose_records(
    driver: Driver,
    plan: ConstructionPlan | None,
    keyed: list[MentionRecord],
    near: dict[str, list[RecordCandidate]],
    texts: list[MentionText],
    llm: LLMClient | None,
    model: str,
) -> tuple[list[ChoiceDecision], dict[str, RecordLink]]:
    """Tier 3 for every mention with near misses: the decisions, and mention id -> the link of each choice
    code verified. Reads the near misses' data only when an LLM will be asked."""
    chunks: dict[str, list[MentionText]] = {}
    for t in texts:
        chunks.setdefault(t.mention, []).append(t)
    requests = [
        ChoiceRequest(
            mention=m.id,
            name=m.name,
            lines=choice_lines(m.name, [(t.document, t.text) for t in chunks.get(m.id, [])]),
            texts=[t.text for t in chunks.get(m.id, [])],
            candidates=near[m.id],
        )
        for m in keyed
        if m.id in near
    ]
    ids = sorted({c.element_id for r in requests for c in r.candidates})
    views = read_candidate_views(driver, plan, ids) if llm is not None and plan is not None else {}
    decisions = choose_records(requests, views, llm, model)
    return decisions, chosen_links(decisions, requests)


def _units(
    keyed: list[MentionRecord], individuals: list[MentionRecord], matches: dict[str, RecordMatch]
) -> list[Unit]:
    """A record's linked mentions as one unit (the same record is evidence), every other mention alone."""
    by_record: dict[str, list[MentionRecord]] = {}
    alone: list[MentionRecord] = list(individuals)
    for m in keyed:
        link = matches[m.id].link
        if link is None:
            alone.append(m)
        else:
            by_record.setdefault(_ref(link.record), []).append(m)
    units = [_unit(members, record) for record, members in sorted(by_record.items())]
    return units + [_unit([m], None) for m in sorted(alone, key=lambda m: m.id)]


def _unit(members: list[MentionRecord], record: str | None) -> Unit:
    founder = min(members, key=lambda m: m.id)
    return Unit(
        id=founder.id,
        type=founder.type,
        names=sorted({m.name for m in members}),
        mentions=sorted(m.id for m in members),
        record=record,
        chunks=sum(m.chunks for m in members),
    )


def _group_assignments(
    group: list[Unit],
    mentions: dict[str, MentionRecord],
    matches: dict[str, RecordMatch],
    decisions: list[IndividualDecision],
) -> list[Assignment]:
    """One assignment per mention of a group of units: the group's record, else its individual, named after
    the fullest name of its most mentioned unit. A mention that reached the group by a join says so, with the
    quotes that justified it."""
    linked = next((matches[m].link for u in group if u.record for m in u.mentions), None)
    founder = min(group, key=lambda u: (u.record is None, -len(display_name(u).split()), -u.chunks, u.id))
    out = []
    for unit in group:
        joined = None if unit is founder else _joining_decision(unit, decisions)
        for mention_id in unit.mentions:
            m = mentions[mention_id]
            own = matches.get(mention_id)  # None: an individual-class mention, never matched to records
            if linked is not None:
                base = _record_assignment(m, (own.link if own else None) or linked)
            else:
                base = _individual(m, own, founder.id, display_name(founder))
            out.append(_joined(base, joined) if joined is not None else base)
    return out


def _joining_decision(unit: Unit, decisions: list[IndividualDecision]) -> IndividualDecision | None:
    return next((d for d in decisions if d.action == "joined" and unit.id in (d.a, d.b)), None)


def _joined(base: Assignment, decision: IndividualDecision) -> Assignment:
    """The assignment of a mention that reached its group by an adjudicated join."""
    return base.model_copy(
        update={"reason": "adjudicated", "score": None, "evidence": decision.evidence, "by": decision.by}
    )


def _record_assignment(mention: MentionRecord, link: RecordLink) -> Assignment:
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
        by=link.by,
    )


def _individual(mention: MentionRecord, match: RecordMatch | None, founder: str, name: str) -> Assignment:
    """A mention of an individual. The reason says why it is no record: an individual type (`own_name`, no
    match was tried), a keyed mention no record fits (`no_record`), or several fit (`ambiguous_record`)."""
    reason = "own_name" if match is None else "ambiguous_record" if match.tied else "no_record"
    return Assignment(
        mention=mention.id,
        said=mention.name,
        kind="individual",
        canonical=individual_id(founder),
        name=name,
        type=mention.type,
        reason=reason,
        evidence=", ".join(_ref(r) for r in match.tied) if match else "",
    )


def _ambiguous(keyed: list[MentionRecord], matches: dict[str, RecordMatch]) -> list[AmbiguousMention]:
    return [
        AmbiguousMention(
            mention=m.id, name=m.name, doc_id=m.doc_id, records=[_ref(r) for r in matches[m.id].tied]
        )
        for m in keyed
        if matches[m.id].link is None and matches[m.id].tied
    ]
