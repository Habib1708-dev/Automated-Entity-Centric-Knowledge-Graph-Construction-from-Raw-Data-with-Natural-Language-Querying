"""DeepSeek implementation of the LLM port: DeepSeek's OpenAI-compatible chat completions API.

Role in the pipeline: an alternative to llm/gemini.py for generation, chosen by `LLM_PROVIDER=deepseek`
(R69). DeepSeek has no embedding model, so the composition root (cli.py) keeps Gemini for embeddings.
Design: Adapter over `POST /chat/completions` with httpx, like llm/ollama.py; retry, failure reporting and
token accounting come from the shared loop in retry.py. DeepSeek has no schema-constrained decoding, only
a JSON mode (`response_format: json_object`), so the pydantic schema is sent as JSON Schema in a system
message and the reply is validated in code; a reply that does not fit, or an empty one (the API docs warn
that JSON mode "may occasionally return empty content"), is a failed attempt that is retried.
Not here: choosing the provider (cli.py) and caching (cache.py).
"""

import json

import httpx

from ..core.errors import LLMUnavailableError
from .base import CallListener, T, ThinkingLevel
from .retry import RawReply, generate_with_retry

# JSON mode needs the word "json" in the prompt and a description of the format (API docs). The schema is
# the pydantic model's own JSON Schema, so the reply is held to exactly what `model_validate_json` checks.
SYSTEM_PROMPT = """Reply with one JSON object and nothing else. It must validate against this JSON Schema:
{schema}"""

# Upper bound per reply, reasoning included. The API's default is far lower, and a truncated JSON reply
# fails validation; an extraction reply with its reasoning stays well under this.
_MAX_TOKENS = 32_768

# The project's thinking levels on DeepSeek's reasoning efforts (low, high, max). DeepSeek has no "medium":
# it maps to "high", the model's default. "minimal" switches thinking off. "" leaves the default.
_EFFORT: dict[ThinkingLevel, str] = {"low": "low", "medium": "high", "high": "high"}


class DeepSeekClient:
    """`LLMClient` backed by DeepSeek. Thread-safe: httpx.Client is, and no other state is kept."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        listener: CallListener | None = None,
        timeout_s: float = 300.0,
        http: httpx.Client | None = None,
    ):
        """`http` is for tests (an httpx.Client with a MockTransport); by default one is created.

        Raises `LLMUnavailableError` when `api_key` is empty.
        """
        if not api_key:
            raise LLMUnavailableError("DEEPSEEK_API_KEY is not set (see .env.example)")
        self._http = http or httpx.Client(
            base_url=base_url, timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"}
        )
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
        body = self._body(prompt, schema, model, temperature, thinking)

        def send() -> RawReply:
            response = self._http.post("/chat/completions", json=body)
            response.raise_for_status()
            return _reply(response.json())

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

    @staticmethod
    def _body(
        prompt: str, schema: type[T], model: str, temperature: float, thinking: ThinkingLevel
    ) -> dict[str, object]:
        """The request body. The temperature is sent although thinking mode ignores it (API docs, no error):
        it takes effect when thinking is switched off ("minimal")."""
        system = SYSTEM_PROMPT.format(schema=json.dumps(schema.model_json_schema()))
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": _MAX_TOKENS,
        }
        if thinking == "minimal":
            body["thinking"] = {"type": "disabled"}
        elif thinking:
            body["reasoning_effort"] = _EFFORT[thinking]
        return body


def _reply(data: dict) -> RawReply:
    """The reply's text and usage. `completion_tokens` includes the reasoning, which `RawReply` counts apart
    (visible answer and thinking are both billed as output, cost = completion + thinking)."""
    usage = data.get("usage") or {}
    completion = usage.get("completion_tokens")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    visible = completion - reasoning if completion is not None and reasoning is not None else completion
    return RawReply(
        text=data["choices"][0]["message"].get("content") or "",
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=visible,
        thinking_tokens=reasoning,
    )
