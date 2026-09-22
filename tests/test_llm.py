"""The LLM port's decorators and helpers: disk cache behaviour, call reporting, prompt versioning.
No network: the inner client is a ScriptedLLM."""

from pydantic import BaseModel

from kgbuilder.llm.base import LLMCallRecord, prompt_version
from kgbuilder.llm.cache import CachedLLM

from .fakes import ScriptedLLM


class Answer(BaseModel):
    text: str


def make(tmp_path, listener=None):
    inner = ScriptedLLM(lambda prompt, schema: Answer(text=prompt.upper()))
    return inner, CachedLLM(inner, tmp_path / "cache", listener)


def test_second_identical_call_is_served_from_cache(tmp_path):
    inner, cached = make(tmp_path)
    first = cached.generate("hi", Answer, model="m")
    second = cached.generate("hi", Answer, model="m")
    assert first == second == Answer(text="HI")
    assert len(inner.calls) == 1


def test_model_temperature_and_prompt_are_part_of_the_key(tmp_path):
    inner, cached = make(tmp_path)
    cached.generate("hi", Answer, model="m")
    cached.generate("hi", Answer, model="other")
    cached.generate("hi", Answer, model="m", temperature=0.7)
    cached.generate("hello", Answer, model="m")
    assert len(inner.calls) == 4


def test_corrupt_cache_entry_is_a_miss_and_gets_rewritten(tmp_path):
    inner, cached = make(tmp_path)
    cached.generate("hi", Answer, model="m")
    (entry,) = (tmp_path / "cache").glob("*.json")
    entry.write_text("{not json", encoding="utf-8")
    assert cached.generate("hi", Answer, model="m") == Answer(text="HI")
    assert len(inner.calls) == 2
    assert Answer.model_validate_json(entry.read_text(encoding="utf-8")) == Answer(text="HI")


def test_listener_is_told_about_cache_hits_only(tmp_path):
    records: list[LLMCallRecord] = []
    _, cached = make(tmp_path, records.append)
    cached.generate("hi", Answer, model="m")  # miss: reporting is the inner client's job
    cached.generate("hi", Answer, model="m")  # hit
    assert [r.cache_hit for r in records] == [True]
    assert records[0].model == "m" and records[0].prompt_tokens is None


def test_prompt_version_is_stable_and_changes_with_the_text():
    assert prompt_version("a {x}") == prompt_version("a {x}")
    assert prompt_version("a {x}") != prompt_version("a {x}.")
    assert len(prompt_version("a")) == 12


class _FlakyModels:
    """Stands in for the SDK's `client.models`: fails `failures` times, then returns `text` with `usage`."""

    def __init__(self, failures: int, text: str, usage: object | None = None):
        self.failures, self.text, self.usage, self.calls = failures, text, usage, 0

    def generate_content(self, **_):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("simulated outage")
        return type("Response", (), {"text": self.text, "usage_metadata": self.usage})()

    def embed_content(self, *, model, contents):
        vectors = [type("Embedding", (), {"values": [1.0, 0.0]})() for _ in contents]
        return type("EmbedResponse", (), {"embeddings": vectors})()


# The usage fields the Gemini SDK reports for a thinking model
_USAGE = type(
    "Usage", (), {"prompt_token_count": 100, "candidates_token_count": 20, "thoughts_token_count": 300}
)()


def _gemini_with(models, listener=None):
    import pytest

    from kgbuilder.llm.gemini import GeminiClient

    pytest.importorskip("google.genai")
    client = GeminiClient("fake-key", "embed-model", max_attempts=3, backoff_s=0, listener=listener)
    client._client = type(
        "Client", (), {"models": models}
    )()  # swap the SDK client; nothing leaves the process
    return client


def test_gemini_retries_then_succeeds_and_reports_every_attempt():
    records = []
    models = _FlakyModels(failures=2, text='{"text": "ok"}')
    assert _gemini_with(models, records.append).generate("p", Answer, model="m") == Answer(text="ok")
    assert models.calls == 3
    # the two outages are reported as failed requests, then the success
    assert [r.ok for r in records] == [False, False, True]
    assert "simulated outage" in records[0].response and records[0].prompt_tokens is None
    assert all(r.cache_hit is False for r in records)


