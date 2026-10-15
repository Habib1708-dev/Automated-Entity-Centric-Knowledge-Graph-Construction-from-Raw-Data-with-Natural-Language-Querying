"""The agent's prompt (R135): what the model reads each turn, the reply it gives, and how the tools and the
earlier turns are written into the prompt.

Role in the pipeline: the loop (loop.py) renders `PROMPT` per turn and asks for the reply model
`step_model` builds for its tools; with the tools closed at a cap it renders `CLOSING` instead.
Design: one prompt, one structured reply per turn. The model proposes one call or the answer; code parses
the call's arguments into the tool's own model (the arguments travel as a JSON object in text, so one reply
schema serves every tool), runs it and writes the result back as JSON. The reply's `action` is one of two
values and its `tool` one of the tools' names (R138): a model held to the response schema cannot name no
tool, which R136's model did on 78 calls when `tool` was an optional text. The prompt is domain-neutral
(`prompt-engineering` skill): structural roles only, no example from a dataset under evaluation, and every
rule has a code-side check (an unknown tool, unparseable arguments or a repeated call is refused by the
loop; citations are checked by citations.py; the answer forms are scored by code as the reader's are).
R147: the answer-form rule and the answer fields' descriptions are the reader's (query/reader.py), filled
in here; the reply declares the form the question asks for (`asks`, required in `step_model`), and the
loop sends an answer back once when it leaves that form's field empty (`AgentStep.misses_form`,
`FORM_ANSWER`). R163: the shared rule, the fields and `asks` name several years, a month alone and a time of
day as things, and a date, a day of a month with or without its year, as a statement. R176: the text a set
answer resting on one word search is sent back with (`RECHECK_ANSWER`), rendered by recheck.py. R204: the
reply's `outcome` (answer, clarify, out_of_scope, insufficient_evidence) with `readings` for a question
back, the outcome rule in PROMPT and CLOSING, and the texts a reply failing an outcome check is sent back
with (`OUTCOME_*`).
Not here: the loop and its caps (loop.py), the citation check (citations.py), when a recheck is sent
(recheck.py), the outcome checks (outcome.py).
"""

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, create_model

from ..query.answers import AgentOutcome, AnswerForm
from ..query.reader import ANSWER_FIELDS, FORM_RULE
from .tool import Tool

# The reader's answer-form rule as the agent reads it (R147), in PROMPT and CLOSING alike
_FORM_RULE = FORM_RULE.format(source="the tools")

# The outcome rule (R204), in PROMPT and CLOSING alike: the user asked that the planner tell an answer apart
# from a question back, a subject the dataset does not cover, and a covered subject whose answer no result
# states (a refusal is the screen's, never the model's). Before R204 every "cannot answer" was free text, so
# a missing fact and an off-topic question looked alike. The split the rule spells out is the one a model
# gets wrong: "there are none" shown by a result is an answer (`entities` []), not missing evidence. The
# label is the model's reading; code checks it against what the loop saw (agent/outcome.py: an answer cites
# what it rests on, a label other than "answer" fills no answer field and follows at least one tool call, a
# "clarify" names two readings) and sends a reply failing a check back once.
_OUTCOME_RULE = """- Set `outcome` to what the results let you give:
  - "answer": they state the answer, and you cite them; a result showing there are none is an answer
    (`entities` []);
  - "insufficient_evidence": the dataset holds what the question is about, but no result states the answer;
  - "out_of_scope": the schema and your searches show the dataset does not hold what the question is about;
  - "clarify": the question can be read in several ways that would get different answers; list them in
    `readings` and ask in `text` which one is meant.
  With any outcome but "answer", leave `entities` and `number` empty and say why in `text`.
"""

