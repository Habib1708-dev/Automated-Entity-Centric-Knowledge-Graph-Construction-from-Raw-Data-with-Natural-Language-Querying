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
    return next(iter(sentences_naming(text, names)), None)


def sentences_naming(text: str, names: list[str]) -> list[str]:
    """Every sentence of `text` containing one of `names` (compared with `norm`), verbatim, in order.

    The identity stage (R75) reads all of them: a record's key or a telling attribute ("Maria Lopez
    (Finance Office)") may stand in any sentence that names the mention, not only the first.
    """
    wanted = [norm(name) for name in names if norm(name)]
    return [s for s in split_sentences(text) if any(w in norm(s) for w in wanted)]


def claim_sentences(quote: str, ends: list[str]) -> list[str]:
    """The sentences of a claim's quote that name one of its ends (its own wording of subject and object),
        else the whole quote as one.

        A quote is the extractor's choice and may span several lines of a note ("Mon: pump A on duty.
    Wed: B
        visited the station."); what the claim says stands in the sentences naming its ends, and a thing named
        on another line says nothing about it (found in R76's generality run). The attachment route and path
        truth read the same sentences, so the check holds exactly where the route did.
    """
    return sentences_naming(quote, ends) or [quote]


# A full stop after a capital initial ("J.") or a form of address ("Dr.") ends no sentence (found in R75:
# "Dr. J. Pike (Soil Ecology)" became three sentences, none naming the person). Capital letters only, and
# the closed list is of the language, not of a domain; a line break always ends a sentence.
_NO_SENTENCE_END = re.compile(r"(?<![A-Za-z])(?:[A-Z]|Dr|Mr|Mrs|Ms|Mx|Prof|Rev|St)\.$")


def split_sentences(text: str) -> list[str]:
    """The sentences of `text`, verbatim and stripped, empty ones left out."""
    pieces, start = [], 0
    for end in _SENTENCE_END.finditer(text):
        piece = text[start : end.start()]
        if "\n" not in end.group() and _NO_SENTENCE_END.search(piece):
            continue  # the piece goes on into the next one
        pieces.append(piece)
        start = end.end()
    pieces.append(text[start:])
    return [p.strip() for p in pieces if p.strip()]


def word_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Where `phrase` occurs in `norm(text)` as whole words: (start, end) offsets, in order."""
    wanted = norm(phrase)
    if not wanted:
        return []
    # (?<![a-z0-9]) and (?![a-z0-9]): the phrase may not start or end inside a longer word or number
    pattern = rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])"
    return [m.span() for m in re.finditer(pattern, norm(text))]


def contains_words(text: str, phrase: str) -> bool:
    """True when `phrase` occurs in `text` as whole words, both compared with `norm`: "Finance Office"
    is in "Maria Lopez (Finance Office) presented", "ESCAPE" is not in "the car escaped"."""
    return bool(word_spans(text, phrase))
