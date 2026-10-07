"""Recall of a build's claims against a reader's (R111): the sheet the judge matches, and the scores code
computes.

Role in the pipeline: `kg claim-recall` (pipeline/claim_stages.py), offline, on committed files only: R77's
sentence sample and its gold (assertion.py: every claim a careful reader finds in each sampled sentence, with
its truth, modality and condition, written from the text alone before any output), and a build's claim sheet
(claim_eval.py: every stored claim of each chunk, with every field). The judge (Claude in the session) says,
for each gold claim, which stored claims of the sentence's chunk state its content whatever their truth,
modality and condition, or why none does (R68's causes, coverage.py); this module checks and scores that.
Design: pure. The sheet joins the three files by chunk. A sentence whose chunk stores no claim shows none, and
no chunk text (the claim sheet carries only chunks with claims). Recall is reported overall, per stratum and
on the random strata alone, the fair estimate ("cue" sentences were drawn for negation, modal and condition
words), and within the schema (the misses no fact type can hold left out). For matched claims code compares
the gold labels with the stored fields (`assertion.exact_field`), as R77 did, with no judge. The verdict file
records the lead's review as the shared verdict files do (anchor_verdicts.py): every miss and a seeded 10 % of
the matches reviewed, each change kept with the blind answer.
Not here: the gold (assertion.py), the claim sheet (claim_eval.py), the causes (coverage.py), the stage.
"""

import json
import math
import random
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ValidationError, model_validator

from ..core.errors import EvaluationError
from .anchor_verdicts import REVIEW_SEED, REVIEW_SHARE
from .assertion import FIELDS, AssertionGold, GoldClaim, exact_field
from .claim_eval import ChunkText, ClaimItem, ClaimSheet
from .coverage import Cause
from .interval import Proportion
from .judge import JudgeMeta
from .sentences import SentenceSample

# Strata drawn at random (R68's sentences, R77's "natural" ones): only these estimate recall over the text
RANDOM_STRATA = frozenset({"r68", "natural"})
MATCHED = "matched"  # the outcome of a gold claim some stored claim states; a miss's outcome is its cause

RECALL_QUESTION = (
    "Which stored claims of this sentence's chunk state this reader's claim: its subject, relation and "
    "object by meaning, whatever their truth, modality and condition? None: give the first cause that fits."
)


class RecallSentence(BaseModel):
    """One sampled sentence with the reader's claims and what the build stores for its chunk."""

    id: str
    stratum: str
    chunk_id: str
    text: str  # the sentence
    chunk: ChunkText | None  # None when the chunk stores no claim
    gold: list[GoldClaim]
    stored: list[ClaimItem]


class RecallSheet(BaseModel):
    dataset: str
    claim_sheet: str  # the claim sheet joined, as given
    question: str = RECALL_QUESTION
    relations: dict[str, str]  # the claim sheet's, so a cause can name the fact type that could hold a claim
    types: dict[str, str]
    sentences: list[RecallSentence]

    def keys(self) -> list[str]:
        """Every gold claim's key, "<sentence id>#<index>", in sheet order."""
        return [claim_key(s.id, i) for s in self.sentences for i in range(len(s.gold))]


def claim_key(sentence: str, index: int) -> str:
    return f"{sentence}#{index}"


def recall_sheet(
    dataset: str, sample: SentenceSample, gold: AssertionGold, claims: ClaimSheet, claim_sheet: str
) -> RecallSheet:
    """The sheet of `gold`'s sentences, each with its chunk's stored claims from `claims`.

    Raises EvaluationError when the gold does not answer the sample sentence by sentence, in order."""
    if [s.id for s in gold.sentences] != [s.id for s in sample.sentences]:
        raise EvaluationError(["the gold's sentences are not the sample's, in its order"])
    by_chunk: dict[str, list[ClaimItem]] = {}
    for c in claims.claims:
        by_chunk.setdefault(c.chunk_id, []).append(c)
    return RecallSheet(
        dataset=dataset,
        claim_sheet=claim_sheet,
        relations=claims.relations,
        types=claims.types,
        sentences=[
            RecallSentence(
                id=s.id,
                stratum=g.stratum,
                chunk_id=s.chunk_id,
                text=s.text,
                chunk=claims.chunks.get(s.chunk_id),
                gold=g.claims,
                stored=by_chunk.get(s.chunk_id, []),
            )
            for s, g in zip(sample.sentences, gold.sentences, strict=True)
        ],
    )


