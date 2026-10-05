"""Paired comparison of two question-answering systems on the same questions (R73): the questions only one
of them answered right, and an exact McNemar test on those, overall and per question type.

Role in the pipeline: `kg qa-compare` reads two outcome files of `kg qa-score` (qa.py `QAOutcome`) and
reports here whether one system beats the other beyond one sample's variation, which every later step of
the layered-model arm must show (task file, Step 3).
Design: pure arithmetic over outcome rows. Two systems answering the same 38 questions are not two
independent samples: the questions both get right or both get wrong say nothing about which is better,
so only the discordant questions are tested, and two overlapping Wilson intervals are no verdict.
Not here: scoring one system (qa.py), reading files and MLflow (pipeline/qa_stages.py).
"""

import math
from collections.abc import Sequence

from pydantic import BaseModel

from ..core.errors import EvaluationError
from .qa import QAOutcome
from .qa_gold import QuestionType

# The significance level of every paired comparison in a step report (task file, Step 3): "beyond one
# sample's variation" means p below it.
ALPHA = 0.05


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value for `only_a` questions right in A only and `only_b` right in B only.

    Under "no difference" each discordant question is equally likely to fall either way, so the smaller
    count follows Binomial(n, 1/2); the p-value doubles its lower tail (capped at 1). With no discordant
    question there is no evidence either way: p = 1. Exact rather than the chi-square form, because a
    step changes a handful of questions, where the chi-square approximation is unreliable.
    Raises `ValueError` for a negative count.
    """
    if only_a < 0 or only_b < 0:
        raise ValueError(f"discordant counts cannot be negative, got {only_a} and {only_b}")
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2**n
    return min(1.0, 2 * tail)


class PairedComparison(BaseModel):
    """Two systems on the same questions: each one's right answers, the discordant counts, and p."""

    questions: int
    a_correct: int
    b_correct: int
    only_a: int  # right in A, wrong in B
    only_b: int  # right in B, wrong in A
    p_value: float

    @property
    def differs(self) -> bool:
        """Whether the two systems differ beyond one sample's variation (p < ALPHA)."""
        return self.p_value < ALPHA


class PairedReport(BaseModel):
    """The comparison of system `a` against system `b`, overall and for every question type."""

    a: str
    b: str
    overall: PairedComparison
    by_type: dict[QuestionType, PairedComparison]

    def metrics(self) -> dict[str, float | int]:
        """Flat MLflow metrics: the counts and p overall and per type (stable names, every type present)."""
        out: dict[str, float | int] = {}
        for suffix, c in [("", self.overall), *((f"_{t.value}", c) for t, c in self.by_type.items())]:
            out.update(
                {
                    f"questions{suffix}": c.questions,
                    f"a_correct{suffix}": c.a_correct,
                    f"b_correct{suffix}": c.b_correct,
                    f"only_a{suffix}": c.only_a,
                    f"only_b{suffix}": c.only_b,
                    f"p_value{suffix}": c.p_value,
                }
            )
        return out


def compare_outcomes(
    a: Sequence[QAOutcome], b: Sequence[QAOutcome], a_name: str = "a", b_name: str = "b"
) -> PairedReport:
    """Compare two outcome files question by question.

    Raises `EvaluationError` when the two do not cover the same questions once each with the same types
    (then they are not answers to one gold file), or when an outcome is unjudged: a free-text answer
    without a verdict would silently drop out of one side.
    """
    _check(a, b)
    right_b = {o.question_id: bool(o.correct) for o in b}
    pairs = [(o.type, bool(o.correct), right_b[o.question_id]) for o in a]
    return PairedReport(
        a=a_name,
        b=b_name,
        overall=_compare([(x, y) for _, x, y in pairs]),
        by_type={t: _compare([(x, y) for qt, x, y in pairs if qt == t]) for t in QuestionType},
    )


def _compare(pairs: list[tuple[bool, bool]]) -> PairedComparison:
    only_a = sum(x and not y for x, y in pairs)
    only_b = sum(y and not x for x, y in pairs)
    return PairedComparison(
        questions=len(pairs),
        a_correct=sum(x for x, _ in pairs),
        b_correct=sum(y for _, y in pairs),
        only_a=only_a,
        only_b=only_b,
        p_value=mcnemar_exact(only_a, only_b),
    )


def _check(a: Sequence[QAOutcome], b: Sequence[QAOutcome]) -> None:
    issues = []
    for name, side in (("a", a), ("b", b)):
        ids = [o.question_id for o in side]
        if len(ids) != len(set(ids)):
            issues.append(f"outcomes {name} hold a question more than once")
        if unjudged := sorted(o.question_id for o in side if o.correct is None):
            issues.append(f"outcomes {name} have {len(unjudged)} unjudged answers (first: {unjudged[:3]})")
    type_a = {o.question_id: o.type for o in a}
    type_b = {o.question_id: o.type for o in b}
    if only := sorted(type_a.keys() ^ type_b.keys()):
        issues.append(f"{len(only)} questions are in one outcome file only (first: {only[:3]})")
    if retyped := sorted(q for q in type_a.keys() & type_b.keys() if type_a[q] != type_b[q]):
        issues.append(f"{len(retyped)} questions have different types (first: {retyped[:3]})")
    if issues:
        raise EvaluationError(issues)
