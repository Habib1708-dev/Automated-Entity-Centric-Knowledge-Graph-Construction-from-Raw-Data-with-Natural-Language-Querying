# kgbuilder: rules for Claude

Thesis project: automated knowledge graph construction from CSV/JSON and text into Neo4j.
An LLM proposes declarative plans and schemas; deterministic code validates and executes them.
Read [README.md](README.md) for usage, [PLAN.md](PLAN.md) for the original feature plan, and
[REFACTOR_PLAN.md](REFACTOR_PLAN.md) for the audit and the step-by-step remediation roadmap.

## 1. Work in steps. This is the most important rule.

The first version of this codebase was generated in one shot and the 8-step plan was skipped.
That must not happen again.

- **One big-enough step at a time.** A step is one entry of `REFACTOR_PLAN.md` (or, for new features, of
  `PLAN.md`). Steps are done strictly one after the other, never interleaved: a step is finished (gate
  green, roadmap updated, committed) before the next one starts. Several steps may be done in one session
  when the task calls for it, but never several at once.
- **A step is big enough to matter and small enough to review**: one coherent concern, roughly
  150 to 600 changed lines, ending in something runnable and tested. If a step grows past that, split it
  and record the split in `REFACTOR_PLAN.md` before continuing. Do not make steps artificially small
  either: a step that leaves the tree half-migrated is too small.
- **Every step follows the `implement-step` skill**: state scope, run the test baseline, implement,
  add or update tests, run `uv run pytest` and `uv run ruff check` (both green, exit codes unmasked),
  update the step's status in `REFACTOR_PLAN.md`, commit, then report what changed, what was verified,
  and what is next before moving on.
- **Refactoring steps preserve behaviour.** Do not mix a behaviour change into a structural move.
  If a bug is found during a refactor, note it in `REFACTOR_PLAN.md` under "Found along the way" and fix
  it in its own step (or a clearly separated commit), with a test that fails before the fix.
- **No speculative code.** Do not add features, options, abstractions or files that the current step
  does not need. No dead code, no commented-out code, no TODOs without a roadmap entry.
- **A comprehensive test runs only with the user's explicit permission.** A comprehensive test is any
  LLM run on the whole dataset or with a preset marked `ask_permission` in `presets.yaml` (today:
  `quality`). Ask each time, naming what the run answers and its estimated cost, and wait for a yes in the
  current conversation. An earlier yes, a roadmap acceptance criterion or a plan never counts as permission.
  `.claude/hooks/run_guard.py` makes Claude Code ask as well; the rule holds even where the hook cannot see
  the run (a script, an unusual command line).
- **Pipeline runs are rare and follow the `run-policy` skill.** Runs cost money, free quota and time, so
  the default is no run: `uv run pytest` and `uv run ruff check` prove docs, refactors, small fixes and
  anything the tests cover. `smoke` and `dev` run on small subsets (`samples/`) with a cheap model and only
  show that the code works; at most one such run per step. `quality` runs on the whole dataset and is
  started only with the user's agreement, for comparisons and reported numbers. Every report names the
  runs made and their cost, or says why none was made.
- One commit per step, message `step N: <what>`, only after the gate is green. Never chain the commit
  after the checks with `;` or behind a pipe that hides their exit code.

## 2. Architecture rules

Details and the target package layout live in the `project-organization` skill. The invariants:

- **Dependencies point inwards.** `cli` → `pipeline` → feature packages → `core`. `core` imports nothing
  from the rest of the project. Feature packages do not import each other's internals; shared helpers
  (text normalisation, Cypher identifier escaping, the Neo4j connection) live in `core` / `graph`,
  never inside an unrelated feature module.
- **Depend on abstractions at the boundaries.** The LLM, the graph database and the experiment tracker
  are accessed through small `Protocol` interfaces and injected. Business logic never imports
  `google.genai`, never calls `GraphDatabase.driver(...)`, and never imports `mlflow` directly.
- **One composition root.** Concrete objects (Gemini client, Neo4j driver, MLflow tracker, settings) are
  built in one place (`cli.py` / the pipeline factory) and passed down. No module-level singletons and no
  reading of global `settings` deep inside functions; pass the values in.
