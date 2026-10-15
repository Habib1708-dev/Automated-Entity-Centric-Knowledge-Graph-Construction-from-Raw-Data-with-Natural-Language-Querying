"""The working set (R211): before an answer stands, the agent itself gives a verdict on every item it read,
each shown whole, and answers again in agreement with its verdicts.

Role in the pipeline: the loop (loop.py) hands its draft answer to the working set in the experimental variant
`agent_workset` only (plan R211-R213, pipeline/qa_systems.py); the agent itself has none. R192 found the
agent's failures were mostly items it had read and left out (a member stated in other words); R193's evidence
ledger closed 4 of 8 such failures with a second judge, which missed a plain statement itself (R195, LC08)
and reached passages only. The user, 2026-10-15, asked instead that every return be kept whole and the
planner reason over a limited set of them; the forced verdict per item is kept from the ledger, since the
agent had dropped members while their passages were in front of it (R192, NC11).
Design: the LLM proposes, code decides. Code builds the set: every passage the loop read, fetched whole from
the store (a tool may have shown an excerpt), and every structured query that ran with its rows uncut (R208),
in the order first returned, the first `limit` kept and the rest named, so nothing is cut silently (R207).
The agent's own model, at its own thinking level and with the tools closed, replies with one verdict per
item and its final answer; code checks that every item has a verdict and that each included item's quote is
verbatim in it, and sends a reply failing that back once; the second reply stands. Code adds no member and
removes none: the reply is the answer. Strategy pattern: the loop holds one working set, or none.
Not here: when the loop asks (loop.py), the evidence ledger (evidence.py), the citation check (citations.py).
"""

from typing import Literal

from pydantic import BaseModel, Field, create_model

from ..core.text import norm
from ..query.answers import QueryRecord, WorksetMark
from ..query.graph_store import GraphStore
from .citations import Ledger
from .prompt import AgentStep

# The working-set prompt, appended to the closing prompt (which holds the question, the turns and the
# answer rules, so every id stays citable). Rule by rule: the draft is shown so the agent revises its own
# answer rather than starting over; "every item, by the id in its tag" is the forced decision per item that
# R192's NC11 lacked (all gold passages shown, two members dropped), and code counts the verdicts; "in any
# wording: other words for the same idea count" is R192's literal reading, said once (the ledger's wording);
# the negation rule is the ledger's and the semantic filter's; the quote rule is the verbatim-evidence wording
# code checks; "agree with your verdicts" ties the answer to them, and "other results you cite" keeps a
# member a node or a claim supports, since the set holds passages and query rows only.
PROMPT = """Before your answer stands, check it against every item you read. Your draft answer was:

<draft>{draft}</draft>

Below is each passage and query result the tools returned to you, whole.{left_out}

Rules:
- In `verdicts`, give one verdict for every item below, by the id in its tag.
- `include` is true only when the item itself states something the question asks for, in any wording: other
  words for the same idea count. An item saying the opposite, saying it did not happen, or saying only that
  it might happen, does not.
- When `include` is true, give the thing it states it about in `thing` and ONE contiguous quote copied
  verbatim from the item's text in `quote`; when it is false, say why in `reason`.
- Then answer again so that your answer agrees with your verdicts: name what each included item states and
  cite the item; keep what other results you cite support; set `outcome` as the rules say.

<items>
{items}
</items>"""

# The working-set prompt without the draft (R214), for the variants testing R213's reading that the agent
# anchors on its own answer: in 5 of the 6 failures R213 left, the gold passage was in the set and the agent
# excluded it on a literal reason (a dent judged "not damaged"), where the ledger's judge, which never sees an
# answer, included it. The rules are PROMPT's; "answer so that" replaces "answer again", since no answer is
# shown; `{meaning}` is empty, or MEANING for the variant that adds the targeted rule.
PROMPT_NO_DRAFT = """Before you answer, check every item you read. Below is each passage and query result \
the tools returned to you, whole.{left_out}

Rules:
- In `verdicts`, give one verdict for every item below, by the id in its tag.
- `include` is true only when the item itself states something the question asks for, in any wording: other
  words for the same idea count. An item saying the opposite, saying it did not happen, or saying only that
  it might happen, does not.{meaning}
- When `include` is true, give the thing it states it about in `thing` and ONE contiguous quote copied
  verbatim from the item's text in `quote`; when it is false, say why in `reason`.
- Then answer so that your answer agrees with your verdicts: name what each included item states and cite
  the item; keep what other results you cite support; set `outcome` as the rules say.

<items>
{items}
</items>"""

