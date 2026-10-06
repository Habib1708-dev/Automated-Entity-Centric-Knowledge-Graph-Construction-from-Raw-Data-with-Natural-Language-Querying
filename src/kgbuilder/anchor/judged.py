"""The judged anchor-graph criteria C3, C4 and C6 (R93), computed by code from the judge's verdicts.

Role in the pipeline: after the judge answered the sheets of `anchor/sheets.py` into verdict files
(`validation/anchor_verdicts.py`); read by the scoring stage. Nothing here judges: every rate is k/n of
labels with a 95 % Wilson interval, and every pass rule is the direction's (section 7.2), applied to the
point rate of a census.
  - Rates: a verdict is accepted when VALID or VALID_ALTERNATIVE; AMBIGUOUS and UNJUDGEABLE leave the
    denominator and are counted. Next to the rate: the strict rate (VALID only) and the worst case
    (AMBIGUOUS and UNJUDGEABLE counted as INCORRECT).
  - C3: wrong merges of individuals are merge items judged INCORRECT; wrong merges of records are C4's
    links judged INCORRECT (R93 decision 3); hard: none of either. Concept merges and the split groups
    (VALID = rightly apart) are comparative. R75's identity pairs are rescored on the snapshot by code.
  - C4: precision over the links (hard: >= the minimum, and no `cross_scope_link` flag judged INCORRECT);
    the unlinked mentions named after a key are reported apart (accepted = a missed link).
  - C6: purity per arm over the pairs W2 reaches in that arm, record and individual starts pooled (hard),
    concept starts apart; the starts below the minimum; the pairs only arm B reaches.
  - Every flag's agreement with the verdicts: how many flagged items were judged INCORRECT, and the
    INCORRECT items no flag raised.
Not here: the verdict file rules (validation/anchor_verdicts.py), the sheets, MLflow.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping

from pydantic import BaseModel

from ..audit.snapshot import GraphSnapshot
from ..core.errors import EvaluationError
from ..core.text import norm
from ..validation.anchor_verdicts import ACCEPTED, JUDGED, Label, VerdictFile
from ..validation.gold import IdentityPair
from ..validation.identity import IdentityScore, SheetMention, score_identity
from ..validation.interval import Proportion
from .navigation import Arm
from .sheets import C3Sheet, C4Sheet, C6Sheet, CodeItem, CodeSide, NodeView, RecordView


class Rates(BaseModel):
    """The labels of one group of items as rates."""

    accepted: Proportion  # VALID + VALID_ALTERNATIVE over VALID + VALID_ALTERNATIVE + INCORRECT
    strict: Proportion  # VALID over the same denominator
    worst_case: Proportion  # accepted over every item
    counts: dict[str, int]


def rates(labels: Iterable[Label]) -> Rates:
    labels = list(labels)
    judged = [x for x in labels if x in JUDGED]
    accepted = sum(x in ACCEPTED for x in judged)
    return Rates(
        accepted=Proportion.of(accepted, len(judged)),
        strict=Proportion.of(sum(x is Label.VALID for x in judged), len(judged)),
        worst_case=Proportion.of(accepted, len(labels)),
        counts={label.value: sum(x is label for x in labels) for label in Label},
    )


class FlagAgreement(BaseModel):
    """One code flag against the verdicts on the items it raised."""

    flag: str
    flagged: int
    confirmed: int  # judged INCORRECT
    refuted: int  # judged VALID or VALID_ALTERNATIVE
    open: int  # AMBIGUOUS or UNJUDGEABLE


def agreement(code: list[CodeItem], final: Mapping[str, Label]) -> tuple[list[FlagAgreement], list[str]]:
    """Each flag's agreement, and the items judged INCORRECT that no flag raised."""
    by_flag: dict[str, list[Label]] = defaultdict(list)
    for item in code:
        for flag in item.flags:
            by_flag[flag].append(final[item.id])
    out = [
        FlagAgreement(
            flag=flag,
            flagged=len(labels),
            confirmed=sum(x is Label.INCORRECT for x in labels),
            refuted=sum(x in ACCEPTED for x in labels),
            open=sum(x not in JUDGED for x in labels),
        )
        for flag, labels in sorted(by_flag.items())
    ]
    unflagged = [i.id for i in code if not i.flags and final[i.id] is Label.INCORRECT]
    return out, unflagged


