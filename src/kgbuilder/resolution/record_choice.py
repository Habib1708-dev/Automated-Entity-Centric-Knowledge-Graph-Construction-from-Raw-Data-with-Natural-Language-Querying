"""Tiers 2 and 3 of record matching: the near misses of a mention no rule linked, and an LLM's choice among
them, which code checks before it links (R95b).

Role in the pipeline: `kg resolve` (particulars.py), after code matched every keyed mention (records.py) and
before individuals are joined; also the offline replay of a build's matching (audit/relink.py).
Design: the LLM proposes, code decides, as for individuals (individuals.py). A partial overlap or a close
spelling ("drawer" against "Drawer Unit") is evidence for a candidate, never for a link: R93 judged such
links wrong when code made them alone. Outside a scope (a document about nothing) the near misses are strict:
a name variant, or the one record whose key attribute holds the name (R99). In both, a record whose key is a
word of the name but does not decide (`records.key_decides`, R108: "Corvid Mini" for the key "Corvid") is a
near miss; a stated kind named by a record's name up to an ending is a near name inside a scope (R108). The
LLM sees the sentences
naming the mention and every near miss with what the data says about it (cells, one-hop relations), and
answers one listed id or none, with a quote. Code links only when:
  - the id is one of those listed, so a choice never leaves the scope of the mention's document;
  - the quote stands in one of the mention's chunks and names the mention;
  - no other listed record has the same name: a choice between twins is a coin toss.
Anything else (none, a failure, a malformed answer) is no link, so without an LLM this tier abstains and the
matching is only ever more cautious than code alone. Every decision is logged with its outcome.
Not here: the name tests (names.py), the code rules (records.py), joining individuals (individuals.py).
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from neo4j import Driver
from pydantic import BaseModel, Field

from ..core.errors import LLMResponseError
from ..core.identity import record_ref
from ..core.text import norm, sentences_naming
from ..llm.base import LLMClient
from ..structured.plan import ConstructionPlan, NodeRule, name_property
from .names import near_name, same_name
from .records import RecordCandidate, RecordLink, RecordMatch, keys_in_name
from .variants import compatible

log = logging.getLogger(__name__)

_WORKERS = 8  # independent LLM calls, as in concept and individual resolution

# A list longer than this is not shown: a name that nearly names so many records of one scope says too
# little to choose by, and the prompt would grow with the scope. Furniture's longest list is 16 (R95b).
_MAX_CANDIDATES = 20

# Enough sentences to see how the name is used, few enough to keep each call cheap; a "sentence" longer than
# _SENTENCE_CHARS (a table row, a list without full stops) is cut, as in the resolver's lines.
_MAX_LINES = 5
_SENTENCE_CHARS = 400

# The choice of a record. Intent, rule by rule:
#   - "the very same thing": the question R93's wrong links got wrong, asked directly;
#   - "written in other words": plurals, fuller or shorter names and a describing word are the right links
#     code cannot accept alone without accepting the wrong ones too ("weighted base" for a record "Base");
#   - "the side of it the sentence talks about": the record's name with a word for its design or how it is
#     made still speaks of the record, as the judge reads it (R96: R95b's prompt listed "a property of it"
#     among the none cases, and its chooser declined a link R93 judged right);
#   - "a version of it": a variant of the same line in another size or form is the record (R108: the key
#     rule's right links the chooser now decides, "Civic Type R" and "Rogue Hybrid" judged right);
#   - "another thing that only shares words": the wrong links in structural terms (a piece of the record,
#     something made for it, a larger thing): "holes for the handle" is not the handle;
#   - "another line sold under a name of its own" (R108): the record's name with a word that makes another
#     line's name; "2017-2022 Rogue Sport" was linked to the record "ROGUE" by its key, and "the name with a
#     describing word" alone would let the chooser accept it too;
#   - "other things of the record's kind than the ones it stands for" (R108): a plural may mean the record's
#     own things ("two of these nightstands", judged right) or others ("MULTIPLE OUTBACKS IN THE PAST", the
#     owner's earlier cars, linked to the 2019 record), and "a plural" alone would accept both;
#   - "a whole ... unless the sentences name the piece": a name that fits a whole and its pieces means the
#     whole (R96: the declined link was shown a whole with three of its pieces);
#   - "several still fit equally": a guess between look-alikes is worse than no link (code refuses twins);
#   - the quote is what code checks; "leave quote empty" lets the model refuse without inventing one.
# The examples come from an invented domain (a telescope), never from evaluated data.
CHOICE_PROMPT = """A text names a thing "{name}". Is it the very same thing as one of the records below?

