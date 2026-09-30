"""The corpora the gold sets are written from, for the tests that keep prompt rules from quoting them.

A prompt rule in a gold corpus's words steers the model toward that corpus (found in R34), so the prompt
tests (test_pipeline.py, test_text.py) check that no four consecutive words of a rule occur in any corpus
listed here: the furniture reviews, the held-out complaints and the synthetic generality corpus (R70).
"""

import re
from pathlib import Path

from kgbuilder.core.text import norm

REPO = Path(__file__).resolve().parent.parent
EVALUATION_CORPORA = [
    REPO / "data" / "product_reviews",
    REPO / "heldout" / "nhtsa" / "data" / "complaints",
    REPO / "tests" / "fixtures" / "generality",
]


def words(text: str) -> list[str]:
    """The words of `text` after `norm` (case, accents and markdown ignored), punctuation dropped."""
    return re.findall(r"[a-z0-9]+", norm(text))


def corpus_four_grams() -> set[tuple[str, ...]]:
    """Every run of four consecutive words in the text documents of the evaluation corpora."""
    paths = sorted(p for d in EVALUATION_CORPORA for p in d.rglob("*") if p.suffix in {".md", ".txt"})
    corpus = words(" ".join(p.read_text(encoding="utf-8") for p in paths))
    return {tuple(corpus[i : i + 4]) for i in range(len(corpus) - 3)}


def quoted_four_grams(rules: str) -> list[str]:
    """The four-word runs of `rules` that occur in an evaluation corpus; empty when none does."""
    seen = corpus_four_grams()
    rule_words = words(rules)
    return [
        " ".join(rule_words[i : i + 4])
        for i in range(len(rule_words) - 3)
        if tuple(rule_words[i : i + 4]) in seen
    ]
