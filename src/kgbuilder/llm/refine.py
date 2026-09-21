"""The propose -> validate in code -> critique -> retry loop shared by every LLM proposal stage.

Role in the pipeline: used by the construction-plan proposer and the text-schema proposer.
Design: Template Method, written as a higher-order function. The loop is fixed here; each caller
supplies the three varying steps (propose, validate, critique) as callables. Code validation always runs
first, and the LLM critic only ever sees proposals that are already mechanically valid, because critic
calls are the expensive ones and a critic must not be asked to find what code can find exactly.
Not here: prompts, models or schemas; the callables close over those.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Critique(BaseModel):
    """An LLM reviewer's structured reply, shared by every critic prompt."""

    verdict: Literal["valid", "retry"]
    issues: list[str]


@dataclass
class RoundLog:
    """What one round found. Kept for the MLflow artifact that shows how a proposal converged."""

    round: int
    source: str  # "code" | "critic" | "none" (accepted)
    issues: list[str]


@dataclass
class Refinement(Generic[T]):
    """Outcome of the loop. `value` is the last proposal even when it was not accepted."""

    value: T
    rounds: int
    open_issues: list[str]
    history: list[RoundLog] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.open_issues


def render_feedback(previous: BaseModel, issues: list[str]) -> str:
    """The text appended to the next proposal prompt: the rejected proposal and what was wrong with it."""
    problems = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous answer is below, followed by the problems found in it. Fix every problem.\n"
        f"<previous_answer>\n{previous.model_dump_json(indent=1)}\n</previous_answer>\n"
        f"<problems>\n{problems}\n</problems>"
    )


def refine(
    propose: Callable[[str], T],
    validate: Callable[[T], list[str]],
    critique: Callable[[T], list[str]] | None = None,
    max_rounds: int = 3,
) -> Refinement[T]:
    """Run the loop until a proposal has no issues, or `max_rounds` is used up.

    `propose(feedback)` returns a proposal; `feedback` is "" in round 1.
    `validate(proposal)` returns exact, code-computed problems.
    `critique(proposal)` returns an LLM reviewer's problems; called only when `validate` found none.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    feedback = ""
    history: list[RoundLog] = []
    for round_number in range(1, max_rounds + 1):
        proposal = propose(feedback)
        issues, source = validate(proposal), "code"
        if not issues and critique is not None:
            issues, source = critique(proposal), "critic"
        history.append(RoundLog(round_number, source if issues else "none", issues))
        if not issues:
            return Refinement(proposal, round_number, [], history)
        feedback = render_feedback(proposal, issues)
    return Refinement(proposal, max_rounds, issues, history)
