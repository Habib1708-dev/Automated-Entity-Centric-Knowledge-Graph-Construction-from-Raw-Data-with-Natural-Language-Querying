"""Blocking rules for entity resolution (resolution/blocking.py): the absolute score threshold, the
rank-based mutual nearest neighbours and why the latter needs no scale, and the settings that pick one."""

import pytest
from pydantic import ValidationError

from kgbuilder.config import Settings
from kgbuilder.resolution.blocking import MutualNearest, ScoreThreshold, blocking_from
from kgbuilder.resolution.matchers import EntityRecord


def entity(id: str) -> EntityRecord:
    return EntityRecord(id=id, name=id, type="Component", aliases=[id], mentions=1)


RAILS, METAL, HANDLES, SHADE = (entity(i) for i in ("rails", "metal", "handles", "shade"))
MEMBERS = [RAILS, METAL, HANDLES, SHADE]
# cosine*100 as an embedding model might give it: rails/metal are the synonyms, handles is a hub close to
# both rails and metal, shade is unrelated to everything
SCORES = {
    frozenset(("rails", "metal")): 79.0,
    frozenset(("rails", "handles")): 77.0,
    frozenset(("metal", "handles")): 76.0,
    frozenset(("rails", "shade")): 55.0,
    frozenset(("metal", "shade")): 54.0,
    frozenset(("handles", "shade")): 60.0,
}


def score(a: EntityRecord, b: EntityRecord) -> float:
    return SCORES[frozenset((a.id, b.id))]


def keys(pairs: dict) -> set[frozenset[str]]:
    return set(pairs)


def test_threshold_nominates_every_pair_at_or_above_it():
    assert keys(ScoreThreshold(77).pairs(MEMBERS, score)) == {
        frozenset(("rails", "metal")),
        frozenset(("rails", "handles")),
    }


def test_mutual_nearest_keeps_a_pair_only_when_both_rank_each_other_high():
    # k=1: rails and metal are each other's nearest; handles' nearest is rails, but rails' is metal
    assert keys(MutualNearest(1).pairs(MEMBERS, score)) == {frozenset(("rails", "metal"))}
    # k=2 adds the hub's pairs with rails and metal, never the unrelated shade: shade ranks handles
    # first, but handles ranks rails and metal above shade
    assert keys(MutualNearest(2).pairs(MEMBERS, score)) == {
        frozenset(("rails", "metal")),
        frozenset(("rails", "handles")),
        frozenset(("metal", "handles")),
    }


def test_mutual_nearest_needs_no_scale_but_a_threshold_does():
    """Another embedding model may put every score 20 points lower and squeeze them: the ranks stay, so
    mutual nearest nominates the same pairs, while a threshold chosen on the first scale finds none."""

    def other_model(a: EntityRecord, b: EntityRecord) -> float:
        return 0.5 * score(a, b) + 20  # 79 -> 59.5

    assert (
        MutualNearest(2).pairs(MEMBERS, other_model).keys() == MutualNearest(2).pairs(MEMBERS, score).keys()
    )
    assert ScoreThreshold(77).pairs(MEMBERS, other_model) == {}


def test_ties_are_broken_by_id_so_a_graph_always_gives_the_same_pairs():
    flat = [entity(i) for i in ("c", "a", "b")]
    pairs = MutualNearest(1).pairs(flat, lambda a, b: 50.0)
    assert keys(pairs) == {frozenset(("a", "b"))}  # a's nearest is b (by id), b's is a; c's is a


def test_the_settings_choose_the_rule():
    assert blocking_from("off", 78, 2) is None
    assert isinstance(blocking_from("threshold", 78, 2), ScoreThreshold)
    rule = blocking_from("mutual_nearest", 78, 3)
    assert isinstance(rule, MutualNearest) and rule.k == 3
    with pytest.raises(ValueError):
        MutualNearest(0)
    with pytest.raises(ValidationError):
        Settings(er_neighbours=0)