class C3Result(BaseModel):
    merges: dict[str, Rates]  # by kind: individual, concept
    wrong_merges: dict[str, list[str]]  # by kind: the merge items judged INCORRECT
    record_wrong_merges: list[str]  # C4 link items judged INCORRECT
    splits: Rates  # accepted = rightly kept apart
    wrong_splits: list[str]
    identity_pairs: IdentityScore  # R75's pairs on the snapshot, by code
    hard_passed: bool


class C4Result(BaseModel):
    links: Rates
    unlinked: Rates  # accepted = the mention does refer to the record: a missed link
    incorrect: list[str]
    cross_scope_confirmed: list[str]
    flags: list[FlagAgreement]
    incorrect_unflagged: list[str]
    hard_passed: bool


class StartPurity(BaseModel):
    start: str
    kind: str
    purity: Proportion


class C6Result(BaseModel):
    purity: dict[str, dict[str, Rates]]  # arm -> "record_individual" | "concept" -> rates
    hard_passed: dict[str, bool]  # arm -> record and individual starts at or above the minimum
    starts_below: dict[str, list[StartPurity]]  # arm -> record and individual starts below the minimum
    only_layered: Rates  # the pairs arm B reaches and arm A does not
    flags: list[FlagAgreement]
    incorrect_unflagged: list[str]


def score_c3(
    file: VerdictFile, code: CodeSide, c4: VerdictFile, c4_code: CodeSide, pairs: IdentityScore
) -> C3Result:
    """C3 from its verdicts, C4's verdicts on the links (record merges) and the rescored R75 pairs."""
    final = {i: v.label for i, v in file.final().items()}
    links = {i: v.label for i, v in c4.final().items()}
    merges = {kind: [i for i in code.items if i.kind == kind] for kind in ("individual", "concept")}
    split = [i for i in code.items if i.kind == "split"]
    wrong = {kind: [i.id for i in items if final[i.id] is Label.INCORRECT] for kind, items in merges.items()}
    records = [i.id for i in c4_code.items if i.kind == "link" and links[i.id] is Label.INCORRECT]
    return C3Result(
        merges={kind: rates(final[i.id] for i in items) for kind, items in merges.items()},
        wrong_merges=wrong,
        record_wrong_merges=records,
        splits=rates(final[i.id] for i in split),
        wrong_splits=[i.id for i in split if final[i.id] is Label.INCORRECT],
        identity_pairs=pairs,
        hard_passed=not wrong["individual"] and not records,
    )


def score_c4(file: VerdictFile, code: CodeSide, min_precision: float) -> C4Result:
    final = {i: v.label for i, v in file.final().items()}
    links = [i for i in code.items if i.kind == "link"]
    link_rates = rates(final[i.id] for i in links)
    confirmed = [i.id for i in links if "cross_scope_link" in i.flags and final[i.id] is Label.INCORRECT]
    flags, unflagged = agreement(code.items, final)
    rate = link_rates.accepted.rate
    return C4Result(
        links=link_rates,
        unlinked=rates(final[i.id] for i in code.items if i.kind == "unlinked"),
        incorrect=[i.id for i in links if final[i.id] is Label.INCORRECT],
        cross_scope_confirmed=confirmed,
        flags=flags,
        incorrect_unflagged=unflagged,
        hard_passed=not confirmed and rate is not None and rate >= min_precision,
    )


def score_c6(file: VerdictFile, code: CodeSide, min_purity: float) -> C6Result:
    final = {i: v.label for i, v in file.final().items()}
    purity, passed, below = {}, {}, {}
    for arm in Arm:
        items = [i for i in code.items if arm.value in i.arms]
        hard = [i for i in items if i.kind in ("record", "individual")]
        purity[arm.value] = {
            "record_individual": rates(final[i.id] for i in hard),
            "concept": rates(final[i.id] for i in items if i.kind == "concept"),
        }
        rate = purity[arm.value]["record_individual"].accepted.rate
        passed[arm.value] = rate is None or rate >= min_purity  # no record start: nothing can fail
        below[arm.value] = _starts_below(hard, final, min_purity)
    flags, unflagged = agreement(code.items, final)
    return C6Result(
        purity=purity,
        hard_passed=passed,
        starts_below=below,
        only_layered=rates(final[i.id] for i in code.items if i.arms == [Arm.LAYERED.value]),
        flags=flags,
        incorrect_unflagged=unflagged,
    )


