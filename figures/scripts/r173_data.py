"""The numbers the R174 figures show: the three measurements of the agent on unseen questions, read from the
committed per-question records (`tests/gold/r157/outcomes.json`, the frozen engine on 250; `tests/gold/r166/
outcomes.json`, the engine before the R167-R170 fixes on 100; `tests/gold/r173/outcomes.json`, after them on
100), and the paid runs behind them (`tests/gold/r156/r165/r172/runs.json`).

Role: the only reader of data for `r173_charts.py` and `r160_r173_diagrams.py`; the test checks these
functions against the counts the roadmap quotes (243/250, 94/100, 98/100; the wrong answers and their
causes; the six questions whose trace used a new operation; the costs).
Design: pure functions over the records; the accuracy functions of `r157_data` are reused on each
measurement's outcomes (a rate is counted from the recorded booleans with its 95 % Wilson interval), so a
figure can only show what a record holds. A two-proportion z test compares two measurements' rates; the
question sets differ and each was run once, so the comparison says whether a difference could be chance,
never that the fixes caused it.
Must not: read `out/`, MLflow or a database, or draw.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from kgbuilder.validation.interval import Proportion

from . import r157_data as base

GOLD = base.GOLD


@dataclass(frozen=True)
class Outcome:
    """One question's result in a confirmation set: both scores, the traced causes, and the R167-R170
    operation its trace used (`distinct`, `empty_cell`; none before R167)."""

    dataset: str
    question_id: str
    type: str
    exact: bool
    meaning: bool
    causes: tuple[str, ...]
    operations: tuple[str, ...]

    @property
    def wrong(self) -> bool:
        return not (self.exact and self.meaning)


@dataclass(frozen=True)
class Measurement:
    """One measurement of the agent on questions it had not seen: which engine, on which set."""

    key: str  # the roadmap step that scored it
    label: str  # as a panel title names it
    engine: str  # the engine's state in one phrase
    outcomes: tuple[Outcome, ...]

    @property
    def n(self) -> int:
        return len(self.outcomes)

    @property
    def pooled(self) -> base.Accuracy:
        return base.by_dataset(list(self.outcomes))[-1]


# the three measurements in time order: the panels' order in every chart
KEYS = ("r157", "r166", "r173")
LABELS = {
    "r157": "R157: frozen engine, 250 questions",
    "r166": "R166: before the fixes, 100 questions",
    "r173": "R173: after the fixes, 100 questions",
}
ENGINES = {
    "r157": "frozen at R151 (tag query-engine-r151)",
    "r166": "with R161-R164 (counting rule, word forms, form rule, varied examples)",
    "r173": "with R167-R170 (empty cell, distinct and periods, hyphens, headings in excerpts)",
}
# the operations a trace can use, as the records name them, and as a reader sees them
OPERATIONS = ("distinct", "empty_cell")
OPERATION_LABELS = {
    "distinct": "distinct count (R168)",
    "empty_cell": "empty-cell test (R167)",
}


def _load(path: Path) -> tuple[Outcome, ...]:
    rows = json.loads(path.read_text(encoding="utf-8"))["questions"]
    return tuple(
        Outcome(
            r["dataset"],
            r["question_id"],
            r["type"],
            r["exact"],
            r["meaning"],
            tuple(r["causes"]),
            tuple(r.get("operations", ())),
        )  # fmt: skip
        for r in rows
    )


def measurements(gold: Path = GOLD) -> list[Measurement]:
    """The three measurements in time order; R157's outcomes carry no operation (none existed)."""
    frozen = tuple(
        Outcome(o.dataset, o.question_id, o.type, o.exact, o.meaning, o.causes, ())
        for o in base.load_outcomes(gold / "r157" / "outcomes.json")
    )
    return [
        Measurement("r157", LABELS["r157"], ENGINES["r157"], frozen),
        Measurement("r166", LABELS["r166"], ENGINES["r166"], _load(gold / "r166" / "outcomes.json")),
        Measurement("r173", LABELS["r173"], ENGINES["r173"], _load(gold / "r173" / "outcomes.json")),
    ]


@dataclass(frozen=True)
class OperationUse:
    """One R167-R170 operation and the questions whose trace used it, with how many were right."""

    operation: str
    question_ids: tuple[str, ...]
    right: int


def operations_used(outcomes: tuple[Outcome, ...]) -> list[OperationUse]:
    """Every operation in `OPERATIONS` order with the questions that used it (a question may use several)."""
    return [
        OperationUse(
            op,
            tuple(o.question_id for o in outcomes if op in o.operations),
            sum(o.meaning for o in outcomes if op in o.operations),
        )
        for op in OPERATIONS
    ]


@dataclass(frozen=True)
class Comparison:
    """Two measurements' pooled rates by meaning compared by a two-proportion z test (pooled standard error),
    two-sided; the question sets differ, so it answers only whether the difference could be chance."""

    a: str
    b: str
    a_rate: Proportion
    b_rate: Proportion
    z: float
    p: float


def compare(a: Measurement, b: Measurement) -> Comparison:
    pa, pb = a.pooled.meaning, b.pooled.meaning
    pooled = (pa.k + pb.k) / (pa.n + pb.n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / pa.n + 1 / pb.n))
    z = ((pa.rate or 0.0) - (pb.rate or 0.0)) / se
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return Comparison(a.key, b.key, pa, pb, z, p)


@dataclass(frozen=True)
class RunCost:
    """One paid measurement run: what it cost, on how many questions, and what was wasted beside it."""

    key: str
    label: str
    cost: float  # USD, the runs that were scored
    questions: int
    wasted: float  # USD spent on a run that was not scored (R172's first NTSB command), 0 otherwise

    @property
    def per_question(self) -> float:
        return self.cost / self.questions


def run_costs(gold: Path = GOLD) -> list[RunCost]:
    """The three agent runs' costs from their committed run records, in time order."""
    costs = []
    for key, step, label in (
        ("r157", "r156", "R156 run (scored in R157)"),
        ("r166", "r165", "R165 run (scored in R166)"),
        ("r173", "r172", "R172 run (scored in R173)"),
    ):
        record = json.loads((gold / step / "runs.json").read_text(encoding="utf-8"))
        wasted = sum(w["cost_usd"] for w in record.get("wasted_run", {}).values())
        questions = sum(int(r["exact_code_scored"].split("/")[1]) for r in record["runs"].values())
        questions += record["unjudged_free_text"]
        costs.append(RunCost(key, label, record["cost_usd"], questions, wasted))
    return costs
