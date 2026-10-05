"""The guards of concept resolution (R66, R75): opposite polarity, two names used as two things in one
sentence, a part and its whole by a part-of claim; each drops a nominated pair and counts it once, also
across the passes of `decide_in_passes`. Pure: no Neo4j."""

from kgbuilder.resolution.guards import CompoundName, OpposedPolarity, PartAndWhole, SameSentence
from kgbuilder.resolution.matchers import EntityRecord
from kgbuilder.resolution.resolver import decide_in_passes, find_candidates


def concept(cid: str, name: str, *aliases: str, polarities: tuple[str, ...] = ()) -> EntityRecord:
    return EntityRecord(
        id=cid, name=name, type="Piece", aliases=[name, *aliases], mentions=1, polarities=list(polarities)
    )


RAILS, SLIDES = concept("r", "rails"), concept("s", "slides")


def test_two_names_side_by_side_in_one_sentence_are_two_things():
    guard = SameSentence(["The rails and the slides both rattle.", "Unrelated sentence."])
    assert guard.blocks(RAILS, SLIDES) and guard.blocks(SLIDES, RAILS)


def test_a_name_inside_the_other_is_not_a_second_thing():
    guard = SameSentence(["The drawer rails rattle."])
    assert not guard.blocks(concept("d", "drawer rails"), RAILS)  # "rails" lies inside "drawer rails"


def test_names_in_different_sentences_are_not_blocked():
    assert not SameSentence(["The rails rattle.", "The slides stick."]).blocks(RAILS, SLIDES)


def test_a_merged_concept_is_blocked_through_any_of_its_names():
    guard = SameSentence(["The runners and the slides rattle."])
    assert guard.blocks(concept("r", "rails", "runners"), SLIDES)


def test_a_part_of_claim_keeps_a_part_and_its_whole_apart_in_either_direction():
    guard = PartAndWhole([("gearbox casing", "Gearbox")])
    casing, gearbox = concept("c", "gearbox casing"), concept("g", "gearbox")
    assert guard.blocks(casing, gearbox) and guard.blocks(gearbox, casing)
    assert not guard.blocks(casing, concept("x", "casing"))


def test_opposite_tones_are_blocked():
    good = concept("g", "resistant to scratches", polarities=("positive",))
    bad = concept("b", "resistant to scratch", polarities=("negative",))
    assert OpposedPolarity().blocks(good, bad) and not OpposedPolarity().blocks(good, good)


def test_a_blocked_pair_is_dropped_and_counted_once_over_all_passes():
    records = [concept("a", "rail"), concept("b", "rails"), concept("c", "railz")]
    guards = [SameSentence(["The rail and the rails differ."])]
    blocked: dict = {}
    assert {(c.a, c.b) for c in find_candidates(records, 80, guards=guards, blocked=blocked)} == {
        ("a", "c"),
        ("b", "c"),
    }
    assert blocked == {"same_sentence": {frozenset("ab")}}
    # a second pass over the merged view meets the blocked pair again: still one pair
    log: dict = {}
    decide_in_passes(records, 80, None, None, 80, None, guards=guards, blocked=log)
    assert {name: len(pairs) for name, pairs in log.items()} == {"same_sentence": 1}


def test_a_name_with_one_more_word_at_its_end_names_another_thing():
    """Found in R75's held-out run: the adjudicator called TRANSMISSION and TRANSMISSION BOX one kind, and no
    part-of claim joined them. The last word of a compound names the thing ("a transmission box" is a box)."""
    guard = CompoundName()
    assert guard.blocks(concept("t", "TRANSMISSION"), concept("b", "TRANSMISSION BOX"))
    assert guard.blocks(concept("c", "gearbox casing"), concept("g", "gearbox"))
    # a word in front narrows the same thing, and a phrase at the end is no compound: both stay allowed
    assert not guard.blocks(concept("b", "base"), concept("w", "weighted base"))
    assert not guard.blocks(concept("d", "small dent"), concept("e", "small dent on one edge"))
    assert not guard.blocks(concept("l", "leg"), concept("s", "legs"))
