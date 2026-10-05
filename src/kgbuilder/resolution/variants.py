"""Name variants: when two written names could name the same individual ("Dr. J. Pike", "Jon Pike" and
"Jonathan Pike") (R75, layered-model Step 5).

Role in the pipeline: the identity stage nominates pairs of individuals by these rules (individuals.py) and
matches a titled name to a record without its title (records.py). A variant is never evidence on its own:
"J. Pike" fits Jonathan and Judith alike, so a nominated pair is joined only with evidence.
Design: pure functions over normalised words. The title list is language-level (forms of address), not a
domain's, and only a leading title is dropped, so a family name that happens to be one is kept.
Not here: deciding a pair (individuals.py), matching records (records.py).
"""

import re

from ..core.text import norm

# Forms of address that may precede a name and say nothing about which individual it is
_TITLES = frozenset({"dr", "mr", "mrs", "ms", "mx", "miss", "prof", "professor", "sir", "dame", "rev"})

# A short form must keep this many letters of the full first name ("Jon" for "Jonathan"); one letter is an
# initial ("J."), two would match too much ("Jo" for "Joanna" and "Jonathan" alike)
_MIN_SHORT_FORM = 3


def name_words(name: str) -> list[str]:
    """The words of a name after `norm`, punctuation dropped, leading titles left out: "Dr. J. Pike" ->
    ["j", "pike"]."""
    words = re.findall(r"[a-z0-9]+", norm(name))
    while len(words) > 1 and words[0] in _TITLES:
        words = words[1:]
    return words


def without_title(name: str) -> str:
    """The name as words without its leading titles: "Dr Jonathan Pike" -> "jonathan pike"."""
    return " ".join(name_words(name))


def _first_names_fit(a: str, b: str) -> bool:
    if a == b:
        return True
    short, full = sorted((a, b), key=len)
    if len(short) == 1:  # an initial
        return full.startswith(short)
    return len(short) >= _MIN_SHORT_FORM and full.startswith(short)


def compatible(a: str, b: str) -> bool:
    """True when the names could name one individual: the same words, or two names of at least two words
    with the same last word and first words that fit (equal, an initial, or a short form). Middle words are
    not compared: they are written as often as they are left out."""
    x, y = name_words(a), name_words(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if min(len(x), len(y)) < 2 or x[-1] != y[-1]:  # a single word says too little to pair on
        return False
    return _first_names_fit(x[0], y[0])
