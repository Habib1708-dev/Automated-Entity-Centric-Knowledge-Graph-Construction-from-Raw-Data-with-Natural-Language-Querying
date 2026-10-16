"""The thesis figures (R129, figures/): the R128, R131 and R157 data functions return the committed counts (so
a chart can only show a committed number), the build draws every figure into a fresh folder, and every
committed figure is built, captioned in its folder's README.md and listed in the index, with no caption naming
a missing file. R174: the r173 data functions return the three measurements' committed counts, the
wrong answers by cause, the operations used and the costs. R229/R230: the r229 data functions return the four
selected builds' committed precision, recall, mention and audit counts, and every figure says it is a selected
comparison. R231: the r231 data functions return the four datasets' question-answering counts. No Neo4j, no
network: the figures read committed records only."""

import re
from pathlib import Path

import pytest
from figures.scripts import r128_data, r131_data, r157_data, r173_data, r229_charts, r229_data, r231_data
from figures.scripts.build import build

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "figures"
TYPES_HARDEST = ("aggregation", "negation_sensitive")  # by meaning, the two fig2's title names


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


def test_the_r157_data_functions_return_the_committed_counts():
    outcomes = r157_data.load_outcomes()
    by_dataset = {a.group: a for a in r157_data.by_dataset(outcomes)}
    pooled = by_dataset["pooled"]
    assert (pooled.exact.k, pooled.meaning.k, pooled.exact.n) == (239, 243, 250)
    per_dataset = {d: (a.exact.k, a.meaning.k, a.meaning.n) for d, a in by_dataset.items() if d != "pooled"}
    assert per_dataset == {
        "ntsb": (50, 50, 50),
        "lighthouses": (46, 48, 50),
        "furniture": (46, 46, 50),
        "heldout": (49, 49, 50),
        "generality": (48, 50, 50),
    }
    # the intervals the captions quote: pooled exact 92.3-97.5, by meaning 94.3-98.6, furniture 81.2-96.8
    assert (round(pooled.exact.low, 3), round(pooled.exact.high, 3)) == (0.923, 0.975)
    assert (round(pooled.meaning.low, 3), round(pooled.meaning.high, 3)) == (0.943, 0.986)
    furniture = by_dataset["furniture"].meaning
    assert (round(furniture.low, 3), round(furniture.high, 3)) == (0.812, 0.968)
    split = r157_data.exact_split(outcomes)  # exact match: code-scored answers plus the judged free text
    assert (split.code.k, split.code.n, split.free_text.k, split.free_text.n) == (216, 227, 23, 23)
    assert split.code.n + split.free_text.n == len(outcomes)
    by_type = r157_data.by_type(outcomes)
    per_type = {a.group: (a.exact.k, a.meaning.k, a.meaning.n) for a in by_type}
    assert per_type == {
        "lookup": (40, 40, 40),
        "structured_filter": (45, 45, 45),
        "disambiguation": (38, 39, 39),
        "multi_hop": (42, 44, 45),
        "negation_sensitive": (32, 33, 35),
        "aggregation": (42, 42, 46),
    }
    # fig2's title: the two hardest types by meaning, aggregation 79.7-96.6, and their intervals overlap
    hardest = sorted(by_type, key=lambda a: (a.meaning.rate, a.exact.rate))[:2]
    assert tuple(a.group for a in hardest) == TYPES_HARDEST
    aggregation, negation = (a.meaning for a in hardest)
    assert (round(aggregation.low, 3), round(aggregation.high, 3)) == (0.797, 0.966)
    assert aggregation.high > negation.low and negation.high > aggregation.low
    assert len(r157_data.wrong(outcomes)) == 11
    causes = {c.cause: (c.both, c.exact_only) for c in r157_data.by_cause(outcomes)}
    assert causes == {  # the ids fig3 prints at each bar
        "agent_reasoning": (("LB09", "LB11", "FB10", "FB14", "FB18", "FB38", "HB41"), ()),
        "tool_limit": (("LB09", "LB11", "FB10", "FB14", "FB38", "HB41"), ()),
        "retrieval_miss": (("FB18",), ()),
        "graph_build": (("FB14",), ()),
        "answer_form": ((), ("LB06", "LB39", "GB05", "GB33")),
    }
    costs = r157_data.load_costs()
    assert (round(costs.builds, 3), round(costs.agent, 3)) == (1.529, 3.870)
    # R155's sum is the two builds plus the refused first plan, as the captions say
    assert (round(costs.builds - costs.refused_plan, 3), round(costs.refused_plan, 3)) == (1.329, 0.200)
    assert costs.agent_runs["ntsb"] == "fbf2339f"


