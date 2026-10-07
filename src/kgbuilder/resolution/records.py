"""Which record of the domain graph a mention of a keyed type is (R75, layered-model Step 5).

Role in the pipeline: the first decision of `kg resolve` (identity.py): every mention of a keyed type is
matched against the records of its type's plan labels; a match makes the record its canonical entity, no
match leaves it to the individuals. Concept and individual types never reach a record.
Design: pure matching over `RecordCandidate`s, unit-tested without a database; `read_records` and
`read_scopes` are the only reads. The rules, in order, each a reason on the identity edge:
  1. `key`: the record's key is a whole token of the mention's name ("pump HP40-1183"), and `key_decides`:
     a key that is a plain word, not an identifier, decides only when the name adds nothing but numbers and
     the record's own attribute values (R108: "2017-2022 Rogue Sport" -> the key "ROGUE" was a line of its
     own, where "Civic Type R" -> "CIVIC" was a version; code cannot tell them apart, the chooser can);
  2. `name`: the mention's name is a record's name inside the scope of the mention's document; only a
     document without a scope may match one record of the whole domain (R11's scoped linking, moved here
     from linking.py; R94 ended the fallback for documents with a scope). A name is a record's when it is
     the same after normalisation, or the same words up to a short ending and spelled alike (names.py).
     Since R95a nothing else decides alone: a record's name inside a longer name ("pre-drilled holes for the
     drawer handle" -> Drawer Handle) and a spelling that differs inside a word ("drawer slides" -> Drawer
     Sides) were R93's wrong links, and code cannot tell them from right ones. A mention the pass stated a
     kind links only by the same name: up to an ending it may mean other things of the record's kind (R108:
     "MULTIPLE OUTBACKS IN THE PAST" -> the 2019 Outback), which only the sentence tells;
  3. `key_in_sentence`: no name decides, and exactly one key (of the tied records when names tie) is written
     right next to the mention's name in a sentence ("pump HP40-1183", "the vehicle (RAV4)"): a key
     elsewhere in the sentence is no evidence, since a sentence may list many records (found in R75's
     held-out run: "certain Toyota Camry, Corolla, Rav4" linked "Camry" to the RAV4);
  4. `attribute`: names tie, and a key attribute of exactly one tied record is written in a sentence
     naming the mention ("Maria Lopez (Finance Office)" -> the Maria Lopez whose team is Finance Office);
  5. `variant_attribute`: no name matches, and among the records whose names are variants of the mention's
     ("Dr. J. Pike" of "Jonathan Pike", variants.py) exactly one has a key attribute in such a sentence.
A name is also matched without its leading title ("Dr Jonathan Pike" is "Jonathan Pike").
Records still tied are not linked: the mention is logged as ambiguous, because a wrong link answers
questions about the wrong record, and a missing one only leaves the mention to stand for itself.
A mention no rule links may still have near misses in its scope; an LLM chooses among them, and code checks
the choice (`chosen`, record_choice.py).
Not here: the name test (names.py), joining individuals (individuals.py), concepts (concepts.py), writing
(identity_graph.py).
"""

import re
from collections.abc import Callable
from typing import Literal

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.text import contains_words, norm, squash
from ..structured.plan import ConstructionPlan, name_property
from ..text.schema import MentionClass
from .linking import DomainNode
from .names import name_score
from .variants import compatible

# A key shorter than this, once squashed, is too likely to appear by accident ("P1" in "part 1");
# the same bound as section matching (linking.py).
_MIN_KEY_CHARS = 4

# An attribute value shorter than this ("SE", "1") occurs in too many sentences to tell records apart.
_MIN_ATTRIBUTE_CHARS = 3

# `name_score` of the same name after normalisation; below it, the same words only up to an ending
_SAME_NAME = 100.0

# How far from a document's domain node a mention of that document may link, counted in domain
# relationships. 2 reaches a thing's sub-records and theirs, but not their suppliers (3): a document names
# the parts of what it is about, not who made them (R11).
_SCOPE_HOPS = 2

# `chosen` (R95b): an LLM chose the record among the near misses, and code verified the choice
LinkReason = Literal["key", "name", "key_in_sentence", "attribute", "variant_attribute", "chosen"]


