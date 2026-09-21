---
name: code-quality
description: Code quality standard for kgbuilder - SOLID applied to this codebase, the design-pattern catalogue, file headers and explanatory comments, typing, errors, tests, and the self-review checklist. Use when writing, refactoring or reviewing any Python in this project, when choosing a design pattern, or when writing docstrings and comments.
---

# Code quality

## SOLID, in this codebase

| Principle | What it means here | Smell to fix |
|---|---|---|
| **S**ingle responsibility | A module does one pipeline concern; a function does one thing at one level of abstraction. Reading data, deciding, and writing to Neo4j are three functions. | `validate_graph` (90 lines, four check families), `resolve_entities` (score + adjudicate + merge + clean), `link_graphs` |
| **O**pen/closed | New checks, matchers, providers and stages are new classes registered in a list, not new branches in an old function. | adding a validation check by editing the big function |
| **L**iskov | Every `LLMClient`, `Tracker` or `Stage` implementation is usable wherever the protocol is expected, including fakes and the Null Object. No `isinstance` checks on implementations. | `if self._mlflow:` guards in every method |
| **I**nterface segregation | Small protocols: `LLMClient.generate`, `Embedder.embed`, `Tracker.start_run`. A function that only needs to read the graph gets a reader, not the whole context. | passing a god-object around |
| **D**ependency inversion | Logic depends on protocols; adapters are injected from the composition root. | `from . import llm` + global `settings` + `get_driver()` inside stage code; tests that `monkeypatch` module globals |

## Pattern catalogue

Use a pattern only where it is listed here or where it removes real duplication. Name it in the docstring.

| Pattern | Where | Why |
|---|---|---|
| **Adapter** | `llm/gemini.py`, `tracking/mlflow_tracker.py`, `graph/connection.py` | isolate third-party SDKs behind project protocols |
| **Decorator** | `llm/cache.py` wraps any `LLMClient` | caching is orthogonal to the provider; also the place for tracing and retry |
| **Template Method** (or a plain higher-order function) | `llm/refine.py`: propose → validate in code → optional critic → feedback → retry | the plan proposer and the text-schema proposer duplicate this loop today |
| **Strategy** | validation checks, ER matchers, chunkers | open/closed extension points |
| **Null Object** | `NullTracker` | tracking can be off without `if` guards |
| **Factory / composition root** | `cli.py` builds settings → adapters → `StageContext` | single place for wiring |
| **Pipeline** | `pipeline/stage.py`: uniform `Stage.run(ctx)`; the runner handles tracking, ordering, skipping | removes the repeated `with track(...)`, `driver = get_driver(); try/finally` blocks |
| **Repository** (light) | graph read/write functions grouped per graph layer, taking a driver/session | keeps Cypher out of decision logic |

Do not add: abstract base classes with a single implementation and no test fake, builders for plain
pydantic models, event buses, plugin registries with entry points, generic "manager" classes.

## File header (mandatory for every file)

```python
"""<One line: what this file is for.>

Role in the pipeline: <which stage/layer uses it, what comes before and after>.
Design: <pattern used, key decision, or invariant it protects>.  (omit if trivial)
Not here: <the nearby responsibility that belongs elsewhere>.    (omit if obvious)
"""
```

`__init__.py`, `conftest.py` and test files get a header too (tests: what behaviour is covered and
whether Neo4j is needed).

## Comments and docstrings

- **Docstrings** on every public function, class and pydantic model: purpose, arguments that are not
  self-evident, return value, exceptions raised, side effects (graph writes, files, LLM calls).
  Pydantic fields that feed an LLM schema use `Field(description=...)`; others get a trailing comment.
- **Inline comments explain why, not what**: the reason for a threshold, the trap a Cypher clause avoids,
  why an order matters, what a regex matches (give an example input). Good examples already in the code:
  `# the type is a property, not a label: a Product label here would collide with the domain graph's`.
- **Always commented**: prompts (intent + what each rule guards against), every threshold and magic
  number, every Cypher query longer than one clause, every regex, every `except`.
- Block comments mark the phases of a longer function (`# 1. collect candidates`), but if you need more
  than three, split the function.
- Never: commented-out code, comments that restate the line, change history in comments, TODO without a
  roadmap entry.

## Python standards

- Python 3.11+, full type hints, `X | None`, `list[str]`. `from __future__ import annotations` not needed.
- Data crossing a module boundary is a pydantic model (or a frozen dataclass), never a bare `dict`/tuple.
  Fix on sight: `stage_resolve -> dict`, `propose_text_schema -> tuple[TextSchema, int, list[str]]`.
- No module-level mutable state (`_client = None`, `settings = Settings()` read from deep code).
- Exceptions: subclasses of `KgBuilderError` (`PlanRejectedError`, `LLMUnavailableError`, ...). The CLI
  converts them to exit codes; nothing else catches broadly. `except Exception` needs a comment and a log.
- Logging with `logging.getLogger(__name__)`; `typer.echo` only in `cli.py`; no `print`.
- Resources (drivers, connections, DuckDB) are opened by the composition root or a context manager and
  closed exactly once.
- Names: functions are verbs, classes are nouns, no abbreviations beyond the domain's own (`er`, `fk`
  only inside their module). No single-letter names outside comprehensions and tight loops.
- Lint and format with ruff (`uv run ruff check .`, `uv run ruff format .`), line length 110.

## Tests

- Unit tests (`tests/unit`) need no Neo4j and no network: inject `ScriptedLLM`, `RecordingTracker`, and
  test pure functions directly (profiling, plan validation, chunking, evidence verification, scoring).
- Integration tests (`tests/integration`) are marked `@pytest.mark.neo4j` and skip when Neo4j is down.
- Each bug fix starts with a failing test. Each new class has a test for its contract.
- Test names state behaviour: `test_verify_rejects_quote_not_in_chunk`.
- No monkeypatching of module globals once the protocols exist; pass fakes in.

## Self-review checklist (run before reporting a step as done)

- [ ] Every touched file has an accurate purpose header.
- [ ] Public API has docstrings; prompts, thresholds, Cypher and regexes have why-comments.
- [ ] No function over about 40 lines; no file over about 250 lines without a reason.
- [ ] No new import that violates the dependency rules (`project-organization` skill).
- [ ] No third-party SDK imported outside its adapter. No global `settings` / singleton access added.
- [ ] No bare dict/tuple crossing module boundaries; no bare `RuntimeError`.
- [ ] Cypher parameterised; identifiers via `cypher_ident`; writes idempotent.
- [ ] Stage logs params, metrics, artifacts to MLflow; LLM calls traced (`mlflow-tracking` skill).
- [ ] Tests added or updated; `uv run pytest` green; `uv run ruff check .` clean.
- [ ] No dead code, no speculative options, no leftovers from the old location.