# The targeted rule against literal reading (R214), the user's choice after R213. It names the three ways
# R213's excluded gold passages differed from the question: a more specific case (a dent for "damaged"), the
# same verdict in other words ("operates as expected" for "normal"), the same fact told as what happened
# ("took two people" for "needs two people"); and it forbids the reason the agent gave for each, a difference
# of words. The example is from an invented domain (prompt-engineering skill). Code cannot check a reading by
# meaning, so the check is the measurement: R215's controls show whether it adds wrong members.
MEANING = """
- Read the question for its meaning, not its words. An item states what the question asks for when it
  states a more specific case of it, the same idea in other words, or the same fact told as what happened.
  Never exclude an item only because its words differ from the question's. For example, for "Which kettles
  were faulty on arrival?", "the lid was cracked when I unpacked it" states it."""

# Said in PROMPT when the set is cut at its limit (R207: every cut is announced), naming what is left out.
LEFT_OUT = " {count} more items you read are not shown here: {ids}."

# Appended, once, to a reply that leaves an item without a verdict or quotes an included item wrongly; the
# second reply stands, so a model that cannot meet the check costs one reply and changes nothing by itself.
SEND_BACK = """Your reply did not meet the rules: {problems}. Reply again with one verdict for every item, \
each included item's quote copied verbatim from its text, and your answer."""

VERDICTS = "One verdict for every item shown, by the id in its tag, before the answer."


class ItemVerdict(BaseModel):
    id: str = Field(description="The item's id, exactly as in its tag.")
    include: bool = Field(description="Whether the item itself states something the question asks for.")
    thing: str = Field(default="", description="When included: the thing it states it about, by its name.")
    quote: str = Field(default="", description="When included: ONE contiguous quote copied verbatim from it.")
    reason: str = Field(default="", description="When not included: why, in a few words.")


class WorksetItem(BaseModel):
    """One item of the set, whole: a passage (its document's name, its stored text) or a query run (its tool
    and what it read, its statement, its columns and every row returned)."""

    id: str
    kind: Literal["passage", "query"]
    source: str
    statement: str = ""
    text: str


class WorksetReview(BaseModel):
    """What the working set did for one answer: the items shown, the ids cut at the limit, the verdicts as
    code checked them, whether the reply was sent back, the problems the final reply still had, and whether
    the answer changed from the draft."""

    items: list[str] = []
    left_out: list[str] = []
    marks: list[WorksetMark] = []
    sent: bool = False
    problems: list[str] = []
    changed: bool = False


class Workset:
    """Builds the set from the loop's ledger and checks the agent's verdicts; `store` gives each passage
    whole, `limit` the most items shown, `template` the prompt (PROMPT shows the draft, PROMPT_NO_DRAFT does
    not, R214) and `meaning` the text put in its `{meaning}` ("" or MEANING; PROMPT has no such place)."""

    def __init__(self, store: GraphStore, limit: int, template: str = PROMPT, meaning: str = ""):
        self._store = store
        self._limit = limit
        self._template = template
        self._meaning = meaning

    def applies(self, step: AgentStep) -> bool:
        """Whether `step` is a reply the working set reviews: an answer declaring things or a number, or an
        abstention saying no result states the answer or the dataset does not hold its subject. A question
        back ("clarify") names readings, not a set, and an empty answer names nothing to check."""
        if step.action != "answer" or step.is_empty():
            return False
        if step.answers():
            return step.asks in ("things", "number")
        return step.outcome in ("insufficient_evidence", "out_of_scope")

    def items(self, ledger: Ledger) -> tuple[list[WorksetItem], list[str]]:
        """Every passage read and every query run, in the order first returned, whole; the first `limit`
        and the ids of the rest."""
        read = {c.chunk_id: c for c in ledger.read()}
        queries = {q.query_id: q for q in ledger.queries()}
        order = [i for i in ledger.returned() if i in read or i in queries]
        kept, left_out = order[: self._limit], order[self._limit :]
        whole = {c.chunk_id: c for c in self._store.chunks([i for i in kept if i in read])}
        items = []
        for i in kept:
            if i in queries:
                items.append(_query_item(queries[i]))
                continue
            # a passage the store no longer holds keeps the text a tool returned of it
            stored = whole.get(i)
            source, text = (stored.context, stored.text) if stored else (read[i].context, read[i].text)
            items.append(WorksetItem(id=i, kind="passage", source=source, text=text))
        return items, left_out

    def message(self, draft: AgentStep, items: list[WorksetItem], left_out: list[str]) -> str:
        """The template with the draft answer (when it shows one), every item whole, the items cut at the
        limit named, and the meaning rule (when it has a place for one)."""
        cut = LEFT_OUT.format(count=len(left_out), ids=", ".join(left_out)) if left_out else ""
        fields = {"outcome", "entities", "number", "text", "citations"}
        # str.format ignores the names a template does not use, so one call serves every template
        return self._template.format(draft=draft.model_dump_json(include=fields), left_out=cut,
                                     items=render(items), meaning=self._meaning)  # fmt: skip

    def send_back(self, problems: list[str]) -> str:
        """`SEND_BACK` naming the problems of a reply."""
        return SEND_BACK.format(problems="; ".join(problems))


