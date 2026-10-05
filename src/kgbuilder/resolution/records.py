"""Which record of the domain graph a mention of a keyed type is (R75, layered-model Step 5).

Role in the pipeline: the first decision of `kg resolve` (identity.py): every mention of a keyed type is
matched against the records of its type's plan labels; a match makes the record its canonical entity, no
match leaves it to the individuals. Concept and individual types never reach a record.
Design: pure matching over `RecordCandidate`s, unit-tested without a database; `read_records` and
`read_scopes` are the only reads. The rules, in order, each a reason on the identity edge:
  1. `key`: the record's key is a whole token of the mention's name ("pump HP40-1183");
  2. `name` / `contained`: the name matches a record's name inside the scope of the mention's document (a
     near-exact fuzzy match, else the record's whole name inside the mention's, R60/R67), else one record
     of the whole domain (R11's scoped linking, moved here from linking.py);
  3. `key_in_sentence`: no name decides, and exactly one key (of the tied records when names tie) stands
     in a sentence naming the mention;
  4. `attribute`: names tie, and a key attribute of exactly one tied record is written in a sentence
     naming the mention ("Maria Lopez (Finance Office)" -> the Maria Lopez whose team is Finance Office).
Records still tied are not linked: the mention is logged as ambiguous, because a wrong link answers
questions about the wrong record, and a missing one only leaves the mention to stand for itself.
Not here: names written differently (b2, individuals.py), concepts (concepts.py), writing (identity.py).
"""

from typing import Literal

from neo4j import Driver
from pydantic import BaseModel
from rapidfuzz import fuzz

from ..core.cypher import cypher_ident
from ..core.text import contains_words, norm, squash
from ..structured.plan import ConstructionPlan, name_property
from .linking import DomainNode

# Names shorter than this, once squashed, are too likely to occur inside an unrelated name ("bed" in
# "embedded") to be trusted for containment; the same bound as document matching (linking.py).
_MIN_CONTAINED_CHARS = 4

# A key shorter than this, once squashed, is too likely to appear by accident ("P1" in "part 1");
# the same bound as section matching (linking.py).
_MIN_KEY_CHARS = 4

# An attribute value shorter than this ("SE", "1") occurs in too many sentences to tell records apart.
_MIN_ATTRIBUTE_CHARS = 3

# How far from a document's domain node a mention of that document may link, counted in domain
# relationships. 2 reaches a thing's sub-records and theirs, but not their suppliers (3): a document names
# the parts of what it is about, not who made them (R11).
_SCOPE_HOPS = 2

LinkReason = Literal["key", "name", "contained", "key_in_sentence", "attribute"]


class RecordCandidate(DomainNode):
    """A record a mention may be: its label, display name, key and the key attributes asked for."""

    key: str  # the unique column's value as text
    attributes: dict[str, str] = {}  # key attribute -> value as text


class NameMatch(BaseModel):
    record: RecordCandidate
    score: float  # rapidfuzz token_sort_ratio, 0..100; 100 for a contained whole name


class RecordLink(BaseModel):
    """The record a mention is, and why."""

    record: RecordCandidate
    reason: LinkReason
    score: float
    evidence: str  # the name or sentence that shows it
    scoped: bool  # found inside the scope of the mention's document


class RecordMatch(BaseModel):
    """The outcome for one mention: a link, or the records it could not choose between (ambiguous)."""

    link: RecordLink | None = None
    tied: list[RecordCandidate] = []


def name_matches(names: list[str], records: list[RecordCandidate], threshold: float) -> list[NameMatch]:
    """All records sharing the best fuzzy score for `names`, if it reaches `threshold`; several mean a tie.

    token_sort_ratio ignores word order ("Chair Stockholm" = "Stockholm Chair") but not extra words, so with
    a threshold around 90 only near-exact names match: a wrong link is worse than a missing one.
    """
    wanted = {norm(n) for n in names if norm(n)}
    if not wanted:
        return []
    scored = [
        NameMatch(record=r, score=max(fuzz.token_sort_ratio(w, norm(r.name)) for w in wanted))
        for r in records
    ]
    best = max((m.score for m in scored), default=0.0)
    return [m for m in scored if m.score == best] if best >= threshold else []


def contained_matches(names: list[str], records: list[RecordCandidate]) -> list[NameMatch]:
    """The records whose whole name occurs word for word inside one of `names`, longest name only.

    R60's rule: the plan names a record by one column ("CIVIC"), the text writes it in full ("2016 Honda
    Civic"), which no fuzzy threshold accepts without accepting garbage too. Whole words, so "ESCAPE" never
    matches "escaped"; the longest contained name wins ("Coffee Table" over "Table").
    """
    words = [set(norm(n).split()) for n in names if norm(n)]
    hits = [
        r
        for r in records
        if len(squash(r.name)) >= _MIN_CONTAINED_CHARS and any(set(norm(r.name).split()) <= w for w in words)
    ]
    if not hits:
        return []
    best = max(len(squash(r.name)) for r in hits)
    return [NameMatch(record=r, score=100.0) for r in hits if len(squash(r.name)) == best]


class _ByName(BaseModel):
    matches: list[NameMatch]
    scoped: bool
    contained: bool


