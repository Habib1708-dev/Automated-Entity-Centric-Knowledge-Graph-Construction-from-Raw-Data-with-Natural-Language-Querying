"""A thinking level for one role (schema design, extraction), applied to every request of that role.

Role in the pipeline: a stage wraps its LLM client with the level from the settings, so the feature code
(proposer, extraction, resolver) keeps calling `generate` without knowing that levels exist.
Design: Decorator over `LLMClient`. Not here: how a provider turns the level into a request (gemini.py).
"""

from .base import LLMClient, T, ThinkingLevel


class ThinkingLLM:
    """`LLMClient` that sends `level` with each request unless the caller asked for a level itself."""

    def __init__(self, inner: LLMClient, level: ThinkingLevel):
        self._inner = inner
        self._level = level

    def generate(
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str,
        temperature: float = 0.0,
        thinking: ThinkingLevel = "",
    ) -> T:
        return self._inner.generate(
            prompt, schema, model=model, temperature=temperature, thinking=thinking or self._level
        )


def with_thinking(llm: LLMClient, level: ThinkingLevel) -> LLMClient:
    """`llm` sending `level` with each request; `llm` itself when no level is set (the model's default)."""
    return ThinkingLLM(llm, level) if level else llm