class RecallVerdict(BaseModel):
    """One gold claim: the stored claims stating it, or the cause of the miss; never both, never neither."""

    key: str
    claim: str  # a copy of the gold claim, checked against it
    matched: list[str] = []  # ids of stored claims of the sentence's chunk
    cause: Cause | None = None
    # a miss names the fact type that could hold the claim, except when none could (as R68's verdicts)
    schema_type: str | None = None
    reason: str

    @model_validator(mode="after")
    def _consistent(self) -> "RecallVerdict":
        if bool(self.matched) == (self.cause is not None):
            raise ValueError(f"{self.key}: give matched or a cause, exactly one of them")
        if self.matched and self.schema_type is not None:
            raise ValueError(f"{self.key}: schema_type is only for a miss")
        if self.cause is not None and (self.schema_type is None) != (self.cause == Cause.NO_SCHEMA_TYPE):
            raise ValueError(f"{self.key}: a miss names its schema type, except no_schema_type")
        if not self.reason.strip():
            raise ValueError(f"{self.key}: a verdict needs a reason")
        return self

    @property
    def outcome(self) -> str:
        return MATCHED if self.matched else str(self.cause)


class RecallChange(BaseModel):
    """One outcome the lead changed in review: "matched" or a cause, before (blind) and after."""

    key: str
    before: str
    after: str
    reason: str


class RecallVerdicts(BaseModel):
    judge: JudgeMeta
    sheet: str
    verdicts: list[RecallVerdict]
    reviewed: list[str] = []  # keys the lead reviewed, changed or not
    changes: list[RecallChange] = []

    def blind(self) -> dict[str, str]:
        """Each gold claim's outcome as the blind judge gave it, before the lead's changes."""
        before = {c.key: c.before for c in self.changes}
        return {v.key: before.get(v.key, v.outcome) for v in self.verdicts}


def review_sample(blind: dict[str, str]) -> list[str]:
    """The matches the lead must review: a seeded 10 % (rounded up) of the blind matches, with the seed and
    share of the shared verdict files."""
    matched = sorted(k for k, outcome in blind.items() if outcome == MATCHED)
    return sorted(random.Random(REVIEW_SEED).sample(matched, math.ceil(REVIEW_SHARE * len(matched))))


def recall_issues(sheet: RecallSheet, file: RecallVerdicts) -> list[str]:
    """Every way `file` fails to answer `sheet`; empty when it fits. Checked: each gold claim answered once,
    as written; matched ids are stored claims of the sentence's chunk; every claim whose blind or final
    outcome is a miss was reviewed, and the seeded sample of blind matches; each change names a reviewed
    claim, ends at its final outcome and differs from its blind one."""
    gold = {claim_key(s.id, i): (c.claim, {x.id for x in s.stored}) for s in sheet.sentences for i, c in
            enumerate(s.gold)}  # fmt: skip
    counts = Counter(v.key for v in file.verdicts)
    issues = [f"{k} answered {n} times" for k, n in sorted(counts.items()) if n > 1]
    issues += [f"no verdict for {k}" for k in gold if k not in counts]
    issues += [f"verdict for unknown claim {k}" for k in sorted(counts.keys() - gold.keys())]
    for v in (v for v in file.verdicts if v.key in gold):
        claim, stored = gold[v.key]
        if v.claim != claim:
            issues.append(f"{v.key}: the claim differs from the gold's")
        issues += [
            f"{v.key}: {i} is no stored claim of the sentence's chunk" for i in v.matched if i not in stored
        ]
    final = {v.key: v.outcome for v in file.verdicts}
    reviewed = set(file.reviewed)
    for c in file.changes:
        if c.key not in reviewed:
            issues.append(f"change of {c.key} without a review")
        if final.get(c.key) != c.after or c.before == c.after:
            issues.append(f"change of {c.key} does not end at its final outcome")
    blind = file.blind()
    must = {k for k in final if blind.get(k) != MATCHED or final[k] != MATCHED}
    issues += [f"{k} ({final[k]}) not reviewed" for k in sorted(must - reviewed)]
    issues += [f"sampled match {k} not reviewed" for k in review_sample(blind) if k not in reviewed]
    return issues


