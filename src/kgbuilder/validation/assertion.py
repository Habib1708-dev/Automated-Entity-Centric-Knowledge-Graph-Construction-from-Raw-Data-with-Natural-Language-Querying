"""The assertion of a claim (R77, layered-model Step 7): the gold labels, the judge's matching file, and how
well the graph keeps each claim's truth, modality and condition.

Role in the pipeline: `kg assertion SHEET GOLD VERDICTS`, with no graph. The gold (written by Claude from
the text alone, before any output: `evaluation` skill) lists the claims of every sampled sentence with
their truth (affirmed / negated), modality (actual / possible / conditional) and condition. After a run the
judge matches each claim to the observations of the coverage sheet (coverage_sheet.py) that state its
content, whatever their assertion, and says for each field whether the stored observations keep it. Code
checks the file against the gold and the sheet and computes every rate twice, as the project's two scores:
**kept** (the judge, by meaning: a negation the names carry counts) and **exact** (code: the stored field
equals the gold label, which is what a count over the field needs).
Design: pydantic models with the validators that make each verdict checkable on its own; `_check` ties
the file to its gold and sheet; scoring is pure. Rules: `tests/gold/r77/assertion_rules.md`.
Not here: the sheet (coverage_sheet.py), the coverage estimate (coverage.py), MLflow (pipeline/stages.py).
"""

from pathlib import Path
from typing import Literal, TypeVar, get_args

from pydantic import BaseModel, ValidationError, model_validator

from ..core.errors import EvaluationError
from ..core.text import norm
from ..text.extraction import Modality, Truth
from .coverage_sheet import CoverageSheet, SheetObservation
from .interval import Proportion
from .judge import JudgeMeta

AssertionField = Literal["truth", "modality", "condition"]
FIELDS: tuple[AssertionField, ...] = get_args(AssertionField)
# The groups scored per gold value: each truth and each modality, and the condition of the conditional
# claims (the only ones that have one): (metric name, the field scored, the label grouped on, its value).
_GROUPS: list[tuple[str, AssertionField, str, str]] = [
    *((f"truth_{t}", "truth", "truth", t) for t in get_args(Truth)),
    *((f"modality_{m}", "modality", "modality", m) for m in get_args(Modality)),
    ("condition_conditional", "condition", "modality", "conditional"),
]
M = TypeVar("M", bound=BaseModel)


class GoldClaim(BaseModel):
    """One claim of a sentence with its three labels, written from the text alone."""

    claim: str
    truth: Truth
    modality: Modality
    condition: str | None = None  # the condition's words, verbatim from the sentence; only when conditional
    note: str | None = None  # the labeller's reason for a hard case

    @model_validator(mode="after")
    def _condition_only_when_conditional(self) -> "GoldClaim":
        if (self.condition is not None) != (self.modality == "conditional"):
            raise ValueError(f"claim {self.claim!r}: a condition exactly when the modality is conditional")
        return self


class GoldSentence(BaseModel):
    id: str  # the sample sentence's id
    stratum: str  # how it was drawn: "r68" or "natural" (random), "cue", "named"
    claims: list[GoldClaim] = []


class AssertionGold(BaseModel):
    labeller: dict[str, str | int]
    sentences: list[GoldSentence]


class FieldsKept(BaseModel):
    """The judge's reading of the matched observations: does each keep the claim's label?"""

    truth: bool
    modality: bool
    condition: bool


class MatchedClaim(BaseModel):
    """One gold claim after the run: the observations stating its content, and the fields they keep."""

    claim: str  # a copy of the gold claim, so a reader sees what was matched; checked against the gold
    matched: list[str] = []  # observation ids of the sentence's chunk; empty when none states it
    fields: FieldsKept | None = None
    reason: str

    @model_validator(mode="after")
    def _fields_iff_matched(self) -> "MatchedClaim":
        if bool(self.matched) != (self.fields is not None):
            raise ValueError(f"claim {self.claim!r}: judge the fields exactly when observations are matched")
        return self


class MatchedSentence(BaseModel):
    id: str
    claims: list[MatchedClaim] = []


class AssertionVerdicts(BaseModel):
    """The verdict file. `judge` names the model and the run whose graph the sheet was written from."""

    judge: JudgeMeta
    sheet: str
    gold: str
    sentences: list[MatchedSentence]


