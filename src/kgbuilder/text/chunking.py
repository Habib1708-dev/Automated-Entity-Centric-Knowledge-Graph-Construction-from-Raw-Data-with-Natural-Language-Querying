"""Split documents into chunks that are small enough for extraction and large enough to carry context.

Role in the pipeline: `kg ingest-text`, between loading documents and writing the lexical graph.
Design: pure functions, no I/O. Chunk ids are `<doc_id>#<index>`, so they depend on the chunk settings;
that is why later stages read chunks back from the graph instead of calling this module again.
Not here: graph writes (lexical.py).
"""

import re

from pydantic import BaseModel

from .documents import Document

# A horizontal rule on its own line ("---" or "***"): the separator between reviews in the corpus.
_SECTION_BREAK = re.compile(r"\n\s*(?:---+|\*\*\*+)\s*\n")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    index: int  # position within the document, 0-based
    text: str


def _split_oversized(section: str, max_chars: int) -> list[str]:
    """Cut a section that exceeds `max_chars` on paragraph boundaries, and paragraphs on whitespace."""
    parts: list[str] = []
    current = ""
    for para in _PARAGRAPH_BREAK.split(section):
        if current and len(current) + len(para) + 2 > max_chars:
            parts.append(current)
            current = ""
        while len(para) > max_chars:  # a single oversized paragraph: cut on whitespace
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars  # no whitespace at all (a URL, a table row): hard cut
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        current = f"{current}\n\n{para}".strip()
    if current:
        parts.append(current)
    return parts


def _with_overlap(parts: list[str], overlap_chars: int) -> list[str]:
    """Prefix every part but the first with the tail of its predecessor, cut on a word boundary.

    Overlap exists so that a fact straddling a cut is still visible in one chunk. It is applied only
    inside one section: sections are separate reviews, and leaking one review into the next would let
    the extractor attribute a statement to the wrong review.
    """
    if overlap_chars <= 0:
        return parts
    result = parts[:1]
    for previous, part in zip(parts, parts[1:], strict=False):
        tail = previous[-overlap_chars:]
        tail = tail[tail.find(" ") + 1 :] if " " in tail else tail  # do not start mid-word
        result.append(f"{tail}\n\n{part}")
    return result


def _pack(pieces: list[str], min_chars: int) -> list[str]:
    """Join pieces shorter than `min_chars` onto the following piece (a heading alone is not a chunk)."""
    packed: list[str] = []
    carry = ""
    for piece in pieces:
        piece = f"{carry}\n\n{piece}".strip() if carry else piece
        carry = ""
        if len(piece) < min_chars:
            carry = piece
        else:
            packed.append(piece)
    if carry:  # a short tail has no successor: join it backwards
        packed.append(f"{packed.pop()}\n\n{carry}" if packed else carry)
    return packed


def chunk_document(
    doc: Document, max_chars: int = 1500, min_chars: int = 200, overlap_chars: int = 0
) -> list[Chunk]:
    """Split on section rules, cut oversized sections (with optional overlap), then pack tiny pieces.

    A chunk can exceed `max_chars` by up to `overlap_chars` plus one packed neighbour; the limits steer
    the split, they are not hard guarantees.
    """
    sections = [s.strip() for s in _SECTION_BREAK.split(doc.text) if s.strip()]
    pieces: list[str] = []
    for section in sections:
        if len(section) <= max_chars:
            pieces.append(section)
        else:
            pieces.extend(_with_overlap(_split_oversized(section, max_chars), overlap_chars))
    return [
        Chunk(chunk_id=f"{doc.doc_id}#{i}", doc_id=doc.doc_id, index=i, text=text)
        for i, text in enumerate(_pack(pieces, min_chars))
    ]