# The agent's prompt. Rule by rule:
# - one call per turn: the loop runs and checks one call at a time, so each result is seen before the next
#   call is chosen, and a cap can stop between any two calls;
# - where to start: the graph's start nodes come from find_nodes (R131/R132 measured them), while counting,
#   ranking and filtering questions need the records or tables, which the schema names;
# - counts and list differences from the tools (R161): R157's agent counted 40 listed rows by eye (LB11, 11
#   for 13) and subtracted one list from another in its head, dropping an entry (FB10). Code cannot tell a
#   number read off rows from one a tool measured, so the code side is the tools': a cut or paged listing's
#   note says how to count some of its entries (structured_tools.COUNT_HINT), a group of listed rows is
#   refused with that measure named (structured_query.check_query), and `agent_number_answers_stated` (R142)
#   counts the numbers a cited result holds. One list less another is several `!=` conditions (all must
#   hold) over the ids a tool returned, which the rule names; no single operator does it. The rule asks only
#   what a tool can do: comparing values from two calls has no operator, so it is not forbidden;
# - "only what the tools return": the answer is judged on grounded facts; the model's own knowledge would
#   hide a failed search behind a lucky guess (the reader's rule); what to do when they do not answer is
#   the outcome rule (R204, above);
# - the answer-form rule is the reader's `FORM_RULE` (R147), because the same code scores both (sets and
#   numbers by code); the loop checks it against the form the reply declares (`asks`);
# - "never by a ref": R135's dev run listed refs as the answer (code maps a ref to its name as well);
# - the citation rule keeps the reader's verbatim wording for passages ("ONE contiguous quote copied
#   verbatim"), which `kg qa` checks, and lets a node, a claim or a query result be cited by id alone;
# - no repeated call: the loop refuses one anyway, and saying so saves the turn.
# Built at import: the versions hash this module string; the rendered rule holds no braces for `.format`.
PROMPT = (
    """You answer a question about one dataset. Tools search its knowledge graph, the text it was read \
from and its source tables; you call them one at a time, then answer from what they returned.

Rules:
- Each turn, either call one tool (`action` "call", `tool` its name, `arguments` a JSON object of its
  arguments) or answer (`action` "answer").
- Start from the nodes the question names (find_nodes) and walk from them; read the schema first when the
  question asks how many, which is largest or which records meet a condition.
- Take a count or a sum of entries from a tool's `measure`, with their conditions in `where`, never by
  counting listed rows yourself; to leave out the entries another result returned, add one `!=` condition
  per id it returned.
- Use only what the tools returned.
"""
    + _OUTCOME_RULE
    + _FORM_RULE
    + """
- Write an entity by its name, never by a ref.
- Cite every id your answer relies on, exactly as a tool returned it: a passage by its chunk id with ONE
  contiguous quote copied verbatim from its text; a node, a claim or a query result by its id alone.
- Never repeat a call: its result is already below.

<question>{question}</question>

<tools>
{tools}
</tools>

<turns>
{turns}
</turns>

Tool calls left: {left}."""
)

# Sent instead of PROMPT when a cap is reached: the same turns, no tool left, so the model answers from what
# it has or says that the turns do not answer the question (the plan's containment rule). The answer-form
# rule is PROMPT's (R147): the closing answer is scored as any other, though no code check follows it. The
# outcome rule is PROMPT's too (R204); a closing reply is never sent back for it, only settled by code.
CLOSING = (
    """You answer a question about one dataset from the tool results below. No tool can be called any \
more: reply now (`action` "answer") from these results alone.
"""
    + _OUTCOME_RULE
    + _FORM_RULE
    + """
- Cite every id your answer relies on exactly as a tool returned it; a passage by its chunk id with ONE \
contiguous quote copied verbatim from its text.

<question>{question}</question>

<turns>
{turns}
</turns>"""
)

# The text an answer citing ids no tool returned is sent back with, once (citations.py).
CITATION_RETRY = (
    "Your answer cited ids no tool returned: {ids}. Answer again, citing only ids from the results above."
)

# Written back, once, as the result of an answer that names nothing, gives no number and no text (R138):
# R136's model sent such answers after a tool had returned the answer (two held-out questions), and an empty
# answer is never right. The tools stay open, so the model can answer from what it has or search again;
# code decides what is empty (`AgentStep.is_empty`), so the rule needs no wording in PROMPT. R147 points to
# PROMPT's form rule instead of restating an older one, so the two cannot disagree.
EMPTY_ANSWER = (
    "Your answer was empty: no entities, no number and no text. Answer again from the results above, in the "
    "field the rules call for; if the results do not answer the question, set `outcome` as the rules say "
    "and say why in `text`."
)

