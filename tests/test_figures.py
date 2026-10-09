"""The thesis figures (R129, figures/): the R128 and R131 data functions return the committed counts (so a
chart can only show a committed number), the build draws every figure into a fresh folder, and every committed
figure is built, captioned in its folder's README.md and listed in the index, with no caption naming a missing
file. No Neo4j, no network: the figures read committed records only."""

import re
from pathlib import Path

import pytest
from figures.scripts import r128_data, r131_data
from figures.scripts.build import build

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "figures"


def test_the_r128_data_functions_return_the_committed_counts():
    table = r128_data.load_table()
    chunk = r128_data.series(table, "pooled", "chunk_lexical", "complete")
    card = r128_data.series(table, "pooled", "card_lexical_template", "seed_recall")
    assert [(p.k, p.n) for p in chunk] == [(86, 99), (88, 99), (90, 99), (91, 99), (95, 99)]
    assert [p.k for p in card] == [164, 185, 194, 201, 208] and card[0].n == 213
    assert r128_data.shortfall(table, "pooled", "claim_dense", "chunks")[3] == pytest.approx(56 / 99)
    assert r128_data.shortfall(table, "pooled", "graph_retrieval", "seeds")[1] == pytest.approx(113 / 141)
    by_key = {(p.card, p.text, p.measure, p.k): p for p in r128_data.pairings()}
    lexical = by_key[("card_lexical_template", "chunk_lexical", "seeds", 5)]
    assert (lexical.only_card, lexical.only_text) == (58, 14) and lexical.p < 0.001
    dense = by_key[("card_dense_template", "chunk_dense", "evidence", 5)]
    assert (dense.only_card, dense.only_text, round(dense.p, 3)) == (4, 12, 0.077)
    assert len(by_key) == 25  # five pairings at five budgets


def test_the_r131_data_functions_return_the_committed_counts():
    tables, choice = r131_data.load_tables(), r131_data.load_choice()
    pooled = tables["pooled"]
    assert [p.k for p in r131_data.recall(pooled, r131_data.CHOSEN)] == [171, 194, 203, 205, 206, 207, 207]
    assert [p.k for p in r131_data.recall(pooled, r131_data.RERANKER)][:3] == [189, 198, 204]
    assert r131_data.recall(pooled, r131_data.LEXICAL)[2].k == 194
    assert pooled.score(r131_data.CHOSEN, 5).seed_recall.n == 213
    assert (choice.setting, choice.k) == (r131_data.CHOSEN, 15)
    lexical = {p.dataset: p for p in r131_data.against_lexical(choice)}["pooled"]
    assert (lexical.only_a, lexical.only_b, round(lexical.p, 3)) == (11, 2, 0.022)
    pool = {(p.dataset, p.k): p for p in r131_data.reranker_against_pool()}
    assert (pool[("pooled", 5)].only_a, pool[("pooled", 5)].only_b, pool[("pooled", 5)].p) == (22, 5, 0.0015)
    assert (pool[("pooled", 10)].only_a, pool[("pooled", 10)].only_b) == (9, 5)


def test_every_figure_is_built_captioned_and_indexed(tmp_path):
    built = build(results=tmp_path / "results", diagrams=tmp_path / "diagrams")
    names = sorted(p.name for p in built)
    assert all(p.stat().st_size > 0 for p in built) and len(names) == 20  # ten figures, SVG and PNG
    committed = sorted(p for p in FIGURES.rglob("*") if p.suffix in (".svg", ".png"))
    assert sorted(p.name for p in committed) == names  # nothing committed that the build does not make
    index = (FIGURES / "README.md").read_text(encoding="utf-8")
    for figure in committed:
        caption = (figure.parent / "README.md").read_text(encoding="utf-8")
        assert f"]({figure.name})" in caption or f"[.{figure.suffix[1:]}]({figure.name})" in caption
        if figure.suffix == ".png":
            assert figure.stem in index
    for readme in FIGURES.rglob("README.md"):  # every file a caption links exists
        for target in re.findall(r"\]\(([^)#]+)\)", readme.read_text(encoding="utf-8")):
            assert (readme.parent / target).exists(), f"{readme}: {target}"
