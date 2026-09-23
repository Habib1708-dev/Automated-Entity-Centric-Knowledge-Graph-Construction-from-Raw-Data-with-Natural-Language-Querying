"""LLM-as-a-judge scoring: the judge sheet (what still needs a verdict after exact matching) and the
validated precision/recall computed from the judge's verdicts.

Role in the pipeline: `kg eval gold.json` writes `out/judge_sheet.json`; the judge (Claude in the Claude
Code session, never the model that built the graph: see the `evaluation` skill) fills a verdict per
unsettled fact and per unfound gold triple into `out/judge_verdicts.json`; `kg eval gold.json --verdicts
out/judge_verdicts.json` turns them into metrics. Scheme: gold for recall, the review text for precision,
each with an exact-match shortcut so the judge only sees what string matching could not settle. The
sheet and the verdict file also carry the entity-resolution part (er.py), optional in the verdict file so
that verdict files written before it existed still score.
Design: pure functions over `StoredFact` lists, no LLM call and no graph access here. Code decides what
needs judging and computes every number; the judge supplies verdicts only ("the LLM proposes, code
decides"). Not here: exact-match scoring (evaluate.py) and MLflow logging (pipeline/stages.py).
"""

import hashlib
import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, model_validator

from ..core.errors import EvaluationError
from ..core.text import norm
from .checks.base import StoredFact
from .er import ErSheet, PairVerdict
from .gold import GoldTriple, doc_of, in_scope, matches


class Verdict(StrEnum):
    """What the judge says about one extracted fact, judged against its own review's text."""

    SUPPORTED = "SUPPORTED"  # the text states it, with this relation
    UNSUPPORTED = "UNSUPPORTED"  # it does not; `reason_code` says why
    AMBIGUOUS = "AMBIGUOUS"  # the text can honestly be read both ways; leaves the precision denominator


class UnsupportedReason(StrEnum):
    """Diagnostic for `UNSUPPORTED`: no effect on the score, tells what kind of errors the extractor makes."""

    NOT_IN_TEXT = "not_in_text"  # true or not, the review does not say it
    WRONG_RELATION = "wrong_relation"  # both entities are real, the relation is not what the text supports
    WRONG_ENTITY = "wrong_entity"  # subject or object misread
    CONTRADICTED = "contradicted"  # the text says the opposite


class SheetFact(BaseModel):
    """An in-scope fact as the judge sees it; `gold_index` is set when exact matching settled it."""

    id: str
    doc_id: str
    subject: str
    predicate: str
    object: str
    subject_type: str
    object_type: str
    evidence: str | None  # the quote the extractor claimed; the judge checks it against the review
    gold_index: int | None = None

    @property
    def needs_verdict(self) -> bool:
        return self.gold_index is None


class SheetGold(BaseModel):
    """One gold triple with whether exact matching found it; the judge looks only at the unfound ones."""

    index: int
    subject: str
    predicate: str
    object: str
    doc_id: str | None
    evidence: str | None
    found: bool


class JudgeSheet(BaseModel):
    facts: list[SheetFact]
    gold: list[SheetGold]
    er: ErSheet | None = None  # set when the gold file has `er_pairs`

    def to_judge(self) -> list[SheetFact]:
        return [f for f in self.facts if f.needs_verdict]

    def gold_to_find(self) -> list[SheetGold]:
        return [g for g in self.gold if not g.found]


