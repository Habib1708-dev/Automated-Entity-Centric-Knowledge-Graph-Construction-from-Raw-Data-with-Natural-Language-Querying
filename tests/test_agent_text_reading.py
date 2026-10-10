"""The agent's exhaustive text tools (R141, agent/text_tools.py `ReadDocument` and `FindPassages`) with a fake
`TextReader`, without Neo4j: a document named by its id or by a passage id of it, read in order with the
total and a note when cut; an unknown document answered with a note; words passed lowercase, each passage
shown as its sentences holding a word, a note when cut or when nothing matches, the passages offered for
citation; a blank word refused by the arguments; and the excerpt rule itself."""

import pytest
from pydantic import ValidationError

from kgbuilder.agent import ChunkText, FindPassages, ReadDocument
from kgbuilder.agent.text_tools import DocumentArguments, PassageArguments, excerpt


class FakeText:
    """`TextReader` over fixed passages per document; records what it is asked."""

    def __init__(self, documents: dict[str, list[ChunkText]]):
        self._documents = documents
        self.asked: list[tuple] = []

    def document(self, doc_id, limit):
        self.asked.append(("document", doc_id, limit))
        found = self._documents.get(doc_id, [])
        return found[:limit], len(found)

    def matching(self, words, limit):
        self.asked.append(("matching", list(words), limit))
        found = [c for chunks in self._documents.values() for c in chunks
                 if all(w in c.text.lower() for w in words)]  # fmt: skip
        return found[:limit], len(found)


def passage(chunk_id: str, text: str) -> ChunkText:
    return ChunkText(chunk_id=chunk_id, context="Kettle notes", text=text)


NOTES = [
    passage("notes.md#0", "The Meridian Kettle boils fast. Its lid is loose."),
    passage("notes.md#1", "Handle stays cool.\nThe lid rattles when it boils."),
    passage("notes.md#2", "Delivered late."),
]


def test_a_document_is_read_in_order_named_by_its_id_or_by_a_passage_of_it():
    reader = FakeText({"notes.md": NOTES})
    whole = ReadDocument(reader, limit=5).run(DocumentArguments(document="notes.md"))
    by_passage = ReadDocument(reader, limit=2).run(DocumentArguments(document="notes.md#1"))
    assert [c.chunk_id for c in whole.chunks] == ["notes.md#0", "notes.md#1", "notes.md#2"]
    assert (whole.total, whole.note) == (3, "")
    assert reader.asked[1] == ("document", "notes.md", 2)
    assert (by_passage.total, by_passage.note) == (3, "2 of 3 passages shown.")
    assert by_passage.chunks[0].text == NOTES[0].text  # whole passages, not excerpts
    assert by_passage.cites() == ["notes.md#0", "notes.md#1"]


def test_an_unknown_document_is_answered_with_a_note():
    result = ReadDocument(FakeText({}), limit=5).run(DocumentArguments(document="other.md#4"))
    assert (
        result.chunks == []
        and result.note == "No document 'other.md'; give what a passage id has before '#'."
    )


def test_every_passage_holding_the_words_is_found_and_shown_as_its_sentences():
    reader = FakeText({"notes.md": NOTES, "log.md": [passage("log.md#0", "A LID was replaced.")]})
    result = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=[" Lid "]))
    assert reader.asked == [("matching", ["lid"], 5)]  # trimmed and lowercase
    assert [c.chunk_id for c in result.chunks] == ["notes.md#0", "notes.md#1", "log.md#0"]
    assert [c.text for c in result.chunks] == [
        "Its lid is loose.",
        "The lid rattles when it boils.",
        "A LID was replaced.",
    ]
    assert (result.total, result.note) == (3, "")
    assert result.cites() == ["notes.md#0", "notes.md#1", "log.md#0"]


def test_find_passages_says_when_it_cut_the_list_or_found_nothing():
    reader = FakeText({"notes.md": NOTES})
    cut = FindPassages(reader, limit=1, chars=300).run(PassageArguments(words=["lid"]))
    none = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["lid", "spout"]))
    assert (len(cut.chunks), cut.total, cut.note) == (
        1,
        2,
        "1 of 2 passages shown; add a word to narrow them.",
    )
    assert (none.chunks, none.note) == ([], "No passage holds all these words.")


def test_the_words_must_be_one_to_five_and_none_blank():
    for words in ([], ["a"] * 6, ["lid", "  "]):
        with pytest.raises(ValidationError):
            PassageArguments(words=words)


def test_an_excerpt_keeps_the_sentences_holding_a_word_and_is_cut():
    text = "First line.\nThe lid is loose! Nothing else. Is the lid new? Yes."
    assert excerpt(text, ["lid"], 300) == "The lid is loose! ... Is the lid new?"
    assert excerpt(text, ["lid"], 10) == "The lid is"
    # a phrase across a sentence end matches no single sentence: the passage is shown whole
    assert excerpt("It is loose. Lid off.", ["loose. lid"], 300) == "It is loose. Lid off."