# Written back, once per question, as the result of a reply whose outcome what the loop saw does not bear out
# (R204, agent/outcome.py decides which). One text per check, each naming the way out, so a reply that was
# right after all is sent again and then stands (an uncited answer, an abstention without a search), or is
# settled by code (a "clarify" with fewer than two readings becomes "insufficient_evidence"; answer fields
# beside a label other than "answer" are cleared). The tools stay open.
OUTCOME_UNCITED = (
    'Your reply has `outcome` "answer" but cites nothing the tools returned. Cite the results the answer '
    'rests on; if no result states it, set `outcome` "insufficient_evidence" or "out_of_scope" and say why '
    "in `text`."
)
OUTCOME_FIELDS = (
    'Your reply has `outcome` "{outcome}" but fills `entities` or `number`. If the results state the '
    'answer, reply with `outcome` "answer" and cite them; otherwise leave `entities` and `number` empty.'
)
OUTCOME_READINGS = (
    'Your reply has `outcome` "clarify" but `readings` does not list two different readings of the '
    "question. List them, or answer, or set the outcome the results call for."
)
OUTCOME_UNSEARCHED = (
    'Your reply has `outcome` "{outcome}" before any tool was called. Search the dataset (its schema and '
    "its text) first; if the outcome still holds, send the same reply again."
)

# Written back, once, as the result of an answer that declares a form (`asks`) and leaves that form's field
# empty (R147): R145 found a number given only in words with its qualifier ("over" a number, H36) and named
# things given only as a sentence. Code checks the reply against its own declaration
# (`AgentStep.misses_form`), never against the gold; the tools stay open. When the results do not give the
# field, or the answer is that there are none (`entities` []), the model sends the same answer again, which
# then stands (once per question). So a true "none" or "not found" written in `text` beside an empty field,
# as PROMPT's rule asks, is sent back once by design: it costs one turn, counted in
# `agent_answers_reformed`, and is never forced into a made-up value.
FORM_ANSWER = (
    "Your answer says the question asks for {asks} but leaves `{field}` empty; its text was "
    '"{text}"; answer again with the answer in `{field}`, keeping `text` only to add to it. If the results '
    "above do not give it, or the answer is that there are none (`entities` []), send the same answer again."
)

# Written back, once, as the result of a set answer that rests on one word search (R176, recheck.py): the
# search tool finds the words given, not other wordings of the idea (its description says so), and R173's
# ND11, R166's LC08 and R157's FB38 and HB41 each took one such search as complete. The message names the
# search and its arguments, so the model sees what it searched, and the tools that look further. The way out
# is the same answer again, which then stands (once per question), so a complete search is never forced to
# find more. Code decides when it is sent (`SingleSearchRecheck.applies`); the rule needs no wording in
# PROMPT.
RECHECK_ANSWER = (
    "Your answer rests on one {search} search ({arguments}), which finds those words only; the text may "
    "word the same idea otherwise. Search another wording, or use {wider}, then answer; if that search is "
    "complete, send the same answer again."
)

# What `asks` means, as the reply schema writes it (R147); "call" replies carry it too, since it is required.
# Dates and times as FORM_RULE puts them (R163): a year alone is a number; several years, a month alone or a
# time of day are things; a date, a day of a month with or without its year, is a statement. R157's agent
# declared "statement" for two years, a month and a time of day (LB39, GB05, GB33), then answered in text,
# which `misses_form` cannot send back: the declaration itself has to name them. Why no code check catches a
# wrong declaration: see FORM_RULE's intent comment (query/reader.py).
ASKS = (
    'answer: what the question asks for: "number" (one number, a single year among them), "things" (one or '
    "several named things, or values that are neither one number nor a date: several years, a month alone "
    'or a time of day among them), or "statement" (anything else, a date among them: a day of a month, with '
    'or without its year); with "call" any value, unread.'
)

# What `outcome` and `readings` mean, as the reply schema writes them (R204); the rule itself is
# `_OUTCOME_RULE`, so the descriptions only point to it and cannot disagree with it.
OUTCOME = 'answer: what the results let you give, as the outcome rule says; with "call" any value, unread.'
READINGS = 'answer with outcome "clarify": the different readings of the question, each in a few words.'


class AgentCitation(BaseModel):
    id: str = Field(description="An id exactly as a tool returned it: a chunk, a claim, a node or a query.")
    quote: str = Field(default="", description="For a chunk: one contiguous passage copied verbatim from it.")