def _starts_below(items: list[CodeItem], final: Mapping[str, Label], minimum: float) -> list[StartPurity]:
    by_start: dict[str, list[CodeItem]] = defaultdict(list)
    for i in items:
        by_start[i.node].append(i)
    out = []
    for start, group in sorted(by_start.items()):
        p = rates(final[i.id] for i in group).accepted
        if p.rate is not None and p.rate < minimum:
            out.append(StartPurity(start=start, kind=group[0].kind, purity=p))
    return out


def rescore_identity(s: GraphSnapshot, pairs: list[IdentityPair]) -> IdentityScore:
    """R75's identity pairs on the snapshot: each mention with the node its REFERS_TO reaches (itself when
    it has none), scored by R75's own scorer."""
    canonical = {a.mention: a.canonical for a in s.references}
    mentions = [
        SheetMention(id=m.id, doc_id=m.doc_id, name=m.name, type=m.type, canonical=canonical.get(m.id, m.id))
        for m in s.mentions
    ]
    return score_identity(mentions, pairs)


# --- the evidence check: a quote must stand in what the item showed ---------------------------------


def _record_text(r: RecordView) -> list[str]:
    return [r.ref, r.key, r.name or "", *r.cells.values(), *r.relations]


def _node_text(n: NodeView, chunks: Mapping[str, str]) -> list[str]:
    out = [n.name, *(_record_text(n.record) if n.record else [])]
    return (
        out + [chunks[c] for m in n.mentions for c in m.chunks if c in chunks] + [m.name for m in n.mentions]
    )


def item_texts(sheet: C3Sheet | C4Sheet | C6Sheet) -> dict[str, list[str]]:
    """Every item's shown text: where its verdict's quote must stand."""
    chunks = {c: x.text + "\n" + x.heading for c, x in sheet.chunks.items()}
    if isinstance(sheet, C3Sheet):
        out = {i.id: _node_text(i.node, chunks) for i in sheet.merges}
        return out | {i.id: [x for n in i.nodes for x in _node_text(n, chunks)] for i in sheet.splits}
    if isinstance(sheet, C4Sheet):
        return {
            i.id: [chunks[c] for c in i.mention.chunks if c in chunks]
            + _record_text(i.record)
            + [x for r in i.same_name for x in _record_text(r)]
            for i in sheet.links
        }
    return {i.id: [chunks[i.chunk], *_node_text(i.start, chunks)] for i in sheet.pairs}


def evidence_issues(sheet: C3Sheet | C4Sheet | C6Sheet, file: VerdictFile) -> list[str]:
    """Quotes not found (after `norm`) in what their item showed; C3 outliers and groups that name a mention
    or node the item did not show."""
    texts = item_texts(sheet)
    shown: dict[str, set[str]] = {}
    if isinstance(sheet, C3Sheet):
        shown = {i.id: {m.id for m in i.node.mentions} for i in sheet.merges}
        shown |= {i.id: {n.id for n in i.nodes} for i in sheet.splits}
    issues = []
    for v in file.verdicts:
        quote = norm(v.evidence)
        if quote and not any(quote in norm(t) for t in texts.get(v.id, [])):
            issues.append(f"{v.id}: the evidence is not in the item")
        named = set(v.outliers) | {x for group in v.together for x in group}
        if named - shown.get(v.id, set()):
            issues.append(
                f"{v.id}: names {sorted(named - shown.get(v.id, set()))}, which the item does not show"
            )
    return issues


def check_evidence(sheet: C3Sheet | C4Sheet | C6Sheet, file: VerdictFile) -> None:
    """Raises `EvaluationError` when a quote or a named mention is not in its item."""
    if issues := evidence_issues(sheet, file):
        raise EvaluationError(issues)
