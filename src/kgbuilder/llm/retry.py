"""The retry-and-report loop shared by every LLM provider adapter.

Role in the pipeline: used inside `GeminiClient.generate` and `OllamaClient.generate`; feature code never
calls it. A provider supplies one function that sends the request and returns the raw reply; this module
parses the reply into the schema, retries with backoff, and reports every attempt to the call listener.
Design: Template Method as a higher-order function: the loop is fixed, the provider-specific `send` step
varies. It exists so that retry, failure reporting and token accounting cannot drift apart between
providers.
Not here: HTTP or SDK details (the adapters) and caching (cache.py).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core.errors import LLMResponseError
from .base import CallListener, LLMCallRecord, T

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawReply:
    """What a provider returned for one request, before parsing. Token counts are None when not reported."""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None  # the visible answer only
    thinking_tokens: int | None = None  # hidden reasoning, billed as output by providers that bill


def generate_with_retry(
    send: Callable[[], RawReply],
    schema: type[T],
    *,
    model: str,
    temperature: float,
    prompt: str,
    max_attempts: int,
    backoff_s: float,
    listener: CallListener | None,
) -> T:
    """Call `send` until its reply parses into `schema`, at most `max_attempts` times.

    Every attempt is reported to `listener`, failed ones with `ok=False` and the usage of the reply when
    there was one. Raises `LLMResponseError` (chained to the last cause) when every attempt failed.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        reply: RawReply | None = None  # stays None when `send` raised before answering: no usage to report
        try:
            reply = send()
            result = schema.model_validate_json(reply.text)
        except Exception as e:
            # Deliberately broad: transport errors (rate limit, 5xx, network) share no usable base class
            # across providers, and a malformed reply (ValidationError) is retried too, because truncation
            # and safety blocks are not deterministic even at temperature 0.
            last_error = e
            _report(listener, model, temperature, prompt, f"error: {e}", started, reply, ok=False)
        else:
            _report(listener, model, temperature, prompt, result.model_dump_json(), started, reply)
            return result
        log.warning("LLM call failed (attempt %d/%d): %s", attempt, max_attempts, last_error)
        if attempt < max_attempts:
            time.sleep(backoff_s * 2 ** (attempt - 1))  # 2s, 4s, 8s ...
    raise LLMResponseError(f"{model} failed {max_attempts} times for {schema.__name__}") from last_error


def report_embedding(
    listener: CallListener | None,
    model: str,
    batch: list[str],
    vectors: int,
    latency_s: float,
    prompt_tokens: int | None = None,
) -> None:
    """Report one embedding batch. The texts are summarised, not logged: they are the chunks themselves."""
    if listener is not None:
        listener(
            LLMCallRecord(
                model=model,
                temperature=0.0,
                prompt=f"{len(batch)} texts, {sum(len(t) for t in batch)} chars",
                response=f"{vectors} vectors",
                latency_s=latency_s,
                cache_hit=False,
                kind="embed",
                prompt_tokens=prompt_tokens,
            )
        )


def _report(
    listener: CallListener | None,
    model: str,
    temperature: float,
    prompt: str,
    response_text: str,
    started: float,
    reply: RawReply | None,
    ok: bool = True,
) -> None:
    if listener is None:
        return
    listener(
        LLMCallRecord(
            model=model,
            temperature=temperature,
            prompt=prompt,
            response=response_text,
            latency_s=time.perf_counter() - started,
            cache_hit=False,
            ok=ok,
            prompt_tokens=reply.prompt_tokens if reply else None,
            completion_tokens=reply.completion_tokens if reply else None,
            thinking_tokens=reply.thinking_tokens if reply else None,
        )
    )