class AgentStep(BaseModel):
    """The model's reply to one turn: one tool call, or the answer. The model is asked for the subclass
    `step_model` builds, whose `tool` is one of the tools' names; this base keeps `tool` a text so the loop
    still refuses an unknown name from a model that does not hold to the schema."""

    action: Literal["call", "answer"] = Field(description='"call" to call a tool, "answer" to answer.')
    tool: str = Field(default="", description="call: the name of the tool.")
    arguments: str = Field(default="{}", description="call: the tool's arguments as a JSON object.")
    # before the answer fields, so a model writing fields in order declares the form before it answers;
    # optional here (a test or an older reply may leave it out), required in `step_model`
    asks: AnswerForm | None = Field(default=None, description=ASKS)
    # R204: before the answer fields for the reason `asks` is; optional here (None reads as "answer", as
    # an older reply or a test gives it), required in `step_model`
    outcome: AgentOutcome | None = Field(default=None, description=OUTCOME)
    readings: list[str] | None = Field(default=None, description=READINGS)
    entities: list[str] | None = Field(default=None, description=f"answer: {ANSWER_FIELDS['entities']}.")
    number: float | None = Field(default=None, description=f"answer: {ANSWER_FIELDS['number']}.")
    text: str | None = Field(default=None, description=f"answer: {ANSWER_FIELDS['text']}.")
    citations: list[AgentCitation] = Field(default=[], description="answer: the ids the answer relies on.")

    def is_empty(self) -> bool:
        """An answer naming nothing, with no number and no text. `entities` [] is an answer ("none")."""
        return self.entities is None and self.number is None and not (self.text or "").strip()

    def answers(self) -> bool:
        """The reply claims an answer (R204): `outcome` "answer", or none given. Only such a reply is sent
        back for its form (R147) or its one search (R176), or reviewed against the passages read (R193): a
        reply saying why it cannot answer must not be pushed into a made-up value."""
        return self.outcome in (None, "answer")

    def misses_form(self) -> bool:
        """The answer leaves out the form it declares (R147): "things" with no entity named while it gives
        text and no number, or "number" with no number. Entities [] next to a text count as missing: R143b's
        model wrote [] beside text answers (the field said "[] if none"). A "things" answer carrying a number
        stands: sending it back could move a right number into `entities`. "number" needs no text to miss
        (the loop asks only for an answer that is not empty), so a number listed as an entity is sent back
        too. "statement" and no declaration never miss."""
        if self.asks == "things":
            return not self.entities and self.number is None and bool((self.text or "").strip())
        return self.asks == "number" and self.number is None


def step_model(names: list[str]) -> type[AgentStep]:
    """The reply model the model is asked for: `AgentStep` with `tool` required and one of `names`, and
    `asks` required (R147). Required because a model held to a schema may leave an optional field out
    (R136: 78 calls with no tool), and an answer whose form is not declared cannot be checked; an answer
    names a tool too, and a call declares a form, which code ignores."""
    tool = Literal[tuple(names)]  # built at run time: the names are the tools this loop was given
    return create_model(
        "AgentStep",
        __base__=AgentStep,
        tool=(tool, Field(description='The tool to call with "call"; with "answer" any name, unread.')),
        asks=(AnswerForm, Field(description=ASKS)),
        outcome=(AgentOutcome, Field(description=OUTCOME)),
    )


def tool_list(tools: Sequence[Tool] | Sequence[type[Tool]]) -> str:
    """Each tool as the model reads it: its name, what it does, and its arguments' JSON schema. Reads class
    attributes only, so the tool classes give the same text as the built tools: R146 versions it from them."""
    lines = []
    for tool in tools:
        schema = json.dumps(tool.arguments.model_json_schema(), separators=(",", ":"))
        lines.append(f"- {tool.name}: {tool.description}\n  arguments: {schema}")
    return "\n".join(lines)


def turn_text(turn: int, tool: str, arguments: str, result: str) -> str:
    """One earlier call and what came back (a result as JSON, or code's refusal)."""
    head = f'<turn n="{turn}" tool="{tool}">'
    return f"{head}\n<arguments>{arguments}</arguments>\n<result>{result}</result>\n</turn>"


def sent_back_text(turn: int, message: str) -> str:
    """An answer code sent back with the tools open and code's reply to it, as an earlier turn: an empty
    answer (R138) or one that leaves out its declared form (R147)."""
    return f'<turn n="{turn}" action="answer">\n<result>{message}</result>\n</turn>'


def form_answer(step: AgentStep) -> str:
    """`FORM_ANSWER` for an answer that leaves out its declared form: the form, its field and the answer's
    text, so the model sees what it wrote (R147). Only for a step whose `misses_form()` is true, so `asks`
    is "things" or "number": any form but "things" is written as a number."""
    things = step.asks == "things"
    return FORM_ANSWER.format(
        asks="things" if things else "a number",
        field="entities" if things else "number",
        text=step.text or "",
    )
