"""Scoring the mentions of a build against the R101 definition (R102): recall of the gold mentions, and judged
precision of the mentions the pass added.

Role in the pipeline: `kg mention-eval` (pipeline/mention_stages.py), after a build, offline. The gold is
R101's (validation/mention_gold.py: the things sampled sentences name); the judge (Claude in the session)
answers the sheet this module builds, and this module scores the answers.
Design: pure; the caller gives each chunk's mention names and the pass's mentions, so no graph is read here.
  - Recall: a gold mention is found when a mention of the sentence's chunk has the same name after `norm`
    (`hit`); when only a near name stands there (one holds the other as whole words: "drawers" for "storage
    drawers") the judge decides whether it names the same thing (`candidate`, the judged mapping for
    paraphrases); otherwise it is a `miss`. Exact recall counts hits only; recall counts the candidates the
    judge accepted too.
  - Precision: a seeded sample of the pass's mentions, each shown with its chunk, judged by the definition
    (VALID: In, verbatim, its class right; INCORRECT: Out, not a thing's name, or the wrong class). The class
    is the one the pass stated (R104); for a pass that stated none (R102's builds), the one its type gives.
The sample's size and seed are fixed here, before any verdict exists.
Not here: the gold format (mention_gold.py), the verdict rules (anchor_verdicts.py, reused), the pass.
"""

import random
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from ..core.text import contains_words, norm
from ..text.schema import MentionClass
from .anchor_verdicts import ACCEPTED, JUDGED, Label, VerdictFile
from .interval import Proportion
from .mention_gold import MentionGold
from .sentences import SentenceSample

# Enough judged mentions per build to read a precision near the 0.90 bound (R101) without judging every one
PRECISION_SAMPLE = 60
PRECISION_SEED = 102  # fixed with the sample size, before any verdict was written

RecallStatus = Literal["hit", "candidate", "miss"]

RECALL_QUESTION = (
    "Does one of the candidate mention names, read in this sentence, name the same thing as the gold "
    "mention? VALID: yes; INCORRECT: none of them does; AMBIGUOUS: the sentence allows both."
)
PRECISION_QUESTION = (
    "Is this mention, read in its chunk, an entry the definition (tests/gold/r101/rules.md) admits, written "
    "as a name and with the right class (particular: one named thing; kind: a kind of thing, a state, an "
    "event, an action or a role)?"
)


class RecallItem(BaseModel):
    """One gold mention and what the build has for it in the sentence's chunk."""

    id: str  # "<sentence id>|<normalised gold name>"
    sentence: str  # the sample's sentence id
    text: str  # the sentence
    chunk_id: str
    name: str
    kind: MentionClass
    status: RecallStatus
    candidates: list[str] = []  # the near names of a candidate, sorted


class PrecisionItem(BaseModel):
    """One mention the pass added, as the judge sees it."""

    id: str  # mention id
    name: str
    type: str
    mention_class: MentionClass
    chunk_id: str
    text: str  # its first chunk's text


class MentionSheet(BaseModel):
    dataset: str
    build: str
    recall_question: str = RECALL_QUESTION
    precision_question: str = PRECISION_QUESTION
    recall: list[RecallItem]  # every gold mention; the judge answers the candidates only
    precision: list[PrecisionItem]

    def to_judge(self) -> set[str]:
        return {i.id for i in self.recall if i.status == "candidate"} | {i.id for i in self.precision}


def recall_items(
    gold: MentionGold, sample: SentenceSample, names_in_chunk: Mapping[str, Sequence[str]]
) -> list[RecallItem]:
    """Every gold mention with its status in the build (`names_in_chunk`: chunk id -> the names of the
    mentions that chunk MENTIONS). A sentence is found in the chunk the sample drew it from: the chunker
    settings are the build's, so chunk ids are stable."""
    chunk_of = {s.id: s.chunk_id for s in sample.sentences}
    out = []
    for s in gold.sentences:
        names = names_in_chunk.get(chunk_of[s.id], [])
        for m in s.mentions:
            wanted = norm(m.name)
            if any(norm(n) == wanted for n in names):
                status: RecallStatus = "hit"
                near: list[str] = []
            else:
                near = sorted({n for n in names if contains_words(n, m.name) or contains_words(m.name, n)})
                status = "candidate" if near else "miss"
            out.append(
                RecallItem(
                    id=f"{s.id}|{wanted}",
                    sentence=s.id,
                    text=s.text,
                    chunk_id=chunk_of[s.id],
                    name=m.name,
                    kind=m.kind,
                    status=status,
                    candidates=near,
                )  # fmt: skip
            )
    return out


def precision_sample(found: Sequence[PrecisionItem], size: int = PRECISION_SAMPLE) -> list[PrecisionItem]:
    """A seeded sample of `size` pass mentions (all of them when there are fewer), in id order."""
    ordered = sorted(found, key=lambda i: i.id)
    if len(ordered) <= size:
        return ordered
    return sorted(random.Random(PRECISION_SEED).sample(ordered, size), key=lambda i: i.id)


class MentionScores(BaseModel):
    """Recall of the gold (exact, and with the judged mapping), by class, and judged precision of the pass."""

    gold_mentions: int
    hits: int
    candidates: int
    misses: int
    recall_exact: Proportion
    recall: Proportion | None  # None without verdicts
    recall_by_class: dict[str, Proportion]  # exact recall per class
    precision: Proportion | None  # None without verdicts or without pass mentions
    precision_incorrect: list[str] = []  # the pass mentions judged INCORRECT
    precision_ambiguous: int = 0


def score_mentions(sheet: MentionSheet, verdicts: VerdictFile | None) -> MentionScores:
    """The scores of `sheet`; with `verdicts`, the judged mapping and precision too."""
    items = sheet.recall
    hits = sum(i.status == "hit" for i in items)
    labels = {v.id: v.label for v in verdicts.verdicts} if verdicts else {}
    mapped = sum(i.status == "candidate" and labels.get(i.id) is Label.VALID for i in items)
    by_class = {
        k: Proportion.of(
            sum(i.status == "hit" for i in items if i.kind == k), sum(i.kind == k for i in items)
        )
        for k in ("particular", "kind")
    }
    judged = [labels[i.id] for i in sheet.precision if i.id in labels]
    precision = (
        Proportion.of(sum(lab in ACCEPTED for lab in judged), sum(lab in JUDGED for lab in judged))
        if verdicts is not None and sheet.precision
        else None
    )
    return MentionScores(
        gold_mentions=len(items),
        hits=hits,
        candidates=sum(i.status == "candidate" for i in items),
        misses=sum(i.status == "miss" for i in items),
        recall_exact=Proportion.of(hits, len(items)),
        recall=Proportion.of(hits + mapped, len(items)) if verdicts is not None else None,
        recall_by_class=by_class,
        precision=precision,
        precision_incorrect=[i.id for i in sheet.precision if labels.get(i.id) is Label.INCORRECT],
        precision_ambiguous=sum(lab is Label.AMBIGUOUS for lab in judged),
    )


def mention_evidence_issues(sheet: MentionSheet, verdicts: VerdictFile) -> list[str]:
    """Every verdict whose quote does not stand in what its item showed (the sentence of a recall item, the
    chunk of a precision item), compared with `norm`; UNJUDGEABLE may have none."""
    shown = {i.id: i.text for i in sheet.recall} | {i.id: i.text for i in sheet.precision}
    return [
        f"the quote of {v.id} is not in its item"
        for v in verdicts.verdicts
        if v.label is not Label.UNJUDGEABLE and norm(v.evidence) not in norm(shown.get(v.id, ""))
    ]
