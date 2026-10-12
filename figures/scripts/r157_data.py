"""The numbers the R157 figures show, read from the committed benchmark record
(`tests/gold/r157/outcomes.json`, committed in R158): accuracy per dataset and per question type by both
scores, and the wrong answers by the cause R157 traced; plus the run ids and costs the captions and the
protocol diagram name, and how the exact-match score splits into the answers code scored and the free-text
answers the judges decided (`tests/gold/r155/runs.json`, `tests/gold/r156/runs.json`).

Role: the only reader of data for `r157_charts.py` and `r152_r157_diagrams.py`; the test checks these
functions against the counts the roadmap quotes (239 and 243 of 250, per dataset, per type, 11 wrong
answers).
Design: pure functions over the per-question outcomes; a rate is counted from the recorded booleans and
carries its 95 % Wilson interval (`Proportion.of`), so a figure can only show what the record holds.
Must not: read `out/`, MLflow or a database, or draw.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from kgbuilder.validation.interval import Proportion

GOLD = Path(__file__).resolve().parents[2] / "tests" / "gold"
RECORD = GOLD / "r157" / "outcomes.json"

# the order the roadmap's R157 table uses: the two new datasets first, then the three thesis datasets
DATASETS = ("ntsb", "lighthouses", "furniture", "heldout", "generality")
NEW = frozenset({"ntsb", "lighthouses"})  # fetched for the benchmark (R153, R154), never used for tuning
LABELS = {
    "ntsb": "NTSB aviation",
    "lighthouses": "Lighthouses",
    "furniture": "Furniture",
    "heldout": "Held-out (NHTSA)",
    "generality": "Generality",
    "pooled": "Pooled (five datasets)",
}
TYPES = ("lookup", "structured_filter", "disambiguation", "multi_hop", "negation_sensitive", "aggregation")
TYPE_LABELS = {
    "lookup": "Lookup",
    "structured_filter": "Structured filter",
    "disambiguation": "Disambiguation",
    "multi_hop": "Multi-hop",
    "negation_sensitive": "Negation-sensitive",
    "aggregation": "Aggregation",
}
# R157's cause classes (REFACTOR_PLAN R157, the record's `cause_classes`), in the order the chart lists them
CAUSES = ("agent_reasoning", "tool_limit", "retrieval_miss", "graph_build", "answer_form")


@dataclass(frozen=True)
class Outcome:
    """One benchmark question's result: exact match (by code; a free-text answer by the judges' verdict),
    right by meaning (the judges), its causes."""

    dataset: str
    question_id: str
    type: str
    exact: bool
    meaning: bool
    causes: tuple[str, ...]

    @property
    def wrong(self) -> bool:
        """Wrong by either score: the answers R157 traced to a cause."""
        return not (self.exact and self.meaning)


@dataclass(frozen=True)
class Accuracy:
    """One group's accuracy by both scores (`group` is a dataset, a question type or "pooled")."""

    group: str
    exact: Proportion
    meaning: Proportion


@dataclass(frozen=True)
class CauseCount:
    """One cause class and the wrong answers it was traced in: wrong by both scores, and wrong by exact
    match only (right by meaning)."""

    cause: str
    both: tuple[str, ...]
    exact_only: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.both) + len(self.exact_only)


def load_outcomes(path: Path = RECORD) -> list[Outcome]:
    """The 250 committed outcomes, in the record's order."""
    rows = json.loads(path.read_text(encoding="utf-8"))["questions"]
    return [
        Outcome(r["dataset"], r["question_id"], r["type"], r["exact"], r["meaning"], tuple(r["causes"]))
        for r in rows
    ]


def _accuracy(group: str, rows: list[Outcome]) -> Accuracy:
    n = len(rows)
    return Accuracy(
        group,
        Proportion.of(sum(r.exact for r in rows), n),
        Proportion.of(sum(r.meaning for r in rows), n),
    )


def by_dataset(outcomes: list[Outcome]) -> list[Accuracy]:
    """Accuracy per dataset in `DATASETS` order, then pooled over all five."""
    per = [_accuracy(d, [o for o in outcomes if o.dataset == d]) for d in DATASETS]
    return [*per, _accuracy("pooled", outcomes)]


def by_type(outcomes: list[Outcome]) -> list[Accuracy]:
    """Accuracy per question type in `TYPES` order, pooled over the datasets."""
    return [_accuracy(t, [o for o in outcomes if o.type == t]) for t in TYPES]


def wrong(outcomes: list[Outcome]) -> list[Outcome]:
    """The answers wrong by either score, in the record's order."""
    return [o for o in outcomes if o.wrong]


def by_cause(outcomes: list[Outcome]) -> list[CauseCount]:
    """Every cause class with the wrong answers traced to it; a question with several causes is counted
    under each, so the counts add up to more than the wrong answers."""
    failed = wrong(outcomes)
    return [
        CauseCount(
            cause,
            tuple(o.question_id for o in failed if cause in o.causes and not o.meaning),
            tuple(o.question_id for o in failed if cause in o.causes and o.meaning),
        )
        for cause in CAUSES
    ]


@dataclass(frozen=True)
class Costs:
    """The paid runs behind the benchmark (USD, from the run records) and the agent's run id per dataset."""

    builds: float  # R155 in all: the two new graphs plus the refused first plan
    refused_plan: float  # R155's first plan, refused before the composite keys (part of `builds`)
    agent: float  # R156: the frozen agent on the 250 questions
    agent_runs: dict[str, str]


def load_costs(gold: Path = GOLD) -> Costs:
    """The costs and run ids of R155 and R156 from their committed run records."""
    builds = json.loads((gold / "r155" / "runs.json").read_text(encoding="utf-8"))
    agent = json.loads((gold / "r156" / "runs.json").read_text(encoding="utf-8"))
    return Costs(
        builds["cost_usd"],
        builds["failed_plan"]["cost_usd"],
        agent["cost_usd"],
        {d: run["run"] for d, run in agent["runs"].items()},
    )


@dataclass(frozen=True)
class ExactSplit:
    """Who decided the exact-match score: code scored most answers; a free-text gold answer has no exact form,
    so its exact-match outcome is the judges' verdict (R157, `*_bench_agent_verdicts.json`)."""

    code: Proportion  # the answers code scored, right of scored (R156's `exact_code_scored`)
    free_text: Proportion  # the free-text answers, right by the judges' verdict


def exact_split(outcomes: list[Outcome], gold: Path = GOLD) -> ExactSplit:
    """The code-scored part from R156's run record; the free-text part is the rest of the recorded exact
    matches (R156's `unjudged_free_text` answers), so the two add up to the record's exact-match count."""
    runs = json.loads((gold / "r156" / "runs.json").read_text(encoding="utf-8"))
    scored = [run["exact_code_scored"].split("/") for run in runs["runs"].values()]
    code = Proportion.of(sum(int(k) for k, _ in scored), sum(int(n) for _, n in scored))
    exact = sum(o.exact for o in outcomes)
    return ExactSplit(code, Proportion.of(exact - code.k, runs["unjudged_free_text"]))