- **The LLM proposes, code decides.** Anything that can be computed exactly is computed in code. Every LLM
  output is parsed into a pydantic model and validated in code before it is used or stored.
- **All Cypher is parameterised.** Labels, types and property keys (which cannot be parameters) go through
  the single `cypher_ident` escape helper. All graph writes are idempotent (`MERGE`).

## 3. Code quality rules

Details, the pattern catalogue and the review checklist live in the `code-quality` skill. The invariants:

- **SOLID, applied pragmatically.** One reason to change per module and per function. Extend by adding a
  new class (a new validation check, a new matcher, a new LLM provider), not by editing a long function.
  A function longer than about 40 lines or with more than one "and" in its description gets split.
- **Use a design pattern only when it removes real duplication or a real `if` ladder.** The patterns this
  project is expected to use are listed in the skill (Strategy, Adapter, Decorator, Template Method,
  Null Object, Factory, Pipeline). Name the pattern in the class docstring when you use one.
- **Every file starts with a purpose header**: a module docstring that says what the file is for, where it
  sits in the pipeline, and what it must not do. This includes `__init__.py`, tests and `conftest.py`.
- **Explanatory comments.** Public functions and classes have docstrings (what, inputs, outputs, failure
  modes). Inline comments explain *why*: the reasoning, the invariant, the trap. They never restate the code.
  Prompts, thresholds and Cypher queries always get a comment on intent.
- **Typed.** Full type hints on all signatures; pydantic models for data that crosses a module boundary;
  no bare `dict` reports. No `Any` without a comment.
- **Errors are specific.** Raise project exception types from `core/errors.py`, not bare `RuntimeError`.
  Never swallow an exception silently; log it or re-raise it.
- **Tests come with the code.** Unit tests run without Neo4j or a network (fakes injected through the
  protocols). Tests that need Neo4j are marked `@pytest.mark.neo4j`. No `monkeypatch` of module globals
  once the ports exist.

## 4. MLflow rules

Details live in the `mlflow-tracking` skill. The invariants:

- **Every pipeline stage is one MLflow run**; a full pipeline is a parent run with nested stage runs.
- Every run logs **params** (models, temperature, prompt version hash, thresholds, dataset path),
  **metrics** (counts, rates, rounds, latency, token usage, `cost_usd` from `prices.yaml`) and **artifacts**
  (the files written to `out/`).
- Every LLM call is **traced** (prompt, response, model, latency, cache hit or miss).
- All MLflow access goes through the `Tracker` protocol in `tracking/`. A new stage without tracking is
  an incomplete stage. Tracking failures must never break the pipeline (Null Object fallback).
- Any change to a prompt, model or threshold is evaluated by comparing MLflow runs before and after, and
  the comparison is mentioned in the step report. A comparison about quality needs a `quality` run, which
  is proposed with its estimated cost and made only with the user's agreement (`run-policy` skill).

## 5. Commands

```
uv sync                                   # install
docker compose up -d                      # Neo4j 5 + APOC
uv run pytest                             # all tests (Neo4j tests skip when it is down)
uv run pytest -m "not neo4j"              # fast unit tests only
uv run ruff check . ; uv run ruff format . # lint and format
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
uv run kg --preset dev run --goal "..."   # whole pipeline on the preset's dataset: smoke / dev (subsets) / quality (data/)
```

## 6. Skills

| Skill | Use it when |
|---|---|
| `implement-step` | starting any roadmap step (also available as `/implement-step N`) |
| `project-organization` | creating, moving or renaming a file; deciding where code belongs |
| `code-quality` | writing or reviewing any code; choosing a pattern; writing comments |
| `mlflow-tracking` | adding or changing a stage, an LLM call, a metric, a prompt or a threshold |
| `run-policy` | before any pipeline run (any preset); deciding whether a change needs one at all |
| `reply-style` | writing any reply to the user (always: simple language, explain the why, end with a summary) |

Skill files live in `.claude/skills/` and are git-ignored: they exist only in the local checkout.
