"""The committed question-answer gold files (R70): each fits its corpus as the pipeline chunks it (every
quote verbatim in its chunk, every cited record in its staged file, every question typed), every answer
of a question with a query equals what DuckDB computes from the source files (R73), the two real
datasets ask every question type at least five times, the earlier gold questions are carried over with
their answers unchanged, and the generality corpus asks every hard case. No Neo4j, no LLM.
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
GOLD_FILES = [*REAL, "generality_qa.json"]


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


@pytest.mark.parametrize("name", REAL)
def test_the_real_datasets_ask_every_type_at_least_five_times_in_30_to_50_questions(name):
    gold = load_qa_gold(QA_GOLD / name)
    counts = Counter(q.type for q in gold.questions)
    assert 30 <= len(gold.questions) <= 50
    assert {t: counts[t] for t in QuestionType if counts[t] < 5} == {}


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


def test_the_generality_corpus_spans_at_least_three_domains_in_10_to_12_documents():
    corpus = REPO / "tests" / "fixtures" / "generality"
    documents = [p for p in corpus.rglob("*") if p.suffix in {".md", ".txt"}]
    assert 10 <= len(documents) <= 12
    # one folder per domain: the folders are the unrelated domains the corpus claims to cover
    assert len({p.parent for p in documents}) >= 3
    assert any(p.suffix == ".csv" for p in Path(corpus).rglob("*"))
