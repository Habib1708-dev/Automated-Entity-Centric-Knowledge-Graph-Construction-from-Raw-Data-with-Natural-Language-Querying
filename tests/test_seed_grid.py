"""The seed grid of plan R130-R136 (R130: hybrid/seed_fusion.py, hybrid/seed_rerank.py,
validation/seed_grid.py, validation/seed_choice.py, `kg seed-grid`), without Neo4j or a network: fusing two
card lists by RRF and by turns, each read only `candidates` deep; the reranker's prompt, its short ids and the
code check of its reply (ids outside the pool dropped, repeats counted once, the rest appended in pool order);
the pre-registered grid and its tie order; fused seeds rescored at every budget; the stopping rule (K within
2 targets of K = 50); the choice rule's three outcomes (a free setting wins; a reranker wins on the choice
dataset but is not confirmed; a reranker confirmed on the other datasets); and the stage with and without
`--rerank`. R131: the committed grid of the three builds reproduces its choice by the rule."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError, EvaluationError, MissingInputError
from kgbuilder.hybrid.seed_fusion import SeedSetting, fuse, interleave
from kgbuilder.hybrid.seed_rerank import PROMPT, RankedNodes, RerankOptions, SeedReranker, check_order
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.seed_stages import SeedDataset, SeedGridStage, read_template_cards
from kgbuilder.validation.retrieval_scores import RetrievalFingerprint, RetrievalOutcome, score_retrieval
from kgbuilder.validation.seed_choice import SeedChoice, choose, knee
from kgbuilder.validation.seed_grid import (
    BASELINES,
    BUDGETS,
    GRID,
    GridEntry,
    SeedTable,
    fused_seeds,
    seeded_report,
    tables_markdown,
)

from .evaluation_corpora import REPO, quoted_four_grams
from .fakes import RecordingTracker, ScriptedLLM

FP = RetrievalFingerprint(gold_hash="g", targets_hash="t", graph_digest="d", embed_model="e")
OPTIONS = RerankOptions(model="m", temperature=0.0, thinking="")


def rrf_setting(candidates=50, k=60, pool=None):
    method = "rerank" if pool else "rrf"
    return SeedSetting(
        name="s", representation="template", method=method, candidates=candidates, rrf_k=k, pool=pool
    )


def report(system, rows, budgets=BUDGETS, fingerprint=FP):
    """A report of `system` from rows (question id, seeds, targets)."""
    outcomes = [
        RetrievalOutcome(
            question_id=q, type="lookup", system=system, gold_chunks=[], ranked=[], seeds=seeds,
            gold_targets=targets, latency_ms=1.0,
        )
        for q, seeds, targets in rows
    ]  # fmt: skip
    return score_retrieval(outcomes, list(budgets), system, fingerprint)


# fusion


def test_rrf_puts_a_node_high_in_both_lists_first_and_keeps_every_node_once():
    assert fuse(["a", "b", "c"], ["c", "a", "d"], rrf_setting()) == ["a", "c", "b", "d"]


def test_a_list_is_read_only_candidates_deep():
    assert fuse(["a", "b", "c"], ["d", "e", "f"], rrf_setting(candidates=1)) == ["a", "d"]


def test_interleave_takes_turns_dense_first_and_skips_repeats():
    assert interleave([["a", "b", "c"], ["b", "d"]]) == ["a", "b", "d", "c"]


def test_a_rerank_setting_fuses_its_pool_by_rrf_and_cuts_it_to_the_pool_size():
    assert fuse(["a", "b", "c"], ["c", "a", "d"], rrf_setting(pool=2)) == ["a", "c"]


@pytest.mark.parametrize(
    "fields",
    [
        {"method": "rrf", "candidates": 5},  # RRF needs its constant
        {"method": "rrf", "candidates": 5, "rrf_k": 60, "pool": 10},  # only a rerank has a pool
        {"method": "rerank", "candidates": 5, "rrf_k": 60},  # a rerank needs its pool
    ],
)
def test_an_incomplete_setting_is_refused(fields):
    with pytest.raises(ValueError):
        SeedSetting(name="s", representation="template", **fields)


# the reranker


def test_the_check_drops_unknown_ids_counts_repeats_once_and_appends_the_rest_in_pool_order():
    ids = {"N1": "a", "N2": "b", "N3": "c", "N4": "d"}
    out = check_order(["N3", "N9", "N3", " N1 "], ids)
    assert out.order == ["c", "a", "b", "d"]
    assert (out.dropped, out.repeated, out.missing) == (1, 1, 2)


def test_the_reranker_shows_the_question_and_the_cards_under_short_ids_and_maps_the_reply_back():
    prompts = []
    llm = ScriptedLLM(lambda prompt, schema: prompts.append(prompt) or RankedNodes(order=["N2", "N1"]))
    reranker = SeedReranker(llm, OPTIONS, {"Thing:1": "card one", "Thing:2": "card two"})
    out = reranker.rerank("which one?", ["Thing:1", "Thing:2"])
    assert out.order == ["Thing:2", "Thing:1"] and (out.dropped, out.missing) == (0, 0)
    prompt = prompts[0]
    assert "which one?" in prompt and "[N1]\ncard one" in prompt and "[N2]\ncard two" in prompt
    assert "Thing:1" not in prompt  # the model copies short ids, never the refs
    assert llm.calls == [("RankedNodes", "m")]


def test_a_pool_node_without_a_card_is_refused_before_any_call():
    llm = ScriptedLLM(lambda prompt, schema: RankedNodes(order=[]))
    with pytest.raises(MissingInputError):
        SeedReranker(llm, OPTIONS, {"a": "card"}).rerank("q", ["a", "b"])
    assert llm.calls == []


def test_the_rerank_prompt_quotes_no_evaluation_corpus():
    assert quoted_four_grams(PROMPT) == []


# the grid


def test_the_grid_lists_the_free_settings_before_the_reranker_and_fewer_candidates_first():
    choice = [e for e in GRID if e.role == "choice"]
    methods = [e.setting.method for e in choice]
    assert methods.index("rerank") == len(methods) - 2  # the two rerank pools come last
    free = [e.setting.candidates for e in choice if e.setting.method == "rrf"]
    assert free == sorted(free) and set(free) == {10, 25, 50}
    assert {e.setting.representation for e in GRID if e.role == "record"} == {"summary"}
    assert len({e.name for e in GRID}) == len(GRID)


def test_fused_seeds_are_rescored_at_every_budget_without_chunks():
    dense = report("card_dense_template", [("Q1", ["x", "n1"], [["n1"], ["n2"]])], budgets=(5,))
    lexical = report("card_lexical_template", [("Q1", ["n2"], [["n1"], ["n2"]])], budgets=(5,))
    seeds = fused_seeds(dense, lexical, rrf_setting())
    assert seeds == {"Q1": ["x", "n2", "n1"]}
    fused = seeded_report(dense, "fused", seeds)
    assert fused.budgets == list(BUDGETS) and fused.overall.seed_recall[5].k == 2
    assert fused.overall.complete[5].n == 0  # no evidence measured here


def test_lists_of_other_runs_are_not_fused():
    dense = report("card_dense_template", [("Q1", ["a"], [["a"]])])
    other = report(
        "card_lexical_template",
        [("Q1", ["a"], [["a"]])],
        fingerprint=FP.model_copy(update={"graph_digest": "x"}),
    )
    with pytest.raises(EvaluationError):
        fused_seeds(dense, other, rrf_setting())


# the rule


def test_the_knee_is_the_smallest_k_within_two_targets_of_k_50():
    # one question, ten targets n0..n9, found one per position: recall at K = min(K, 10)... capped by seeds
    seeds = [f"x{i}" for i in range(12)] + [f"n{i}" for i in range(10)]  # n0 at 13 ... n9 at 22
    r = report("s", [("Q1", seeds, [[f"n{i}"] for i in range(10)])])
    assert r.overall.seed_recall[50].k == 10 and r.overall.seed_recall[20].k == 8
    assert knee(r) == 20


def _rows(found):
    """Six questions, one target each; `found` says which are found at the first seed."""
    return [(f"Q{i}", [f"n{i}"] if hit else ["z"], [[f"n{i}"]]) for i, hit in enumerate(found)]


ENTRIES = [
    GridEntry(name="base", role="baseline"),
    GridEntry(name="free", role="choice", setting=rrf_setting()),
    GridEntry(name="ranked", role="choice", setting=rrf_setting(pool=50)),
]


def _reports(free, ranked):
    return {"base": report("base", _rows([False] * 6)), "free": report("free", _rows(free)),
            "ranked": report("ranked", _rows(ranked))}  # fmt: skip


def test_a_free_setting_that_finds_the_most_on_the_choice_dataset_is_chosen():
    reports = {"one": _reports([True] * 6, [True] * 5 + [False]), "two": _reports([True] * 6, [False] * 6)}
    choice = choose(reports, ENTRIES, "one", "base")
    assert (choice.best, choice.setting, choice.rerank_confirmation) == ("free", "free", None)
    assert choice.k == 5
    pooled = next(p for p in choice.against_baseline if p.dataset == "pooled" and p.a == "free")
    assert (pooled.seed_found.only_a, pooled.seed_found.only_b) == (12, 0)


def test_a_reranker_best_on_the_choice_dataset_but_not_confirmed_falls_back_to_the_free_setting():
    reports = {"one": _reports([True] * 5 + [False], [True] * 6), "two": _reports([True] * 6, [True] * 6)}
    choice = choose(reports, ENTRIES, "one", "base")
    assert (choice.best, choice.best_free, choice.setting) == ("ranked", "free", "free")
    assert choice.rerank_confirmation.seed_found.p_value == 1.0


def test_a_reranker_confirmed_on_the_other_datasets_is_kept():
    reports = {"one": _reports([False] * 6, [True] * 6), "two": _reports([False] * 6, [True] * 6)}
    choice = choose(reports, ENTRIES, "one", "base")
    assert choice.setting == "ranked" and choice.rerank_confirmation.seed_found.p_value < 0.05


def test_the_choice_dataset_must_be_among_the_datasets():
    with pytest.raises(ConfigurationError):
        choose({"one": _reports([True] * 6, [True] * 6)}, ENTRIES, "furniture", "base")


# the stage


QUESTIONS = ["F01", "F02", "F03"]  # worded by the committed furniture QA gold
REFS = [f"Thing:{i}" for i in range(8)]


def _saved(tmp_path):
    """Two datasets' R128-like card reports; F03 has no target, so it is never reranked."""
    folders = {}
    for name in ("one", "two"):
        folder = folders[name] = tmp_path / name
        folder.mkdir()
        rows = {"dense": [REFS[:4], REFS[2:6], REFS[:2]], "lexical": [REFS[3:7], REFS[1:3], REFS[5:]]}
        targets = [[["Thing:3"]], [["Thing:2"], ["Thing:5"]], []]
        for mode, seeds in rows.items():
            for rep in ("template", "summary"):
                system = f"card_{mode}_{rep}"
                r = report(
                    system, list(zip(QUESTIONS, seeds, targets, strict=True)), budgets=(5, 10, 15, 20, 50)
                )
                (folder / f"retrieval_{system}.json").write_text(r.model_dump_json(), encoding="utf-8")
    return folders