def load_recall_verdicts(path: Path, sheet: RecallSheet) -> RecallVerdicts:
    """Read a verdict file and check it against its sheet.

    Raises EvaluationError naming every issue: a malformed file, or one that does not answer the sheet."""
    try:
        file = RecallVerdicts.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError) as e:
        raise EvaluationError([f"{path}: {e}"]) from e
    if issues := recall_issues(sheet, file):
        raise EvaluationError([f"{path}: {i}" for i in issues])
    return file


class RecallScores(BaseModel):
    """Recall with Wilson intervals, the misses per cause, and the stored fields of the matched claims."""

    claims: int
    recall: Proportion
    recall_random: Proportion  # the random strata only: the estimate over the text
    recall_by_stratum: dict[str, Proportion]
    recall_in_schema: Proportion  # the misses no fact type could hold left out
    recall_in_schema_random: Proportion
    misses: dict[str, int]  # every cause, 0 when unused
    misses_random: dict[str, int]
    # per field, the matched claims whose every matched stored claim has the gold's label; the condition
    # only over conditional gold claims (the only ones that have one)
    fields_exact: dict[str, Proportion]


class _Row(NamedTuple):
    """One gold claim with its sentence's stratum, its verdict and the stored claims it matched."""

    stratum: str
    gold: GoldClaim
    verdict: RecallVerdict
    matched: list[ClaimItem]


def score_recall(sheet: RecallSheet, file: RecallVerdicts) -> RecallScores:
    """The scores of a checked verdict file (`load_recall_verdicts`)."""
    by_key = {v.key: v for v in file.verdicts}
    rows: list[_Row] = []
    for s in sheet.sentences:
        stored = {x.id: x for x in s.stored}
        for i, g in enumerate(s.gold):
            v = by_key[claim_key(s.id, i)]
            rows.append(_Row(s.stratum, g, v, [stored[x] for x in v.matched]))
    randoms = [r for r in rows if r.stratum in RANDOM_STRATA]
    fields: dict[str, Proportion] = {}
    for field in FIELDS:
        pool = [r for r in rows if r.matched and (field != "condition" or r.gold.modality == "conditional")]
        fields[field] = Proportion.of(sum(exact_field(field, r.gold, r.matched) for r in pool), len(pool))
    return RecallScores(
        claims=len(rows),
        recall=_recall(rows),
        recall_random=_recall(randoms),
        recall_by_stratum={
            st: _recall([r for r in rows if r.stratum == st]) for st in sorted({r.stratum for r in rows})
        },
        recall_in_schema=_recall(rows, in_schema=True),
        recall_in_schema_random=_recall(randoms, in_schema=True),
        misses=_misses(rows),
        misses_random=_misses(randoms),
        fields_exact=fields,
    )


def _recall(rows: Sequence[_Row], in_schema: bool = False) -> Proportion:
    """Matched over the rows; `in_schema` leaves out the misses no fact type could hold."""
    kept = [r for r in rows if not (in_schema and r.verdict.cause == Cause.NO_SCHEMA_TYPE)]
    return Proportion.of(sum(bool(r.matched) for r in kept), len(kept))


def _misses(rows: Sequence[_Row]) -> dict[str, int]:
    counted = Counter(str(r.verdict.cause) for r in rows if r.verdict.cause is not None)
    return {str(c): counted[str(c)] for c in Cause}
