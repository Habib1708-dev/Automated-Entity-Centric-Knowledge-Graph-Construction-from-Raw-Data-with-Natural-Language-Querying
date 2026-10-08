"""Scores of retrieval without the reader (R117): how many gold evidence chunks a system ranks within each
budget, whether it ranks all of a question's, how many gold targets its start nodes (seeds) find, and its
latency; per question type with n and Wilson intervals, and two systems paired question by question.

Role in the pipeline: `kg retrieve-eval` (pipeline/retrieval_stages.py) ranks every question of a QA gold
file with one system and scores the outcome rows here; `kg retrieve-compare` pairs two of its reports.
Retrieval is measured apart from answering, so a change to retrieval shows in what it retrieves before a
reader's own variation can hide it.
Design: pure arithmetic over outcome rows, like qa.py; the caller ranks and places the targets. Evidence is
the QA gold's chunks (qa_gold.py); seeds are compared with the target gold (R89) as placed on the loaded
build: record refs and canonical ids, the ids every system's seeds use. Rates pool their counts over the
questions (micro), as recall@k and C5 do; the per-question outcomes "complete" and "seeds found" are what
two systems are paired on, with the exact McNemar test (paired.py).
Not here: ranking (query/), placing targets (anchor/targets.py), files and MLflow (pipeline/).
"""

import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..core.errors import EvaluationError
from .interval import Proportion
from .paired import PairedReport, compare_pairs
from .qa_gold import QuestionType


class RetrievalOutcome(BaseModel):
    """One question ranked by one system: a row of the report `retrieval_<system>.json`."""

    question_id: str
    type: QuestionType
    system: str
    gold_chunks: list[str]  # the question's evidence chunks; empty: it has no evidence measure
    ranked: list[str]  # the chunk ids the system ranked, best first, up to the largest budget
    # the stable ids of the nodes the system started from, best first; None for a system that starts from no
    # node (vector search), which then has no seed scores at all rather than zeros
    seeds: list[str] | None
    # each gold target's nodes in the build; [] for a target the build lacks (a miss, as in C2)
    gold_targets: list[list[str]]
    latency_ms: float

    def evidence_hits(self, k: int) -> int:
        """Gold chunks among the top k ranked."""
        return len(set(self.ranked[:k]) & set(self.gold_chunks))

    def complete(self, k: int) -> bool:
        """Every gold chunk is among the top k ranked."""
        return self.evidence_hits(k) == len(set(self.gold_chunks))

    def targets_found(self, k: int) -> int:
        """Gold targets with a node among the top k seeds."""
        top = set((self.seeds or [])[:k])
        return sum(bool(top & set(nodes)) for nodes in self.gold_targets)

    def seeds_found(self, k: int) -> bool:
        """Every gold target has a node among the top k seeds."""
        return self.targets_found(k) == len(self.gold_targets)


class RetrievalScores(BaseModel):
    """The scores of one question type, or of all questions; each maps a budget K to its proportion."""

    questions: int
    evidence_recall: dict[int, Proportion]  # gold chunks in the top K, over every gold chunk
    complete: dict[int, Proportion]  # questions with every gold chunk in the top K, over those with any
    # targets with a node among the top K seeds, over every target; empty for a system without seeds
    seed_recall: dict[int, Proportion]
    seed_found: dict[int, Proportion]  # questions whose every target is found, over those with any


class RetrievalFingerprint(BaseModel):
    """What two reports must share to be paired: the questions, the targets, the graph and the embedding
    model (the fairness contract of plan R116-R125)."""

    gold_hash: str
    targets_hash: str
    graph_digest: str
    embed_model: str