class FactVerdict(BaseModel):
    """The judge's verdict on one sheet fact. The triple fields are optional copies for a human reader."""

    id: str
    verdict: Verdict
    reason: str
    evidence: str | None = None  # required for SUPPORTED: the sentence of the review that states the fact
    reason_code: UnsupportedReason | None = None  # required for UNSUPPORTED
    vague: bool = False  # SUPPORTED but too unspecific to answer any goal question
    gold_index: int | None = None  # SUPPORTED and, by meaning, the same claim as this gold triple
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "FactVerdict":
        # every verdict kind has exactly the fields that make it checkable by a reader
        if self.verdict == Verdict.SUPPORTED and not self.evidence:
            raise ValueError(f"fact {self.id}: SUPPORTED needs the evidence sentence")
        if self.verdict == Verdict.UNSUPPORTED and self.reason_code is None:
            raise ValueError(f"fact {self.id}: UNSUPPORTED needs a reason_code")
        if self.verdict != Verdict.UNSUPPORTED and self.reason_code is not None:
            raise ValueError(f"fact {self.id}: reason_code is only for UNSUPPORTED")
        if self.verdict != Verdict.SUPPORTED and (self.vague or self.gold_index is not None):
            raise ValueError(f"fact {self.id}: vague and gold_index are only for SUPPORTED")
        return self


class GoldVerdict(BaseModel):
    """For a gold triple exact matching did not find: the sheet fact that states it by meaning, or None."""

    gold_index: int
    matched_fact: str | None
    reason: str


class JudgeMeta(BaseModel):
    model: str
    date: str
    run_id: str | None = None  # MLflow run whose output was judged; informative, the fact ids do the checking
    git_sha: str | None = None


class Verdicts(BaseModel):
    """The verdict file. `gold` names the gold file the sheet was built from."""

    judge: JudgeMeta
    gold: str
    facts: list[FactVerdict] = []
    recall: list[GoldVerdict] = []
    # None: this file does not judge entity resolution (every verdict file before R33); then no
    # `er_accuracy_valid` is computed, instead of refusing the file
    er: list[PairVerdict] | None = None


class JudgeReport(BaseModel):
    """Validated scores with the counts they came from, so a reader can recompute every rate."""

    facts_in_scope: int
    exact_matched: int
    judged: int
    supported: int
    unsupported: int
    ambiguous: int
    vague: int
    gold_total: int
    gold_found_exact: int
    gold_found_judge: int
    gold_corrections: int
    unsupported_by_reason: dict[UnsupportedReason, int]
    precision_validated: float
    recall_validated: float
    f1_validated: float
    ambiguous_rate: float
    vague_rate: float

    def metrics(self) -> dict[str, float]:
        """Flat MLflow metrics; every reason count is present (0 when unused) so names stay stable."""
        out = {
            "precision_validated": self.precision_validated,
            "recall_validated": self.recall_validated,
            "f1_validated": self.f1_validated,
            "ambiguous_rate": self.ambiguous_rate,
            "vague_rate": self.vague_rate,
            "judged_facts": self.judged,
            "gold_corrections": self.gold_corrections,
        }
        out.update(
            {f"unsupported_{reason.value}": count for reason, count in self.unsupported_by_reason.items()}
        )
        return out


