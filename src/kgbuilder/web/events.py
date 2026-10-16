"""The events one question streams to the page (R222), and the observer and listener that make them.

Role in the pipeline: the ask service (ask.py) runs `kg ask`'s stage with a `StreamObserver` as the agent's
observer and a `StreamListener` on every LLM call, so the page sees each step as it happens: the model's
reply time per turn, each tool call with its arguments, latency and result, each send-back, each LLM
request with its tokens and cost, then the answer with its provenance and the question's cost.
Design: Observer (agent/observer.py) and the LLM port's `CallListener`, both turning what they hear into
one discriminated union of pydantic events, handed to an `emit` function the caller owns (a queue).
Must not: steer the loop, or raise: both run inside the loop or the LLM client.
"""

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..agent.observer import SendBack
from ..agent.results import ToolResult
from ..llm.base import LLMCallRecord
from ..query.answers import AgentCall, SystemAnswer
from ..tracking.base import ModelPrice, UsageMeter

# A tool result is shown on the page as its JSON, cut here: the agent read it whole, and the answer's
# provenance (shown passages, query rows) is in the final event; this is only the live view of the step.
RESULT_CHARS = 4000


class ModelEvent(BaseModel):
    """The model replied during `turn` after `ms` milliseconds."""

    event: Literal["model"] = "model"
    turn: int
    ms: float


class CallEvent(BaseModel):
    """A tool call settled: run (its result as JSON, cut at `RESULT_CHARS`) or refused (`call.refused`)."""

    event: Literal["call"] = "call"
    call: AgentCall
    result: str = ""
    cut: bool = False  # the result was longer than RESULT_CHARS


class SentBackEvent(BaseModel):
    """The reply of `turn` went back to the model once, for `reason`."""

    event: Literal["sent_back"] = "sent_back"
    turn: int
    reason: SendBack


class LLMEvent(BaseModel):
    """One LLM request: its model, whether the cache answered it, its tokens and latency, and its cost
    (None when prices.yaml has no price for the model)."""

    event: Literal["llm"] = "llm"
    model: str
    kind: Literal["generate", "embed"]
    ok: bool
    cache_hit: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    thinking_tokens: int | None
    latency_s: float
    cost_usd: float | None


class AnswerEvent(BaseModel):
    """The answer with its trace and provenance, the question's wall-clock seconds and its cost."""

    event: Literal["answer"] = "answer"
    answer: SystemAnswer
    seconds: float
    cost_usd: float | None


class ErrorEvent(BaseModel):
    """The question failed with an expected error (`KgBuilderError`); `message` says why."""

    event: Literal["error"] = "error"
    message: str


StreamEvent = Annotated[
    ModelEvent | CallEvent | SentBackEvent | LLMEvent | AnswerEvent | ErrorEvent, Field(discriminator="event")
]
Emit = Callable[[StreamEvent], None]


class StreamObserver:
    """An `AgentObserver` emitting each loop event as a stream event."""

    def __init__(self, emit: Emit):
        self._emit = emit

    def model_replied(self, turn: int, ms: float) -> None:
        self._emit(ModelEvent(turn=turn, ms=ms))

    def call_done(self, call: AgentCall, result: ToolResult | None) -> None:
        text = result.model_dump_json() if result is not None else ""
        self._emit(CallEvent(call=call, result=text[:RESULT_CHARS], cut=len(text) > RESULT_CHARS))

    def sent_back(self, turn: int, reason: SendBack) -> None:
        self._emit(SentBackEvent(turn=turn, reason=reason))


class StreamListener:
    """A `CallListener` emitting each LLM request as an event and keeping the question's total cost, priced
    by the same `UsageMeter` the MLflow runs use, so the page and the run log agree."""

    def __init__(self, emit: Emit, prices: dict[str, ModelPrice] | None):
        self._emit = emit
        self._prices = prices
        self._total = UsageMeter(prices)

    def __call__(self, record: LLMCallRecord) -> None:
        one = UsageMeter(self._prices)
        one.add(record)
        self._total.add(record)
        self._emit(
            LLMEvent(
                model=record.model,
                kind=record.kind,
                ok=record.ok,
                cache_hit=record.cache_hit,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                thinking_tokens=record.thinking_tokens,
                latency_s=round(record.latency_s, 3),
                cost_usd=one.as_metrics().get("cost_usd"),
            )  # fmt: skip
        )

    def cost_usd(self) -> float | None:
        """The cost of every request heard so far; None when a model used has no price."""
        return self._total.as_metrics().get("cost_usd")
