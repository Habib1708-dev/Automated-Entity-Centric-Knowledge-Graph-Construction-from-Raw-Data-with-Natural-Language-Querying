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
Not here: the loop and its caps (loop.py), the citation check (citations.py).
"""

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, create_model

from .tool import Tool

# The agent's prompt. Rule by rule:
# - one call per turn: the loop runs and checks one call at a time, so each result is seen before the next
#   call is chosen, and a cap can stop between any two calls;
# - where to start: the graph's start nodes come from find_nodes (R131/R132 measured them), while counting,
#   ranking and filtering questions need the records or tables, which the schema names;
# - "only what the tools return": the answer is judged on grounded facts; the model's own knowledge would
#   hide a failed search behind a lucky guess (the reader's rule);
# - the answer forms are the reader's, because the same code scores them (sets and numbers by code); R135's
#   dev run added "never by a ref" (an answer listed refs; code maps a ref to its name as well) and "which
#   year" (a year was given in words where the gold scores a number);
# - the citation rule keeps the reader's verbatim wording for passages ("ONE contiguous quote copied
#   verbatim"), which `kg qa` checks, and lets a node, a claim or a query result be cited by id alone;
# - no repeated call: the loop refuses one anyway, and saying so saves the turn.
PROMPT = """You answer a question about one dataset. Tools search its knowledge graph, the text it was read \
from and its source tables; you call them one at a time, then answer from what they returned.

Rules:
- Each turn, either call one tool (`action` "call", `tool` its name, `arguments` a JSON object of its
  arguments) or answer (`action` "answer").
- Start from the nodes the question names (find_nodes) and walk from them; read the schema first when the
  question asks how many, which is largest or which records meet a condition.
- Use only what the tools returned. When it does not answer the question, say so in `text` and leave
  `entities` and `number` empty.
- When the question asks which things, list them in `entities`, each once, by the names the tools or the
  question give them, never by a ref. When the answer is a number (how many, how much, which year), give it
  in `number`. Otherwise answer in a short `text`.
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

# Sent instead of PROMPT when a cap is reached: the same turns, no tool left, so the model answers from what
# it has or says that the turns do not answer the question (the plan's containment rule).
CLOSING = """You answer a question about one dataset from the tool results below. No tool can be called any \
more: answer now (`action` "answer") from these results alone, or say in `text` that they do not answer the \
question. List things in `entities` by name, give a number (a count, an amount, a year) in `number`, \
otherwise a short `text`. Cite \
every id your answer relies on exactly as a tool returned it; a passage by its chunk id with ONE contiguous \
quote copied verbatim from its text.

<question>{question}</question>

<turns>
{turns}
</turns>"""

# The text an answer citing ids no tool returned is sent back with, once (citations.py).
CITATION_RETRY = (
    "Your answer cited ids no tool returned: {ids}. Answer again, citing only ids from the results above."
)

# Written back, once, as the result of an answer that names nothing, gives no number and no text (R138):
# R136's model sent such answers after a tool had returned the answer (two held-out questions), and an empty
# answer is never right. The tools stay open, so the model can answer from what it has or search again;
# code decides what is empty (`AgentStep.is_empty`), so the rule needs no wording in PROMPT.
EMPTY_ANSWER = (
    "Your answer was empty: no entities, no number and no text. Answer again from the results above: the "
    "things in `entities`, a number in `number`, otherwise a short `text`; if the results do not answer the "
    "question, say so in `text`."
)


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
    entities: list[str] | None = Field(
        default=None, description="answer: the things that answer a question asking which things; [] if none."
    )
    number: float | None = Field(default=None, description="answer: a count or an amount the question asks.")
    text: str | None = Field(
        default=None, description="answer: a short answer in words, for any other question."
    )
    citations: list[AgentCitation] = Field(default=[], description="answer: the ids the answer relies on.")

    def is_empty(self) -> bool:
        """An answer naming nothing, with no number and no text. `entities` [] is an answer ("none")."""
        return self.entities is None and self.number is None and not (self.text or "").strip()


def step_model(names: list[str]) -> type[AgentStep]:
    """The reply model the model is asked for: `AgentStep` with `tool` required and one of `names`. Required
    because a model held to a schema may leave an optional field out (R136: 78 calls with no tool); an
    answer names a tool too, which code ignores."""
    tool = Literal[tuple(names)]  # built at run time: the names are the tools this loop was given
    return create_model(
        "AgentStep",
        __base__=AgentStep,
        tool=(tool, Field(description='The tool to call with "call"; with "answer" any name, unread.')),
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


def empty_answer_text(turn: int, message: str) -> str:
    """An empty answer and code's reply to it, as an earlier turn (R138)."""
    return f'<turn n="{turn}" action="answer">\n<result>{message}</result>\n</turn>'
