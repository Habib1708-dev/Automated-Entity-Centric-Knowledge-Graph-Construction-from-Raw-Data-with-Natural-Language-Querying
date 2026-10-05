"""The R75 identity gold (tests/gold/r75): every pair's names occur in their documents, every evidence quote
is verbatim in its document and each side has one, no pair is listed twice, and the hard cases of the
task file are there; and the identity score on hand-made mentions (pure) and through `kg eval` (Neo4j)."""

import json
from pathlib import Path

import pytest

from kgbuilder.core.text import contains_words, norm
from kgbuilder.validation.gold import IdentityPair, MentionRef, load_gold
from kgbuilder.validation.identity import SheetMention, score_identity

REPO = Path(__file__).resolve().parent.parent
GOLD = REPO / "tests" / "gold" / "r75"
CORPORA = {
    "furniture": REPO / "data",
    "heldout": REPO / "heldout" / "nhtsa" / "data",
    "generality": REPO / "tests" / "fixtures" / "generality",
}


def pairs(dataset: str) -> list[IdentityPair]:
    return load_gold(GOLD / f"{dataset}_gold.json").identity_pairs


def text(dataset: str, doc_id: str) -> str:
    return (CORPORA[dataset] / doc_id).read_text(encoding="utf-8")


@pytest.mark.parametrize("dataset", CORPORA)
def test_every_identity_pair_names_its_mentions_and_quotes_its_documents_verbatim(dataset):
    found = pairs(dataset)
    assert found
    for p in found:
        for side in (p.a, p.b):
            assert any(contains_words(text(dataset, side.doc_id), n) for n in side.names), side
        assert {q.doc_id for q in p.evidence} == {p.a.doc_id, p.b.doc_id}, p  # one quote per side at least
        for q in p.evidence:
            assert norm(q.quote) in norm(text(dataset, q.doc_id)), q


@pytest.mark.parametrize("dataset", CORPORA)
def test_no_identity_pair_is_listed_twice(dataset):
    keys = [frozenset(((p.a.doc_id, *p.a.names), (p.b.doc_id, *p.b.names))) for p in pairs(dataset)]
    assert len(keys) == len(set(keys))


def test_the_generality_gold_holds_the_hard_cases_of_the_task_file():
    found = pairs("generality")
    by_names = {(p.a.names[0], p.b.names[0]): p.same for p in found}
    lopez = [p for p in found if p.a.names == p.b.names == ["Maria Lopez"]]
    assert {p.same for p in lopez} == {True, False}  # one name, one person in some pairs, two in others
    assert by_names[("Jonathan Pike", "Jon Pike")] and by_names[("Jonathan Pike", "Dr. J. Pike")]
    assert not by_names[("Judith Pike", "J. Pike")]
    assert not by_names[("HP40-1183", "HP40-2291")]  # two pumps of one model


def test_the_furniture_and_heldout_gold_hold_the_recorded_wrong_merges():
    furniture = {(p.a.names[0], p.b.names[0]): p.same for p in pairs("furniture")}
    heldout = {(p.a.names[0], p.b.names[0]): p.same for p in pairs("heldout")}
    assert furniture[("drawer slides", "drawer rails")] is False
    assert heldout[("TRANSMISSION BOX", "TRANSMISSION")] is False
    assert heldout[("BRAKE SUDDENLY", "ACTIVATED THE BRAKES")] is True  # the same kind, see its note
    r65 = json.loads((REPO / "tests" / "gold" / "r65" / "furniture_gold.json").read_text("utf-8"))
    assert len(pairs("furniture")) == len(r65["er_pairs"]) - 3 + 1  # three part pairs left to er_accuracy


def mention(mid: str, doc: str, name: str, canonical: str) -> SheetMention:
    return SheetMention(id=mid, doc_id=doc, name=name, type="Person", canonical=canonical)


def gold_pair(a: tuple[str, str], b: tuple[str, str], same: bool) -> IdentityPair:
    return IdentityPair(
        a=MentionRef(doc_id=a[0], names=[a[1]]),
        b=MentionRef(doc_id=b[0], names=[b[1]]),
        same=same,
        evidence=[],
    )


def test_identity_is_scored_on_canonical_entities_and_missing_names_are_counted_apart():
    mentions = [
        mention("1", "a.md", "Jonathan Pike", "S-131"),
        mention("2", "b.md", "Jon Pike", "S-131"),  # joined: right
        mention("3", "c.md", "J. Pike", "i-3"),  # left alone: a missed join
        mention("4", "d.md", "Maria Lopez", "S-219"),
        mention("5", "e.md", "Maria Lopez", "S-219"),  # joined: wrong
        mention("6", "f.md", "Judith Pike", "i-6"),  # kept apart: right
    ]
    score = score_identity(
        mentions,
        [
            gold_pair(("a.md", "Jonathan Pike"), ("b.md", "jon pike"), True),
            gold_pair(("a.md", "Jonathan Pike"), ("c.md", "J. Pike"), True),
            gold_pair(("d.md", "Maria Lopez"), ("e.md", "Maria Lopez"), False),
            gold_pair(("f.md", "Judith Pike"), ("c.md", "J. Pike"), False),
            gold_pair(("a.md", "Jonathan Pike"), ("x.md", "Dr Pike"), True),  # never extracted
        ],
    )
    assert (score.precision, score.recall, score.apart) == (0.5, 0.5, 0.5)
    assert (score.scored, score.not_extracted, score.joined) == (4, 1, 2)
    assert score.metrics()["identity_apart_rate"] == 0.5 and score.metrics()["identity_not_extracted"] == 1
    empty = score_identity([], [gold_pair(("a.md", "x"), ("b.md", "y"), True)])
    assert empty.precision is None and "identity_precision" not in empty.metrics()
