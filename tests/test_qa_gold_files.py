"""The committed question-answer gold files (R70): each fits its corpus as the pipeline chunks it (every
quote verbatim in its chunk, every cited record in its staged file, every question typed), every answer
of a question with a query equals what DuckDB computes from the source files (R73), the two real
datasets ask every question type at least five times and every record type at least ten times (R73), the
earlier gold questions are carried over with their answers unchanged, and the generality corpus asks every
hard case. R143a: the fresh files (ten questions per dataset) pass the same per-file checks. R152: so do the
benchmark files, with 50 questions each, every type at least six times, and none asked before; R160: so do
the confirmation files, 20 questions each, every type at least three times; R171: a second confirmation set
(`_qa_confirm2.json`), written blind after plan R167-R170, passes the same checks; R177: so does a third
(`_qa_confirm3.json`), written blind after plan R175-R176. R180: the failure set (`tests/gold/r180/`) is the
20 questions judged wrong by meaning across R157-R179, copied unchanged. R185: experiment A's targeted
files (`_qa_semantic.json`) pass the per-file checks and ask 2 retrieval questions each over every passage
of an idea; its run files (`tests/gold/r185/`) are those questions, the experiment's old failures and the 10
seeded controls, copied unchanged. No Neo4j, no LLM.
"""

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from kgbuilder.core.text import norm
from kgbuilder.validation.qa_gold import HardCase, QAGold, QuestionType, check_qa_gold, load_qa_gold
from kgbuilder.validation.qa_records import check_record_answers

from .qa_corpus import REPO, rebuild_corpus

QA_GOLD = REPO / "tests" / "gold" / "qa"
REAL = ["furniture_qa.json", "heldout_qa.json"]
# R143a: ten fresh questions per dataset, written after R136's failures were analysed, for the agent's
# re-measurement on questions its fixes were not shaped by; the per-file checks hold for them too
FRESH = ["furniture_qa_fresh.json", "heldout_qa_fresh.json", "generality_qa_fresh.json"]
# R152: the benchmark's 50 unseen questions per dataset (plan R152-R157), written after R151's freeze from the
# source files only; the per-file checks hold for them, and below their size, type mix and novelty
BENCH = [
    "furniture_qa_bench.json",
    "heldout_qa_bench.json",
    "generality_qa_bench.json",
    "lighthouses_qa_bench.json",
    "ntsb_qa_bench.json",
]
# R160: 20 more unseen questions per dataset, written before R161-R164's fixes, to measure them
CONFIRM_DATASETS = ("furniture", "heldout", "generality", "lighthouses", "ntsb")
CONFIRM = [f"{name}_qa_confirm.json" for name in CONFIRM_DATASETS]
# R171: the second confirmation set, 20 more per dataset written blind after R167-R170, to measure them
CONFIRM += [f"{name}_qa_confirm2.json" for name in CONFIRM_DATASETS]
# R177: the third confirmation set, 20 more per dataset written blind after R175-R176, to measure them
CONFIRM += [f"{name}_qa_confirm3.json" for name in CONFIRM_DATASETS]
# R185: experiment A's targeted questions (plan R183-R191), 2 per dataset written blind on ideas worded
# differently across passages
SEMANTIC = [f"{name}_qa_semantic.json" for name in CONFIRM_DATASETS]
GOLD_FILES = [*REAL, "generality_qa.json", *FRESH, *BENCH, *CONFIRM, *SEMANTIC]


@pytest.fixture(scope="module", params=GOLD_FILES)
def gold_file(request) -> tuple[str, QAGold]:
    return request.param, load_qa_gold(QA_GOLD / request.param)


def test_every_quote_is_verbatim_in_its_chunk_and_every_record_is_in_its_file(gold_file, tmp_path):
    _, gold = gold_file
    corpus = rebuild_corpus(gold.corpus, tmp_path / "staging")
    check_qa_gold(gold, corpus.chunks, corpus.rows)


def test_every_stored_record_answer_equals_what_its_query_computes_from_the_data(gold_file):
    _, gold = gold_file
    check_record_answers(gold, REPO / gold.corpus.data_dir)


def test_every_question_has_a_type_and_evidence(gold_file):
    # the models reject a question without them; this pins that the files were read through the models
    _, gold = gold_file
    assert all(isinstance(q.type, QuestionType) and (q.chunks or q.records or q.sql) for q in gold.questions)


