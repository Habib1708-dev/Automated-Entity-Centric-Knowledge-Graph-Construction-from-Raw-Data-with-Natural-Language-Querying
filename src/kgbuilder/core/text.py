"""Text normalisation and sentence picking shared by evidence verification, entity resolution, linking,
derivation and validation.

Role in the pipeline: every comparison between an LLM-produced string and source text (or between two
entity names) goes through `norm`, so all stages agree on what "the same text" means.
Not here: fuzzy scoring (resolution/) and chunking (text/).
"""

import re
import unicodedata

# Markdown emphasis, code, heading and quote markers. The LLM usually drops them when it copies a quote,
# so they must not count as a mismatch.
_MARKDOWN_MARKERS = re.compile(r"[*_`#>]")
_WHITESPACE = re.compile(r"\s+")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def norm(text: str) -> str:
    """Return the case-, accent-, whitespace- and markdown-insensitive form of `text`.

    Example: ``"**Västerås**  Bookshelf" -> "vasteras bookshelf"``.
    """
    # NFKD splits "ä" into "a" + combining diaeresis; dropping the combining marks removes the accent
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_markdown = _MARKDOWN_MARKERS.sub("", without_accents.lower())
    return _WHITESPACE.sub(" ", without_markdown).strip()


def squash(text: str) -> str:
    """Return `norm(text)` with everything but letters and digits removed.

    Used to compare names with file names, where separators differ:
    ``"Stockholm Chair"`` and ``"stockholm_chair_reviews"`` both contain ``"stockholmchair"``.
    """
    return _NON_ALPHANUMERIC.sub("", norm(text))


# A sentence ends at ".", "!" or "?" followed by whitespace, or at a line break (headings, list items).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def pick_sentence(text: str, names: list[str]) -> str | None:
    """The first sentence of `text` containing one of `names` (compared with `norm`), returned verbatim.

    None when no sentence does (an entity can reach a chunk through the document context or through an
    alias merged from another chunk). Used for a derived fact's quote and for the entity-resolution
    context: both must show what the chunk really says, never an invented sentence.
    """
    wanted = [norm(name) for name in names if norm(name)]
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if sentence and any(w in norm(sentence) for w in wanted):
            return sentence
    return None
