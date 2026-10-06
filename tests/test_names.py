"""The name test of record matching (R95a, resolution/names.py), pure: a name is a record's when it is the
same after normalisation, or the same words up to a short ending and spelled alike. A spelling that differs
inside a word, a stem too short to tell an ending, or a word with a digit is no match. No Neo4j."""

from kgbuilder.resolution.names import name_score


def test_the_same_name_after_normalisation_scores_100_whatever_its_spacing_title_or_script():
    assert name_score("bed-side table", "Bedside Table", threshold=90) == 100  # spacing and hyphens
    assert name_score("Dr Jonathan Pike", "Jonathan Pike", threshold=90) == 100  # a leading title
    assert name_score("Chair Stockholm", "Stockholm Chair", threshold=90) == 100  # word order
    assert name_score("Москва", "Киев", threshold=90) is None  # no letters dropped, so no empty match
    assert name_score("  ", "Table", threshold=90) is None


def test_words_may_differ_only_in_a_short_ending_and_the_names_must_be_spelled_alike():
    # the plurals and singulars R93 judged right: the same words up to their endings, at least 90 alike
    assert round(name_score("drawers", "Drawer", threshold=90), 1) == 92.3
    assert name_score("center supports", "Center Support", threshold=90) is not None
    assert name_score("Norrköping Nightstands", "Norrköping Nightstand", threshold=90) is not None
    # an ending apart, but too short a name to be spelled 90 alike: "pane" is not a "Panel"
    assert name_score("pane", "Panel", threshold=90) is None
    # 96 alike, but the letter that differs is inside the word: another word (R93: INCORRECT)
    assert name_score("drawer slides", "Drawer Sides", threshold=90) is None
    # at least four letters before an ending: "car" and "card" share three
    assert name_score("red car", "Red Card", threshold=90) is None


def test_a_word_with_a_digit_has_no_ending():
    # 90 alike, and only the last character differs: still another model, another unit
    assert name_score("Model 2019", "Model 2018", threshold=90) is None
    assert name_score("unit A-1062", "Unit A-1063", threshold=90) is None