Where "{name}" is named ([document] sentence):
{lines}

Records (id, then what the data holds about it):
{records}

Choose a record if "{name}" is that record itself, perhaps written in other words: a plural, a fuller or
shorter name, the name with a describing word, a version of it (a variant of the same line, in another size
or form), or the name with a word for the side of it the sentence talks about, such as its design or how it
is made ("the brass focuser" and "the focuser design" are the record "Focuser"; "the Corvid ED" is the record
"Corvid").
Answer none if "{name}" is another thing that only shares words with a record: a piece of it, something made
for it or fixed to it, or a larger thing it belongs to ("the thread of the focuser" and "the focuser cap" are
not the record "Focuser").
Answer none if a word added to the record's name makes the name of another line sold under a name of its own
("the Corvid Voyager" is not the record "Corvid" when the Voyager is a line of its own).
Answer none if "{name}" means other things of the record's kind than the ones it stands for, such as earlier
ones the sentence sets apart from it ("the Corvids I owned before" are not the record "Corvid" whose data
gives this year's edition).
If "{name}" fits a whole and also pieces of that whole (the relations show which record is a piece of which),
it is the whole, unless the sentences name the piece. Answer none when several records still fit equally well.
If you choose a record, copy its id into record and copy one sentence from the lines above into quote,
verbatim. Otherwise answer none and leave quote empty."""

# What the model answers when no record is the thing
NONE = "none"


class RecordChoice(BaseModel):
    """The LLM's response schema for one choice; code checks both fields."""

    record: str = Field(description="the id of the chosen record, copied from the list, or none")
    quote: str = Field(default="", description="a sentence from the lines shown, copied verbatim, or empty")


class CandidateView(BaseModel):
    """What the data holds about a near miss beyond its id and name (R93's sheet view of a record)."""

    cells: dict[str, str]  # the plan's property columns as text
    relations: list[str]  # one hop, sorted: "TYPE -> Label:KEY (Name)" or "Label:KEY (Name) TYPE -> this"


class ChoiceRequest(BaseModel):
    """One mention with near misses: what the LLM is shown of it, and what code checks the answer against."""

    mention: str  # mention id
    name: str
    lines: list[str]  # "[document] sentence" for the sentences of its chunks that name it
    texts: list[str]  # the texts of its chunks: a quote must stand in one
    candidates: list[RecordCandidate]  # its near misses, in the order shown
    scoped: bool = True  # its document has a scope; without one the near misses are strict (R99)


ChoiceAction = Literal[
    "chosen",  # linked: a listed id, a verified quote, no twin
    "none",  # the model said no listed record is the thing
    "not_listed",  # the model answered an id that was not shown
    "quote_not_verified",  # the quote is not in the mention's chunks or does not name it
    "twin",  # another listed record has the chosen one's name
    "no_sentence",  # no sentence names the mention, so no quote could be checked: not asked
    "too_many",  # more than _MAX_CANDIDATES near misses: not asked
    "skipped",  # no LLM: not asked
    "failed",  # the call failed or its reply did not parse
]


class ChoiceDecision(BaseModel):
    """One mention with near misses and what became of it; the audit log in resolve.json."""

    mention: str
    name: str
    candidates: list[str]  # record refs, in the order shown
    action: ChoiceAction
    record: str | None = None  # the chosen record's ref, when `chosen`
    evidence: str = ""  # the verified quote of a link
    by: str = "code"  # the model that was asked, or "code"


def near_misses(
    name: str,
    match: RecordMatch,
    scopes: list[list[RecordCandidate]],
    borderline: float,
    *,
    domain: list[RecordCandidate],
) -> list[RecordCandidate]:
    """Tier 2: the records the mention's name nearly names, once each, ordered by label and key.

    Only for a mention code decided nothing about: no link, and no tie (a tie is a choice among records the
    name does name, which stays ambiguous). Inside a scope, any near name of the scope's records
    (`names.near_name`) and any whose key is a word of the name (R108). A document without a scope
    (`scopes` empty) has only the records of the mention's type, `domain`, which vouch for nothing (R94), so
    there only strict evidence nominates (`_unscoped`).
    """
    if match.link is not None or match.tied:
        return []
    if scopes:
        found = {r.element_id: r for scope in scopes for r in scope if near_name(name, r.name, borderline)}
        found |= {r.element_id: r for scope in scopes for r in keys_in_name(name, scope)}
    else:
        found = {r.element_id: r for r in _unscoped(name, domain)}
    return sorted(found.values(), key=lambda r: (r.label, r.key))


def _unscoped(name: str, domain: list[RecordCandidate]) -> list[RecordCandidate]:
    """The candidates of a mention outside any scope (R99): a record whose name is a variant of the
    mention's (`variants.compatible`: a title, an initial, a short form: "Jon Pike" for "Jonathan Pike"),
    or the one record whose key attribute holds the mention's name ("the pump of model X"), or a record
    whose key is a word of the name (R108: rule 1's reach, now a nomination). A value two
    records hold (an attribute twin: a model of two pumps) names a kind of record, not one, so it nominates
    nothing; a twin of names is refused at the choice, as inside a scope."""
    holders: dict[str, set[str]] = defaultdict(set)
    for r in domain:
        for value in r.attributes.values():
            holders[norm(value)].add(r.element_id)
    owner = holders.get(norm(name), set())
    # a key in the name nominates where it no longer links alone (R108): the domain-wide reach of rule 1
    keyed = {r.element_id for r in keys_in_name(name, domain)}
    return [
        r
        for r in domain
        if compatible(name, r.name) or (len(owner) == 1 and r.element_id in owner) or r.element_id in keyed
    ]


def choice_lines(name: str, chunks: list[tuple[str, str]]) -> list[str]:
    """Up to `_MAX_LINES` distinct "[document] sentence" lines for the sentences that name `name`, from
    `chunks` as (document, text) in reading order."""
    lines: list[str] = []
    for document, text in chunks:
        for sentence in sentences_naming(text, [name]):
            line = f"[{document}] {sentence[:_SENTENCE_CHARS]}"
            if line not in lines and len(lines) < _MAX_LINES:
                lines.append(line)
    return lines


def choice_prompt(request: ChoiceRequest, views: dict[str, CandidateView]) -> str:
    """The prompt for one request; `views` maps a candidate's element id to what the LLM sees of it."""
    records = "\n".join(_render(c, views[c.element_id]) for c in request.candidates)
    return CHOICE_PROMPT.format(name=request.name, lines="\n".join(request.lines), records=records)


def _render(candidate: RecordCandidate, view: CandidateView) -> str:
    """One record of the list. Its id is the candidate's own ref: the string code compares the answer with."""
    cells = "; ".join(f"{k} = {v}" for k, v in sorted(view.cells.items())) or "(none)"
    relations = "; ".join(view.relations) or "(none)"
    return f"- {_ref(candidate)}: {candidate.name}\n  data: {cells}\n  relations: {relations}"


def choose_records(
    requests: list[ChoiceRequest], views: dict[str, CandidateView], llm: LLMClient | None, model: str
) -> list[ChoiceDecision]:
    """Tier 3: ask the LLM about each request (in parallel) and decide each answer, in the requests' order.

    Not asked: a request without a sentence (no quote could be checked), with too many near misses, or any
    request when `llm` is None. A failed call is logged and decided as `failed`; nothing here raises for one.
    """
    askable = [i for i, r in enumerate(requests) if _unasked(r, llm) is None]
    replies: dict[int, RecordChoice | None] = {}
    if llm is not None and askable:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            answers = pool.map(lambda i: _ask(llm, model, requests[i], views), askable)
            replies = dict(zip(askable, answers, strict=True))
    out = []
    for i, request in enumerate(requests):
        action = _unasked(request, llm)
        decision = (
            _decide(request, replies[i], model) if action is None else _decision(request, action, by="code")
        )
        out.append(decision)
    return out


def _unasked(request: ChoiceRequest, llm: LLMClient | None) -> ChoiceAction | None:
    """Why a request is not asked, or None when it is."""
    if not request.lines:
        return "no_sentence"
    if len(request.candidates) > _MAX_CANDIDATES:
        return "too_many"
    return "skipped" if llm is None else None


def _ask(
    llm: LLMClient, model: str, request: ChoiceRequest, views: dict[str, CandidateView]
) -> RecordChoice | None:
    """One call; None when it failed (the provider kept failing or the reply did not fit the schema)."""
    try:
        return llm.generate(choice_prompt(request, views), RecordChoice, model=model)
    except LLMResponseError as e:  # one failed choice is no link, never a failed stage
        log.warning("record choice for %s %r failed: %s", request.mention, request.name, e)
        return None


def _decide(request: ChoiceRequest, reply: RecordChoice | None, model: str) -> ChoiceDecision:
    """Code's decision on one reply (module header): a link only for a listed, quoted, twin-free choice."""
    if reply is None:
        return _decision(request, "failed", by=model)
    answer = reply.record.strip()
    if not answer or answer.lower() == NONE:
        return _decision(request, "none", by=model)
    listed = {_ref(c): c for c in request.candidates}
    if answer not in listed:
        return _decision(request, "not_listed", by=model)
    if not _quote_holds(reply.quote, request):
        return _decision(request, "quote_not_verified", by=model)
    chosen = listed[answer]
    if any(c is not chosen and same_name(c.name, chosen.name) for c in request.candidates):
        return _decision(request, "twin", by=model)
    return _decision(request, "chosen", by=model, record=answer, evidence=reply.quote.strip())


def _quote_holds(quote: str, request: ChoiceRequest) -> bool:
    """True when the quote stands in one of the mention's chunks and names the mention (compared with
    `norm`): a quote from elsewhere, or one about another thing, shows nothing about this mention."""
    q = norm(quote)
    return bool(q) and norm(request.name) in q and any(q in norm(t) for t in request.texts)


def _decision(
    request: ChoiceRequest, action: ChoiceAction, by: str, record: str | None = None, evidence: str = ""
) -> ChoiceDecision:
    return ChoiceDecision(
        mention=request.mention,
        name=request.name,
        candidates=[_ref(c) for c in request.candidates],
        action=action,
        record=record,
        evidence=evidence,
        by=by,
    )


def _ref(record: RecordCandidate) -> str:
    return record_ref(record.label, record.key)


def chosen_links(decisions: list[ChoiceDecision], requests: list[ChoiceRequest]) -> dict[str, RecordLink]:
    """Mention id -> the link of each `chosen` decision: the record the LLM chose and code verified."""
    by_mention = {r.mention: r for r in requests}
    links = {}
    for d in decisions:
        if d.action != "chosen":
            continue
        record = next(c for c in by_mention[d.mention].candidates if _ref(c) == d.record)
        links[d.mention] = RecordLink(
            record=record,
            reason="chosen",
            score=None,
            evidence=d.evidence,
            scoped=by_mention[d.mention].scoped,
            by=d.by,
        )
    return links


def relation_line(rel_type: str, outgoing: bool, other_ref: str, other_name: str) -> str:
    """One relation of a record as the LLM and R93's sheets show it: "PART_OF -> Product:P-7 (Name)" when it
    points away from the record, "Assembly:A-3 (Name) PART_OF -> this" when it points at it."""
    if outgoing:
        return f"{rel_type} -> {other_ref} ({other_name})"
    return f"{other_ref} ({other_name}) {rel_type} -> this"


def read_candidate_views(driver: Driver, plan: ConstructionPlan, ids: list[str]) -> dict[str, CandidateView]:
    """Element id -> what the data holds about each record of `ids`: its plan property columns as text and
    its one-hop relations to other records. One read; writes nothing."""
    if not ids:
        return {}
    rules = {rule.label: rule for rule in plan.nodes}
    records, _, _ = driver.execute_query(
        "MATCH (n) WHERE elementId(n) IN $ids "
        # one hop to other records only: an edge to a document or a mention is no fact of the data
        "OPTIONAL MATCH (n)-[r]-(m) WHERE any(l IN labels(m) WHERE l IN $labels) "
        "RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS cells, "
        # collect drops the null a node without relations yields
        "collect(CASE WHEN r IS NULL THEN null ELSE {type: type(r), outgoing: startNode(r) = n, "
        "labels: labels(m), cells: properties(m)} END) AS relations",
        ids=ids,
        labels=list(rules),
    )
    out = {}
    for row in records:
        rule = next(rules[label] for label in row["labels"] if label in rules)
        relations = []
        for rel in row["relations"]:
            other = next(rules[label] for label in rel["labels"] if label in rules)
            ref, name = _identify(other, rel["cells"])
            relations.append(relation_line(rel["type"], rel["outgoing"], ref, name))
        cells = {p: str(row["cells"][p]) for p in rule.properties if row["cells"].get(p) is not None}
        out[row["id"]] = CandidateView(cells=cells, relations=sorted(relations))
    return out


# Any: a node's properties as the driver returns them (strings, numbers, dates)
def _identify(rule: NodeRule, cells: dict[str, Any]) -> tuple[str, str]:
    """A neighbour's ref and display name from its properties, as `records.read_records` names a record."""
    key = str(cells.get(rule.unique_column, ""))  # the importer always writes it; a display never fails
    name = cells.get(name_property(rule))
    return record_ref(rule.label, key), str(name) if name is not None else key
