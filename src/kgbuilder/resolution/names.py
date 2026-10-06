"""When a written name is a record's name: the same after normalisation, or the same words up to their
endings (R95a); and when it nearly is one, a near miss an LLM may choose (R95b).

Role in the pipeline: the name test of record matching in `kg resolve` (records.py) and of its offline replay
(audit/relink.py).
Design: pure functions over normalised words, no record types and no reads. A name links on its own only
when the test is sure: a record's name inside a longer name ("pre-drilled holes for the drawer handle" for
Drawer Handle) and a spelling that differs inside a word ("drawer slides" for Drawer Sides) were R93's wrong
links, and no lexical test tells them from right ones. Such near misses are only shown to an LLM, whose
choice code checks (record_choice.py).
Not here: which records a mention may match (records.py), name variants of individuals (variants.py).
"""

import re
from os.path import commonprefix

from rapidfuzz import fuzz

from ..core.text import norm
from .variants import without_title

# A word of a name: letters and digits of any script, after `norm` ("Drawer-Unit" -> "drawer", "unit";
# "Москва" stays one word). The underscore is excluded, so "stockholm_chair" is two words.
_WORD = re.compile(r"[^\W_]+")

# Two words are one word up to its ending when they share at least _MIN_STEM letters from their start and
# neither goes on for more than _MAX_ENDING letters after them: "drawer"/"drawers" and "shelf"/"shelves"
# ("shel" + "f" / "ves") pass, "sides"/"slides" (they share "s") and "car"/"card" (three letters: too short
# a stem to tell an ending from another word) do not. No list of endings, so the rule holds for any
# language that inflects at the end of its words (R95a).
_MIN_STEM = 4
_MAX_ENDING = 3

# A near miss (R95b) needs one word shared up to its ending from a stem of _MIN_SHARED_STEM letters: looser
# than a link ("leg"/"legs" share "leg"), since a near miss is only shown to an LLM. Shorter words ("of",
# "a") are left out: they would make almost every record a near miss.
_MIN_SHARED_STEM = 3


def _words(name: str) -> list[str]:
    return _WORD.findall(norm(name))


def _one_word(a: str, b: str, min_stem: int = _MIN_STEM) -> bool:
    """True when two words are one word up to its ending: they share `min_stem` letters from their start
    and neither goes on for more than `_MAX_ENDING`. A word with a digit has no ending: "A-1063" is not
    "A-1062", nor "2019" "2018"."""
    if a == b:
        return True
    if any(ch.isdigit() for ch in a + b):
        return False
    stem = len(commonprefix([a, b]))
    return stem >= min_stem and max(len(a), len(b)) - stem <= _MAX_ENDING


def _same_words(x: list[str], y: list[str]) -> bool:
    """True when two names have the same words up to their endings, in their written order or sorted."""
    return len(x) == len(y) and any(
        all(_one_word(a, b) for a, b in zip(p, q, strict=True)) for p, q in ((x, y), (sorted(x), sorted(y)))
    )


def _same(x: list[str], y: list[str]) -> bool:
    """True when two names split into words are one name: the same words in any order, or the same letters
    with other spacing."""
    return bool(x) and (sorted(x) == sorted(y) or "".join(x) == "".join(y))


def _score(said: list[str], record: list[str], threshold: float) -> float | None:
    """`name_score` for two names already split into words."""
    if not said or not record:
        return None
    if _same(said, record):
        return 100.0
    score = fuzz.token_sort_ratio(" ".join(said), " ".join(record))
    return score if score >= threshold and _same_words(said, record) else None


def name_score(name: str, record_name: str, threshold: float) -> float | None:
    """How well a mention's `name` names a record called `record_name` on its own; None when it does not.

    100 when the two are the same name after normalisation: any word order ("Chair Stockholm"), spacing or
    hyphens ("bed-side table" for "Bedside Table"), and without a leading title ("Dr Jonathan Pike").
    Else the spelling score (rapidfuzz token_sort_ratio) of a name with the same words up to their endings
    ("drawers" for "Drawer": 92.3), when it reaches `threshold`. Both tests are needed (R95a): the endings
    say where two names may differ, the score how much. "drawer slides" is 96 alike "Drawer Sides" but
    differs inside a word, and "pane" is "Panel" up to an ending but only 89 alike: neither is a link.
    """
    record = _words(record_name)
    scores = [_score(_words(said), record, threshold) for said in {name, without_title(name)}]
    return max((s for s in scores if s is not None), default=None)


def same_name(a: str, b: str) -> bool:
    """True when two names are one name after normalisation (`name_score` 100, no title dropped)."""
    return _same(_words(a), _words(b))


def near_name(name: str, record_name: str, borderline: float) -> bool:
    """True when a mention's `name` nearly names a record called `record_name`: a near miss (R95b).

    A word of each is one word up to its ending from a 3-letter stem ("drawer" in "Drawer Unit", "base" in
    "weighted base", "legs" for "Leg"), or the two names are spelled at least `borderline` alike. The record's
    name inside the mention's, the reverse, and a close spelling are all near misses: an LLM tells which of
    them is the thing, and code never links one on this test alone.
    """
    record = _words(record_name)
    said = {w for n in {name, without_title(name)} for w in _words(n)}
    if any(
        min(len(a), len(b)) >= _MIN_SHARED_STEM and _one_word(a, b, _MIN_SHARED_STEM)
        for a in said
        for b in record
    ):
        return True
    written = _words(name)
    return (
        bool(written and record) and fuzz.token_sort_ratio(" ".join(written), " ".join(record)) >= borderline
    )
