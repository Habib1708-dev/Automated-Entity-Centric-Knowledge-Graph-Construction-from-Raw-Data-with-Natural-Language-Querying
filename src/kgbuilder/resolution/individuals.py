"""One individual across documents: which mentions of particular things are the same one (R75 part b2).

Role in the pipeline: the second decision of `kg resolve` (identity.py), after records. Its input is units:
the mentions linked to one record (they are that record already: the same record is evidence), and every
other keyed or individual-class mention alone. Its output is groups of units that are one thing.
Design: the user's priority (2026-10-05): "Maria Lopez" of two documents is one person only when the text
gives evidence for it. So a pair is first nominated, then decided:
  - nominated by a name variant (variants.py: "J. Pike" and "Jonathan Pike"), by spelling (the resolver's
    fuzzy score) or by meaning (the concepts' blocking rule over name embeddings, `meaning_pairs`, computed
    apart so an offline replay can give a build's logged pairs instead, audit/reidentify.py);
  - joined only on an LLM adjudication that answers "same" AND quotes one line of each side, which code
    finds among the lines that side was shown (identity_evidence.py, R100: every sentence naming it with its
    neighbours, a record unit's data, the records both sides name); a "same" without such quotes is refused
    (`quote_not_verified`), "different" and "unsure" keep the pair apart, and a pair one of whose sides no
    sentence names is not asked (`no_sentence`), so a missed join is preferred to a wrong one;
  - never joined when the two would hold two different records (`different_records`): a conflicting key.
A join by a record's key attribute in the sentence ("Dr. J. Pike (Soil Ecology)") is a record link
(records.py, rule 5), so it reaches this module as a member of the record's unit.
Not here: records (records.py), concepts (concepts.py), writing the outcome (identity.py, identity_graph.py).
"""

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, Field

from ..core.errors import LLMResponseError
from ..core.similarity import name_similarity
from ..core.text import norm
from ..llm.base import Embedder, LLMClient
from .blocking import Blocking, PairKey
from .identity_evidence import EvidenceLine, IdentityEvidence
from .matchers import EmbeddingMatcher, EntityRecord, Matcher
from .record_choice import CandidateView
from .variants import compatible

log = logging.getLogger(__name__)

_WORKERS = 8  # independent LLM calls, as in concept resolution

# The adjudication of two individuals (R100; R75's prompt showed three first sentences per side).
# Rule by rule:
#   - "particular" and "may share a name": the same name is the commonest false evidence;
#   - the lines: every sentence naming a side with its neighbours, "*" on those that name it, because the
#     evidence (a role, a date, an event) often stands next to the name: R93's nine generality splits were
#     all answered apart on headings and first mentions;
#   - "a similar name ... naming the same record or place is not enough": two different people can belong to
#     one organisation; the records both sides name are shown as context only;
#   - "different" and "unsure": the model may say the lines do not settle it; both keep the pair apart, and
#     code counts them apart, so a reader sees how often the evidence is missing rather than contrary;
#   - the quotes are what code checks: one line per side, from that side's own lines; empty otherwise.
# The examples are an invented observatory, never evaluated data.
IDENTITY_PROMPT = """Are A and B, both of type {etype}, the same particular person, organisation, place or
object? Two different ones may share a name, and one may be written in several ways.

Answer "same" only when the lines show that A and B are one: the same role, position, place, organisation,
date or event is stated for both. A similar name is not enough, and neither is naming the same record or
place: two different people can belong to one organisation. Answer "different" when the lines show two
different ones (a role, a place or a time that cannot hold for both). Answer "unsure" when the lines do not
settle it.
For example, "E. Varga (dome technician)" and "Edit Varga, who services the dome" can be "same"; two
"E. Varga" whose lines only both mention the observatory are "unsure".
If you answer "same", copy one of A's lines into quote_a and one of B's lines into quote_b, verbatim: the
lines that show it. Otherwise leave both quotes empty.

A: {a}
{record_a}Lines that name A, with their neighbours ([document] sentence; "*" marks a line that names A):
{lines_a}

B: {b}
{record_b}Lines that name B, with their neighbours ([document] sentence; "*" marks a line that names B):
{lines_b}

Records the texts of both A and B also name:
{shared}"""


