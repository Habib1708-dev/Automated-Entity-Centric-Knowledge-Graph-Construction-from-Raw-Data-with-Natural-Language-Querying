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
as that turn's result, with the tools still open (R138). An answer that is not empty but leaves out the
form its reply declares (`asks`: things, or a number) is written back once the same way (R147), and only
while a turn is left (on the last turn it stands as the answer); neither send-back happens with the tools
closed, and each uses a turn, so the loop cannot spin. A set answer resting on one word search is written
back once the same way when the loop holds a `recheck` rule (R176, recheck.py), after the form check, so an
answer may be sent back for its form and then for its search, two turns at most. When the loop holds an
evidence ledger (R193, evidence.py; the variant `agent_evidence` only), the answer it is about to finish
with, whether given with the tools open or at a cap, is reviewed once against every passage read; when the
ledger finds members it leaves out, they go back with the tools closed (no turn, no call), and the reply is
the answer, before the citation check as every answer is. R204: a reply's `outcome` is checked by the
loop's `outcome` check (outcome.py) after the empty check and sent back once, the tools open, when what the
loop saw does not bear it out; the form check, the recheck and the evidence review apply only to a reply
claiming an answer (`AgentStep.answers`); every final reply is settled (outcome.py's `settle`) after its
citation check, and its outcome goes into the trace. R211: when the loop holds a working set (workset.py;
the variant `agent_workset` only), the answer it is about to finish with, after any evidence review, is
given back to the model with every item it read shown whole; the model gives a verdict per item and answers
again, the tools closed, sent back once when code finds a verdict missing or a quote not in its item. The
trace times the
model's calls and the whole loop with `timer`, kept apart from the timeout's `clock` (R142). R221: `run`
takes an observer (observer.py) told of every model reply, settled call and send-back as it happens; it
only watches, so the loop is the same without one.
Not here: the prompt text (prompt.py), the tools (graph_tools.py, text_tools.py, structured_tools.py),
the answer's shape for `kg qa` (system.py).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError

from ..llm.base import LLMClient
from ..query.answers import AgentCall, AgentStop, AgentTrace
from .citations import CheckedCitations, Ledger
from .evidence import EvidenceLedger, EvidenceReview
from .observer import NO_OBSERVER, AgentObserver
from .outcome import OutcomeCheck, settle
from .prompt import (
    CITATION_RETRY,
    CLOSING,
    EMPTY_ANSWER,
    PROMPT,
    AgentStep,
    form_answer,
    sent_back_text,
    step_model,
    tool_list,
    turn_text,
)  # fmt: skip
from .recheck import SingleSearchRecheck
from .tool import Tool
from .workset import Workset, WorksetReview, answer_of, changed, check, reply_model, verdicts_of

M = TypeVar("M", bound=BaseModel)


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
    observer: AgentObserver = NO_OBSERVER  # told of each reply, call and send-back (R221)
    ledger: Ledger = field(default_factory=Ledger)
    calls: list[AgentCall] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)  # each earlier turn as the prompt writes it
    seen: dict[str, int] = field(default_factory=dict)  # a run call's tool and arguments -> its turn
    prompt_chars: int = 0
    turn: int = 0
    emptied: bool = False  # an empty answer was written back (once per question)
    reformed: bool = False  # an answer missing its declared form was written back (once per question, R147)
    rechecked: bool = False  # a set answer resting on one search was written back (once per question, R176)
    outcome_sent: bool = False  # a reply failing an outcome check was written back (once per question, R204)
    evidence: EvidenceReview | None = None  # the evidence ledger's review of the answer (R193)
    workset: WorksetReview | None = None  # what the working set did for the answer (R211)
    timed: float = 0.0  # the timer's reading when the loop began (R142)
    llm_s: float = 0.0  # seconds spent in the model's calls (R142)

    def calls_run(self) -> int:
        return sum(not c.refused for c in self.calls)


