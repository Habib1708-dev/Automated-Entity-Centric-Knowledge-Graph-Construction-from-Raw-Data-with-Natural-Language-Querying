"""Test doubles for the project's ports, so tests never need a network or monkeypatching."""

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from kgbuilder.structured.staging import DeclaredKeys


class ScriptedLLM:
    """`LLMClient` whose replies come from a function `(prompt, schema) -> model`. Records every call."""

    def __init__(self, script: Callable[[str, type[BaseModel]], BaseModel]):
        self._script = script
        self.calls: list[tuple[str, str]] = []  # (schema name, model)
        self.thinking: list[str] = []  # the thinking level of each call, "" = model default

    def generate(self, prompt, schema, *, model, temperature=0.0, thinking=""):
        self.calls.append((schema.__name__, model))
        self.thinking.append(thinking)
        return self._script(prompt, schema)


class RecordingRun:
    """`Run` that keeps everything it is given, for assertions."""

    def __init__(self, name: str, params: dict):
        self.name = name
        self.logged_params: dict = dict(params)
        self.logged_metrics: dict = {}
        self.artifacts: list[str] = []

    def params(self, **values):
        self.logged_params.update(values)

    def metrics(self, **values):
        self.logged_metrics.update({k: v for k, v in values.items() if v is not None})

    def artifact(self, path):
        self.artifacts.append(str(path))

    def text(self, content, artifact_path):
        self.artifacts.append(artifact_path)


class RecordingTracker:
    """`Tracker` that records runs in order instead of writing to MLflow."""

    def __init__(self):
        self.runs: list[RecordingRun] = []
        self.llm_calls: list = []

    @contextmanager
    def start_run(self, name, **params):
        run = RecordingRun(name, params)
        self.runs.append(run)
        yield run

    def record_llm_call(self, record):
        self.llm_calls.append(record)

    def run(self, name: str) -> RecordingRun:
        return next(r for r in self.runs if r.name == name)


class FakeTables:
    """`TableSource` whose tables are CSV texts held in memory, with the keys it is told to declare."""

    def __init__(self, tables: dict[str, str], keys: DeclaredKeys | None = None):
        self._tables = tables
        self._keys = keys or DeclaredKeys()

    def describe(self) -> str:
        return "fake db schema shop"

    def tables(self) -> list[str]:
        return sorted(self._tables)

    def write_csv(self, table: str, dest: Path) -> None:
        dest.write_text(self._tables[table], encoding="utf-8")

    def declared_keys(self) -> DeclaredKeys:
        return self._keys