def fact_id(fact: StoredFact) -> str:
    """Stable id of a stored fact: same graph, same id; a rebuilt graph with other names gives other ids,
    which is how a verdict file written for another graph is detected."""
    key = "|".join(
        [fact.chunk_id or "", fact.predicate, norm(fact.subject_names[0]), norm(fact.object_names[0])]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def build_sheet(facts: list[StoredFact], gold: list[GoldTriple]) -> JudgeSheet:
    """Apply the exact-match shortcut and list what is left for the judge."""
    scoped = in_scope(facts, gold)
    sheet_facts = [
        SheetFact(
            id=fact_id(f),
            doc_id=doc_of(f) or "",
            subject=f.subject_names[0],
            predicate=f.predicate,
            object=f.object_names[0],
            subject_type=f.subject_type,
            object_type=f.object_type,
            evidence=f.evidence,
            gold_index=next((i for i, g in enumerate(gold) if matches(g, f)), None),
        )
        for f in scoped
    ]
    sheet_gold = [
        SheetGold(
            index=i,
            subject=g.subject,
            predicate=g.predicate,
            object=g.object,
            doc_id=g.doc_id,
            evidence=g.evidence,
            found=any(matches(g, f) for f in scoped),
        )
        for i, g in enumerate(gold)
    ]
    return JudgeSheet(facts=sheet_facts, gold=sheet_gold)


def load_verdicts(path: Path) -> Verdicts:
    return Verdicts.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _check_coverage(sheet: JudgeSheet, verdicts: Verdicts) -> None:
    """The verdict file must cover exactly what the sheet asks for: nothing missing, nothing stale."""
    issues: list[str] = []
    needed = {f.id for f in sheet.to_judge()}
    given = [v.id for v in verdicts.facts]
    if len(given) != len(set(given)):
        issues.append("a fact has more than one verdict")
    if missing := needed - set(given):
        issues.append(f"{len(missing)} facts have no verdict (first: {sorted(missing)[:3]})")
    if unknown := set(given) - needed:
        issues.append(
            f"{len(unknown)} verdicts are for facts not on the sheet (first: {sorted(unknown)[:3]})"
        )

    gold_needed = {g.index for g in sheet.gold_to_find()}
    gold_given = [v.gold_index for v in verdicts.recall]
    if len(gold_given) != len(set(gold_given)):
        issues.append("a gold triple has more than one verdict")
    if missing := gold_needed - set(gold_given):
        issues.append(f"{len(missing)} unfound gold triples have no verdict (indices {sorted(missing)[:3]})")
    if unknown := set(gold_given) - gold_needed:
        issues.append(
            f"gold verdicts for triples exact matching already found (indices {sorted(unknown)[:3]})"
        )
    all_ids = {f.id for f in sheet.facts}
    if dangling := [
        v.matched_fact for v in verdicts.recall if v.matched_fact and v.matched_fact not in all_ids
    ]:
        issues.append(f"matched_fact ids not on the sheet: {dangling[:3]}")
    if issues:
        raise EvaluationError(issues)


def score_verdicts(sheet: JudgeSheet, verdicts: Verdicts) -> JudgeReport:
    """Validated precision and recall. Exact matches count as supported without a verdict."""
    _check_coverage(sheet, verdicts)
    by_verdict = {kind: [v for v in verdicts.facts if v.verdict == kind] for kind in Verdict}
    exact = len(sheet.facts) - len(sheet.to_judge())
    supported, unsupported = len(by_verdict[Verdict.SUPPORTED]), len(by_verdict[Verdict.UNSUPPORTED])
    ambiguous = len(by_verdict[Verdict.AMBIGUOUS])
    vague = sum(v.vague for v in by_verdict[Verdict.SUPPORTED])
    found_exact = sum(g.found for g in sheet.gold)
    found_judge = sum(v.matched_fact is not None for v in verdicts.recall)

    # AMBIGUOUS leaves the denominator; an empty denominator scores 1.0 (nothing claimed, nothing wrong)
    judgeable = exact + supported + unsupported
    precision = (exact + supported) / judgeable if judgeable else 1.0
    recall = (found_exact + found_judge) / len(sheet.gold) if sheet.gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return JudgeReport(
        facts_in_scope=len(sheet.facts),
        exact_matched=exact,
        judged=len(verdicts.facts),
        supported=supported,
        unsupported=unsupported,
        ambiguous=ambiguous,
        vague=vague,
        gold_total=len(sheet.gold),
        gold_found_exact=found_exact,
        gold_found_judge=found_judge,
        gold_corrections=sum(v.gold_index is None for v in by_verdict[Verdict.SUPPORTED]),
        unsupported_by_reason={
            reason: sum(v.reason_code == reason for v in by_verdict[Verdict.UNSUPPORTED])
            for reason in UnsupportedReason
        },
        precision_validated=precision,
        recall_validated=recall,
        f1_validated=f1,
        ambiguous_rate=ambiguous / len(verdicts.facts) if verdicts.facts else 0.0,
        vague_rate=vague / (exact + supported) if exact + supported else 0.0,
    )