class SameIndividual(BaseModel):
    """The LLM's response schema for one adjudication; code checks the quotes and joins only on "same"."""

    answer: Literal["same", "different", "unsure"]
    quote_a: str = Field(default="", description="one of A's lines, copied verbatim, or empty")
    quote_b: str = Field(default="", description="one of B's lines, copied verbatim, or empty")


class Unit(BaseModel):
    """Mentions already known to be one thing: a record's mentions, or one mention alone."""

    id: str  # its founding mention's id
    type: str
    names: list[str]  # the mentions' names
    mentions: list[str]  # mention ids
    record: str | None = None  # the record it is, as `record_ref`, when it is a record's mentions
    chunks: int  # chunks mentioning its mentions: the most mentioned unit of a group names it


Signal = Literal["variant", "spelling", "meaning"]
# What became of a nominated pair. `apart`: the model answered "different"; `unsure`: the lines did not
# settle it (R100); both keep the pair apart, counted separately so a reader sees how often evidence is
# missing. `no_sentence`: a side has no line naming it, so no quote could be checked: not asked (R100).
# `failed`: asked, but the provider kept failing or its reply did not parse (R100).
Action = Literal[
    "joined",
    "apart",
    "unsure",
    "quote_not_verified",
    "different_records",
    "no_sentence",
    "skipped",
    "failed",
]


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


# A quote copied with its line's "[document]" prefix or a list marker is the same line
_LINE_PREFIX = re.compile(r"^\s*[-*]?\s*(\[[^\]]*\]\s*)?\*?\s*")


def verified(reply: SameIndividual, a_lines: list[EvidenceLine], b_lines: list[EvidenceLine]) -> bool:
    """True when each quote stands verbatim (after `norm`) in one of the lines shown for its own side, and
    each side was shown a line naming it. The quote need not name its side: the evidence (a role, an event)
    often stands in the sentence next to the name, which is why the neighbours are shown. A quote from the
    other side, or from outside the lines shown, shows nothing."""

    def holds(quote: str, lines: list[EvidenceLine]) -> bool:
        q = norm(_LINE_PREFIX.sub("", quote))
        return (
            bool(q)
            and any(line.names_it for line in lines)
            and any(q in norm(line.sentence) for line in lines)
        )

    return holds(reply.quote_a, a_lines) and holds(reply.quote_b, b_lines)


def join(
    units: list[Unit],
    pairs: list[tuple[Unit, Unit, Signal]],
    adjudicate: Adjudicate | None,
    shown: dict[str, list[EvidenceLine]],
    model: str,
) -> Joining:
    """Decide the nominated `pairs` and group the units; `shown` is the lines each unit is shown with (unit
    id -> lines), which quotes are checked against. Adjudications run in parallel; unions are applied in the
    pairs' order, each refused when it would put two different records in one group. A failed adjudication
    keeps its pair apart (`failed`); nothing here raises for one."""
    replies: list[SameIndividual | None] = [None] * len(pairs)
    asked: set[int] = set()
    if adjudicate is not None:
        # two records are never one thing, and a side no line names has nothing to quote: neither is asked
        askable = [
            i
            for i, (a, b, _) in enumerate(pairs)
            if not (a.record and b.record) and _named(shown, a) and _named(shown, b)
        ]
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            answers = pool.map(lambda i: _ask(adjudicate, pairs[i][0], pairs[i][1]), askable)
            for i, reply in zip(askable, answers, strict=True):
                replies[i] = reply
        asked = set(askable)
    parent = {u.id: u.id for u in units}
    record = {u.id: u.record for u in units}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving keeps the trees flat
            x = parent[x]
        return x

    decisions = []
    for i, ((a, b, signal), reply) in enumerate(zip(pairs, replies, strict=True)):
        action, evidence = _decide(a, b, reply, shown, i in asked)
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
                by=model if i in asked else "code",
            )  # fmt: skip
        )
    groups: dict[str, list[str]] = {}
    for u in sorted(units, key=lambda u: u.id):
        groups.setdefault(find(u.id), []).append(u.id)
    return Joining(groups=list(groups.values()), decisions=decisions)