def test_gemini_reports_thinking_tokens_apart_from_the_visible_answer():
    records = []
    models = _FlakyModels(failures=0, text='{"text": "ok"}', usage=_USAGE)
    _gemini_with(models, records.append).generate("p", Answer, model="m")
    assert (records[0].prompt_tokens, records[0].completion_tokens, records[0].thinking_tokens) == (
        100,
        20,
        300,
    )


def test_gemini_reports_the_tokens_of_a_reply_that_did_not_fit_the_schema():
    import pytest

    from kgbuilder.core.errors import LLMResponseError

    records = []
    models = _FlakyModels(failures=0, text="not json", usage=_USAGE)
    with pytest.raises(LLMResponseError):
        _gemini_with(models, records.append).generate("p", Answer, model="m")
    # the provider answered and billed each time, so each failed attempt carries its usage
    assert [r.ok for r in records] == [False] * 3
    assert sum(r.thinking_tokens for r in records) == 900


def test_gemini_reports_each_embedding_batch():
    records = []
    vectors = _gemini_with(_FlakyModels(0, ""), records.append).embed(["a", "bb"])
    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert len(records) == 1 and records[0].kind == "embed" and records[0].model == "embed-model"


def test_gemini_gives_up_with_a_typed_error_on_unparsable_replies():
    import pytest

    from kgbuilder.core.errors import LLMResponseError

    models = _FlakyModels(failures=0, text="not json")
    with pytest.raises(LLMResponseError):
        _gemini_with(models).generate("p", Answer, model="m")
    assert models.calls == 3


def test_gemini_without_key_is_unavailable():
    import pytest

    from kgbuilder.core.errors import LLMUnavailableError
    from kgbuilder.llm.gemini import GeminiClient

    with pytest.raises(LLMUnavailableError):
        GeminiClient("", "embed-model")


def _ollama(handler, listener=None):
    """An OllamaClient whose HTTP requests go to `handler` instead of a server."""
    import httpx

    from kgbuilder.llm.ollama import OllamaClient

    http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaClient("unused", "embed-model", num_ctx=16384, backoff_s=0, listener=listener, http=http)


def test_ollama_asks_for_the_schema_without_thinking_in_a_large_window_and_reports_usage():
    import json

    import httpx

    sent, records = [], []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": '{"text": "ok"}'}, "prompt_eval_count": 12, "eval_count": 5}
        )

    assert _ollama(handler, records.append).generate("p", Answer, model="qwen") == Answer(text="ok")
    body = sent[0]
    assert body["format"] == Answer.model_json_schema() and body["think"] is False
    assert body["options"] == {"temperature": 0.0, "num_ctx": 16384}
    assert (records[0].prompt_tokens, records[0].completion_tokens, records[0].ok) == (12, 5, True)


def test_ollama_retries_a_server_error_and_reports_the_failed_attempt():
    import httpx

    replies = iter(
        [
            httpx.Response(500, text="model loading"),
            httpx.Response(200, json={"message": {"content": '{"text": "ok"}'}}),
        ]
    )
    records = []
    assert _ollama(lambda _: next(replies), records.append).generate("p", Answer, model="m").text == "ok"
    assert [r.ok for r in records] == [False, True]


def test_ollama_embeds_in_batches_and_reports_each_one():
    import json

    import httpx

    def handler(request):
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"embeddings": [[0.5, 0.5] for _ in texts], "prompt_eval_count": 3})

    records = []
    assert _ollama(handler, records.append).embed(["a", "b"]) == [[0.5, 0.5], [0.5, 0.5]]
    assert records[0].kind == "embed" and records[0].prompt_tokens == 3


def test_the_composition_root_builds_the_configured_provider():
    from kgbuilder.cli import build_provider
    from kgbuilder.config import Settings
    from kgbuilder.llm.ollama import OllamaClient

    # _env_file=None: the developer's own .env must not decide what this test sees
    ollama = Settings(_env_file=None, llm_provider="ollama")
    assert isinstance(build_provider(ollama, lambda _: None), OllamaClient)
    assert build_provider(Settings(_env_file=None, gemini_api_key=""), lambda _: None) is None
