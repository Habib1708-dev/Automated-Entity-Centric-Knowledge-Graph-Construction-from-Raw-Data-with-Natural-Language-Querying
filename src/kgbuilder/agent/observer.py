"""What the agent loop reports while it runs (R221), so a caller can show each step as it happens.

Role in the pipeline: the agent loop (loop.py) tells an observer about every model reply, every tool call
and every send-back, in the order they happen; the web interface (R222) streams them to the page. `kg qa`
and `kg ask` pass none and get `NO_OBSERVER`.
Design: Observer, with a Null Object default. The events carry only what the loop already has: a tool
call's `AgentCall` as the trace records it, plus the result the tool returned (the trace keeps only its
ids). The observer watches and never steers: the loop's replies, calls and trace are the same with or
without one.
Not here: the trace (query/answers.py), the loop's rules (loop.py), any transport to a page.
"""

from typing import Literal, Protocol

from ..query.answers import AgentCall
from .results import ToolResult

# Why a reply was sent back to the model: an empty answer (R138), an outcome the loop did not bear out
# (R204), an answer missing its declared form (R147), a set answer resting on one search (R176), members the
# evidence ledger found left out (R193), or citations of ids no tool returned (R135).
SendBack = Literal["empty", "outcome", "form", "recheck", "evidence", "citations"]


class AgentObserver(Protocol):
    """Receives the loop's events in order. Must be cheap and must not raise: it runs inside the loop."""

    def model_replied(self, turn: int, ms: float) -> None:
        """The model replied during `turn` (0 before the first), after `ms` milliseconds; closing and review
        replies are reported with the turn they follow."""
        ...

    def call_done(self, call: AgentCall, result: ToolResult | None) -> None:
        """A proposed call was settled: run, with its `result`, or refused (`call.refused`, `result` None)."""
        ...

    def sent_back(self, turn: int, reason: SendBack) -> None:
        """The reply of `turn` was sent back to the model once, for `reason`."""
        ...


class NoObserver:
    """`AgentObserver` that ignores everything (Null Object): the loop as `kg qa` runs it."""

    def model_replied(self, turn: int, ms: float) -> None: ...

    def call_done(self, call: AgentCall, result: ToolResult | None) -> None: ...

    def sent_back(self, turn: int, reason: SendBack) -> None: ...


NO_OBSERVER = NoObserver()
