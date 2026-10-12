"""The agent's exhaustive text tools (R141, agent/text_tools.py `ReadDocument` and `FindPassages`) with a fake
`TextReader`, without Neo4j: a document named by its id or by a passage id of it, read in order with the
total and a note when cut; an unknown document answered with a note; words passed lowercase, each passage
shown as its sentences holding a word, a note when cut or when nothing matches, the passages offered for
citation; a blank word refused by the arguments; and the excerpt rule itself. R149: a word held by a
passage's context counts (the fake mirrors the protocol's rule), and an uncut list of two or more passages
from one document says how many of its passages they are and points to `read_document`; a cut list or a
single passage points nowhere."""

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
        # the protocol's rule (R149): each word held by the passage's text or by its context
        found = [c for chunks in self._documents.values() for c in chunks
                 if all(w in c.text.lower() or w in c.context.lower() for w in words)]  # fmt: skip
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


def test_a_word_of_the_context_finds_passages_not_repeating_it_and_one_document_is_pointed_to_read():
    log = [ChunkText(chunk_id="log.md#0", context="Service log", text="The kettle was descaled.")]
    reader = FakeText({"notes.md": NOTES, "log.md": log})
    # no passage's text says 'notes': the context 'Kettle notes' holds it for every passage of notes.md
    named = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["notes", "lid"]))
    assert [c.chunk_id for c in named.chunks] == ["notes.md#0", "notes.md#1"]
    assert named.note == "These are 2 of the 3 passages of notes.md; read_document reads all 3."
    assert reader.asked[-1] == ("document", "notes.md", 1)  # the document's total, read by code
    # held through the context alone, no sentence holds the word: each passage shows whole
    whole = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["kettle notes"]))
    assert [c.text for c in whole.chunks] == [c.text for c in NOTES]
    both = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["kettle"]))
    assert [c.chunk_id for c in both.chunks] == ["notes.md#0", "notes.md#1", "notes.md#2", "log.md#0"]
    assert both.note == ""  # two documents: none pointed to


def test_a_cut_list_a_single_passage_or_a_whole_document_points_to_no_document():
    # the shown passages are all a.md's, but the cut hides b.md's: the list is not one document's
    a = [passage(f"a.md#{i}", "The lid is loose.") for i in range(3)]
    b = [passage(f"b.md#{i}", "The lid is new.") for i in range(2)]
    cut = FindPassages(FakeText({"a.md": a, "b.md": b}), limit=3, chars=300).run(
        PassageArguments(words=["lid"])
    )
    assert [c.chunk_id for c in cut.chunks] == ["a.md#0", "a.md#1", "a.md#2"] and cut.total == 5
    assert cut.note == "3 of 5 passages shown; add a word to narrow them."
    # one passage found is a lookup: reading its whole document is not asked for
    reader = FakeText({"notes.md": NOTES})
    single = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["late"]))
    assert [c.chunk_id for c in single.chunks] == ["notes.md#2"] and single.note == ""
    assert reader.asked == [("matching", ["late"], 5)]
    # every passage of the one document is shown: nothing is left for read_document
    every = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["kettle notes"]))
    assert len(every.chunks) == 3 and every.note == ""


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
