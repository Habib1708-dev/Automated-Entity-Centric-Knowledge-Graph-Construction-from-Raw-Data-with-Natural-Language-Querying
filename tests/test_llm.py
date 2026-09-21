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
    """Stands in for the SDK's `client.models`: fails `failures` times, then returns `text`."""

    def __init__(self, failures: int, text: str):
        self.failures, self.text, self.calls = failures, text, 0

    def generate_content(self, **_):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("simulated outage")
        return type("Response", (), {"text": self.text, "usage_metadata": None})()


def _gemini_with(models, listener=None):
    import pytest

    from kgbuilder.llm.gemini import GeminiClient

    pytest.importorskip("google.genai")
    client = GeminiClient("fake-key", "embed-model", max_attempts=3, backoff_s=0, listener=listener)
    client._client = type(
        "Client", (), {"models": models}
    )()  # swap the SDK client; nothing leaves the process
    return client


def test_gemini_retries_then_succeeds_and_reports_the_call():
    records = []
    models = _FlakyModels(failures=2, text='{"text": "ok"}')
    assert _gemini_with(models, records.append).generate("p", Answer, model="m") == Answer(text="ok")
    assert models.calls == 3
    assert len(records) == 1 and records[0].cache_hit is False


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
