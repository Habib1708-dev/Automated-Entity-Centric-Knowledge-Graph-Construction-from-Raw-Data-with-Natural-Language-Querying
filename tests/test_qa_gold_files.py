"""The committed question-answer gold files (R70): each fits its corpus as the pipeline chunks it (every
quote verbatim in its chunk, every cited record in its staged file, every question typed), every answer
of a question with a query equals what DuckDB computes from the source files (R73), the two real
datasets ask every question type at least five times and every record type at least ten times (R73), the
earlier gold questions are carried over with their answers unchanged, and the generality corpus asks every
hard case. R143a: the fresh files (ten questions per dataset) pass the same per-file checks. R152: so do the
benchmark files, with 50 questions each, every type at least six times, and none asked before. No Neo4j,
no LLM.
"""

import json
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
GOLD_FILES = [*REAL, "generality_qa.json", *FRESH, *BENCH]


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
