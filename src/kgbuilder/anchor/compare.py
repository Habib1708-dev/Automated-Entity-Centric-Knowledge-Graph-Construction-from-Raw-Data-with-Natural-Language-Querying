"""The arms of the anchor-graph evaluation compared question by question (R92).

Role in the pipeline: in `kg anchor-compare` (pipeline/anchor_stages.py), after `kg anchor-eval` wrote the
reports of arm A (anchor) and arm B (layered) and the stage computed arm C (vector retrieval, vector.py).
The direction (section 7.3) pairs A with B on every criterion with per-question outcomes and A with C on
C5, with McNemar's exact test (`validation/paired.mcnemar_exact`).
  C5  a question's outcome is "every gold chunk within the budget" (`QuestionReach.complete`), per start
      mode and budget
  C8  a multi-hop question's outcome is "every gold connection reached"
The per-question outcomes are kept beside the counts, so a reviewer can name the questions that differ.
Design: pure functions over the reports' models. Two sides must hold the same questions, else
`EvaluationError`: a question missing on one side would drop out silently.
Not here: computing any criterion (criteria.py, vector.py).
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..core.errors import EvaluationError
from ..validation.paired import PairedComparison, mcnemar_exact
from .criteria import Connectivity, EvidenceReach
from .report import START_MODES, AnchorReport
from .vector import Ranked


class Paired(BaseModel):
    """One comparison: which side is which, the counts and p, and the questions only one side got."""

    a: str
    b: str
    result: PairedComparison
    only_a: list[str]
    only_b: list[str]


def pair(a: Mapping[str, bool], b: Mapping[str, bool], a_name: str, b_name: str) -> Paired:
    """McNemar on two outcome maps (question id -> success) over the same questions."""
    if set(a) != set(b):
        raise EvaluationError([f"{a_name} and {b_name} differ in questions: {sorted(set(a) ^ set(b))}"])
    only_a = sorted(q for q in a if a[q] and not b[q])
    only_b = sorted(q for q in a if b[q] and not a[q])
    return Paired(
        a=a_name,
        b=b_name,
        result=PairedComparison(
            questions=len(a),
            a_correct=sum(a.values()),
            b_correct=sum(b.values()),
            only_a=len(only_a),
            only_b=len(only_b),
            p_value=mcnemar_exact(len(only_a), len(only_b)),
        ),
        only_a=only_a,
        only_b=only_b,
    )


def complete_within(reach: EvidenceReach, k: int) -> dict[str, bool]:
    return {q.question: q.complete(k) for q in reach.questions}


def all_connected(c: Connectivity) -> dict[str, bool]:
    return {q.question: not q.unreached for q in c.questions}


class ArmComparison(BaseModel):
    """Every pairing of one dataset, by name (`c5_gold_start_complete_at_5` for A against B,
    `c5_end_to_end_vs_vector_complete_at_10` for A against C, `c8_all_connections`), and arm C's C5 with
    its rankings (the best chunks of each question and their cosine)."""

    vector: EvidenceReach
    rankings: dict[str, list[Ranked]]
    pairs: dict[str, Paired]

    def metrics(self) -> dict[str, float]:
        out: dict[str, float] = {"c5_vector_questions": float(len(self.vector.questions))}
        out |= {
            f"c5_vector_recall_at_{k}": p.rate for k, p in self.vector.recall_at.items() if p.rate is not None
        }
        out |= {
            f"c5_vector_complete_at_{k}": p.rate
            for k, p in self.vector.complete_at.items()
            if p.rate is not None
        }
        for name, p in self.pairs.items():
            out |= {
                f"{name}_a": float(p.result.a_correct),
                f"{name}_b": float(p.result.b_correct),
                f"{name}_only_a": float(p.result.only_a),
                f"{name}_only_b": float(p.result.only_b),
                f"{name}_p_value": p.result.p_value,
            }
        return out


def compare_arms(
    anchor: AnchorReport,
    layered: AnchorReport,
    vector: EvidenceReach,
    rankings: Mapping[str, Sequence[Ranked]],
    budgets: Sequence[int],
) -> ArmComparison:
    """A against B on C5 (both start modes) and C8, and A against C on C5, at every budget."""
    pairs = {}
    for mode in START_MODES:
        for k in budgets:
            pairs[f"c5_{mode}_complete_at_{k}"] = pair(
                complete_within(anchor.reach[mode], k),
                complete_within(layered.reach[mode], k),
                "anchor",
                "layered",
            )
            pairs[f"c5_{mode}_vs_vector_complete_at_{k}"] = pair(
                complete_within(anchor.reach[mode], k), complete_within(vector, k), "anchor", "vector"
            )
    pairs["c8_all_connections"] = pair(
        all_connected(anchor.connectivity), all_connected(layered.connectivity), "anchor", "layered"
    )
    return ArmComparison(vector=vector, rankings={q: list(r) for q, r in rankings.items()}, pairs=pairs)
