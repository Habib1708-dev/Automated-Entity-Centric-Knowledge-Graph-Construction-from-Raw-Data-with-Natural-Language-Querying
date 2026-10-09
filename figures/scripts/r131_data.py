"""The numbers the R131 figures show, read from the committed record (`tests/gold/r131/`): each seeding
setting's Seed Recall at every budget K, the pre-registered choice, and the paired comparisons behind the
seeding decision.

Role: the only reader of data for `r131_charts.py` and the decision diagram; the test checks these
functions against the counts the roadmap quotes.
Design: pure functions over the models `kg seed-grid` wrote (`SeedTable`, `SeedChoice`) and the exploratory
pairing file; nothing is recomputed from per-question outcomes, so a figure can only show a committed number.
Must not: read `out/` or MLflow, or draw.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from kgbuilder.validation.interval import Proportion
from kgbuilder.validation.seed_choice import SeedChoice
from kgbuilder.validation.seed_grid import BUDGETS, SeedTable

RECORD = Path(__file__).resolve().parents[2] / "tests" / "gold" / "r131"
DATASETS = ("furniture", "heldout", "generality")
LABELS = {
    "furniture": "Furniture (choice set)",
    "heldout": "Held-out (NHTSA)",
    "generality": "Generality",
    "pooled": "Pooled (three datasets)",
}
CHOSEN = "rrf60_c25_template"
RERANKER = "rerank_c25_template"
LEXICAL = "card_lexical_template"
DENSE = "card_dense_template"


def load_tables(path: Path = RECORD / "seed_grid.json") -> dict[str, SeedTable]:
    """The committed seed tables by name: the three datasets and `pooled`."""
    tables = json.loads(path.read_text(encoding="utf-8"))["tables"]
    return {t["name"]: SeedTable.model_validate(t) for t in tables}


def load_choice(path: Path = RECORD / "seed_choice.json") -> SeedChoice:
    """The committed choice of the pre-registered rule."""
    return SeedChoice.model_validate_json(path.read_text(encoding="utf-8"))


def recall(table: SeedTable, row: str) -> list[Proportion]:
    """`row`'s Seed Recall at every budget, in budget order."""
    return [table.score(row, k).seed_recall for k in BUDGETS]


def template_rows(table: SeedTable) -> list[str]:
    """Every template-card row the rule saw (choice and reference), in the grid's order."""
    return list(dict.fromkeys(s.name for s in table.scores if s.role in ("choice", "reference")))


@dataclass(frozen=True)
class Pairing:
    """One paired comparison at one K, oriented `a` against `b`: the questions with every target found only
    by `a` / only by `b`, and the exact McNemar p."""

    dataset: str
    a: str
    b: str
    k: int
    only_a: int
    only_b: int
    p: float


def against_lexical(choice: SeedChoice, row: str = CHOSEN) -> list[Pairing]:
    """`row` against the lexical cards alone at the chosen K, per dataset then pooled (pre-registered)."""
    return [
        Pairing(p.dataset, p.a, p.b, p.k, p.seed_found.only_a, p.seed_found.only_b, p.seed_found.p_value)
        for p in choice.against_baseline
        if p.a == row
    ]


def reranker_against_pool(path: Path = RECORD / "explore_rerank_vs_pool.json") -> list[Pairing]:
    """The reranker (25 + 25 candidates) against its own RRF pool, the chosen setting, at K = 5 and 10, per
    dataset then pooled (exploratory: chosen after the R131 table was seen)."""
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    return [
        Pairing(r["dataset"], r["a"], r["b"], r["k"], r["only_a"], r["only_b"], r["p"])
        for r in rows
        if r["a"] == RERANKER
    ]
