"""Judged correctness of a build's claims (R110): the sheet the judge answers, and the scores code computes.

Role in the pipeline: `kg claim-eval` (pipeline/claim_stages.py), after a build, offline. Claim precision was
last judged in R66 / R68; the extraction prompt has changed since (R77's truth, modality and condition, R81,
R82), so the claims a build stores now are unmeasured. The judge (Claude in the session, never the model that
built the graph: the `evaluation` skill) answers every claim of the sheet; this module scores the answers.
Design: pure; the caller gives the claims, the types of their ends and the chunks, so no graph is read here.
  - The sheet shows every claim of the build once, extracted and derived apart (`origin`), with every field
    the graph stores (its two ends with their types, the relation, the quote, tone, time, truth, modality,
    condition and their verbatim cue words), each chunk's text once, and the schema's descriptions of the
    relations and types, so the judge reads a claim as the graph means it.
  - The verdicts use the shared file and review rules (anchor_verdicts.py). An INCORRECT verdict names its
    faults (`FAULTS`); a content fault means the stored triple says something the text does not, a field
    fault that the triple is right and a stored field around it is not. So one judging pass gives both the
    strict precision (every stored field right) and the content precision (the triple right).
  - Scores per origin, with Wilson intervals, the count of every fault, and the strict precision per relation.
Not here: the review rules and the file format (anchor_verdicts.py), the snapshot (audit/), the stage.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from ..core.text import norm
from ..text.chunking import Chunk
from ..text.schema import TextSchema
from ..text.subject_graph import ObservationRow
from .anchor_verdicts import ACCEPTED, JUDGED, Label, Verdict, VerdictFile
from .interval import Proportion

Origin = Literal["extracted", "derived"]
ORIGINS: tuple[Origin, ...] = ("extracted", "derived")

# What can be wrong with a claim. Content faults: the stored triple itself says what the text does not (a
# lost negation inverts it, so truth is one). Field faults: the triple is what the text says, a stored field
# around it is not. The judge names every fault a claim has; code sorts them.
CONTENT_FAULTS = ("not_in_text", "wrong_entity", "wrong_relation", "truth")
FIELD_FAULTS = ("modality", "condition", "polarity", "time", "type")
FAULTS = CONTENT_FAULTS + FIELD_FAULTS

# The question every claim answers; the rules file (tests/gold/r110/claim_judge_rules.md) spells it out
CLAIM_QUESTION = (
    "Read in its chunk, does the text state this claim as the graph stores it: these two things, this "
    "relation as the schema describes it, and its truth, modality, condition, tone, time and end types? "
    "VALID: yes; VALID_ALTERNATIVE: yes under a reading the text equally allows; INCORRECT: no, naming every "
    "fault; AMBIGUOUS: the text honestly supports both; UNJUDGEABLE: the chunk does not show what is needed."
)


class ClaimItem(BaseModel):
    """One claim as the judge sees it: the observation's own wording and every stored field."""

    id: str  # the observation's id
    origin: Origin
    chunk_id: str
    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence: str
    polarity: str
    time: str
    truth: str
    negation: str  # the stored words that deny the statement
    modality: str
    hedge: str  # the stored words that make it only possible
    condition: str


class ChunkText(BaseModel):
    context: str  # what the document is about (its first heading or title)
    text: str


class ClaimSheet(BaseModel):
    dataset: str
    build: str
    question: str = CLAIM_QUESTION
    relations: dict[str, str]  # predicate -> "<subject type> -> <object type>: <description>"
    types: dict[str, str]  # entity type -> its description
    chunks: dict[str, ChunkText]
    claims: list[ClaimItem]
    # claims whose id another claim already had: one node in the graph (MERGE), shown once
    merged: int = 0

    def to_judge(self) -> set[str]:
        return {c.id for c in self.claims}


def claim_item(row: ObservationRow, origin: Origin, mention_types: Mapping[str, str]) -> ClaimItem:
    """The sheet item of one stored observation; `mention_types` maps a mention id to its type."""
    return ClaimItem(
        id=row.id,
        origin=origin,
        chunk_id=row.chunk_id,
        subject=row.subject_name,
        subject_type=mention_types[row.subject],
        predicate=row.predicate,
        object=row.object_name,
        object_type=mention_types[row.object],
        evidence=row.evidence,
        polarity=row.polarity,
        time=row.time,
        truth=row.truth,
        negation=row.negation,
        modality=row.modality,
        hedge=row.hedge,
        condition=row.condition,
    )