class RecordCandidate(DomainNode):
    """A record a mention may be: its label, display name, key and the key attributes asked for."""

    key: str  # the unique column's value as text
    attributes: dict[str, str] = {}  # key attribute -> value as text


class NameMatch(BaseModel):
    record: RecordCandidate
    score: float  # `name_score`: 100 for the same name, else the spelling score of an inflected one


class RecordLink(BaseModel):
    """The record a mention is, and why."""

    record: RecordCandidate
    reason: LinkReason
    score: float | None  # the name score where a name decided; None for an LLM's choice
    evidence: str  # the name or sentence that shows it
    scoped: bool  # found inside the scope of the mention's document
    by: str = "code"  # "code", or the model whose choice code verified (`chosen`)


class RecordMatch(BaseModel):
    """The outcome for one mention: a link, or the records it could not choose between (ambiguous)."""

    link: RecordLink | None = None
    tied: list[RecordCandidate] = []


def name_matches(name: str, records: list[RecordCandidate], threshold: float) -> list[NameMatch]:
    """The records `name` names on its own (`name_score`), all those sharing the best score: several mean a
    tie, and the same name (100) wins over an inflected one. A wrong link is worse than a missing one."""
    scored = [(r, name_score(name, r.name, threshold)) for r in records]
    found = [NameMatch(record=r, score=s) for r, s in scored if s is not None]
    best = max((m.score for m in found), default=None)
    return [m for m in found if m.score == best]


class _ByName(BaseModel):
    matches: list[NameMatch]
    scoped: bool


def _by_name(
    name: str, scopes: list[list[RecordCandidate]], records: list[RecordCandidate], threshold: float
) -> _ByName:
    """The records the name names: inside each scope; only when the document has no scope at all, in the
    whole domain.

    A scope vouches that the document is about the record's neighbourhood; the whole domain vouches for
    nothing. So a document with a scope never links beyond it (R94): R93 judged all 4 such links of the
    furniture build wrong, a desk review's "drawer" reaching a nightstand's "Drawer" because the desk's own
    record is called "Drawer Unit". A document without a scope (no thing it is about) still has the domain.
    """
    if not scopes:
        return _ByName(matches=name_matches(name, records, threshold), scoped=False)
    found: dict[str, NameMatch] = {}
    for scope in scopes:
        for m in name_matches(name, scope, threshold):
            found.setdefault(m.record.element_id, m)
    return _ByName(matches=list(found.values()), scoped=True)


def _key_tokens(text: str) -> set[str]:
    """The squashed tokens of a text: "HP40-1183," and "hp40 1183" differ, "HP40-1183" and "hp401183" not."""
    return {squash(token) for token in norm(text).split()} - {""}


def _with_key_in(texts: list[str], records: list[RecordCandidate]) -> list[RecordCandidate]:
    tokens = set().union(*(_key_tokens(t) for t in texts)) if texts else set()
    return [r for r in records if len(squash(r.key)) >= _MIN_KEY_CHARS and squash(r.key) in tokens]


def keys_in_name(name: str, records: list[RecordCandidate]) -> list[RecordCandidate]:
    """The records whose key is a whole token of `name` (rule 1's candidates; record_choice.py nominates
    them where `key_decides` does not link)."""
    return _with_key_in([name], records)


def key_decides(name: str, record: RecordCandidate) -> bool:
    """Whether `record`, whose key is a whole token of `name`, is that mention by its key alone (rule 1).

    A key with a digit is an identifier ("HP40-1183"): nothing else carries it, so it decides whatever the
    name adds ("pump HP40-1183"). A key that is a plain word ("Corvid") is a name like any other, and a
    record's name inside a longer name decides nothing (R95a): it decides only when every other word of the
    mention's name is a number or a word of the record's own attribute values ("2021 Brightwater Corvid", a
    year and the record's maker). With any other word ("Corvid Mini") the mention may be a version of the
    record or a line of its own, which only knowledge of the world tells: the chooser decides (R108)."""
    key = squash(record.key)
    if any(c.isdigit() for c in key):
        return True
    own = {squash(word) for value in record.attributes.values() for word in norm(value).split()}
    rest = _key_tokens(name) - {key}
    # a token of digits alone is a number or a range ("2017-2022" squashes to "20172022"); "2-door" is a word
    return all(word.isdigit() or word in own for word in rest)


