"""Test doubles for the project's ports, so tests never need a network or monkeypatching."""

from collections.abc import Callable

from pydantic import BaseModel


class ScriptedLLM:
    """`LLMClient` whose replies come from a function `(prompt, schema) -> model`. Records every call."""

    def __init__(self, script: Callable[[str, type[BaseModel]], BaseModel]):
        self._script = script
        self.calls: list[tuple[str, str]] = []  # (schema name, model)

    def generate(self, prompt, schema, *, model, temperature=0.0):
        self.calls.append((schema.__name__, model))
        return self._script(prompt, schema)