def _stage_context(tmp_path, llm=None):
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "grid", tracker=tracker, llm=llm)
    return ctx, tracker


def test_the_stage_without_rerank_scores_the_free_grid_and_calls_no_model(tmp_path):
    folders = _saved(tmp_path)
    ctx, tracker = _stage_context(tmp_path)
    stage = SeedGridStage(
        {n: SeedDataset(reports=f) for n, f in folders.items()}, rerank=False, choose_on="one"
    )
    state = run_stages(ctx, PipelineState(), [stage])
    run = tracker.run("seed_grid")
    assert "rerank_c25_template" not in run.logged_params["grid"] and run.logged_params["rerank"] is False
    assert run.logged_metrics["chosen_k"] == state.seed_choice.k and "rerank_pools" not in run.logged_metrics
    written = json.loads((tmp_path / "grid" / "seed_grid.json").read_text(encoding="utf-8"))
    assert [t["name"] for t in written["tables"]] == ["one", "two", "pooled"]
    assert "## The choice" in (tmp_path / "grid" / "seed_grid.md").read_text(encoding="utf-8")


def test_the_stage_with_rerank_orders_only_the_pools_of_questions_with_targets(tmp_path):
    folders = _saved(tmp_path)
    units = tmp_path / "units.jsonl"
    lines = [
        {"unit": "card", "ref": r, "text": f"card {r}", "evidence_hash": "h", "truncated": False}
        for r in REFS
    ]
    units.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    gold = REPO / "tests" / "gold" / "qa" / "furniture_qa.json"
    llm = ScriptedLLM(lambda prompt, schema: RankedNodes(order=["N2", "N1", "N99"]))
    ctx, tracker = _stage_context(tmp_path, llm)
    datasets = {n: SeedDataset(reports=f, units=units, gold=gold) for n, f in folders.items()}
    run_stages(ctx, PipelineState(), [SeedGridStage(datasets, rerank=True, choose_on="one")])
    run = tracker.run("seed_grid")
    # 2 datasets x 2 rerank pools x 2 questions with targets
    assert len(llm.calls) == 8 and run.logged_metrics["rerank_pools"] == 8
    assert (
        run.logged_metrics["rerank_dropped"] == 8
        and run.logged_params["rerank_model"] == Settings().index_summary_model
    )
    assert "prompts/seed_rerank.txt" in run.artifacts
    orders = (tmp_path / "grid" / "seed_rerank.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["question_id"] for line in orders} == {"F01", "F02"}


def test_rerank_without_cards_or_gold_is_refused(tmp_path):
    with pytest.raises(ConfigurationError):
        SeedGridStage({"one": SeedDataset(reports=tmp_path)}, rerank=True, choose_on="one")


def test_a_units_file_of_summary_cards_is_not_read_as_template_cards(tmp_path):
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps({"unit": "card", "ref": "a", "text": "t", "fallback": False}) + "\n", encoding="utf-8"
    )
    with pytest.raises(EvaluationError):
        read_template_cards(units)


