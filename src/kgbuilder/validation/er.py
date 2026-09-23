"""Entity-resolution scoring against the gold pairs: exact (`er_accuracy`) and judge-validated
(`er_accuracy_valid`).

Role in the pipeline: called by `evaluate` (evaluate.py) for the `er_pairs` section of a gold file. A gold
pair says whether two names are the same real-world thing; the graph gets it right when both names land
on one entity (`same`) or on two (`not same`).
A pair is scored only when the graph contains both things. A name the extractor never produced is not a
resolution error, so it is counted apart as "not extracted" instead of as "not merged" (the flaw found
in R29: 3 of 4 failing pairs named an entity that did not exist).
Two ways to find a name's entity:
- exact: the normalised name is the entity's name or one of its aliases (deterministic, the floor);
- judge: for a pair exact lookup could not place, the judge (Claude in the Claude Code session, see the
  `evaluation` skill) names the entity id that stands for each name in the graph, or none. The judge only
  maps names to entities; code decides merged or not and computes the score ("the LLM proposes, code
  decides").
Not here: graph access (evaluate.py reads the entities), fact scoring (judge.py), MLflow.
"""

from pydantic import BaseModel

from ..core.errors import EvaluationError
from ..core.text import norm
from .gold import GoldPair


class SheetEntity(BaseModel):
    """One `:Entity` of the graph as the judge sees it."""

    id: str
    type: str
    name: str
    aliases: list[str] = []


class SheetPair(BaseModel):
    """A gold pair with the entities exact lookup found for each name (empty: not found by name)."""

    index: int
    a: str
    b: str
    same: bool
    a_entities: list[str]
    b_entities: list[str]

    @property
    def needs_verdict(self) -> bool:
        return not (self.a_entities and self.b_entities)


class ErSheet(BaseModel):
    """The ER part of the judge sheet: every entity, and every pair with its exact-lookup result."""

    entities: list[SheetEntity]
    pairs: list[SheetPair]

    def to_judge(self) -> list[SheetPair]:
        return [p for p in self.pairs if p.needs_verdict]


class PairVerdict(BaseModel):
    """The judge's reading of one unplaced pair: the entity id that stands for each name, or None when
    the graph holds nothing that is that thing."""

    pair_index: int
    a_entity: str | None
    b_entity: str | None
    reason: str


class ErScore(BaseModel):
    """ER accuracy with its counts. `accuracy` is None when no pair could be scored."""

    accuracy: float | None
    scored: int
    correct: int
    not_extracted: int

    @classmethod
    def of(cls, outcomes: list[bool | None]) -> "ErScore":
        """`outcomes`: per pair, right / wrong, or None when a name has no entity in the graph."""
        scored = [o for o in outcomes if o is not None]
        return cls(
            accuracy=sum(scored) / len(scored) if scored else None,
            scored=len(scored),
            correct=sum(scored),
            not_extracted=len(outcomes) - len(scored),
        )


def build_er_sheet(entities: list[SheetEntity], pairs: list[GoldPair]) -> ErSheet:
    """Place each gold name on the entities whose name or aliases equal it after `norm`."""
    by_name: dict[str, set[str]] = {}
    for e in entities:
        for name in [e.name, *e.aliases]:
            by_name.setdefault(norm(name), set()).add(e.id)

    def lookup(name: str) -> list[str]:
        return sorted(by_name.get(norm(name), set()))

    sheet_pairs = [
        SheetPair(index=i, a=p.a, b=p.b, same=p.same, a_entities=lookup(p.a), b_entities=lookup(p.b))
        for i, p in enumerate(pairs)
    ]
    return ErSheet(entities=entities, pairs=sheet_pairs)


def _outcome(same: bool, a_ids: set[str], b_ids: set[str]) -> bool | None:
    if not a_ids or not b_ids:
        return None
    # a name can sit on two entities (two types, same spelling): merged when any entity carries both
    return bool(a_ids & b_ids) == same


def score_er(sheet: ErSheet) -> ErScore:
    """Exact `er_accuracy`: only pairs whose names both exist in the graph are scored."""
    return ErScore.of([_outcome(p.same, set(p.a_entities), set(p.b_entities)) for p in sheet.pairs])


def _check_coverage(sheet: ErSheet, verdicts: list[PairVerdict]) -> None:
    """Exactly one verdict per unplaced pair, and only ids of entities on the sheet."""
    issues: list[str] = []
    needed = {p.index for p in sheet.to_judge()}
    given = [v.pair_index for v in verdicts]
    if len(given) != len(set(given)):
        issues.append("an ER pair has more than one verdict")
    if missing := needed - set(given):
        issues.append(f"{len(missing)} ER pairs have no verdict (indices {sorted(missing)[:3]})")
    if unknown := set(given) - needed:
        issues.append(f"ER verdicts for pairs exact lookup already placed (indices {sorted(unknown)[:3]})")
    ids = {e.id for e in sheet.entities}
    named = {x for v in verdicts for x in (v.a_entity, v.b_entity) if x is not None}
    if dangling := sorted(named - ids):
        issues.append(f"ER verdicts name entities not on the sheet: {dangling[:3]}")
    if issues:
        raise EvaluationError(issues)


def score_er_verdicts(sheet: ErSheet, verdicts: list[PairVerdict]) -> ErScore:
    """`er_accuracy_valid`: exact placements where both names were found, the judge's elsewhere.

    Raises `EvaluationError` when the verdicts do not cover exactly the pairs that need one.
    """
    _check_coverage(sheet, verdicts)
    by_pair = {v.pair_index: v for v in verdicts}
    outcomes: list[bool | None] = []
    for p in sheet.pairs:
        if not p.needs_verdict:
            outcomes.append(_outcome(p.same, set(p.a_entities), set(p.b_entities)))
            continue
        v = by_pair[p.index]
        a_ids = {v.a_entity} if v.a_entity else set()
        b_ids = {v.b_entity} if v.b_entity else set()
        outcomes.append(_outcome(p.same, a_ids, b_ids))
    return ErScore.of(outcomes)
