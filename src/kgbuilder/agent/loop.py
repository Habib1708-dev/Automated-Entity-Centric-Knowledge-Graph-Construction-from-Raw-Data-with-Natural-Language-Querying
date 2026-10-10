"""The agent loop (R135): the model proposes one tool call per turn, code checks and runs it, until the
model answers or a cap closes the tools.

Role in the pipeline: the agent system (system.py) runs one loop per question and turns its `AgentRun` into
the answer `kg qa` scores.
Design: the LLM proposes, code decides and contains. Each turn the model reads the question, the tools and
every earlier call with its result (prompt.py) and replies with an `AgentStep`. Code refuses an unknown
tool, arguments that do not parse into the tool's model, and a call identical to an earlier one, each with
its reason written back as that turn's result; it runs every other call and keeps the ids its result offers
(citations.py). Four caps hold per question: turns, tool calls run, the characters of all prompts sent, and
wall-clock time; at a cap the model is asked once more with the tools closed. The prompt-size cap stands in
for token and cost caps: the LLM port reports usage to the tracker, not to the caller, and the prompt is
what grows with every turn. An answer citing ids no tool returned is sent back once with the ids named;
what it still cites wrongly is dropped and counted. The model is asked for the reply model whose `tool` is
one of this loop's tool names (`step_model`, R138), and an answer with nothing in it is written back once
as that turn's result, with the tools still open (R138). The trace times the model's calls and the whole
loop with `timer`, kept apart from the timeout's `clock` (R142).
Not here: the prompt text (prompt.py), the tools (graph_tools.py, text_tools.py, structured_tools.py),
the answer's shape for `kg qa` (system.py).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from ..llm.base import LLMClient
from ..query.answers import AgentCall, AgentStop, AgentTrace
from .citations import CheckedCitations, Ledger
from .prompt import (
    CITATION_RETRY,
    CLOSING,
    EMPTY_ANSWER,
    PROMPT,
    AgentStep,
    empty_answer_text,
    step_model,
    tool_list,
    turn_text,
)  # fmt: skip
from .tool import Tool


class AgentLimits(BaseModel):
    """The caps of one question's loop."""

    turns: int = Field(ge=1)  # replies asked of the model, the closing one aside
    calls: int = Field(ge=1)  # tool calls run (refused calls use a turn, not a call)
    prompt_chars: int = Field(ge=1)  # characters of every prompt sent, the closing one aside
    timeout_s: float = Field(gt=0)  # wall-clock time, checked before each turn


@dataclass
class AgentRun:
    """One question's loop: the final reply, its checked citations, the ledger of what the tools returned,
    and the trace."""

    answer: AgentStep
    citations: CheckedCitations
    ledger: Ledger
    trace: AgentTrace


@dataclass
class _State:
    """What one question's loop has done so far."""

    started: float
    ledger: Ledger = field(default_factory=Ledger)
    calls: list[AgentCall] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)  # each earlier turn as the prompt writes it
    seen: dict[str, int] = field(default_factory=dict)  # a run call's tool and arguments -> its turn
    prompt_chars: int = 0
    turn: int = 0
    emptied: bool = False  # an empty answer was written back (once per question)
    timed: float = 0.0  # the timer's reading when the loop began (R142)
    llm_s: float = 0.0  # seconds spent in the model's calls (R142)

    def calls_run(self) -> int:
        return sum(not c.refused for c in self.calls)


