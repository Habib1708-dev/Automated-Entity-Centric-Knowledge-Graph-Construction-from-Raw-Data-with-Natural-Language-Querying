"""Guards: the rules that keep two concepts apart however alike they are spelled or embedded (R66, R75).

Role in the pipeline: `resolver.find_candidates` drops a nominated pair that a guard blocks, and counts it
under the guard's name (the `blocked_<name>` metrics of `kg resolve`), so the rules' effect is measured.
Design: Strategy, one class per rule, each domain-neutral (direction document, section 5):
  - `OpposedPolarity`: claims use one kind positively and the other negatively ("resistant to scratches"
    and "scratches easily" spell alike; a merge would turn praise into a complaint, R66);
  - `SameSentence`: one sentence names both as two things ("the rails and the slides"): a writer who
    names two things side by side does not mean one (R75);
  - `PartAndWhole`: a claim of a part-of fact type joins them ("the casing of the gearbox" is a piece of
    the gearbox, not another word for it), which the schema marks per fact type (R75).
A different type is no candidate at all (resolver.py), and numbers are never compared (core/values.py).
Not here: the scores (matchers.py), which pairs are nominated (blocking.py), the decision (resolver.py).
"""

from collections.abc import Iterable
from typing import Protocol

from ..core.text import norm, word_spans
from .matchers import EntityRecord

# guard name -> the pairs of concept ids it kept apart
BlockLog = dict[str, set[frozenset[str]]]


class Guard(Protocol):
    """Blocks a pair of same-type concepts from being joined."""

    name: str

    def blocks(self, a: EntityRecord, b: EntityRecord) -> bool: ...


def names_of(record: EntityRecord) -> set[str]:
    """Every name a concept is written with (its name and aliases), normalised, empty ones left out."""
    return {norm(n) for n in (record.name, *record.aliases) if norm(n)}


class OpposedPolarity:
    """Claims use one positively and the other negatively (R66)."""

    name = "opposed_polarity"

    def blocks(self, a: EntityRecord, b: EntityRecord) -> bool:
        return ("positive" in a.polarities and "negative" in b.polarities) or (
            "negative" in a.polarities and "positive" in b.polarities
        )


class SameSentence:
    """One sentence names both, each at its own place: "the rails and the slides". A name inside the other
    ("rails" in "the drawer rails") is not a second thing, so overlapping occurrences do not count."""

    name = "same_sentence"

    def __init__(self, sentences: Iterable[str]):
        self._sentences = sorted({norm(s) for s in sentences if norm(s)})
        self._containing: dict[str, list[int]] = {}  # name -> the sentences containing it as a substring

    def blocks(self, a: EntityRecord, b: EntityRecord) -> bool:
        names_a, names_b = names_of(a), names_of(b)
        shared = self._with_any(names_a) & self._with_any(names_b)
        return any(self._apart(self._sentences[i], names_a, names_b) for i in sorted(shared))

    def _with_any(self, names: set[str]) -> set[int]:
        found: set[int] = set()
        for name in names:
            if name not in self._containing:
                # a substring scan first: cheap, and the whole-word check runs only on these sentences
                self._containing[name] = [i for i, s in enumerate(self._sentences) if name in s]
            found.update(self._containing[name])
        return found

    @staticmethod
    def _apart(sentence: str, names_a: set[str], names_b: set[str]) -> bool:
        spans_a = [s for n in names_a for s in word_spans(sentence, n)]
        spans_b = [s for n in names_b for s in word_spans(sentence, n)]
        return any(
            x_end <= y_start or y_end <= x_start for x_start, x_end in spans_a for y_start, y_end in spans_b
        )


class PartAndWhole:
    """A claim of a part-of fact type joins them, in either direction. `pairs` are the two ends' own
    wordings of every such claim, so the rule holds for a merged concept through any of its names."""

    name = "part_and_whole"

    def __init__(self, pairs: Iterable[tuple[str, str]]):
        self._pairs = {frozenset((norm(x), norm(y))) for x, y in pairs if norm(x) != norm(y)}

    def blocks(self, a: EntityRecord, b: EntityRecord) -> bool:
        return any(frozenset((x, y)) in self._pairs for x in names_of(a) for y in names_of(b))


# The guards every resolution applies when no graph data is at hand (unit tests, the preview): polarity only
DEFAULT_GUARDS: tuple[Guard, ...] = (OpposedPolarity(),)
