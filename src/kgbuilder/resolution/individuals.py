"""One individual across documents: which mentions of particular things are the same one (R75 part b2).

Role in the pipeline: the second decision of `kg resolve` (identity.py), after records. Its input is units:
the mentions linked to one record (they are that record already: the same record is evidence), and every
other keyed or individual-class mention alone. Its output is groups of units that are one thing.
Design: the user's priority (2026-10-05): "Maria Lopez" of two documents is one person only when the text
gives evidence for it. So a pair is first nominated, then decided:
  - nominated by a name variant (variants.py: "J. Pike" and "Jonathan Pike"), by spelling (the resolver's
    fuzzy score) or by meaning (the concepts' blocking rule over name embeddings, `meaning_pairs`, computed
    apart so an offline replay can give a build's logged pairs instead, audit/reidentify.py);
  - joined only on an LLM adjudication that answers "the same" AND quotes one sentence of each side, which
    code finds in that side's own chunks and which names that side; a "same" without such quotes is
    refused (`quote_not_verified`), so a missed join is preferred to a wrong one;
  - never joined when the two would hold two different records (`different_records`): a conflicting key.
A join by a record's key attribute in the sentence ("Dr. J. Pike (Soil Ecology)") is a record link
(records.py, rule 5), so it reaches this module as a member of the record's unit.
Not here: records (records.py), concepts (concepts.py), writing the outcome (identity.py, identity_graph.py).
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, Field

from ..core.similarity import name_similarity
from ..core.text import norm
from ..llm.base import Embedder, LLMClient
from .blocking import Blocking, PairKey
from .matchers import EmbeddingMatcher, EntityRecord, Matcher
from .mentions import MentionText
from .resolver import MentionRow, mention_lines
from .variants import compatible

_WORKERS = 8  # independent LLM calls, as in concept resolution

# The adjudication of two individuals. Rule by rule: "particular" and "two may share a name" because the
# same name is the commonest false evidence (two people called alike in two documents); the evidence list
# is generic (a role, a place, an organisation, a date or event), never a domain's; the quotes are what code
# checks, so the model is asked to copy them verbatim from the lines shown, one per side; "leave empty" lets
# the model refuse without inventing a quote. The examples of kinds of individuals are structural.
IDENTITY_PROMPT = """Are A and B, both of type {etype}, the same particular person, organisation, place or
object? They are named in the sentences below, often in different documents. Two different individuals may
share a name, and one individual may be written in several ways.
Answer same only when the sentences give evidence that A and B are one: the same role, place, organisation,
date or event, not a similar name alone. Then copy one sentence from A's lines into quote_a and one from B's
lines into quote_b, verbatim, that show it. Otherwise answer not the same and leave both quotes empty.

A: {a}
Where A is named ([document] sentence):
{ctx_a}

B: {b}
Where B is named ([document] sentence):
{ctx_b}"""


class SameIndividual(BaseModel):
    """The LLM's response schema for one adjudication; the quotes are checked by code."""

    same: bool
    quote_a: str = Field(default="", description="a sentence from A's lines, copied verbatim, or empty")
    quote_b: str = Field(default="", description="a sentence from B's lines, copied verbatim, or empty")


class Unit(BaseModel):
    """Mentions already known to be one thing: a record's mentions, or one mention alone."""

    id: str  # its founding mention's id
    type: str
    names: list[str]  # the mentions' names
    mentions: list[str]  # mention ids
    record: str | None = None  # the record it is, as `record_ref`, when it is a record's mentions
    chunks: int  # chunks mentioning its mentions: the most mentioned unit of a group names it


Signal = Literal["variant", "spelling", "meaning"]
Action = Literal["joined", "apart", "quote_not_verified", "different_records", "skipped"]


class IndividualDecision(BaseModel):
    """One nominated pair and what became of it; the audit log of `resolve.json`."""

    a: str  # unit ids
    b: str
    a_name: str
    b_name: str
    type: str
    signal: Signal
    action: Action
    evidence: str = ""  # the two verified quotes of a join
    by: str = "code"  # the model that adjudicated, or "code"


class Joining(BaseModel):
    """The outcome: the groups of unit ids that are one thing (singletons included) and every decision."""

    groups: list[list[str]]
    decisions: list[IndividualDecision]


Adjudicate = Callable[[Unit, Unit], SameIndividual]


def _by_type(units: list[Unit]) -> dict[str, list[Unit]]:
    by_type: dict[str, list[Unit]] = {}
    for unit in sorted(units, key=lambda u: u.id):
        by_type.setdefault(unit.type, []).append(unit)
    return by_type


def nominate(units: list[Unit], borderline: float, near: set[PairKey]) -> list[tuple[Unit, Unit, Signal]]:
    """Same-type pairs worth a decision, in a stable order: names that are variants, spelled alike (at least
    `borderline`) or near in meaning (`near`: unit id pairs, from `meaning_pairs` or a build's log). Pure."""
    pairs: list[tuple[Unit, Unit, Signal]] = []
    for members in _by_type(units).values():
        for a, b in combinations(members, 2):
            if any(compatible(x, y) for x in a.names for y in b.names):
                pairs.append((a, b, "variant"))
            elif max(name_similarity(x, y, borderline) for x in a.names for y in b.names) >= borderline:
                pairs.append((a, b, "spelling"))
            elif frozenset((a.id, b.id)) in near:
                pairs.append((a, b, "meaning"))
    return pairs