class AgentLoop:
    """Answers one question by tool calls under `limits`; `clock` (the timeout's) and `timer` (the trace's
    latencies) are injected so both can be tested."""

    def __init__(
        self, tools: list[Tool], llm: LLMClient, model: str, temperature: float, limits: AgentLimits,
        clock: Callable[[], float] = time.monotonic, timer: Callable[[], float] = time.perf_counter,
    ):  # fmt: skip
        self._tools = {tool.name: tool for tool in tools}
        self._tool_text = tool_list(tools)
        self._step = step_model(list(self._tools))
        self._llm = llm
        self._model = model
        self._temperature = temperature
        self._limits = limits
        self._clock = clock
        self._timer = timer

    def run(self, question: str) -> AgentRun:
        """The answer to `question`. Raises `LLMResponseError` when the model fails; a tool's database error
        propagates as for every system."""
        state = _State(started=self._clock(), timed=self._timer())
        while state.turn < self._limits.turns:
            prompt = PROMPT.format(question=question, tools=self._tool_text, turns=_turns(state),
                                   left=self._limits.calls - state.calls_run())  # fmt: skip
            if stop := self._cap(state, len(prompt)):
                return self._close(question, state, stop)
            state.turn += 1
            state.prompt_chars += len(prompt)
            step = self._generate(prompt, state)
            if step.action == "answer" and step.is_empty() and not state.emptied:
                state.emptied = True
                state.turns.append(empty_answer_text(state.turn, EMPTY_ANSWER))
                continue
            if step.action == "answer":
                return self._finish(question, state, step, "answer")
            self._call(step, state)
        return self._close(question, state, "turns")

    def _cap(self, state: _State, next_prompt: int) -> AgentStop | None:
        """The cap the next turn would break, if any."""
        if state.calls_run() >= self._limits.calls:
            return "calls"
        if state.prompt_chars + next_prompt > self._limits.prompt_chars:
            return "prompt_chars"
        if self._clock() - state.started >= self._limits.timeout_s:
            return "timeout"
        return None

    def _call(self, step: AgentStep, state: _State) -> None:
        """Check the proposed call; run it unless refused; write the turn and its trace."""
        tool = self._tools.get(step.tool)
        arguments, refused = None, ""
        if tool is None:
            refused = f"no tool {step.tool!r}; the tools are {', '.join(self._tools)}"
        else:
            arguments, refused = _parse(tool, step.arguments)
        key = f"{step.tool} {arguments.model_dump_json()}" if arguments is not None else ""
        if not refused and key in state.seen:
            refused = f"identical to the call of turn {state.seen[key]}; its result is above"
        if refused or tool is None or arguments is None:
            state.calls.append(
                AgentCall(turn=state.turn, tool=step.tool, arguments=step.arguments, refused=refused)
            )
            state.turns.append(turn_text(state.turn, step.tool, step.arguments, f"refused: {refused}"))
            return
        started = time.perf_counter()
        result = tool.run(arguments)
        latency = (time.perf_counter() - started) * 1000
        state.seen[key] = state.turn
        state.ledger.add(result)
        call = AgentCall(turn=state.turn, tool=tool.name, arguments=step.arguments, note=result.note,
                         returned=result.cites(), latency_ms=round(latency, 1))  # fmt: skip
        state.calls.append(call)
        state.turns.append(turn_text(state.turn, tool.name, step.arguments, result.model_dump_json()))

    def _close(self, question: str, state: _State, stop: AgentStop) -> AgentRun:
        """A cap is reached: ask once more with the tools closed, and finish with that reply as the answer."""
        step = self._closing_reply(question, state, "")
        return self._finish(question, state, step, stop)

    def _finish(self, question: str, state: _State, step: AgentStep, stop: AgentStop) -> AgentRun:
        """Check the answer's citations, sending it back once (tools closed) when it cites unknown ids."""
        checked = state.ledger.check(step.citations)
        retried = bool(checked.unknown)
        if retried:
            step = self._closing_reply(question, state, CITATION_RETRY.format(ids=", ".join(checked.unknown)))
            checked = state.ledger.check(step.citations)
        trace = AgentTrace(
            calls=state.calls, turns=state.turn, stopped=stop, prompt_chars=state.prompt_chars,
            cited=checked.kept, dropped=checked.unknown, retried=retried, emptied=state.emptied,
            llm_ms=round(state.llm_s * 1000, 1), wall_ms=round((self._timer() - state.timed) * 1000, 1),
        )  # fmt: skip
        return AgentRun(answer=step, citations=checked, ledger=state.ledger, trace=trace)

    def _closing_reply(self, question: str, state: _State, extra: str) -> AgentStep:
        """The model's answer with the tools closed; its prompt is not counted against the cap, which it
        ends."""
        prompt = CLOSING.format(question=question, turns=_turns(state)) + (f"\n\n{extra}" if extra else "")
        return self._generate(prompt, state)

    def _generate(self, prompt: str, state: _State) -> AgentStep:
        """One reply of the model, its time added to the loop's model time."""
        started = self._timer()
        step = self._llm.generate(prompt, self._step, model=self._model, temperature=self._temperature)
        state.llm_s += self._timer() - started
        return step


def _parse(tool: Tool, arguments: str) -> tuple[BaseModel | None, str]:
    """The call's arguments parsed into the tool's model, or why they cannot be."""
    try:
        values = json.loads(arguments or "{}")
    except json.JSONDecodeError as e:
        return None, f"the arguments are not a JSON object ({e.msg})"
    if not isinstance(values, dict):
        return None, "the arguments must be a JSON object"
    try:
        return tool.arguments.model_validate(values), ""
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}" for err in e.errors()
        )
        return None, f"the arguments do not fit {tool.name}: {problems}"


def _turns(state: _State) -> str:
    return "\n".join(state.turns) if state.turns else "(none yet)"