def test_the_r173_data_functions_return_the_committed_counts():
    measurements = {m.key: m for m in r173_data.measurements()}
    pooled = {k: (m.pooled.exact.k, m.pooled.meaning.k, m.n) for k, m in measurements.items()}
    assert pooled == {"r157": (239, 243, 250), "r166": (94, 94, 100), "r173": (98, 98, 100)}
    r173 = measurements["r173"]
    assert (round(r173.pooled.meaning.low, 3), round(r173.pooled.meaning.high, 3)) == (0.930, 0.994)
    wrong = {k: tuple(o.question_id for o in m.outcomes if o.wrong) for k, m in measurements.items()}
    assert wrong["r166"] == ("NC11", "LC05", "LC07", "LC08", "FC05", "HC05") and wrong["r173"] == (
        "ND11",
        "HD05",
    )
    causes = {c.cause: c.both for c in r157_data.by_cause(list(r173.outcomes))}
    assert (causes["tool_limit"], causes["agent_reasoning"]) == (("ND11", "HD05"), ("ND11",))
    by_type = {a.group: (a.meaning.k, a.meaning.n) for a in r157_data.by_type(list(r173.outcomes))}
    assert by_type["aggregation"] == (17, 18) and by_type["multi_hop"] == (21, 21)
    used = {u.operation: (u.question_ids, u.right) for u in r173_data.operations_used(r173.outcomes)}
    assert used == {
        "distinct": (("ND05", "LD06", "HD07"), 3),
        "empty_cell": (("ND08", "LD10", "HD08"), 3),
    }
    assert not any(o.operations for o in measurements["r166"].outcomes)
    against = r173_data.compare(r173, measurements["r166"])
    assert (round(against.z, 2), round(against.p, 2)) == (1.44, 0.15)
    costs = {c.key: (round(c.cost, 3), c.questions, round(c.wasted, 3)) for c in r173_data.run_costs()}
    assert costs == {"r157": (3.870, 250, 0.0), "r166": (1.603, 100, 0.0), "r173": (1.627, 100, 0.501)}


def test_the_r229_data_functions_return_the_committed_counts():
    builds = {b.dataset: b for b in r229_data.load_all()}
    # R230: the figures are a selected comparison of four datasets
    assert list(builds) == list(r229_data.SELECTED) == ["ntsb", "furniture", "heldout", "generality"]
    kn = {ds: ((b.strict.k, b.content.k, b.content.n), (b.recall.k, b.recall.n)) for ds, b in builds.items()}
    assert kn == {
        "ntsb": ((285, 289, 293), (13, 74)),
        "furniture": ((498, 518, 530), (42, 72)),
        "heldout": ((480, 520, 530), (19, 67)),
        "generality": ((202, 208, 216), (12, 66)),
    }
    # fig2's split of the misses: the schema's (no fact type) and the rest
    assert {ds: (b.schema_misses, b.other_misses) for ds, b in builds.items()} == {
        "ntsb": (6, 55),
        "furniture": (11, 19),
        "heldout": (32, 16),
        "generality": (39, 15),
    }
    mentions = {
        ds: (b.mention_exact.k, b.mention_judged.k, b.mention_precision.k) for ds, b in builds.items()
    }
    assert mentions == {
        "ntsb": (133, 153, 51),
        "furniture": (53, 68, 54),
        "heldout": (100, 116, 55),
        "generality": (88, 103, 49),
    }
    audit = {ds: ((b.c3.k, b.c3.n), (b.c4.k, b.c4.n), (b.c6.k, b.c6.n)) for ds, b in builds.items()}
    assert audit["ntsb"] == ((80, 81), (36, 36), (58, 60))
    assert audit["furniture"] == ((150, 151), (63, 63), (109, 109))
    assert all(b.c3_passed and b.c4_passed and b.c6_passed for b in builds.values())


def test_the_excluded_dataset_is_kept_in_the_records_and_the_figures_say_they_are_a_selection():
    # R230/R231: lighthouses leaves the figures, not the records; it fails C4 and so C3 on the same two links
    (lighthouses,) = r229_data.excluded()
    assert lighthouses.dataset == "lighthouses" and (lighthouses.c4.k, lighthouses.c4.n) == (34, 36)
    assert not lighthouses.c3_passed and not lighthouses.c4_passed and lighthouses.c6_passed
    # every build figure's subtitle says it is a selection; by the user's request it names no left-out dataset
    note = r229_charts._selected_note()
    assert note == "Selected comparison of 4 of the 5 measured datasets; see the captions.\n"


def test_the_r231_data_functions_return_the_committed_counts():
    measurements = r231_data.measurements()
    assert [m.key for m in measurements] == ["r157", "r166", "r173"]
    assert all({o.dataset for o in m.outcomes} == set(r229_data.SELECTED) for m in measurements)
    accuracy = {
        m.key: {
            a.group: (a.exact.k, a.meaning.k, a.meaning.n) for a in r231_data.by_dataset(list(m.outcomes))
        }
        for m in measurements
    }
    assert {k: a["pooled"] for k, a in accuracy.items()} == {
        "r157": (193, 195, 200),
        "r166": (77, 77, 80),
        "r173": (78, 78, 80),
    }
    assert accuracy["r157"]["generality"] == (48, 50, 50) and accuracy["r157"]["furniture"] == (46, 46, 50)
    wrong = [o.question_id for o in r157_data.wrong(list(measurements[0].outcomes))]
    assert wrong == ["FB10", "FB14", "FB18", "FB38", "HB41", "GB05", "GB33"]
    weakest = [min(r157_data.by_type(list(m.outcomes)), key=lambda a: a.meaning.rate) for m in measurements]
    assert [(a.group, a.meaning.k, a.meaning.n) for a in weakest] == [
        ("aggregation", 34, 37),
        ("aggregation", 13, 15),
        ("structured_filter", 12, 13),
    ]


def test_every_figure_is_built_captioned_and_indexed(tmp_path):
    built = build(results=tmp_path / "results", diagrams=tmp_path / "diagrams")
    names = sorted(p.name for p in built)
    assert all(p.stat().st_size > 0 for p in built) and len(names) == 56  # twenty-eight figures, SVG and PNG
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