class RetrievalReport(BaseModel):
    """All retrieval scores of one system; `by_type` has every type, so metric names stay stable."""

    system: str
    budgets: list[int]
    fingerprint: RetrievalFingerprint
    seeded: bool  # whether the system starts from nodes; without, it has no seed scores
    overall: RetrievalScores
    by_type: dict[QuestionType, RetrievalScores]
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    targets: int  # gold targets over every question
    targets_unplaced: int  # of them, with no node in the build: misses for any system
    outcomes: list[RetrievalOutcome]

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics: every rate overall with its interval and n, every rate per type with its n;
        an empty rate is None, logged as nothing."""
        out: dict[str, float | int | None] = {
            "questions": self.overall.questions,
            "targets": self.targets,
            "targets_unplaced": self.targets_unplaced,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
        }
        for name, share in _shares(self.overall).items():
            out.update({name: share.rate, f"{name}_low": share.low, f"{name}_high": share.high})
            out[f"{name}_n"] = share.n
        for qtype, scores in self.by_type.items():
            out[f"questions_{qtype.value}"] = scores.questions
            for name, share in _shares(scores).items():
                out.update({f"{name}_{qtype.value}": share.rate, f"{name}_n_{qtype.value}": share.n})
        return out


def _shares(scores: RetrievalScores) -> dict[str, Proportion]:
    """The rates of `scores` under their metric names (`complete_at_5`)."""
    return {
        f"{name}_at_{k}": share
        for name, by_budget in (
            ("evidence_recall", scores.evidence_recall),
            ("complete", scores.complete),
            ("seed_recall", scores.seed_recall),
            ("seed_found", scores.seed_found),
        )
        for k, share in by_budget.items()
    }


def score_retrieval(
    outcomes: Sequence[RetrievalOutcome],
    budgets: Sequence[int],
    system: str,
    fingerprint: RetrievalFingerprint,
) -> RetrievalReport:
    """Score one system's outcome rows at every budget, overall and per question type.

    Raises `ValueError` for a budget below 1, and `EvaluationError` when a question has two rows.
    """
    if not budgets or min(budgets) < 1:
        raise ValueError(f"retrieval budgets must be at least 1, got {list(budgets)}")
    if repeated := sorted(q for q, n in Counter(o.question_id for o in outcomes).items() if n > 1):
        raise EvaluationError([f"questions ranked more than once: {repeated[:3]}"])
    seeded = any(o.seeds is not None for o in outcomes)
    latencies = [o.latency_ms for o in outcomes]
    return RetrievalReport(
        system=system,
        budgets=sorted(budgets),
        fingerprint=fingerprint,
        seeded=seeded,
        overall=_aggregate(outcomes, budgets, seeded),
        by_type={t: _aggregate([o for o in outcomes if o.type == t], budgets, seeded) for t in QuestionType},
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        targets=sum(len(o.gold_targets) for o in outcomes),
        targets_unplaced=sum(not nodes for o in outcomes for nodes in o.gold_targets),
        outcomes=list(outcomes),
    )


def _aggregate(outcomes: Sequence[RetrievalOutcome], budgets: Sequence[int], seeded: bool) -> RetrievalScores:
    # a question without evidence or without targets has nothing to find: it is left out of that measure
    evidence = [o for o in outcomes if o.gold_chunks]
    targeted = [o for o in outcomes if o.gold_targets]
    chunks = sum(len(set(o.gold_chunks)) for o in evidence)
    targets = sum(len(o.gold_targets) for o in targeted)
    return RetrievalScores(
        questions=len(outcomes),
        evidence_recall={
            k: Proportion.of(sum(o.evidence_hits(k) for o in evidence), chunks) for k in budgets
        },
        complete={k: Proportion.of(sum(o.complete(k) for o in evidence), len(evidence)) for k in budgets},
        seed_recall={k: Proportion.of(sum(o.targets_found(k) for o in targeted), targets) for k in budgets}
        if seeded
        else {},
        seed_found={k: Proportion.of(sum(o.seeds_found(k) for o in targeted), len(targeted)) for k in budgets}
        if seeded
        else {},
    )


def _percentile(values: Sequence[float], q: float) -> float | None:
    """The nearest-rank percentile: the smallest value at or below which a share `q` of the values lie.
    None for no values. Nearest-rank rather than interpolated: with 30-odd questions it is a latency one
    question really had."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


class RetrievalComparison(BaseModel):
    """Two systems' reports paired at one budget k: complete retrieval over the questions with evidence, and
    every target found over the questions with targets (only when both systems start from nodes)."""

    k: int
    complete: PairedReport
    seed_found: PairedReport | None

    def metrics(self) -> dict[str, float | int]:
        """The paired counts and p per measure, prefixed `complete_at_<k>_` and `seed_found_at_<k>_`."""
        out = {f"complete_at_{self.k}_{name}": v for name, v in self.complete.metrics().items()}
        if self.seed_found is not None:
            out.update({f"seed_found_at_{self.k}_{name}": v for name, v in self.seed_found.metrics().items()})
        return out


def compare_retrieval(
    a: RetrievalReport, b: RetrievalReport, k: int, a_name: str = "a", b_name: str = "b"
) -> RetrievalComparison:
    """Pair two reports at budget `k` with the exact McNemar test.

    Raises `EvaluationError` when they were computed on different questions, targets, graphs or embedding
    models (then a difference would not be the systems'), when they hold other questions or types, or when
    either lacks the budget `k`.
    """
    _check_pairable(a, b, k)
    by_id = {o.question_id: o for o in b.outcomes}
    pairs = [(o, by_id[o.question_id]) for o in a.outcomes]
    complete = [(x.type, x.complete(k), y.complete(k)) for x, y in pairs if x.gold_chunks]
    seeds = [(x.type, x.seeds_found(k), y.seeds_found(k)) for x, y in pairs if x.gold_targets]
    return RetrievalComparison(
        k=k,
        complete=compare_pairs(complete, a_name, b_name),
        seed_found=compare_pairs(seeds, a_name, b_name) if a.seeded and b.seeded else None,
    )


def _check_pairable(a: RetrievalReport, b: RetrievalReport, k: int) -> None:
    issues = []
    if a.fingerprint != b.fingerprint:
        issues.append(f"the reports differ in what they ran on: {a.fingerprint} against {b.fingerprint}")
    if k not in a.budgets or k not in b.budgets:
        issues.append(f"budget {k} is not in both reports ({a.budgets}, {b.budgets})")
    types_a = {o.question_id: o.type for o in a.outcomes}
    types_b = {o.question_id: o.type for o in b.outcomes}
    if types_a != types_b:
        issues.append("the reports hold other questions or other question types")
    if issues:
        raise EvaluationError(issues)


def load_retrieval_report(path: Path) -> RetrievalReport:
    """A report written by `kg retrieve-eval`, or `EvaluationError` naming what does not fit."""
    try:
        return RetrievalReport.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise EvaluationError([f"{path}: {err['msg']} at {err['loc']}" for err in e.errors()[:3]]) from e
