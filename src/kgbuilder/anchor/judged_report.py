"""The judged criteria of one build as one report (R93 part c): C3, C4 and C6 on the reviewed verdicts and on
the blind ones, each failure's reach into the QA questions, and the MLflow metrics.

Role in the pipeline: built by `kg anchor-judged` (pipeline/anchor_stages.py) from the committed sheets, their
code sides and verdict files, R75's identity pairs and R90's anchor report; written to `out/` as the run's
artifact and read by the step's results table.
Design: the scores are `anchor/judged.py`'s; this module runs them twice, on the final labels and on the
blind ones (the labels before the lead's review), so a reader sees what the review changed, and maps every
item judged INCORRECT to the questions whose placed targets hold its node: the questions a failure can
mislead. Metric names are prefixed by the criterion (`c4_precision`) and the blind run by `blind_`; a rate
without a denominator logs nothing.
Not here: judging, loading or checking verdict files (validation/anchor_verdicts.py), the stage.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from ..validation.anchor_verdicts import Label, VerdictFile
from ..validation.identity import IdentityScore
from ..validation.interval import Proportion
from .judged import C3Result, C4Result, C6Result, Rates, score_c3, score_c4, score_c6
from .navigation import Arm
from .sheets import CodeSide
from .targets import PlacedTarget

# The blind scores worth logging next to the final ones: the hard rules and the rates they rest on.
BLIND_METRICS = ("c3_hard_passed", "c4_precision", "c4_hard_passed", "c6_anchor_", "c6_layered_")


class JudgedScores(BaseModel):
    c3: C3Result
    c4: C4Result
    c6: C6Result


class JudgedReport(BaseModel):
    dataset: str
    judge_model: str
    final: JudgedScores  # after the lead's review
    blind: JudgedScores  # the blind judges' labels
    # item id -> the QA questions whose placed targets hold the item's node, for every item judged INCORRECT
    impact: dict[str, list[str]]

    def metrics(self) -> dict[str, float]:
        out = _scores(self.final)
        out |= {f"blind_{k}": v for k, v in _scores(self.blind).items() if k.startswith(BLIND_METRICS)}
        out["impacted_questions"] = float(len({q for qs in self.impact.values() for q in qs}))
        return out


def blind_view(file: VerdictFile) -> VerdictFile:
    """The file with each verdict at its blind label. Built without validation: a blind INCORRECT whose
    outliers the lead cleared is still a blind INCORRECT."""
    blind = file.blind()
    verdicts = [v.model_copy(update={"label": blind[v.id]}) for v in file.verdicts]
    return file.model_copy(update={"verdicts": verdicts, "changes": []})


def score_all(
    files: Mapping[str, VerdictFile],
    code: Mapping[str, CodeSide],
    pairs: IdentityScore,
    *,
    min_link_precision: float,
    min_purity: float,
) -> JudgedScores:
    """C3, C4 and C6 of one build; `files` and `code` are keyed "C3", "C4", "C6"."""
    return JudgedScores(
        c3=score_c3(files["C3"], code["C3"], files["C4"], code["C4"], pairs),
        c4=score_c4(files["C4"], code["C4"], min_link_precision),
        c6=score_c6(files["C6"], code["C6"], min_purity),
    )


def impact(
    files: Mapping[str, VerdictFile], code: Mapping[str, CodeSide], placed: Mapping[str, list[PlacedTarget]]
) -> dict[str, list[str]]:
    """Every item judged INCORRECT, with the questions one of whose targets is placed on the item's node(s)
    (the start for C6, the record for C4, the node or group for C3)."""
    questions_of: dict[str, set[str]] = {}
    for qid, targets in placed.items():
        for t in targets:
            for node in t.nodes:
                questions_of.setdefault(node, set()).add(qid)
    out = {}
    for criterion, file in files.items():
        nodes = {i.id: i.node.split(",") for i in code[criterion].items}
        for v in file.verdicts:
            if v.label is Label.INCORRECT:
                out[v.id] = sorted({q for n in nodes[v.id] for q in questions_of.get(n, ())})
    return out


def _scores(s: JudgedScores) -> dict[str, float]:
    out: dict[str, float] = {}
    c3, c4, c6 = s.c3, s.c4, s.c6
    for kind, r in c3.merges.items():
        out |= _rate(f"c3_{kind}_merges", r.accepted)
        out[f"c3_{kind}_wrong_merges"] = float(len(c3.wrong_merges[kind]))
    out |= _rate("c3_splits_apart", c3.splits.accepted)
    out |= {
        "c3_record_wrong_merges": float(len(c3.record_wrong_merges)),
        "c3_wrong_splits": float(len(c3.wrong_splits)),
        "c3_hard_passed": float(c3.hard_passed),
    }
    out |= {f"c3_{k}": v for k, v in c3.identity_pairs.metrics().items()}
    out |= _rate("c4_precision", c4.links.accepted) | _counts("c4", c4.links)
    out |= {
        "c4_precision_strict": c4.links.strict.rate,
        "c4_precision_worst": c4.links.worst_case.rate,
        "c4_cross_scope_confirmed": float(len(c4.cross_scope_confirmed)),
        "c4_hard_passed": float(c4.hard_passed),
    }
    out |= _rate("c4_unlinked_refers", c4.unlinked.accepted)
    for arm in Arm:
        p = c6.purity[arm.value]
        out |= _rate(f"c6_{arm.value}_purity", p["record_individual"].accepted)
        out |= _rate(f"c6_{arm.value}_concept_purity", p["concept"].accepted)
        out[f"c6_{arm.value}_hard_passed"] = float(c6.hard_passed[arm.value])
        out[f"c6_{arm.value}_starts_below"] = float(len(c6.starts_below[arm.value]))
    out |= _rate("c6_only_layered_purity", c6.only_layered.accepted)
    for criterion, flags in (("c4", c4.flags), ("c6", c6.flags)):
        for f in flags:
            out |= {
                f"{criterion}_flag_{f.flag}": float(f.flagged),
                f"{criterion}_flag_{f.flag}_confirmed": float(f.confirmed),
            }
    return {k: v for k, v in out.items() if v is not None}


def _rate(name: str, p: Proportion) -> dict[str, float | None]:
    """A rate with its interval and n; nothing but n when there is nothing to rate."""
    return {name: p.rate, f"{name}_low": p.low, f"{name}_high": p.high, f"{name}_n": float(p.n)}


def _counts(prefix: str, r: Rates) -> dict[str, float]:
    return {f"{prefix}_{label.lower()}": float(n) for label, n in r.counts.items() if label != "VALID"}