def _key_next_to(name: str, sentence: str, key: str) -> bool:
    """True when `key` is written right before or after `name` in `sentence`, with at most a bracket, a
    colon or a number sign between ("pump HP40-1183", "the vehicle (RAV4)", "HP40-1183 pump"); a comma
    between them means a list, not a name."""
    name_part, key_part = re.escape(norm(name)), re.escape(norm(key))
    edge_start, edge_end = "(?<![a-z0-9])", "(?![a-z0-9])"
    after = rf"{edge_start}{name_part}\s*[(\[:#]?\s*{key_part}{edge_end}"
    before = rf"{edge_start}{key_part}\s*[)\]]?\s*{name_part}{edge_end}"
    return re.search(f"{after}|{before}", norm(sentence)) is not None


def _with_key_next_to(
    name: str, sentences: list[str], records: list[RecordCandidate]
) -> list[RecordCandidate]:
    return [
        r
        for r in records
        if len(squash(r.key)) >= _MIN_KEY_CHARS and any(_key_next_to(name, s, r.key) for s in sentences)
    ]


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
    stated: MentionClass | None = None,
) -> RecordMatch:
    """The record a mention called `name` is, from its name and the `sentences` that name it (rules 1-5 of
    the module header). `records` are the candidates of its type's labels, `scopes` the candidates near
    each thing its document is about, `stated` the class the mention pass stated (None: a claim's mention).
    Pure."""
    if len(by_key := _with_key_in([name], records)) == 1 and key_decides(name, by_key[0]):
        return RecordMatch(
            link=RecordLink(record=by_key[0], reason="key", score=100.0, evidence=name, scoped=False)
        )
    named = _by_name(name, scopes, records, threshold)
    if stated == "kind":  # R108: a kind is a record by its very name; up to an ending, the chooser decides
        named = named.model_copy(update={"matches": [m for m in named.matches if m.score >= _SAME_NAME]})
    if len(named.matches) == 1:
        m = named.matches[0]
        return RecordMatch(
            link=RecordLink(record=m.record, reason="name", score=m.score, evidence=name, scoped=named.scoped)
        )
    tied = [m.record for m in named.matches]
    link = _by_sentence(name, sentences, records, tied, named)
    return RecordMatch(link=link) if link else RecordMatch(tied=tied)


def _by_sentence(
    name: str,
    sentences: list[str],
    records: list[RecordCandidate],
    tied: list[RecordCandidate],
    named: _ByName,
) -> RecordLink | None:
    """Rules 3-5: what the sentences naming the mention tell when its name decides nothing."""
    if len(keyed := _with_key_next_to(name, sentences, tied or records)) == 1:

        def next_to(found: list[str], candidates: list[RecordCandidate]) -> list[RecordCandidate]:
            return _with_key_next_to(name, found, candidates)

        return _from_sentence(keyed[0], "key_in_sentence", 100.0, sentences, next_to, scoped=False)
    if len(told := _with_attribute_in(sentences, tied)) == 1:
        score = named.matches[0].score
        return _from_sentence(told[0], "attribute", score, sentences, _with_attribute_in, named.scoped)
    if not tied:  # a variant only when no name matched: a tie is a choice among named records, not variants
        variants = [r for r in records if compatible(name, r.name)]
        if len(told := _with_attribute_in(sentences, variants)) == 1:
            return _from_sentence(told[0], "variant_attribute", 0.0, sentences, _with_attribute_in, False)
    return None


def _from_sentence(
    record: RecordCandidate,
    reason: LinkReason,
    score: float,
    sentences: list[str],
    test: Callable[[list[str], list[RecordCandidate]], list[RecordCandidate]],
    scoped: bool,
) -> RecordLink:
    """A link whose evidence is the first sentence that shows it."""
    evidence = next(s for s in sentences if test([s], [record]))
    return RecordLink(record=record, reason=reason, score=score, evidence=evidence, scoped=scoped)


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
