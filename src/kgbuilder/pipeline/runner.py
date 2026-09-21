"""Run stages: one tracked run per stage, skipping, and the human approval pauses.

Role in the pipeline: called by the CLI for single commands (`run_stages`) and for `kg run` (`run_all`).
Design: the runner, not the stage, opens the MLflow run, so tracking cannot be forgotten. Approval is
a callback, so the runner stays free of any UI: the CLI asks on the terminal, tests pass a function.
"""

from collections.abc import Callable
from pathlib import Path

from ..core.errors import KgBuilderError
from .stage import PipelineContext, PipelineState, Stage
from .stages import (
    BuildStage,
    ExtractStage,
    IngestTextStage,
    LinkStage,
    PlanStage,
    ProfileStage,
    ResolveStage,
    TextSchemaStage,
    ValidateStage,
)

# Called with the stage name and the file to review; returns False to stop the pipeline.
Approve = Callable[[str, Path], bool]

# Order matters: build needs the plan, the text schema uses the plan's node descriptions, resolve runs
# before link so that links attach to canonical entities, validate sees the finished graph.
FULL_PIPELINE: list[Stage] = [
    ProfileStage(),
    PlanStage(),
    BuildStage(),
    IngestTextStage(),
    TextSchemaStage(),
    ExtractStage(),
    ResolveStage(),
    LinkStage(),
    ValidateStage(),
]


class ReviewDeclinedError(KgBuilderError):
    """The human reviewer stopped the pipeline at an approval pause."""


def run_stage(
    ctx: PipelineContext, state: PipelineState, stage: Stage, approve: Approve | None = None
) -> None:
    """Run one stage inside its own tracked run, then pause for review when the stage asks for it."""
    with ctx.tracker.start_run(stage.name, **stage.params(ctx, state)) as run:
        stage.run(ctx, state, run)
    if approve is not None and stage.review_file is not None:
        review_path = ctx.out / stage.review_file
        if not approve(stage.name, review_path):
            raise ReviewDeclinedError(f"stopped after '{stage.name}'; edit {review_path} and continue")
        stage.reload(ctx, state)  # the reviewer may have edited the file: the file wins


def run_stages(ctx: PipelineContext, state: PipelineState, stages: list[Stage]) -> PipelineState:
    """Run the given stages unconditionally (a single CLI command: the user asked for exactly these)."""
    for stage in stages:
        run_stage(ctx, state, stage)
    return state


def run_all(
    ctx: PipelineContext,
    state: PipelineState,
    approve: Approve | None = None,
    stages: list[Stage] | None = None,
) -> PipelineState:
    """The whole pipeline under one parent run. Stages whose input is absent are skipped: a data dir
    without tables gets no domain graph, one without documents gets no subject graph."""
    with ctx.tracker.start_run("pipeline", data_dir=state.data_dir, goal=state.goal):
        for stage in stages or FULL_PIPELINE:
            if stage.applies(state):
                run_stage(ctx, state, stage, approve)
    return state