def reply_model(step: type[AgentStep]) -> type[BaseModel]:
    """The reply asked for: `verdicts` first, so a model writing fields in order decides on every item before
    it answers, then every field of the loop's reply model `step` (the answer read back with `answer_of`)."""
    # field definitions built at run time from the loop's reply model, which a type checker cannot follow
    fields: dict = {name: (f.annotation, f) for name, f in step.model_fields.items()}
    verdicts = (list[ItemVerdict], Field(description=VERDICTS))
    return create_model("WorksetStep", verdicts=verdicts, **fields)


def answer_of(reply: BaseModel, step: type[AgentStep]) -> AgentStep:
    """The answer in a working-set reply, as the loop's reply model."""
    return step.model_validate(reply.model_dump(exclude={"verdicts"}))


def verdicts_of(reply: BaseModel) -> list[ItemVerdict]:
    """The verdicts in a working-set reply (`reply_model` puts them in every reply it builds)."""
    return list(getattr(reply, "verdicts", []))


def check(verdicts: list[ItemVerdict], items: list[WorksetItem]) -> tuple[list[WorksetMark], list[str]]:
    """The verdicts as code reads them, and the problems of the reply: an item with no verdict, an included
    item whose quote is not verbatim (under `norm`) in its text. An unknown id is ignored, a repeated id's
    first verdict counts."""
    texts = {item.id: item.text for item in items}
    first: dict[str, ItemVerdict] = {}
    for verdict in verdicts:
        if verdict.id.strip() in texts:
            first.setdefault(verdict.id.strip(), verdict)
    marks, wrong = [], []
    for item_id, text in texts.items():
        verdict = first.get(item_id)
        if verdict is None:
            continue
        found = bool(norm(verdict.quote)) and norm(verdict.quote) in norm(text) if verdict.include else None
        if found is False:
            wrong.append(item_id)
        marks.append(WorksetMark(id=item_id, include=verdict.include, thing=verdict.thing,
                                 quote=verdict.quote, reason=verdict.reason, quote_found=found))  # fmt: skip
    missing = [i for i in texts if i not in first]
    problems = []
    if missing:
        problems.append(f"no verdict for the items {', '.join(missing)}")
    if wrong:
        problems.append(f"the quote of the included items {', '.join(wrong)} is not verbatim in them")
    return marks, problems


def changed(draft: AgentStep, final: AgentStep) -> bool:
    """Whether the final answer differs from the draft in its outcome, its named things or its number."""

    def key(step: AgentStep) -> tuple[object, ...]:
        names = None if step.entities is None else sorted(norm(e) for e in step.entities)
        return step.outcome, names, step.number

    return key(draft) != key(final)


def render(items: list[WorksetItem]) -> str:
    """The items as the prompt shows them, each whole in a tag with its id."""
    parts = []
    for item in items:
        if item.kind == "passage":
            head = f'<item id="{item.id}" kind="passage" document="{item.source}">'
        else:
            head = f'<item id="{item.id}" kind="query" source="{item.source}">'
            head += f"\n<statement>{item.statement}</statement>"
        parts.append(f"{head}\n{item.text}\n</item>")
    return "\n".join(parts)


def _query_item(record: QueryRecord) -> WorksetItem:
    """A query run as an item: its columns and every row returned, uncut, and how many there were in all
    when the rows are one page of more."""
    lines = [" | ".join(record.columns), *(" | ".join(row) for row in record.rows)]
    if not record.rows:
        lines.append("(no rows)")
    elif len(record.rows) < record.total:
        lines.append(f"({len(record.rows)} of {record.total} rows returned)")
    source = f"{record.tool} over {', '.join(record.sources)}"
    return WorksetItem(id=record.query_id, kind="query", source=source, statement=record.statement,
                       text="\n".join(lines))  # fmt: skip