def meaning_pairs(units: list[Unit], embedding: Matcher | None, blocking: Blocking | None) -> set[PairKey]:
    """The unit pairs the blocking rule finds near in meaning over the name embeddings, type by type (a
    rank-based rule such as mutual nearest ranks within one type); none without an embedding or a rule."""
    if embedding is None or blocking is None:
        return set()
    near: set[PairKey] = set()
    for members in _by_type(units).values():
        near |= set(blocking.pairs([_as_record(u) for u in members], embedding.score))
    return near


def _as_record(unit: Unit) -> EntityRecord:
    return EntityRecord(
        id=unit.id, name=display_name(unit), type=unit.type, aliases=unit.names, mentions=unit.chunks
    )


def display_name(unit: Unit) -> str:
    """The fullest name of a unit: the most words, then the longest, then the first in order."""
    return min(unit.names, key=lambda n: (-len(n.split()), -len(n), n))


def verified(reply: SameIndividual, a: Unit, b: Unit, texts: dict[str, list[str]]) -> bool:
    """True when each quote is in a chunk of its own side and names that side (`texts`: unit id -> the
    texts of its chunks). A quote from the other side, or one that names neither, shows nothing."""

    def holds(quote: str, unit: Unit) -> bool:
        q = norm(quote)
        return (
            bool(q)
            and any(q in norm(t) for t in texts.get(unit.id, []))
            and any(norm(n) in q for n in unit.names)
        )

    return holds(reply.quote_a, a) and holds(reply.quote_b, b)


def join(
    units: list[Unit],
    pairs: list[tuple[Unit, Unit, Signal]],
    adjudicate: Adjudicate | None,
    texts: dict[str, list[str]],
    model: str,
) -> Joining:
    """Decide the nominated `pairs` and group the units. Adjudications run in parallel; unions are applied
    in the pairs' order, each refused when it would put two different records in one group."""
    replies: list[SameIndividual | None] = [None] * len(pairs)
    if adjudicate is not None:
        # two records are never one thing, so they are not asked about
        askable = [i for i, (a, b, _) in enumerate(pairs) if not (a.record and b.record)]
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            answers = pool.map(lambda i: adjudicate(pairs[i][0], pairs[i][1]), askable)
            for i, reply in zip(askable, answers, strict=True):
                replies[i] = reply
    parent = {u.id: u.id for u in units}
    record = {u.id: u.record for u in units}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving keeps the trees flat
            x = parent[x]
        return x

    decisions = []
    for (a, b, signal), reply in zip(pairs, replies, strict=True):
        action, evidence = _decide(a, b, reply, texts)
        ra, rb = find(a.id), find(b.id)
        if action == "joined" and ra != rb:
            if record[ra] and record[rb] and record[ra] != record[rb]:
                action, evidence = "different_records", ""
            else:
                parent[rb] = ra
                record[ra] = record[ra] or record[rb]
        decisions.append(
            IndividualDecision(
                a=a.id,
                b=b.id,
                a_name=display_name(a),
                b_name=display_name(b),
                type=a.type,
                signal=signal,
                action=action,
                evidence=evidence,
                by=model if reply is not None else "code",
            )  # fmt: skip
        )
    groups: dict[str, list[str]] = {}
    for u in sorted(units, key=lambda u: u.id):
        groups.setdefault(find(u.id), []).append(u.id)
    return Joining(groups=list(groups.values()), decisions=decisions)


def _decide(
    a: Unit, b: Unit, reply: SameIndividual | None, texts: dict[str, list[str]]
) -> tuple[Action, str]:
    if a.record and b.record:  # two records are two things: their keys differ
        return "different_records", ""
    if reply is None:
        return "skipped", ""
    if not reply.same:
        return "apart", ""
    if not verified(reply, a, b, texts):
        return "quote_not_verified", ""
    return "joined", f"A: {reply.quote_a.strip()} | B: {reply.quote_b.strip()}"


def llm_adjudicator(llm: LLMClient, model: str, rows: list[MentionText], units: list[Unit]) -> Adjudicate:
    """An adjudicator that shows the LLM both units' names with up to three "[document] sentence" lines
    each, the sentences that name them (resolver.mention_lines)."""
    owner = {m: u.id for u in units for m in u.mentions}
    names = {u.id: u.names for u in units}
    context = mention_lines(
        [
            MentionRow(
                entity=owner[r.mention], names=names[owner[r.mention]], document=r.document, text=r.text
            )
            for r in rows
        ]
    )

    def render(unit: Unit) -> str:
        return "\n".join(f"- {line}" for line in context.get(unit.id, [])) or "- (no sentence names it)"

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        prompt = IDENTITY_PROMPT.format(
            etype=a.type, a=display_name(a), b=display_name(b), ctx_a=render(a), ctx_b=render(b)
        )
        return llm.generate(prompt, SameIndividual, model=model)

    return adjudicate


def embedding_for(
    units: list[Unit], embedder: Embedder | None, blocking: Blocking | None
) -> EmbeddingMatcher | None:
    """The meaning matcher over the units' display names, when both an embedder and a blocking are given."""
    if embedder is None or blocking is None or not units:
        return None
    return EmbeddingMatcher(embedder, [_as_record(u) for u in units])
