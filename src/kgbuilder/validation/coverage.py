"""Coverage of what the text states (R68): the judge's verdict file for a coverage sheet, and the numbers
code computes from it.

Role in the pipeline: `kg coverage SHEET VERDICTS`, with no graph. The judge (Claude in the session, never
the model that built the graph: `evaluation` skill) first lists, from the text alone, the claims of every
sampled sentence a question could need; then, with the sheet (coverage_sheet.py) open, it says for each
claim which stored items state it, or the first of ten ordered causes why none does. Code checks the file
against the sheet and computes every rate: how much a query can count or filter on (`coverage`) and how
much it can at least locate in the text (`reachable`).
Design: the validators make every verdict checkable on its own; `_check` ties the file to its sheet (every
sentence once, every cited id stored in that sentence's chunk, every fact type in the schema); scoring is
pure. The causes are the ten of the layered-model direction (section 7), in its order.
Not here: the sheet (coverage_sheet.py), the sample (sentences.py), MLflow (pipeline/stages.py).
"""

from enum import StrEnum
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ValidationError, model_validator

from ..core.errors import EvaluationError
from ..text.extraction import Polarity
from .coverage_sheet import CoverageSheet
from .interval import Proportion
from .judge import JudgeMeta


class Cause(StrEnum):
    """Why nothing stored holds a claim, in the order the judge tries the causes.

    The first cause that fits is the one given, so every miss counts once. The model's misses come
    first: a miss the graph's shape could have held is never blamed on the shape.
    """

    EXTRACTION = "extraction"  # 1 the shape and the schema could hold it; the model did not extract it
    NO_SCHEMA_TYPE = "no_schema_type"  # 2 no fact type of the schema can hold it
    IDENTITY = "identity"  # 3 stored, but a merge made its subject or object another thing
    ATTACHMENT = "attachment"  # 4 stored on a thing it is not about, or what it is about has no node
    ASSERTION = "assertion"  # 5 negated, possible or conditional, and stored as stated
    ATTRIBUTION = "attribution"  # 6 someone other than the writer says it; stored as the writer's claim
    ROLE = "role"  # 7 it needs a participant beyond subject and object (a condition, an agent, a place)
    EVENT_STRUCTURE = "event_structure"  # 8 it links occurrences (order, repetition) no observation links
    CONCEPT = "concept"  # 9 what it is about is typed as a kind that cannot carry it
    OTHER = "other"  # 10


class ClaimVerdict(BaseModel):
    """One claim of a sentence: listed from the text first, then matched. Either stored items state it
    (`covered_by`), or a cause says why nothing does; never both, never neither."""

    claim: str  # the claim in a few words of the sentence
    polarity: Polarity  # its tone toward what it is about
    about: str | None  # the thing of the sentence's chunk it is about; None: something none of them is
    covered_by: list[str] = []  # observation ids and record fields (`<thing>.<field>`) that state it
    cause: Cause | None = None
    # for a miss only: the schema's predicate that could hold it (none for NO_SCHEMA_TYPE: that is the
    # cause), and stored items that hold it in part, the evidence for causes such as ASSERTION
    schema_type: str | None = None
    near: list[str] = []
    reason: str

    @model_validator(mode="after")
    def _consistent(self) -> "ClaimVerdict":
        if bool(self.covered_by) == (self.cause is not None):
            raise ValueError(f"claim {self.claim!r}: give covered_by or a cause, exactly one of them")
        if self.covered_by and (self.schema_type is not None or self.near):
            raise ValueError(f"claim {self.claim!r}: schema_type and near are only for a miss")
        # the causes are tried in order, so any cause after NO_SCHEMA_TYPE implies a type existed
        if self.cause is not None and (self.schema_type is None) != (self.cause == Cause.NO_SCHEMA_TYPE):
            raise ValueError(f"claim {self.claim!r}: a miss names its schema type, except no_schema_type")
        return self


class SentenceVerdict(BaseModel):
    id: str  # the sheet sentence's id
    claims: list[ClaimVerdict] = []  # none when the sentence states nothing a question could need


class CoverageVerdicts(BaseModel):
    """The verdict file. `judge` names the model and the run whose graph the sheet was written from."""

    judge: JudgeMeta
    sheet: str  # the coverage sheet judged
    sentences: list[SentenceVerdict]


