"""Gemini implementation of the LLM port (google-genai SDK).

Role in the pipeline: the only module that talks to the Gemini API, for generation and for embeddings.
Design: Adapter. It translates the project's `LLMClient` / `Embedder` protocols to the SDK, adds retry
with backoff, and turns SDK and parsing failures into `LLMResponseError`. Every request, including failed
attempts and embedding batches, is reported to the listener (Observer) with the usage the API returns.
Not here: caching (llm/cache.py wraps this class) and prompt construction (each feature owns its prompts).
"""

import logging
import time

from ..core.errors import LLMResponseError, LLMUnavailableError
from .base import CallListener, LLMCallRecord, T

log = logging.getLogger(__name__)

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
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            response = None  # stays None when the SDK raised before answering: no usage to report
            try:
                response = self._client.models.generate_content(model=model, contents=prompt, config=config)
                result = schema.model_validate_json(response.text or "")
            except Exception as e:
                # Deliberately broad: SDK errors (rate limit, 5xx, network) share no usable base class, and
                # a malformed reply (ValidationError) is retried too, because truncation and safety blocks
                # are not deterministic even at temperature 0. Everything ends as LLMResponseError below.
                last_error = e
                elapsed = time.perf_counter() - started
                self._report(model, temperature, prompt, f"error: {e}", elapsed, response, ok=False)
            else:
                elapsed = time.perf_counter() - started
                self._report(model, temperature, prompt, result.model_dump_json(), elapsed, response)
                return result
            log.warning("LLM call failed (attempt %d/%d): %s", attempt, self._max_attempts, last_error)
            if attempt < self._max_attempts:
                time.sleep(self._backoff_s * 2 ** (attempt - 1))  # 2s, 4s, 8s ...
        raise LLMResponseError(
            f"{model} failed {self._max_attempts} times for {schema.__name__}"
        ) from last_error

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start : start + _EMBED_BATCH_SIZE]
            started = time.perf_counter()
            response = self._client.models.embed_content(model=self._embed_model, contents=batch)
            vectors.extend(e.values for e in response.embeddings)
            if self._listener is not None:
                # the Gemini API reports no token usage for embeddings, so only count and latency are known
                self._listener(
                    LLMCallRecord(
                        model=self._embed_model,
                        temperature=0.0,
                        prompt=f"{len(batch)} texts, {sum(len(t) for t in batch)} chars",
                        response=f"{len(response.embeddings)} vectors",
                        latency_s=time.perf_counter() - started,
                        cache_hit=False,
                        kind="embed",
                    )
                )
        return vectors

    def _report(
        self,
        model: str,
        temperature: float,
        prompt: str,
        response_text: str,
        latency_s: float,
        response: object | None,  # the SDK's GenerateContentResponse; read with getattr, fields may be absent
        ok: bool = True,
    ) -> None:
        if self._listener is None:
            return
        # `candidates_token_count` is the visible answer only; thinking models report their hidden
        # reasoning separately in `thoughts_token_count`, and both are billed as output
        usage = getattr(response, "usage_metadata", None)
        self._listener(
            LLMCallRecord(
                model=model,
                temperature=temperature,
                prompt=prompt,
                response=response_text,
                latency_s=latency_s,
                cache_hit=False,
                ok=ok,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
                thinking_tokens=getattr(usage, "thoughts_token_count", None),
            )
        )
