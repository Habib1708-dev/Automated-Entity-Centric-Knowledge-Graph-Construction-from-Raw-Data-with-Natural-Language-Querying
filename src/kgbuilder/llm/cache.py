"""Disk cache around any `LLMClient`, so reruns are free and reproducible.

Role in the pipeline: wraps the provider adapter in the composition root. Identical requests (same model,
temperature, prompt and output schema) are answered from `.cache/llm` without calling the provider.
Design: Decorator. It implements `LLMClient` and delegates to another `LLMClient`, so it works for any
provider and can be left out (`--no-cache`-style) without touching feature code.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from pydantic import ValidationError

from .base import CallListener, LLMCallRecord, LLMClient, T


class CachedLLM:
    """`LLMClient` decorator that stores each parsed reply as one JSON file named by the request hash."""

    def __init__(self, inner: LLMClient, cache_dir: Path, listener: CallListener | None = None):
        self._inner = inner
        self._cache_dir = cache_dir
        self._listener = listener  # told about cache hits only; the inner client reports its own calls

    def generate(self, prompt: str, schema: type[T], *, model: str, temperature: float = 0.0) -> T:
        cache_file = self._cache_dir / f"{self._key(prompt, schema, model, temperature)}.json"
        started = time.perf_counter()
        cached = self._read(cache_file, schema)
        if cached is not None:
            self._report_hit(model, temperature, prompt, cached, time.perf_counter() - started)
            return cached

        result = self._inner.generate(prompt, schema, model=model, temperature=temperature)
        self._write(cache_file, result.model_dump_json())
        return result

    @staticmethod
    def _key(prompt: str, schema: type[T], model: str, temperature: float) -> str:
        # The output schema is part of the key: changing a pydantic model must not return stale shapes.
        material = json.dumps([model, temperature, prompt, schema.model_json_schema()], sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _read(cache_file: Path, schema: type[T]) -> T | None:
        try:
            return schema.model_validate_json(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            # Missing, unreadable or half-written entry: treat as a miss and let it be rewritten.
            return None

    @staticmethod
    def _write(cache_file: Path, payload: str) -> None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename is atomic, so parallel extraction workers never read a partial file.
        temp_file = cache_file.with_suffix(f".{os.getpid()}.{time.monotonic_ns()}.tmp")
        temp_file.write_text(payload, encoding="utf-8")
        os.replace(temp_file, cache_file)

    def _report_hit(self, model: str, temperature: float, prompt: str, result, latency_s: float) -> None:
        if self._listener is not None:
            self._listener(
                LLMCallRecord(
                    model=model,
                    temperature=temperature,
                    prompt=prompt,
                    response=result.model_dump_json(),
                    latency_s=latency_s,
                    cache_hit=True,
                )
            )
