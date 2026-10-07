"""The judge's verdict files for the anchor-graph criteria C3, C4 and C6 (R93), the mention sheet (R102) and
the claim sheet (R110): their models, the check that a file answers exactly its sheet, and the lead's review
rules.

Role in the pipeline: written by Claude in the Claude Code session (the `evaluation` skill) from the blind
sheets of `anchor/sheets.py`; read by `anchor/judged.py`, which scores them. Verdicts are data: this module
refuses a file that is incomplete or inconsistent, so no score is ever computed from a stale or partial one.
Design: one verdict per sheet item, with the five labels the user fixed for R87. Every verdict carries a
reason and a verbatim quote (only UNJUDGEABLE may lack the quote: it is the label for an item that does not
show what is needed). The lead judge reviews every INCORRECT, AMBIGUOUS and UNJUDGEABLE verdict and a seeded
10 % of the VALID ones; each change is written down with the label before and the reason, so the blind
label can always be recovered.
Not here: what an item shows (the sheets), whether a quote is in the item's text, and the scores
(`anchor/judged.py`).
"""

import json
import math
import random
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator

from ..core.errors import EvaluationError

# The seed of the lead's 10 % review sample of VALID verdicts: fixed, so the sample is the same on every
# machine and a reader can check that exactly these verdicts were reviewed.
REVIEW_SEED = 93
REVIEW_SHARE = 0.10


class Label(StrEnum):
    """The judge's labels (R87). AMBIGUOUS and UNJUDGEABLE leave every rate's denominator and are counted."""

    VALID = "VALID"
    VALID_ALTERNATIVE = "VALID_ALTERNATIVE"  # right under a reading the text equally allows
    INCORRECT = "INCORRECT"
    AMBIGUOUS = "AMBIGUOUS"  # the text honestly supports both answers
    UNJUDGEABLE = "UNJUDGEABLE"  # the item does not show what a decision needs


ACCEPTED = frozenset({Label.VALID, Label.VALID_ALTERNATIVE})
JUDGED = frozenset({Label.VALID, Label.VALID_ALTERNATIVE, Label.INCORRECT})
MUST_REVIEW = frozenset({Label.INCORRECT, Label.AMBIGUOUS, Label.UNJUDGEABLE})


class Verdict(BaseModel):
    """The verdict on one sheet item."""

    id: str
    label: Label
    reason: str  # one line
    evidence: str = ""  # verbatim from the item's chunk or record
    outliers: list[str] = []  # C3 merge item, INCORRECT: the mention ids that are another thing
    together: list[list[str]] = []  # C3 split item, INCORRECT: node ids that are one thing
    faults: list[str] = []  # claim item, INCORRECT: what is wrong with it (validation/claim_eval.py)

    @model_validator(mode="after")
    def _complete(self) -> "Verdict":
        if not self.reason.strip():
            raise ValueError(f"{self.id}: a verdict needs a reason")
        if not self.evidence.strip() and self.label is not Label.UNJUDGEABLE:
            raise ValueError(f"{self.id}: only UNJUDGEABLE may lack an evidence quote")
        if (self.outliers or self.together or self.faults) and self.label is not Label.INCORRECT:
            raise ValueError(f"{self.id}: outliers, together and faults belong to INCORRECT verdicts only")
        return self


class Change(BaseModel):
    """One label the lead changed in review."""

    id: str
    before: Label  # the blind judge's label
    after: Label
    reason: str


class JudgeHeader(BaseModel):
    """Who judged what: the verdicts hold only for this snapshot and these sheets."""

    model: str  # the exact model id of the session
    date: str
    snapshot_hash: str  # of the snapshot the sheet was built from (anchor/sheets.py)
    sheet: str  # the sheet file, repo-relative
    sheet_hash: str
    sheets_git_sha: str  # the commit holding the sheet


class VerdictFile(BaseModel):
    judge: JudgeHeader
    # the anchor criteria, the mention pass's recall mapping and precision sample (R102, mention_eval.py), and
    # the build's claims (R110, claim_eval.py)
    criterion: Literal["C3", "C4", "C6", "mentions", "claims"]
    verdicts: list[Verdict]
    reviewed: list[str] = []  # ids the lead reviewed, changed or not
    changes: list[Change] = []

    def blind(self) -> dict[str, Label]:
        """Each item's label as the blind judge gave it, before the lead's changes."""
        before = {c.id: c.before for c in self.changes}
        return {v.id: before.get(v.id, v.label) for v in self.verdicts}

    def final(self) -> dict[str, Verdict]:
        return {v.id: v for v in self.verdicts}


def review_sample(blind: dict[str, Label]) -> list[str]:
    """The VALID verdicts the lead must review: a seeded 10 % (rounded up) of the blind VALID ids."""
    valid = sorted(i for i, label in blind.items() if label is Label.VALID)
    return sorted(random.Random(REVIEW_SEED).sample(valid, math.ceil(REVIEW_SHARE * len(valid))))


def verdict_issues(file: VerdictFile, ids: set[str]) -> list[str]:
    """Every way `file` fails to answer exactly the sheet items `ids` under the review rules; empty when
    it fits. Checked: no missing, duplicate or unknown id; every change names a reviewed item, its
    `after` is the final label and its `before` differs; every item whose blind or final label is
    INCORRECT, AMBIGUOUS or UNJUDGEABLE was reviewed; the seeded VALID sample was reviewed."""
    counts = Counter(v.id for v in file.verdicts)
    issues = [f"duplicate verdict {i}" for i, n in sorted(counts.items()) if n > 1]
    issues += [f"no verdict for {i}" for i in sorted(ids - counts.keys())]
    issues += [f"verdict for unknown item {i}" for i in sorted(counts.keys() - ids)]
    final = {v.id: v.label for v in file.verdicts}
    reviewed = set(file.reviewed)
    issues += [f"reviewed unknown item {i}" for i in sorted(reviewed - counts.keys())]
    changed = Counter(c.id for c in file.changes)
    issues += [f"item {i} changed {n} times" for i, n in sorted(changed.items()) if n > 1]
    for c in file.changes:
        if c.id not in reviewed:
            issues.append(f"change of {c.id} without a review")
        if final.get(c.id) is not c.after or c.before is c.after:
            issues.append(f"change of {c.id} does not end at its final label")
    blind = file.blind()
    must = {i for i in final if blind[i] in MUST_REVIEW or final[i] in MUST_REVIEW}
    issues += [f"{i} ({final[i]}) not reviewed" for i in sorted(must - reviewed)]
    issues += [f"VALID sample item {i} not reviewed" for i in review_sample(blind) if i not in reviewed]
    return issues


def load_verdicts(path: Path, ids: set[str]) -> VerdictFile:
    """Read a verdict file and check it against its sheet's item ids.

    Raises `EvaluationError` naming every issue: a malformed file, or one that does not answer exactly
    `ids` under the review rules (`verdict_issues`)."""
    try:
        file = VerdictFile.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError) as e:
        raise EvaluationError([f"{path}: {e}"]) from e
    if issues := verdict_issues(file, ids):
        raise EvaluationError([f"{path}: {i}" for i in issues])
    return file