def claim_sheet(
    dataset: str, build: str, items: Sequence[ClaimItem], chunks: Sequence[Chunk], schema: TextSchema
) -> ClaimSheet:
    """The sheet of `items`, extracted claims first, each id once (the first item of an id is the node
    the graph holds; the others are counted as `merged`), with the chunks the claims come from."""
    ordered = sorted(items, key=lambda c: (ORIGINS.index(c.origin), c.chunk_id, c.id))
    seen: dict[str, ClaimItem] = {}
    for c in ordered:
        seen.setdefault(c.id, c)
    used = {c.chunk_id for c in seen.values()}
    return ClaimSheet(
        dataset=dataset,
        build=build,
        relations={
            f.predicate: f"{f.subject_type} -> {f.object_type}: {f.description}" for f in schema.fact_types
        },
        types={e.name: e.description for e in schema.entity_types},
        chunks={c.chunk_id: ChunkText(context=c.context, text=c.text) for c in chunks if c.chunk_id in used},
        claims=list(seen.values()),
        merged=len(ordered) - len(seen),
    )


def claim_verdict_issues(sheet: ClaimSheet, verdicts: VerdictFile) -> list[str]:
    """Every way the verdicts break the claim rules, beyond the shared file checks: an INCORRECT verdict
    without a fault or with an unknown one, and a quote that is not in the claim's chunk (compared with
    `norm`; UNJUDGEABLE may have none)."""
    chunk_of = {c.id: c.chunk_id for c in sheet.claims}
    issues = []
    for v in verdicts.verdicts:
        if v.label is Label.INCORRECT and not v.faults:
            issues.append(f"{v.id}: INCORRECT without a fault")
        issues += [f"{v.id}: unknown fault {f!r}" for f in v.faults if f not in FAULTS]
        chunk = sheet.chunks.get(chunk_of.get(v.id, ""))
        if v.label is not Label.UNJUDGEABLE and (chunk is None or norm(v.evidence) not in norm(chunk.text)):
            issues.append(f"the quote of {v.id} is not in its chunk")
    return issues


class OriginScores(BaseModel):
    """The judged claims of one origin. Precision counts VALID and VALID_ALTERNATIVE over the judged
    (AMBIGUOUS and UNJUDGEABLE leave the denominator and are counted)."""

    claims: int
    ambiguous: int
    unjudgeable: int
    precision: Proportion  # every stored field right
    content_precision: Proportion  # the triple right (no content fault), whatever its fields
    faults: dict[str, int]  # every fault, 0 when unused
    incorrect: list[str]  # the ids judged INCORRECT


class ClaimScores(BaseModel):
    claims: int
    merged: int
    by_origin: dict[str, OriginScores]
    by_relation: dict[str, Proportion]  # strict precision per predicate, both origins


def score_claims(sheet: ClaimSheet, verdicts: VerdictFile) -> ClaimScores:
    """The scores of a sheet's verdicts; the caller has checked the file against the sheet."""
    final = verdicts.final()
    by_origin = {o: _origin_scores([c for c in sheet.claims if c.origin == o], final) for o in ORIGINS}
    by_relation: dict[str, Proportion] = {}
    for predicate in sorted({c.predicate for c in sheet.claims}):
        labels = [final[c.id].label for c in sheet.claims if c.predicate == predicate]
        by_relation[predicate] = Proportion.of(
            sum(lab in ACCEPTED for lab in labels), sum(lab in JUDGED for lab in labels)
        )
    return ClaimScores(
        claims=len(sheet.claims), merged=sheet.merged, by_origin=by_origin, by_relation=by_relation
    )


def _origin_scores(claims: Sequence[ClaimItem], final: Mapping[str, Verdict]) -> OriginScores:
    verdicts = [final[c.id] for c in claims]
    judged = [v for v in verdicts if v.label in JUDGED]
    faults = Counter(f for v in judged for f in set(v.faults))
    content_wrong = sum(any(f in CONTENT_FAULTS for f in v.faults) for v in judged)
    return OriginScores(
        claims=len(claims),
        ambiguous=sum(v.label is Label.AMBIGUOUS for v in verdicts),
        unjudgeable=sum(v.label is Label.UNJUDGEABLE for v in verdicts),
        precision=Proportion.of(sum(v.label in ACCEPTED for v in judged), len(judged)),
        content_precision=Proportion.of(len(judged) - content_wrong, len(judged)),
        faults={f: faults[f] for f in FAULTS},
        incorrect=sorted(v.id for v in judged if v.label is Label.INCORRECT),
    )
