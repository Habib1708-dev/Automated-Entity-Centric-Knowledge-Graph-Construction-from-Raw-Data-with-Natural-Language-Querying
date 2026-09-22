"""Gemini implementation of the LLM port (google-genai SDK).

Role in the pipeline: the only module that talks to the Gemini API, for generation and for embeddings.
Design: Adapter. It translates the project's `LLMClient` / `Embedder` protocols to the SDK; retry, failure
reporting and token accounting come from the shared loop in retry.py. Every request, including failed
attempts and embedding batches, is reported to the listener (Observer) with the usage the API returns.
Not here: caching (llm/cache.py wraps this class) and prompt construction (each feature owns its prompts).
"""

import time

from ..core.errors import LLMUnavailableError
from .base import CallListener, T
from .retry import RawReply, generate_with_retry, report_embedding

# The embedding endpoint accepts at most 100 texts per request.
_EMBED_BATCH_SIZE = 100


class GeminiClient:
    """`LLMClient` and `Embedder` backed by Gemini. Thread-safe: the SDK client is, and we keep no state."""

    def __init__(
        self,
        api_key: str,
        embed_model: str,
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        listener: CallListener | None = None,
    ):
        if not api_key:
            raise LLMUnavailableError("GEMINI_API_KEY is not set (see .env.example)")
        # Imported here so that the package (and the test suite) loads without the SDK being touched.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._embed_model = embed_model
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._listener = listener

    def generate(self, prompt: str, schema: type[T], *, model: str, temperature: float = 0.0) -> T:
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            # JSON mode + a response schema makes the API constrain decoding to the pydantic model
            response_mime_type="application/json",
            response_schema=schema,
        )

        def send() -> RawReply:
            response = self._client.models.generate_content(model=model, contents=prompt, config=config)
            # `candidates_token_count` is the visible answer only; thinking models report their hidden
            # reasoning separately in `thoughts_token_count`, and both are billed as output
            usage = getattr(response, "usage_metadata", None)
            return RawReply(
                text=response.text or "",
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
                thinking_tokens=getattr(usage, "thoughts_token_count", None),
            )

        return generate_with_retry(
            send,
            schema,
            model=model,
            temperature=temperature,
            prompt=prompt,
            max_attempts=self._max_attempts,
            backoff_s=self._backoff_s,
            listener=self._listener,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start : start + _EMBED_BATCH_SIZE]
            started = time.perf_counter()
            response = self._client.models.embed_content(model=self._embed_model, contents=batch)
            vectors.extend(e.values for e in response.embeddings)
            # the Gemini API reports no token usage for embeddings, so only count and latency are known
            report_embedding(
                self._listener,
                self._embed_model,
                batch,
                len(response.embeddings),
                time.perf_counter() - started,
            )
        return vectors
