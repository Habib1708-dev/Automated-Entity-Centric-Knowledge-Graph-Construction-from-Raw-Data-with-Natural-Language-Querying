"""The groundedness of LLM node summaries (R124b, representation B): the sample the judge reads, the verdict
file, and the counts code computes from it.

Role in the pipeline: `kg summary-sheet` (pipeline/summary_stages.py) draws a seeded, stratified sample of a
summary set and writes it as a sheet: each node's numbered facts, exactly as its prompt showed them, and its
summary. Claude judges the sheet in the Claude Code session (the `evaluation` skill) and writes a verdict
file; `kg summary-judged` refuses a file that does not answer the sheet exactly, then counts.
Design: verdicts are data, counts are code ("the LLM proposes, code decides"). A summary is grounded when the
judge finds nothing; each finding names its fault, quotes the summary verbatim and names the fact it
contradicts or lacks, so a reader can check it. The strata put the nodes whose facts carry a qualified claim
apart, because only there can a denied, hedged or conditional fact be written as plain fact.
Not here: writing or checking summaries (hybrid/summaries.py), reading the graph (the stage).
"""

import json
import random
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator

from ..core.errors import EvaluationError
from .interval import Proportion

# The sample, fixed in the roadmap (R124b) before any sample was drawn: 10 summaries per stratum (all of a
# smaller one), seed 124, so every machine draws the same nodes and a reader can check which were judged.
SAMPLE_SEED = 124
PER_STRATUM = 10
STRATA = ("record_qualified", "record_plain", "individual_qualified", "individual_plain", "concept")

Fault = Literal["unsupported", "polarity_flip", "identity_confusion"]
FAULTS: tuple[Fault, ...] = ("unsupported", "polarity_flip", "identity_confusion")


class Candidate(BaseModel):
    """One summary that may be sampled, with what the judge needs to judge it."""

    ref: str
    kind: Literal["record", "individual", "concept"]
    qualified: bool  # a fact of the node carries a denied, hedged, conditional or disagreeing claim
    title: str
    label: str
    facts: list[str]  # "F1. ..." as the summary's prompt numbered them
    summary: str
    fallback: bool  # the summary is the template card (the code check refused the model twice)

    @property
    def stratum(self) -> str:
        if self.kind == "concept":
            return "concept"
        return f"{self.kind}_{'qualified' if self.qualified else 'plain'}"


class SummarySheet(BaseModel):
    """The sample the judge reads, and where it came from."""

    source: str  # the units file the summaries were read from
    source_hash: str
    seed: int
    per_stratum: int
    population: dict[str, int]  # summaries per stratum before sampling
    rows: list[Candidate]


class Finding(BaseModel):
    """One thing a summary says that its facts do not support."""

    fault: Fault
    quote: str  # verbatim from the summary
    fact: str = ""  # the fact id it contradicts or lacks; "" when no fact is about it
    reason: str

    @model_validator(mode="after")
    def _complete(self) -> "Finding":
        if not self.quote.strip() or not self.reason.strip():
            raise ValueError("a finding needs a quote and a reason")
        if self.fault == "polarity_flip" and not self.fact:
            raise ValueError("a polarity_flip names the fact whose qualifier or direction it loses")
        return self


class SummaryVerdict(BaseModel):
    """The judge's verdict on one sampled summary: grounded when it has no finding."""

    ref: str
    reason: str  # one line: why it is grounded, or the gist of the findings
    findings: list[Finding] = []


class JudgeInfo(BaseModel):
    model: str
    date: str
    sheet_hash: str  # the sheet judged: a verdict file of another sample is refused


class SummaryVerdicts(BaseModel):
    """The verdict file."""

    judge: JudgeInfo
    verdicts: list[SummaryVerdict]


