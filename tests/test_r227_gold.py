"""The audit gold of R227 for the lighthouses and NTSB builds: R75 identity pairs and R89 targets.

R228 judges C3 (identity), C4 (record linking) and C6 (purity) on `out/r155b_<ds>`; C3 rescores these
identity pairs by code and C6 starts from these targets. The files are written once, so these tests pin
what code can check, with R75's and R89's own rules: every pair names its mentions and quotes both
documents verbatim, no pair twice, both answers present; every benchmark question answered once, each
target fitting the rebuilt corpus, and most questions with a start. No Neo4j, no LLM, no `out/` folder.
"""

import pytest

from kgbuilder.core.text import contains_words, norm
from kgbuilder.validation.gold import IdentityPair, load_gold
from kgbuilder.validation.qa_gold import load_qa_gold
from kgbuilder.validation.target_gold import check_target_gold, load_target_gold

from .qa_corpus import REPO, rebuild_corpus

R227 = REPO / "tests" / "gold" / "r227"
CORPORA = {ds: REPO / "heldout" / ds / "data" for ds in ("lighthouses", "ntsb")}


def _pairs(dataset: str) -> list[IdentityPair]:
    return load_gold(R227 / f"{dataset}_identity.json").identity_pairs


def _text(dataset: str, doc_id: str) -> str:
    return (CORPORA[dataset] / doc_id).read_text(encoding="utf-8")


@pytest.mark.parametrize("dataset", CORPORA)
def test_every_identity_pair_names_its_mentions_and_quotes_its_documents_verbatim(dataset):
    pairs = _pairs(dataset)
    assert {p.same for p in pairs} == {True, False}  # joins to make and joins to refuse
    for p in pairs:
        for side in (p.a, p.b):
            assert any(contains_words(_text(dataset, side.doc_id), n) for n in side.names), side
        assert {q.doc_id for q in p.evidence} == {p.a.doc_id, p.b.doc_id}, p
        for q in p.evidence:
            assert norm(q.quote) in norm(_text(dataset, q.doc_id)), q
    keys = [frozenset(((p.a.doc_id, *p.a.names), (p.b.doc_id, *p.b.names))) for p in pairs]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("dataset", CORPORA)
def test_the_targets_cover_the_benchmark_questions_and_fit_the_corpus(dataset, tmp_path):
    gold = load_target_gold(R227 / f"{dataset}_targets.json")
    assert gold.qa_gold == f"tests/gold/qa/{dataset}_qa_bench.json"
    qa = load_qa_gold(REPO / gold.qa_gold)
    corpus = rebuild_corpus(qa.corpus, tmp_path)
    check_target_gold(gold, qa, corpus.chunks, corpus.rows)
    empty = [q for q in gold.questions if not q.targets]
    # as R89: a question without a start leaves C6; if most had none, the criterion would measure little
    assert all(q.note for q in empty) and len(empty) < len(gold.questions) / 3