class AgentLoop:
    """Answers one question by tool calls under `limits`; `clock` (the timeout's) and `timer` (the trace's
    latencies) are injected so both can be tested; `recheck` (R176) is the rule that sends a set answer
    resting on one search back, or None for a loop that never does; `evidence` (R193) the ledger reviewing
    a set answer against the passages read, or None for a loop that never does; `outcome` (R204) the
    check a reply's outcome must pass, or None for a loop that never sends one back (its replies are still
    settled); `workset` (R211) the working set the agent reviews its answer against, or None for a loop that
    never does."""

    def __init__(
        self, tools: list[Tool], llm: LLMClient, model: str, temperature: float, limits: AgentLimits,
        clock: Callable[[], float] = time.monotonic, timer: Callable[[], float] = time.perf_counter,
        recheck: SingleSearchRecheck | None = None, evidence: EvidenceLedger | None = None,
        outcome: OutcomeCheck | None = None, workset: Workset | None = None,
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
        self._recheck = recheck
        self._evidence = evidence
        self._outcome = outcome
        self._workset = workset

    def run(self, question: str, observer: AgentObserver = NO_OBSERVER) -> AgentRun:
        """The answer to `question`, each step reported to `observer` as it happens (R221). Raises
        `LLMResponseError` when the model fails; a tool's database error propagates as for every system."""
        state = _State(started=self._clock(), observer=observer, timed=self._timer())
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
                state.turns.append(sent_back_text(state.turn, EMPTY_ANSWER))
                state.observer.sent_back(state.turn, "empty")
                continue
            # R204, after R138's check (an empty reply is sent back as empty) and before R147's (which asks
            # only a reply claiming an answer for its form); once, and only while a turn is left, for the
            # reasons R147's send-back has
            if message := self._outcome_problem(step, state):
                state.outcome_sent = True
                state.turns.append(sent_back_text(state.turn, message))
                state.observer.sent_back(state.turn, "outcome")
                continue
            # R147, after R138's check: an empty answer is sent back as empty, never also for its form; and
            # only while a turn is left, so the reply comes with the tools open (an answer on the last turn
            # stands, rather than ending as a "turns" stop). An answer runs no call, so the calls cap holds.
            resend = not state.reformed and state.turn < self._limits.turns
            claims = step.action == "answer" and step.answers()
            if claims and not step.is_empty() and step.misses_form() and resend:
                state.reformed = True
                state.turns.append(sent_back_text(state.turn, form_answer(step)))
                state.observer.sent_back(state.turn, "form")
                continue
            # R176: after the form check, so a reformed answer is still rechecked; once, and only while a
            # turn is left, for the reasons R147's send-back has
            rule = self._recheck if claims and not state.rechecked else None
            search = rule.applies(step, state.calls) if rule and state.turn < self._limits.turns else None
            if search is not None and rule is not None:
                state.rechecked = True
                state.turns.append(sent_back_text(state.turn, rule.message(search)))
                state.observer.sent_back(state.turn, "recheck")
                continue
            if step.action == "answer":
                return self._finish(question, state, step, "answer")
            self._call(step, state)
        return self._close(question, state, "turns")

    def _outcome_problem(self, step: AgentStep, state: _State) -> str | None:
        """The message a reply failing an outcome check is sent back with (R204), or None."""
        check = self._outcome
        if check is None or step.action != "answer" or state.outcome_sent:
            return None
        if state.turn >= self._limits.turns:
            return None
        return check.problem(step, state.ledger.check(step.citations).kept, state.calls_run())

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
            refusal = AgentCall(turn=state.turn, tool=step.tool, arguments=step.arguments, refused=refused)
            state.calls.append(refusal)
            state.turns.append(turn_text(state.turn, step.tool, step.arguments, f"refused: {refused}"))
            state.observer.call_done(refusal, None)
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
        state.observer.call_done(call, result)

    def _close(self, question: str, state: _State, stop: AgentStop) -> AgentRun:
        """A cap is reached: ask once more with the tools closed, and finish with that reply as the answer."""
        step = self._closing_reply(question, state, "")
        return self._finish(question, state, step, stop)

    def _finish(self, question: str, state: _State, step: AgentStep, stop: AgentStop) -> AgentRun:
        """Review the answer against the passages read (R193, when the loop holds an evidence ledger), then
        check its citations, sending it back once (tools closed) when it cites unknown ids."""
        step = self._reviewed(question, state, step)
        step = self._worked(question, state, step)
        checked = state.ledger.check(step.citations)
        retried = bool(checked.unknown)
        if retried:
            state.observer.sent_back(state.turn, "citations")
            step = self._closing_reply(question, state, CITATION_RETRY.format(ids=", ".join(checked.unknown)))
            checked = state.ledger.check(step.citations)
        step = settle(step)  # R204: after every send-back, so the outcome scored is the final reply's
        trace = AgentTrace(
            calls=state.calls, turns=state.turn, stopped=stop, prompt_chars=state.prompt_chars,
            cited=checked.kept, dropped=checked.unknown, retried=retried, emptied=state.emptied,
            reformed=state.reformed, rechecked=state.rechecked, asks=step.asks,
            outcome=step.outcome, outcome_sent=state.outcome_sent, readings=step.readings or [],
            evidence_read=state.evidence.read if state.evidence else 0,
            evidence_rows=len(state.evidence.rows) if state.evidence else 0,
            evidence_sent=[r.chunk_id for r in state.evidence.left_out] if state.evidence else [],
            llm_ms=round(state.llm_s * 1000, 1), wall_ms=round((self._timer() - state.timed) * 1000, 1),
            queries=state.ledger.queries(),
            **_workset_fields(state.workset),
        )  # fmt: skip
        return AgentRun(answer=step, citations=checked, ledger=state.ledger, trace=trace)

    def _reviewed(self, question: str, state: _State, step: AgentStep) -> AgentStep:
        """`step`, or the model's reply to the passages the evidence ledger finds it leaves out (once, tools
        closed). An empty answer is not reviewed: it names no set to complete; nor is a reply saying why it
        cannot answer (R204)."""
        rule = self._evidence
        if rule is None or not step.answers() or not rule.applies(step) or step.is_empty():
            return step
        names = [state.ledger.name_of(e) for e in step.entities or []]
        read = [c.chunk_id for c in state.ledger.read()]
        state.evidence = rule.review(question, step, read, names)
        if not state.evidence.left_out:
            return step
        state.observer.sent_back(state.turn, "evidence")
        return self._closing_reply(question, state, rule.message(state.evidence))

    def _worked(self, question: str, state: _State, step: AgentStep) -> AgentStep:
        """`step`, or the agent's answer after its verdicts on every item of the working set (R211, when the
        loop holds one): asked with the tools closed, sent back once when a verdict is missing or an
        included item's quote is not in it; the second reply stands. No turn or call is used."""
        rule = self._workset
        if rule is None or not rule.applies(step):
            return step
        items, left_out = rule.items(state.ledger)
        if not items:
            return step
        prompt = self._closing_prompt(question, state, rule.message(step, items, left_out))
        schema = reply_model(self._step)
        reply = self._ask(prompt, state, schema)
        marks, problems = check(verdicts_of(reply), items)
        sent = bool(problems)
        if sent:
            reply = self._ask(f"{prompt}\n\n{rule.send_back(problems)}", state, schema)
            marks, problems = check(verdicts_of(reply), items)
        final = answer_of(reply, self._step)
        state.workset = WorksetReview(items=[i.id for i in items], left_out=left_out, marks=marks, sent=sent,
                                      problems=problems, changed=changed(step, final))  # fmt: skip
        return final

    def _closing_reply(self, question: str, state: _State, extra: str) -> AgentStep:
        """The model's answer with the tools closed; its prompt is not counted against the cap, which it
        ends."""
        return self._generate(self._closing_prompt(question, state, extra), state)

    def _closing_prompt(self, question: str, state: _State, extra: str) -> str:
        """`CLOSING` over the turns so far, with `extra` after it."""
        return CLOSING.format(question=question, turns=_turns(state)) + (f"\n\n{extra}" if extra else "")

    def _generate(self, prompt: str, state: _State) -> AgentStep:
        """One reply of the model as the loop's reply model."""
        return self._ask(prompt, state, self._step)

    def _ask(self, prompt: str, state: _State, schema: type[M]) -> M:
        """One reply of the model as `schema`, its time added to the loop's model time."""
        started = self._timer()
        reply = self._llm.generate(prompt, schema, model=self._model, temperature=self._temperature)
        took = self._timer() - started
        state.llm_s += took
        state.observer.model_replied(state.turn, round(took * 1000, 1))
        return reply


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


def _workset_fields(review: WorksetReview | None) -> dict[str, object]:
    """The trace's working-set fields (R211); none without a working set, or for an answer it skipped."""
    if review is None:
        return {}
    return {
        "workset_items": review.items, "workset_left_out": review.left_out, "workset_marks": review.marks,
        "workset_sent": review.sent, "workset_problems": review.problems, "workset_changed": review.changed,
    }  # fmt: skip


def _turns(state: _State) -> str:
    return "\n".join(state.turns) if state.turns else "(none yet)"
