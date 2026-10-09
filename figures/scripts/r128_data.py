"""The numbers the R128 figures show, read from the committed record (`tests/gold/r128/`): each technique's
rates per budget K, the share of its lists shorter than K, and the exploratory paired comparisons.

Role: the only reader of data for `r128_charts.py`; the charts draw what these functions return, and the
test checks these functions against the counts the roadmap quotes.
Design: pure functions over `RetrievalTable` (validation/retrieval_table.py), the model `kg retrieve-table`
wrote; nothing is recomputed from per-question outcomes, so a figure can only show a committed number.
Must not: read `out/` or MLflow, or draw.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kgbuilder.validation.interval import Proportion
from kgbuilder.validation.retrieval_table import RetrievalTable

RECORD = Path(__file__).resolve().parents[2] / "tests" / "gold" / "r128"
DATASETS = ("furniture", "heldout", "generality")
LABELS = {
    "furniture": "Furniture (tuning set)",
    "heldout": "Held-out (NHTSA)",
    "generality": "Generality",
    "pooled": "Pooled (three datasets)",
}
Measure = Literal["complete", "evidence_recall", "seed_recall", "seed_found"]


def load_table(path: Path = RECORD / "retrieve_table.json") -> RetrievalTable:
    """The committed technique table of R128 (or another table file of the same model)."""
    return RetrievalTable.model_validate_json(path.read_text(encoding="utf-8"))


def series(table: RetrievalTable, dataset: str, system: str, measure: Measure) -> list[Proportion]:
    """`system`'s proportion for `measure` at every budget of the table, in budget order. A technique that
    starts from no node has no seed measure: `ValueError` names it."""
    t = next(t for t in table.tables if t.name == dataset)
    out = [getattr(t.score(system, k), measure) for k in table.budgets]
    if any(p is None for p in out):
        raise ValueError(f"{system} has no {measure} on {dataset}")
    return out


def shortfall(
    table: RetrievalTable, dataset: str, system: str, lists: Literal["chunks", "seeds"]
) -> list[float]:
    """The share of the questions measured whose `lists` hold fewer than K items, at every budget: of the
    questions with evidence for chunk lists, of those with targets for seed lists."""
    t = next(t for t in table.tables if t.name == dataset)
    shares = []
    for k in table.budgets:
        s = t.score(system, k)
        short, n = (s.short_chunks, s.complete.n) if lists == "chunks" else (s.short_seeds, s.seed_found.n)
        shares.append(short / n)
    return shares


@dataclass(frozen=True)
class Pairing:
    """One exploratory paired comparison at one K, oriented card against text: the questions only the card
    technique gets fully right, those only the text technique does, and the exact McNemar p."""

    card: str
    text: str
    measure: Literal["evidence", "seeds"]
    k: int
    only_card: int
    only_text: int
    p: float


# the pairings figure 4 shows: (record file, card system, text system, measure), chosen after the table was
# seen (R128 calls them exploratory)
EXPLORATORY = (
    ("explore_chunk_lexical_vs_card_lexical_template", "card_lexical_template", "chunk_lexical", "seeds"),
    ("explore_chunk_dense_vs_card_dense_template", "card_dense_template", "chunk_dense", "seeds"),
    ("explore_card_lexical_template_vs_claim_dense", "card_lexical_template", "claim_dense", "seeds"),
    ("explore_chunk_lexical_vs_card_lexical_template", "card_lexical_template", "chunk_lexical", "evidence"),
    ("explore_chunk_dense_vs_card_dense_template", "card_dense_template", "chunk_dense", "evidence"),
)


def pairings(record: Path = RECORD) -> list[Pairing]:
    """Every exploratory pairing at every K, from the pooled table of its two-system record file."""
    out = []
    for file, card, text, measure in EXPLORATORY:
        pooled = next(t for t in load_table(record / f"{file}.json").tables if t.name == "pooled")
        for b in (b for b in pooled.best if b.measure == measure):
            # the table names the better one "best" (side a); orient every pairing card against text
            a, b_ = (
                (b.paired.only_a, b.paired.only_b) if b.best == card else (b.paired.only_b, b.paired.only_a)
            )
            out.append(Pairing(card, text, measure, b.k, a, b_, b.paired.p_value))
    return out
