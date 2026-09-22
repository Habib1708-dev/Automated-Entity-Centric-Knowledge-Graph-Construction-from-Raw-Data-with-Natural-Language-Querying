"""The LLM port: what the pipeline needs from a language model, independent of any provider.

Role in the pipeline: every stage that asks a model for something (plan, text schema, triples, entity
adjudication, embeddings) depends on these protocols and receives an implementation from outside.
Design: Dependency Inversion. Small protocols (Interface Segregation): generating and embedding are
separate, because most stages need only one of them. Calls are reported to an optional listener
(Observer), which is how tracing and usage metering attach without the stages knowing.
Not here: any provider SDK (llm/gemini.py) or caching (llm/cache.py).
"""

import hashlib
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMCallRecord(BaseModel):
    """One request to a model (a `generate` attempt or an `embed` batch), as reported to a `CallListener`.

    Failed attempts are reported too (`ok=False`): they cost time and, when the provider answered with a
    reply that did not fit the schema, tokens.
    """

    model: str
    temperature: float
    prompt: str  # for an embedding batch: a short description, not the texts
    response: str  # the parsed result as JSON (so a cached and a live call look the same), or the error
    latency_s: float
    cache_hit: bool
    kind: Literal["generate", "embed"] = "generate"
    ok: bool = True
    prompt_tokens: int | None = None  # None when the provider did not report usage, or on a cache hit
    completion_tokens: int | None = None  # the visible answer only
    # Hidden reasoning of thinking models. Billed as output, so cost = completion + thinking tokens.
    thinking_tokens: int | None = None


# Receives every request, successful or not. Must be cheap and must not raise: it runs in worker threads.
CallListener = Callable[[LLMCallRecord], None]


class LLMClient(Protocol):
    """Structured generation: the reply is always parsed into a pydantic model, never returned as text."""

    def generate(self, prompt: str, schema: type[T], *, model: str, temperature: float = 0.0) -> T:
        """Return the model's reply parsed into `schema`.

        Raises `LLMResponseError` when the provider keeps failing or the reply does not fit `schema`.
        """
        ...


class Embedder(Protocol):
    """Text embeddings, used for chunk vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...


def prompt_version(template: str) -> str:
    """Short, stable id of a prompt template (before formatting), logged to MLflow with every LLM stage.

    Editing a prompt changes its version automatically, so runs made with different wording can never be
    confused with each other.
    """
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]