def _by_name(
    name: str, scopes: list[list[RecordCandidate]], records: list[RecordCandidate], threshold: float
) -> _ByName:
    """The records the name names: inside each scope (fuzzy, else containment), else in the whole domain.

    Outside every scope only fuzzy matching is used: a scope vouches that the document is about the
    record's neighbourhood, the whole domain vouches for nothing, so containment is not trusted there.
    """
    found: dict[str, NameMatch] = {}
    contained = False
    for scope in scopes:
        in_scope = name_matches([name], scope, threshold)
        if not in_scope:  # fuzzy said nothing at all; a fuzzy tie is not overridden by containment
            in_scope = contained_matches([name], scope)
            contained |= bool(in_scope)
        for m in in_scope:
            found.setdefault(m.record.element_id, m)
    if found:
        return _ByName(matches=list(found.values()), scoped=True, contained=contained)
    return _ByName(matches=name_matches([name], records, threshold), scoped=False, contained=False)


def _key_tokens(text: str) -> set[str]:
    """The squashed tokens of a text: "HP40-1183," and "hp40 1183" differ, "HP40-1183" and "hp401183" not."""
    return {squash(token) for token in norm(text).split()} - {""}


def _with_key_in(texts: list[str], records: list[RecordCandidate]) -> list[RecordCandidate]:
    tokens = set().union(*(_key_tokens(t) for t in texts)) if texts else set()
    return [r for r in records if len(squash(r.key)) >= _MIN_KEY_CHARS and squash(r.key) in tokens]


def _with_attribute_in(sentences: list[str], records: list[RecordCandidate]) -> list[RecordCandidate]:
    return [
        r
        for r in records
        if any(
            len(squash(value)) >= _MIN_ATTRIBUTE_CHARS and any(contains_words(s, value) for s in sentences)
            for value in r.attributes.values()
        )
    ]


def match_record(
    name: str,
    sentences: list[str],
    records: list[RecordCandidate],
    scopes: list[list[RecordCandidate]],
    threshold: float,
) -> RecordMatch:
    """The record a mention called `name` is, from its name and the `sentences` that name it (rules 1-4 of
    the module header). `records` are the candidates of its type's labels, `scopes` the candidates near
    each thing its document is about. Pure."""
    if len(by_key := _with_key_in([name], records)) == 1:
        return RecordMatch(
            link=RecordLink(record=by_key[0], reason="key", score=100.0, evidence=name, scoped=False)
        )
    named = _by_name(name, scopes, records, threshold)
    if len(named.matches) == 1:
        m = named.matches[0]
        reason: LinkReason = "contained" if named.contained else "name"
        return RecordMatch(
            link=RecordLink(record=m.record, reason=reason, score=m.score, evidence=name, scoped=named.scoped)
        )
    tied = [m.record for m in named.matches]
    keyed = _with_key_in(sentences, tied or records)
    if len(keyed) == 1:
        evidence = next(s for s in sentences if _with_key_in([s], keyed))
        return RecordMatch(
            link=RecordLink(
                record=keyed[0], reason="key_in_sentence", score=100.0, evidence=evidence, scoped=False
            )
        )
    if len(told := _with_attribute_in(sentences, tied)) == 1:
        evidence = next(s for s in sentences if _with_attribute_in([s], told))
        score = named.matches[0].score
        return RecordMatch(
            link=RecordLink(
                record=told[0], reason="attribute", score=score, evidence=evidence, scoped=named.scoped
            )
        )
    return RecordMatch(tied=tied)


def read_records(
    driver: Driver, plan: ConstructionPlan, labels: set[str], attributes: set[str]
) -> list[RecordCandidate]:
    """Every record of `labels` with its key, display name and the values of `attributes` it has."""
    out: list[RecordCandidate] = []
    for rule in plan.nodes:
        if rule.label not in labels:
            continue
        label, key, name = (cypher_ident(x) for x in (rule.label, rule.unique_column, name_property(rule)))
        wanted = sorted(attributes & {rule.unique_column, *rule.properties})
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) WHERE n.{key} IS NOT NULL "
            f"RETURN elementId(n) AS id, toString(n.{key}) AS key, "
            f"coalesce(toString(n.{name}), toString(n.{key})) AS name, "
            # the attribute values as text, by key; a missing value stays out of the map
            "apoc.map.fromPairs([a IN $attributes WHERE n[a] IS NOT NULL | [a, toString(n[a])]]) "
            "AS attributes "
            "ORDER BY key",
            attributes=wanted,
        )
        out += [
            RecordCandidate(
                element_id=r["id"], label=rule.label, name=r["name"], key=r["key"], attributes=r["attributes"]
            )
            for r in records
        ]
    return out


def read_scopes(driver: Driver, anchor_ids: list[str], labels: list[str]) -> dict[str, set[str]]:
    """For each anchor node, the element ids of the domain nodes within `_SCOPE_HOPS` of it (itself
    included). Every node on the path must be a domain node: otherwise a path could run through a document
    or a mention and leak scopes."""
    if not anchor_ids:
        return {}
    records, _, _ = driver.execute_query(
        # the hop bound cannot be a parameter; it is our own integer constant, not user input
        f"MATCH (a) WHERE elementId(a) IN $ids MATCH p = (a)-[*0..{_SCOPE_HOPS}]-(n) "
        "WHERE all(x IN nodes(p) WHERE any(l IN labels(x) WHERE l IN $labels)) "
        "RETURN elementId(a) AS anchor, collect(DISTINCT elementId(n)) AS scope",
        ids=anchor_ids,
        labels=labels,
    )
    return {r["anchor"]: set(r["scope"]) for r in records}