class CoverageReport(BaseModel):
    """The estimate, with the counts every rate comes from."""

    sentences: int
    sentences_with_claims: int
    claims: int
    coverage: Proportion  # claims something stored states
    reachable: Proportion  # stored, or about a thing the sentence's chunk hangs on (the text is found)
    covered_by_record_only: int  # stated by a record field and by no observation
    coverage_in_schema: Proportion  # over the claims the schema or a record had a place for
    by_polarity: dict[str, Proportion]
    missed: dict[Cause, int]

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics; every polarity and every cause is present, so names stay stable."""
        out: dict[str, float | int | None] = {
            "sentences": self.sentences,
            "sentences_with_claims": self.sentences_with_claims,
            "claims": self.claims,
            "claims_covered": self.coverage.k,
            "claims_covered_by_record_only": self.covered_by_record_only,
        }
        for name, share in (
            ("coverage", self.coverage),
            ("reachable", self.reachable),
            ("coverage_in_schema", self.coverage_in_schema),
        ):
            out.update({name: share.rate, f"{name}_low": share.low, f"{name}_high": share.high})
        for tone, share in self.by_polarity.items():
            out.update({f"coverage_{tone}": share.rate, f"claims_{tone}": share.n})
        out.update({f"missed_{cause.value}": count for cause, count in self.missed.items()})
        return out


def load_coverage_verdicts(path: Path) -> CoverageVerdicts:
    """The verdict file, or `EvaluationError` naming every verdict that is not checkable on its own."""
    try:
        return CoverageVerdicts.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise EvaluationError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def _check(sheet: CoverageSheet, verdicts: CoverageVerdicts) -> None:
    """The file judges every sentence of the sheet once and cites only what the sheet shows."""
    given = [v.id for v in verdicts.sentences]
    needed = {s.id for s in sheet.sentences}
    issues = ["a sentence has more than one verdict"] if len(given) != len(set(given)) else []
    if missing := needed - set(given):
        issues.append(f"{len(missing)} sentences have no verdict (first: {sorted(missing)[:3]})")
    if unknown := set(given) - needed:
        issues.append(f"{len(unknown)} verdicts are for sentences not on the sheet ({sorted(unknown)[:3]})")
    by_id = {s.id: s for s in sheet.sentences}
    predicates = {f.predicate for f in sheet.text_schema.fact_types}
    for verdict in (v for v in verdicts.sentences if v.id in by_id):
        sentence = by_id[verdict.id]
        items, things = sentence.item_ids(), {t.name for t in sentence.things}
        for c in verdict.claims:
            if foreign := [i for i in [*c.covered_by, *c.near] if i not in items]:
                issues.append(f"sentence {verdict.id}: {foreign[:3]} are not stored for its chunk")
            if c.about is not None and c.about not in things:
                issues.append(f"sentence {verdict.id}: {c.about!r} is not a thing its chunk hangs on")
            if c.schema_type is not None and c.schema_type not in predicates:
                issues.append(f"sentence {verdict.id}: {c.schema_type!r} is no fact type of the schema")
    if issues:
        raise EvaluationError(issues)


def score_coverage(sheet: CoverageSheet, verdicts: CoverageVerdicts) -> CoverageReport:
    """Coverage and reachability of the judged claims, by polarity and schema place, and the misses by
    cause. Raises `EvaluationError` when the verdicts do not fit the sheet (see `_check`)."""
    _check(sheet, verdicts)
    observation_ids = {o.id for s in sheet.sentences for o in s.observations}
    claims = [c for v in verdicts.sentences for c in v.claims]
    covered = [c for c in claims if c.covered_by]
    missed = {cause: sum(c.cause == cause for c in claims) for cause in Cause}
    tones = get_args(Polarity)
    return CoverageReport(
        sentences=len(sheet.sentences),
        sentences_with_claims=sum(bool(v.claims) for v in verdicts.sentences),
        claims=len(claims),
        coverage=Proportion.of(len(covered), len(claims)),
        reachable=Proportion.of(sum(bool(c.covered_by) or c.about is not None for c in claims), len(claims)),
        covered_by_record_only=sum(not observation_ids.intersection(c.covered_by) for c in covered),
        # every claim but a NO_SCHEMA_TYPE miss had a place: covered ones by definition, misses by their type
        coverage_in_schema=Proportion.of(len(covered), len(claims) - missed[Cause.NO_SCHEMA_TYPE]),
        by_polarity={
            tone: Proportion.of(
                sum(c.polarity == tone for c in covered), sum(c.polarity == tone for c in claims)
            )
            for tone in tones
        },
        missed=missed,
    )