# the types that records answer: R73 brings each to ten questions, with answers computed from the data,
# so a paired test per type can show a difference (at n = 6-7 it rarely can)
RECORD_TYPES = (QuestionType.STRUCTURED_FILTER, QuestionType.AGGREGATION, QuestionType.MULTI_HOP)


@pytest.mark.parametrize("name", REAL)
def test_the_real_datasets_ask_every_type_at_least_five_times_and_every_record_type_ten_times(name):
    gold = load_qa_gold(QA_GOLD / name)
    counts = Counter(q.type for q in gold.questions)
    assert 30 <= len(gold.questions) <= 80
    assert {t: counts[t] for t in QuestionType if counts[t] < 5} == {}
    assert {t: counts[t] for t in RECORD_TYPES if counts[t] < 10} == {}


@pytest.mark.parametrize(
    ("name", "earlier"), [(REAL[0], "r65/furniture_gold.json"), (REAL[1], "r65/heldout_gold.json")]
)
def test_every_earlier_gold_question_is_carried_over_with_its_answer_unchanged(name, earlier):
    questions = json.loads((REPO / "tests" / "gold" / earlier).read_text(encoding="utf-8"))["questions"]
    carried = {q.origin: q for q in load_qa_gold(QA_GOLD / name).questions if q.origin}
    assert set(carried) == {f"tests/gold/{earlier}#{i}" for i in range(len(questions))}
    for index, old in enumerate(questions):
        new = carried[f"tests/gold/{earlier}#{index}"]
        assert sorted(norm(e.name) for e in new.expected.entities) == sorted(norm(n) for n in old["expected"])


def test_the_generality_corpus_asks_every_hard_case():
    gold = load_qa_gold(QA_GOLD / "generality_qa.json")
    assert set(HardCase) <= {q.hard_case for q in gold.questions}


def test_the_generality_corpus_spans_at_least_three_domains_in_about_30_documents():
    # version 2 (R73): the 11 documents of R70 and 21 distractors that name the same people, pumps and
    # places in other contexts, so that a reader no longer sees half of the corpus in its top 5 chunks
    corpus = REPO / "tests" / "fixtures" / "generality"
    documents = [p for p in corpus.rglob("*") if p.suffix in {".md", ".txt"}]
    assert 28 <= len(documents) <= 34
    # one folder per domain: the folders are the unrelated domains the corpus claims to cover
    assert len({p.parent for p in documents}) >= 3
    assert any(p.suffix == ".csv" for p in Path(corpus).rglob("*"))


@pytest.mark.parametrize("name", BENCH)
def test_a_benchmark_file_asks_fifty_questions_of_every_type_none_asked_before(name):
    """R152: 50 questions, every type at least six times, about four in five scored by code, and no question
    an earlier gold file of its dataset already asks (compared after normalising case and spacing)."""
    gold = load_qa_gold(QA_GOLD / name)
    counts = Counter(q.type for q in gold.questions)
    assert len(gold.questions) == 50
    assert {t: counts[t] for t in QuestionType if counts[t] < 6} == {}
    by_code = [q for q in gold.questions if q.expected.entities is not None or q.expected.number is not None]
    assert len(by_code) >= 35
    dataset = name.removesuffix("_qa_bench.json")
    earlier = {
        norm(q.question)
        for other in QA_GOLD.glob(f"{dataset}_qa*.json")
        if other.name != name
        for q in load_qa_gold(other).questions
    }
    asked = [norm(q.question) for q in gold.questions]
    assert len(set(asked)) == len(asked) and not set(asked) & earlier


@pytest.mark.parametrize("name", CONFIRM)
def test_a_confirmation_file_asks_twenty_questions_of_every_type_none_asked_before(name):
    """R160: 20 questions, every type at least three times, at least 14 scored by code, and no question an
    earlier gold file of its dataset already asks (compared after normalising case and spacing)."""
    gold = load_qa_gold(QA_GOLD / name)
    counts = Counter(q.type for q in gold.questions)
    assert len(gold.questions) == 20
    assert {t: counts[t] for t in QuestionType if counts[t] < 3} == {}
    by_code = [q for q in gold.questions if q.expected.entities is not None or q.expected.number is not None]
    assert len(by_code) >= 14
    dataset = name.split("_qa_confirm")[0]
    earlier = {
        norm(q.question)
        for other in QA_GOLD.glob(f"{dataset}_qa*.json")
        if other.name != name
        for q in load_qa_gold(other).questions
    }
    asked = [norm(q.question) for q in gold.questions]
    assert len(set(asked)) == len(asked) and not set(asked) & earlier


