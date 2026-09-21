"""Result models shared by every validation check and by the pipeline.

Role in the pipeline: `kg validate` and `kg run` return a `ValidationReport`; the CLI prints it and exits
non-zero when a check failed; every metric in it is logged to MLflow.
"""

from typing import Literal

from pydantic import BaseModel

Category = Literal["structure", "provenance", "consistency", "accuracy"]


class Check(BaseModel):
    """One pass/fail statement about the graph, with the numbers behind it."""

    name: str
    passed: bool
    detail: str
    category: Category


class CheckOutput(BaseModel):
    """What one check family contributes: its checks, plus metrics worth tracking over time."""

    checks: list[Check] = []
    metrics: dict[str, float] = {}

    def add(self, name: str, passed: bool, detail: str, category: Category) -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail, category=category))


class ValidationReport(BaseModel):
    checks: list[Check]
    metrics: dict[str, float]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)