# R131: the committed record of the grid on the three builds

R131 = REPO / "tests" / "gold" / "r131"


def test_r131_the_committed_grid_reproduces_its_choice_by_the_pre_registered_rule():
    grid = json.loads((R131 / "seed_grid.json").read_text(encoding="utf-8"))
    entries = [GridEntry.model_validate(e) for e in grid["entries"]]
    tables = [SeedTable.model_validate(t) for t in grid["tables"]]
    choice = SeedChoice.model_validate_json((R131 / "seed_choice.json").read_text(encoding="utf-8"))
    assert [t.name for t in tables] == ["furniture", "heldout", "generality", "pooled"]
    assert [e.name for e in entries] == [e.name for e in [*BASELINES, *GRID]]
    pooled = tables[3]
    # n: 213 targets over 141 questions; the headline counts at K = 5
    assert (
        pooled.score("card_lexical_template", 5).seed_recall.n,
        pooled.score("rrf60_c25_template", 5).seed_found.n,
    ) == (213, 141)
    assert [
        pooled.score(s, 5).seed_recall.k
        for s in ("card_lexical_template", "rrf60_c25_template", "rerank_c25_template")
    ] == [164, 171, 189]
    # step 1 again on furniture: most targets at 20, then most questions, then the grid's order
    furniture = tables[0]
    rows = [e.name for e in entries if e.role == "choice"]
    best = min(
        rows,
        key=lambda r: (
            -furniture.score(r, 20).seed_recall.k,
            -furniture.score(r, 20).seed_found.k,
            rows.index(r),
        ),
    )
    assert (best, choice.best, choice.setting) == ("rrf60_c25_template", best, best)
    # step 2 again: the smallest K within 2 targets of the chosen row's recall at 50
    at_50 = furniture.score(best, 50).seed_recall.k
    assert choice.k == next(k for k in BUDGETS if furniture.score(best, k).seed_recall.k >= at_50 - 2) == 15
    assert (R131 / "seed_grid.md").read_text(encoding="utf-8") == tables_markdown(
        tables, entries
    ) + "\n" + choice.markdown()
