"""Ollama implementation of the LLM port: a local model over Ollama's HTTP API, for free smoke runs.

Role in the pipeline: an alternative to llm/gemini.py, chosen by `LLM_PROVIDER=ollama`. It lets the whole
pipeline run end to end without API costs, to check that the code works. Small local models are far
weaker than Gemini, so runs made with it say nothing about the quality of the method.
Design: Adapter over `POST /api/chat` and `POST /api/embed`; retry, failure reporting and token
accounting come from the shared loop in retry.py. Only httpx is used, so no Ollama SDK is needed.
Not here: choosing the provider (the composition root in cli.py) and caching (cache.py).
"""

import time

import httpx

from .base import CallListener, T, ThinkingLevel
from .retry import RawReply, generate_with_retry, report_embedding

# Texts per /api/embed request. Ollama has no fixed limit; this keeps one request well under a minute
# on a laptop GPU.
_EMBED_BATCH_SIZE = 32


class OllamaClient:
    """`LLMClient` and `Embedder` backed by a local Ollama server. Thread-safe: httpx.Client is."""

    def __init__(
        self,
        base_url: str,
        embed_model: str,
        num_ctx: int,
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        listener: CallListener | None = None,
        timeout_s: float = 300.0,
        http: httpx.Client | None = None,
    ):
        """`http` is for tests (an httpx.Client with a MockTransport); by default one is created."""
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._embed_model = embed_model
        self._num_ctx = num_ctx
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._listener = listener

    def generate(
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str,
        temperature: float = 0.0,
        thinking: ThinkingLevel = "",
    ) -> T:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # a JSON schema in `format` makes Ollama constrain decoding to it (structured outputs)
            "format": schema.model_json_schema(),
            # Thinking off, whatever `thinking` asks: a small thinking model spent 4,000 tokens thinking and
            # then broke the JSON. Models that cannot think accept the flag and ignore it.
            "think": False,
            # Ollama silently cuts a prompt longer than its context window (often 4,096 tokens by
            # default), and the plan prompt alone is about 8,000, so the window is always set.
            "options": {"temperature": temperature, "num_ctx": self._num_ctx},
        }

        def send() -> RawReply:
            response = self._http.post("/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            return RawReply(
                text=data["message"]["content"],
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
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
            response = self._http.post("/api/embed", json={"model": self._embed_model, "input": batch})
            response.raise_for_status()
            data = response.json()
            vectors.extend(data["embeddings"])
            report_embedding(
                self._listener,
                self._embed_model,
                batch,
                len(data["embeddings"]),
                time.perf_counter() - started,
                prompt_tokens=data.get("prompt_eval_count"),
            )
        return vectors