class AssertionReport(BaseModel):
    """Per field, over the matched claims: kept (judge) and exact (code), overall and per gold value."""

    claims: int
    matched: Proportion  # claims some observation states, whatever its assertion
    kept: dict[AssertionField, Proportion]
    exact: dict[AssertionField, Proportion]
    # per gold value ("truth_negated", "modality_possible", "condition_conditional", ...), kept / exact
    kept_by_value: dict[str, Proportion]
    exact_by_value: dict[str, Proportion]

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics; every field and value is present, so names stay stable."""
        out: dict[str, float | int | None] = {
            "claims": self.claims,
            "claims_matched": self.matched.k,
            "matched": self.matched.rate,
        }
        for kind, shares in (("kept", self.kept), ("exact", self.exact)):
            for field, share in shares.items():
                out.update(
                    {f"{field}_{kind}": share.rate, f"{field}_{kind}_low": share.low, f"{field}_n": share.n}
                )
        for kind, shares in (("kept", self.kept_by_value), ("exact", self.exact_by_value)):
            for value, share in shares.items():
                out.update({f"{value}_{kind}": share.rate, f"{value}_n": share.n})
        return out


def load_assertion_gold(path: Path) -> AssertionGold:
    return _load(AssertionGold, path)


def load_assertion_verdicts(path: Path) -> AssertionVerdicts:
    return _load(AssertionVerdicts, path)


def _load(model: type[M], path: Path) -> M:
    """The file as `model`, or `EvaluationError` naming every entry that is not checkable on its own."""
    try:
        return model.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise EvaluationError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def _check(sheet: CoverageSheet, gold: AssertionGold, verdicts: AssertionVerdicts) -> None:
    """The file matches every gold sentence once, keeps its claims as written, and cites only observations
    of the sentence's own chunk."""
    by_gold = {s.id: s for s in gold.sentences}
    observations = {s.id: {o.id for o in s.observations} for s in sheet.sentences}
    given = [v.id for v in verdicts.sentences]
    issues = ["a sentence has more than one verdict"] if len(given) != len(set(given)) else []
    if missing := set(by_gold) - set(given):
        issues.append(f"{len(missing)} gold sentences have no verdict (first: {sorted(missing)[:3]})")
    if unknown := set(given) - set(by_gold):
        issues.append(f"{len(unknown)} verdicts are for sentences not in the gold ({sorted(unknown)[:3]})")
    if lost := set(by_gold) - set(observations):
        issues.append(f"{len(lost)} gold sentences are not on the sheet ({sorted(lost)[:3]})")
    for verdict in (v for v in verdicts.sentences if v.id in by_gold):
        if [c.claim for c in verdict.claims] != [c.claim for c in by_gold[verdict.id].claims]:
            issues.append(f"sentence {verdict.id}: the claims differ from the gold's")
        stored = observations.get(verdict.id, set())
        if foreign := [i for c in verdict.claims for i in c.matched if i not in stored]:
            issues.append(f"sentence {verdict.id}: {foreign[:3]} are not observations of its chunk")
    if issues:
        raise EvaluationError(issues)


def _kept(field: AssertionField, verdict: MatchedClaim) -> bool:
    return verdict.fields is not None and getattr(verdict.fields, field)


def _exact(field: AssertionField, claim: GoldClaim, stored: list[SheetObservation]) -> bool:
    """Whether every matched observation's field equals the label. A condition agrees when both are empty,
    or when one holds the other's words (the extractor may copy more or fewer words of the clause)."""
    if field == "truth":
        return all(o.truth == claim.truth for o in stored)
    if field == "modality":
        return all(o.modality == claim.modality for o in stored)
    if claim.condition is None:
        return all(not o.condition for o in stored)
    gold = norm(claim.condition)
    return all(o.condition and (norm(o.condition) in gold or gold in norm(o.condition)) for o in stored)


def score_assertion(
    sheet: CoverageSheet, gold: AssertionGold, verdicts: AssertionVerdicts
) -> AssertionReport:
    """Matched claims, and per field how many keep their label, by the judge and exactly. Raises
    `EvaluationError` when the verdicts do not fit the gold or the sheet (see `_check`)."""
    _check(sheet, gold, verdicts)
    stored = {o.id: o for s in sheet.sentences for o in s.observations}
    matches = {v.id: v.claims for v in verdicts.sentences}
    # (gold claim, its verdict, the observations it matched), for the matched claims only
    pairs = [
        (c, v, [stored[i] for i in v.matched])
        for s in gold.sentences
        for c, v in zip(s.claims, matches[s.id], strict=True)
        if v.matched
    ]
    claims = sum(len(s.claims) for s in gold.sentences)
    kept_by_value, exact_by_value = {}, {}
    for name, field, label, value in _GROUPS:
        group = [p for p in pairs if getattr(p[0], label) == value]
        kept_by_value[name] = Proportion.of(sum(_kept(field, v) for _, v, _ in group), len(group))
        exact_by_value[name] = Proportion.of(sum(_exact(field, c, o) for c, _, o in group), len(group))
    return AssertionReport(
        claims=claims,
        matched=Proportion.of(len(pairs), claims),
        kept={f: Proportion.of(sum(_kept(f, v) for _, v, _ in pairs), len(pairs)) for f in FIELDS},
        exact={f: Proportion.of(sum(_exact(f, c, o) for c, _, o in pairs), len(pairs)) for f in FIELDS},
        kept_by_value=kept_by_value,
        exact_by_value=exact_by_value,
    )