def _ask(adjudicate: Adjudicate, a: Unit, b: Unit) -> SameIndividual | None:
    """One adjudication; None when the provider kept failing or its reply did not fit the schema."""
    try:
        return adjudicate(a, b)
    except LLMResponseError as e:  # one failed pair is kept apart and logged, never a failed stage
        log.warning("adjudication of %r / %r failed: %s", display_name(a), display_name(b), e)
        return None


def _named(shown: dict[str, list[EvidenceLine]], unit: Unit) -> bool:
    return any(line.names_it for line in shown.get(unit.id, []))


def _decide(
    a: Unit, b: Unit, reply: SameIndividual | None, shown: dict[str, list[EvidenceLine]], asked: bool
) -> tuple[Action, str]:
    if a.record and b.record:  # two records are two things: their keys differ
        return "different_records", ""
    if not (_named(shown, a) and _named(shown, b)):
        return "no_sentence", ""
    if reply is None:  # asked without an answer, or no adjudicator at all
        return ("failed" if asked else "skipped"), ""
    if reply.answer != "same":
        return ("unsure" if reply.answer == "unsure" else "apart"), ""
    if not verified(reply, shown[a.id], shown[b.id]):
        return "quote_not_verified", ""
    return "joined", f"A: {reply.quote_a.strip()} | B: {reply.quote_b.strip()}"


def llm_adjudicator(llm: LLMClient, model: str, evidence: IdentityEvidence) -> Adjudicate:
    """An adjudicator that shows the LLM both units' names, a record unit's data, each side's lines (every
    sentence naming it with its neighbours) and the records both sides' texts name (identity_evidence.py)."""

    def render_side(unit: Unit) -> tuple[str, str]:
        side = evidence.sides[unit.id]
        record = f"{unit.record} in the data: {_data(side.view)}\n" if side.record and side.view else ""
        lines = "\n".join(
            f"- {'*' if line.names_it else ' '} [{line.document}] {line.sentence}" for line in side.lines
        )
        return record, lines or "- (no sentence names it)"

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        (record_a, lines_a), (record_b, lines_b) = render_side(a), render_side(b)
        shared = [f"- {ref}: {_data(evidence.views.get(ref))}" for ref in evidence.shared(a, b)]
        prompt = IDENTITY_PROMPT.format(
            etype=a.type,
            a=display_name(a),
            b=display_name(b),
            record_a=record_a,
            record_b=record_b,
            lines_a=lines_a,
            lines_b=lines_b,
            shared="\n".join(shared) or "- (none)",
        )
        return llm.generate(prompt, SameIndividual, model=model)

    return adjudicate


def _data(view: CandidateView | None) -> str:
    """A record's cells and one-hop relations as one line, as the record chooser shows them."""
    if view is None:
        return "(nothing)"
    cells = "; ".join(f"{k} = {v}" for k, v in sorted(view.cells.items())) or "(none)"
    relations = "; ".join(view.relations) or "(none)"
    return f"{cells}; relations: {relations}"


def embedding_for(
    units: list[Unit], embedder: Embedder | None, blocking: Blocking | None
) -> EmbeddingMatcher | None:
    """The meaning matcher over the units' display names, when both an embedder and a blocking are given."""
    if embedder is None or blocking is None or not units:
        return None
    return EmbeddingMatcher(embedder, [_as_record(u) for u in units])
