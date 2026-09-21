---
name: implement-step
description: The procedure for implementing exactly one step of REFACTOR_PLAN.md (or PLAN.md) in kgbuilder. Use whenever the user asks to do, start, continue or fix a roadmap step, to refactor part of the codebase, or to add a feature. Enforces scoped, tested, reviewable increments instead of one-shot rewrites.
---

# Implement one step

The argument, if given, is the step number (for example `/implement-step 2`). Without one, take the first
step in `REFACTOR_PLAN.md` whose status is not `done`.

## Procedure

1. **Read the step.** Open `REFACTOR_PLAN.md`, read the step's scope, acceptance criteria and the audit
   findings it closes. Check that every step it depends on is `done`. If not, stop and say so.
2. **State the scope back** in three to six bullets: files to touch, what will change, what will not.
   If the step looks larger than about 600 changed lines, propose a split first and update the roadmap.
3. **Baseline.** Run `uv run pytest -q` and `uv run ruff check .` (once ruff exists). Record the result.
   A red baseline is fixed or reported before anything else is touched.
4. **Implement**, following the `project-organization`, `code-quality` and `mlflow-tracking` skills.
   - Structural moves first, in isolation, with tests green after the move.
   - Then the design change. Then new tests.
   - Every new or touched file gets its purpose header and explanatory comments.
5. **Verify.**
   - `uv run pytest -q` must be green, with no fewer tests than the baseline.
   - `uv run ruff check .` clean.
   - Check each acceptance criterion of the step explicitly. If the step touches a stage, run that
     stage (`uv run kg ...`) when its prerequisites (Neo4j, API key) are available, and confirm the
     MLflow run has the expected params, metrics and artifacts.
6. **Update the docs.** Set the step's status in `REFACTOR_PLAN.md` to `done` with the date and a one-line
   result; tick the findings it closed; add anything discovered to "Found along the way". Update
   `README.md` only if usage or the module map changed.
7. **Stop and report**: what changed (files), what was verified (commands and results), which acceptance
   criteria are met, what was deliberately left out, and which step is next. Do not start the next step.

## Hard rules

- Never widen the scope mid-step. New ideas go to "Found along the way".
- Never mix a behaviour change into a behaviour-preserving refactor.
- Never delete or weaken a test to get green. If a test is wrong, say why in the report.
- Never leave the tree in a half-migrated state: no file that exists in both the old and the new location,
  no compatibility re-exports kept "for now" unless the roadmap says so.
- If something blocks the step (missing API key, Neo4j down), finish everything that does not depend on
  it, and report exactly what could not be verified.