# R180: the 20 questions the agent answered wrong by meaning in R157, R166, R173 and R179, one file per
# dataset, for the thinking-level test of plan R180-R182; each record maps to the gold file it judged
FAILURE_SET = REPO / "tests" / "gold" / "r180"
FAILURE_SOURCES = {"r157": "bench", "r166": "confirm", "r173": "confirm2", "r179": "confirm3"}


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "lighthouses", "ntsb"])
def test_the_failure_set_holds_every_failure_by_meaning_copied_unchanged(dataset):
    """R180: a failure-set file asks exactly the questions its dataset's outcome records judge wrong by
    meaning, each equal field by field to the question in its gold file, over the same corpus, so the run
    on it answers the very questions that failed."""
    subset = load_qa_gold(FAILURE_SET / f"{dataset}_failures.json")
    expected = []
    for record, suffix in FAILURE_SOURCES.items():
        path = REPO / "tests" / "gold" / record / "outcomes.json"
        outcomes = json.loads(path.read_text(encoding="utf-8"))["questions"]
        failed = {q["question_id"] for q in outcomes if q["dataset"] == dataset and not q["meaning"]}
        source = load_qa_gold(QA_GOLD / f"{dataset}_qa_{suffix}.json")
        assert subset.corpus == source.corpus
        expected += [q for q in source.questions if q.id in failed]
    assert subset.questions == expected


@pytest.mark.parametrize("name", SEMANTIC)
def test_a_targeted_file_asks_two_retrieval_questions_over_every_passage_of_an_idea(name):
    """R185: 2 questions, each answered from the text by a set or a number over at least two cited passages
    (the idea is stated more than once), counting or negation-sensitive, none asked before by wording."""
    gold = load_qa_gold(QA_GOLD / name)
    assert len(gold.questions) == 2
    for q in gold.questions:
        assert q.route.value == "retrieval" and q.expected.kind in ("entities", "number")
        assert q.type in (QuestionType.AGGREGATION, QuestionType.NEGATION_SENSITIVE)
        assert len(q.chunks) >= 2 and q.note
    dataset = name.removesuffix("_qa_semantic.json")
    earlier = {
        norm(q.question)
        for other in QA_GOLD.glob(f"{dataset}_qa*.json")
        if other.name != name
        for q in load_qa_gold(other).questions
    }
    assert not {norm(q.question) for q in gold.questions} & earlier


# R185: the 10 controls drawn by code, and experiment A's old failures (R182: wrong at both thinking levels,
# an idea worded otherwise than any searched word)
RUNS_A = REPO / "tests" / "gold" / "r185"
FAILURES_A = {"furniture": ["FB18"], "lighthouses": ["LC08"], "ntsb": ["NC11", "ND11"]}


def test_the_controls_are_the_seeded_draw_from_the_third_sets_right_answers():
    """R185: 2 per dataset by `random.Random(185)`, in dataset order, from R179's right answers, sorted."""
    controls = json.loads((RUNS_A / "controls.json").read_text(encoding="utf-8"))["controls"]
    outcomes = json.loads((REPO / "tests/gold/r179/outcomes.json").read_text(encoding="utf-8"))["questions"]
    draw = random.Random(185)
    for dataset in ["furniture", "heldout", "generality", "lighthouses", "ntsb"]:
        mine = [q for q in outcomes if q["dataset"] == dataset]
        right = sorted(q["question_id"] for q in mine if q["exact"] and q["meaning"])
        assert controls[dataset] == sorted(draw.sample(right, 2))


@pytest.mark.parametrize("dataset", CONFIRM_DATASETS)
def test_a_run_file_of_experiment_a_is_its_questions_copied_unchanged(dataset):
    """R185: the targeted questions, then the old failures (from R180's files), then the controls (from the
    third confirmation set), each equal field by field to its source, over the same corpus."""
    run = load_qa_gold(RUNS_A / f"{dataset}_expA.json")
    controls = json.loads((RUNS_A / "controls.json").read_text(encoding="utf-8"))["controls"][dataset]
    targeted = load_qa_gold(QA_GOLD / f"{dataset}_qa_semantic.json").questions
    failures = []
    if dataset in FAILURES_A:
        failed = load_qa_gold(FAILURE_SET / f"{dataset}_failures.json").questions
        failures = [q for q in failed if q.id in FAILURES_A[dataset]]
    third = load_qa_gold(QA_GOLD / f"{dataset}_qa_confirm3.json")
    assert run.corpus == third.corpus
    assert run.questions == [*targeted, *failures, *(q for q in third.questions if q.id in controls)]
