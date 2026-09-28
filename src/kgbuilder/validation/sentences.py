"""The sentences of the ingested text, and a fixed random sample of them for the coverage estimate (R68).

Role in the pipeline: `kg coverage-sample` splits the graph's chunks into sentences and writes a sample
file, which is committed; `kg coverage-sheet` (coverage_sheet.py) finds each sampled sentence in a graph
again and shows the judge what that graph stores about it. A later arm is measured on the same sample, so
a sentence is identified by its document and its wording, never by a chunk id (chunk ids change with the
chunk settings).
Design: pure functions. A sentence's place in the sample comes from a hash of the seed and its id
("bottom-k" sampling), not from a random generator: the same seed gives the same sample on any machine and
Python version, and a sentence keeps its rank when other documents are added or chunked differently.
Not here: reading chunks from Neo4j (text/lexical.py) and judging what is stored (coverage.py).
"""

import hashlib
import re

from pydantic import BaseModel

from ..text.chunking import Chunk

# A line that only separates sections ("---", "***"): layout, not text.
_RULE_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,})\s*$")
# Where a sentence ends: after ".", "!" or "?" (a run of them, as in "THEN..."), or after one of them
# followed by a closing quote or bracket, and then whitespace. A dot inside a number ("3.2kg", "1,800.5")
# is followed by no whitespace, so it never ends a sentence. Abbreviations ("Dr. Smith") are cut: rare in
# the corpora, and a cut sentence is still judged claim by claim.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’)\]])\s+")
_WORD = re.compile(r"\w+")
# A piece with fewer words is no sentence: a clerk's signature ("*TR"), a stray marker, an empty heading.
_MIN_WORDS = 2


class SampledSentence(BaseModel):
    """One sentence of the sample; `chunk_id` records where it was found when the sample was drawn."""

    id: str  # sentence_id(doc_id, text): stable across graphs and chunk settings
    doc_id: str
    chunk_id: str
    text: str  # verbatim, a substring of its chunk's text


class SentenceSample(BaseModel):
    """The sample file: which sentences, and how they were drawn (so a reader can redraw them)."""

    seed: int
    size: int  # the size asked for; `sentences` is shorter only when the text has fewer sentences
    population: int  # distinct sentences the sample was drawn from
    sentences: list[SampledSentence]  # in reading order: by document, chunk and position


def split_sentences(text: str) -> list[str]:
    """The sentences of `text`, in order, each verbatim (stripped of surrounding whitespace).

    Every line is split on its own, so a heading ("## Rating: ★★★☆☆ (3/5)") is a sentence of its own: it
    can state a claim, and the judge lists none when it states nothing. Separator lines are dropped.
    """
    sentences: list[str] = []
    for line in text.splitlines():
        if _RULE_LINE.match(line):
            continue
        for piece in _SENTENCE_END.split(line.strip()):
            piece = piece.strip()
            if len(_WORD.findall(piece)) >= _MIN_WORDS:
                sentences.append(piece)
    return sentences


def sentence_id(doc_id: str, text: str) -> str:
    """Deterministic id of one sentence of one document."""
    return hashlib.sha1(f"{doc_id}\n{text}".encode()).hexdigest()[:12]


def draw_sample(chunks: list[Chunk], size: int, seed: int) -> SentenceSample:
    """A seeded random sample of `size` distinct sentences of `chunks`, in reading order.

    Every sentence has the same chance: its rank is the SHA-256 of the seed and its id, and the `size`
    lowest ranks are taken. A sentence that occurs twice in one document (a repeated "Highly recommend!")
    is one sentence, found in its first chunk, because the sheet later finds it by its wording.
    Raises `ValueError` for a size below 1.
    """
    if size < 1:
        raise ValueError(f"a sample needs at least one sentence, got size {size}")
    population: dict[str, SampledSentence] = {}
    for chunk in sorted(chunks, key=lambda c: (c.doc_id, c.index)):
        for text in split_sentences(chunk.text):
            sid = sentence_id(chunk.doc_id, text)
            population.setdefault(
                sid, SampledSentence(id=sid, doc_id=chunk.doc_id, chunk_id=chunk.chunk_id, text=text)
            )
    ranked = sorted(population, key=lambda sid: hashlib.sha256(f"{seed}:{sid}".encode()).hexdigest())
    chosen = set(ranked[:size])
    # the dict keeps reading order, so filtering it keeps the sample in reading order as well
    return SentenceSample(
        seed=seed,
        size=size,
        population=len(population),
        sentences=[s for sid, s in population.items() if sid in chosen],
    )