class Groundedness(BaseModel):
    """What code counts from a checked verdict file."""

    judge_model: str
    grounded: Proportion  # summaries without a finding
    by_fault: dict[str, Proportion]  # summaries with at least one finding of the fault
    findings: dict[str, int]  # findings per fault
    by_stratum: dict[str, Proportion]  # grounded summaries per stratum

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics: each rate with its interval and n."""
        out: dict[str, float | int | None] = {}
        rates = {"grounded": self.grounded, **{f"fault_{f}": p for f, p in self.by_fault.items()}}
        rates |= {f"grounded_{s}": p for s, p in self.by_stratum.items()}
        for name, p in rates.items():
            out |= {name: p.rate, f"{name}_low": p.low, f"{name}_high": p.high, f"{name}_n": p.n}
        return out | {f"findings_{f}": n for f, n in self.findings.items()}


def draw_sheet(candidates: list[Candidate], source: str, source_hash: str) -> SummarySheet:
    """The seeded sample: `PER_STRATUM` summaries of each stratum (all of a smaller one), in stratum order
    and, within one, in the candidates' order."""
    rng = random.Random(SAMPLE_SEED)
    by_stratum = {s: [c for c in candidates if c.stratum == s] for s in STRATA}
    rows = []
    for stratum in STRATA:
        pool = by_stratum[stratum]
        picked = set(rng.sample(range(len(pool)), min(PER_STRATUM, len(pool))))
        rows += [c for i, c in enumerate(pool) if i in picked]
    return SummarySheet(
        source=source,
        source_hash=source_hash,
        seed=SAMPLE_SEED,
        per_stratum=PER_STRATUM,
        population={s: len(pool) for s, pool in by_stratum.items()},
        rows=rows,
    )


def load_verdicts(path: Path, sheet: SummarySheet, sheet_hash: str) -> SummaryVerdicts:
    """The verdict file at `path`, checked against the sheet. Raises `EvaluationError` naming every issue:
    a file of another sheet, a sampled node judged never or twice, a node the sheet lacks, a quote the summary
    does not hold, a fact id the node lacks."""
    try:
        verdicts = SummaryVerdicts.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        raise EvaluationError([f"{path} is not a verdict file: {e}"]) from e
    rows = {r.ref: r for r in sheet.rows}
    issues = []
    if verdicts.judge.sheet_hash != sheet_hash:
        issues.append(f"the verdicts judge sheet {verdicts.judge.sheet_hash}, not {sheet_hash}")
    counts = Counter(v.ref for v in verdicts.verdicts)
    issues += [f"{ref} is judged {n} times" for ref, n in counts.items() if n > 1]
    issues += [f"{ref} is not on the sheet" for ref in counts if ref not in rows]
    issues += [f"{ref} is not judged" for ref in rows if ref not in counts]
    for v in verdicts.verdicts:
        row = rows.get(v.ref)
        if row is not None:
            issues += _finding_issues(v, row)
    if issues:
        raise EvaluationError(issues)
    return verdicts


def _finding_issues(verdict: SummaryVerdict, row: Candidate) -> list[str]:
    ids = {line.split(".", 1)[0] for line in row.facts}  # "F3. ..." -> "F3"
    issues = []
    for f in verdict.findings:
        if f.quote not in row.summary:
            issues.append(f"{verdict.ref}: the quote '{f.quote}' is not in the summary")
        if f.fact and f.fact not in ids:
            issues.append(f"{verdict.ref}: {f.fact} is not one of its facts")
    return issues


def score(sheet: SummarySheet, verdicts: SummaryVerdicts) -> Groundedness:
    """The counts of a checked verdict file (`load_verdicts`)."""
    by_ref = {v.ref: v for v in verdicts.verdicts}
    judged = [(row, by_ref[row.ref]) for row in sheet.rows]
    n = len(judged)
    faults = {f: sum(any(x.fault == f for x in v.findings) for _, v in judged) for f in FAULTS}
    sampled = [s for s in STRATA if any(r.stratum == s for r in sheet.rows)]
    strata = {s: [v for row, v in judged if row.stratum == s] for s in sampled}
    return Groundedness(
        judge_model=verdicts.judge.model,
        grounded=Proportion.of(sum(not v.findings for _, v in judged), n),
        by_fault={f: Proportion.of(k, n) for f, k in faults.items()},
        findings={f: sum(x.fault == f for _, v in judged for x in v.findings) for f in FAULTS},
        by_stratum={s: Proportion.of(sum(not v.findings for v in vs), len(vs)) for s, vs in strata.items()},
    )
