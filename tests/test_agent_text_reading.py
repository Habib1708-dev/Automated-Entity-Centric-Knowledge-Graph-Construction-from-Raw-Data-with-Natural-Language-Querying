"""The agent's exhaustive text tools (R141, agent/text_tools.py `ReadDocument` and `FindPassages`) with a fake
`TextReader`, without Neo4j: a document named by its id or by a passage id of it, read in order with the
total and a note when cut; an unknown document answered with a note; words passed lowercase, each passage
shown as its sentences holding a word, a note when cut or when nothing matches, the passages offered for
citation; a blank word refused by the arguments; and the excerpt rule itself. R149: a word held by a
passage's context counts (the fake mirrors the protocol's rule), and an uncut list of two or more passages
from one document says how many of its passages they are and points to `read_document`; a cut list or a
single passage points nowhere. R162: a word is searched as written or as its other forms, and the excerpt
shows them; the stem rule itself (endings, the shortest stem, ids and phrases); a cut word's stem matched
only as a whole word with an ending, so it never finds an unrelated word holding it ("signs" never finds
"design") and nothing the word as written finds is lost; and the forms rule the model reads, its example and
its limits, is what the code does. R169: a space or a hyphen in a phrase matches either, in the literal and
in the stem form, the excerpt shows the sentence found so, and the rule's example is what the code does.
R170: a passage's heading lines stay in the excerpt, in order with the matching sentences, once when a
heading itself holds the word, and a passage with no matching sentence is still shown whole. R207: an
excerpt cut at the cap ends in "...", and `find_passages` says how many passages it cut and which tool shows
them whole."""

import re

import pytest
from pydantic import ValidationError

from kgbuilder.agent import ChunkText, FindPassages, ReadDocument
from kgbuilder.agent.text_tools import (
    _ENDINGS,
    _MIN_STEM,
    DocumentArguments,
    PassageArguments,
    excerpt,
    form_pattern,
    stem,
)


class FakeText:
    """`TextReader` over fixed passages per document; records what it is asked."""

    def __init__(self, documents: dict[str, list[ChunkText]]):
        self._documents = documents
        self.asked: list[tuple] = []

    def document(self, doc_id, limit):
        self.asked.append(("document", doc_id, limit))
        found = self._documents.get(doc_id, [])
        return found[:limit], len(found)

    def matching(self, patterns, limit):
        self.asked.append(("matching", list(patterns), limit))
        # the protocol's rule (R149, R162): each pattern found in the passage's lowercased text or context
        found = [c for chunks in self._documents.values() for c in chunks
                 if all(re.search(p, c.text.lower()) or re.search(p, c.context.lower())
                        for p in patterns)]  # fmt: skip
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


def test_find_passages_marks_a_cut_passage_and_says_how_many_were_cut():
    """R207: an excerpt cut at `chars` ended mid-word with nothing said, so it read as the passage's end."""
    reader = FakeText({"notes.md": NOTES, "log.md": [passage("log.md#0", "A LID was replaced.")]})
    result = FindPassages(reader, limit=5, chars=19).run(PassageArguments(words=["lid"]))
    assert [c.text for c in result.chunks] == [
        "Its lid is loose.",
        "The lid rattles whe...",
        "A LID was replaced.",
    ]
    assert result.note == (
        "1 of these passages are cut at 19 characters, ending in '...'; read_document shows them whole."
    )


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
    assert excerpt(text, ["lid"], 10) == "The lid is..."  # R207: a cut is marked
    # a phrase across a sentence end matches no single sentence: the passage is shown whole
    assert excerpt("It is loose. Lid off.", [form_pattern("loose. lid")], 300) == "It is loose. Lid off."


def test_a_word_finds_its_other_forms_and_the_excerpt_shows_them():
    log = [ChunkText(chunk_id="log.md#0", context="Service log", text="Descaled. It boiled dry once.")]
    reader = FakeText({"notes.md": NOTES, "log.md": log})
    # R157's FB38: 'wobble' never met 'wobbling'; here 'boiling' must meet 'boils' and 'boiled'
    found = FindPassages(reader, limit=5, chars=300).run(PassageArguments(words=["Boiling"]))
    assert reader.asked == [("matching", [form_pattern("boiling")], 5)]  # the reader runs the tool's pattern
    assert [c.text for c in found.chunks] == [
        "The Meridian Kettle boils fast.",
        "The lid rattles when it boils.",
        "It boiled dry once.",
    ]


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("boil", "boil"),
        ("boils", "boil"),
        ("boiled", "boil"),
        ("boiling", "boil"),
        ("rattle", "rattl"),  # '-e' too, so 'rattle' meets 'rattles' and 'rattling'
        ("rattles", "rattl"),  # '-es' before '-s'
        ("quickly", "quickly"),  # '-ly' is no inflection: cut, 'comply' and 'supply' left 'comp' and 'supp'
        ("comply", "comply"),
        ("supply", "supply"),
        ("notes", "note"),  # '-es' would leave three letters: '-s' is the ending that keeps four
        ("lids", "lids"),  # 'lid' is under four letters: matched as written
        ("late", "late"),
        ("press", "press"),  # a double 's' is no plural
        ("presses", "press"),
        ("@handle", "@handle"),  # a sign or a digit: an id, matched as written
        ("kv12-0457", "kv12-0457"),
        ("2025", "2025"),
        ("rating:", "rating:"),
        ("cafés", "cafés"),  # letters outside ASCII: the pattern's word edges are drawn against ASCII
        ("loose handles", "loose handl"),  # a phrase: only its last word is cut
        ("boiling water", "boiling water"),
    ],
)
def test_a_word_loses_one_ending_when_four_letters_stay_and_an_id_or_short_word_is_kept(word, expected):
    assert stem(word) == expected


def found(word: str, text: str) -> bool:
    return re.search(form_pattern(word), text) is not None


@pytest.mark.parametrize(
    ("word", "forms", "unrelated"),
    [
        # R162's first replay: the stem matched anywhere found 'design' for 'signs', 'prevent' for 'vents',
        # 'started' for 'stars', 'notification' for 'notified', 'company' for 'comply'
        ("signs", ["sign", "signed", "signing", "a sign."], ["design", "consigned"]),
        ("vents", ["vent", "vented", "venting"], ["prevent", "eventual", "inventory"]),
        ("stars", ["star", "a star."], ["started", "mustard"]),
        ("notified", ["notifies"], ["notification"]),
        ("comply", ["comply", "complying"], ["company", "completely"]),
        ("wobble", ["wobbling", "wobbles", "wobbled"], []),
        ("loose handles", ["loose handle", "loose handled"], ["loose handlebar", "noose handle"]),
        # R169: a space or a hyphen in a phrase is either, in the stem form too ("loose-handled")
        ("loose handles", ["loose-handle", "loose-handled"], ["loosehandle", "loose_handle"]),
        ("lid seal", ["lid-seal", "lid seal", "lid seals"], ["lidseal"]),
        ("lid-seal", ["lid seal", "lid-seal"], ["lid_seal"]),
    ],
)
def test_a_cut_words_stem_is_matched_only_as_a_whole_word_with_an_ending(word, forms, unrelated):
    assert all(found(word, form) for form in forms)
    assert not any(found(word, text) for text in unrelated)


@pytest.mark.parametrize(
    ("word", "text"),
    [
        ("signs", "designs"),
        ("vents", "prevents"),
        ("loose handles", "loose handles2"),
        ("kv12-0457", "a kv12-0457"),
    ],
)
def test_whatever_the_word_as_written_found_before_it_still_finds(word, text):
    # the pattern keeps the literal match (before R162 the only one): a stem adds forms, it never loses one
    assert found(word, text)


def test_the_excerpt_keeps_every_heading_line_with_the_matching_sentences():
    """R170: FC05's agent saw twenty passages holding "stars" and none of their rating headings, which the
    excerpt had cut as sentences holding no word."""
    text = "## Rating: 3 of 5\nThe lid is loose! Nothing else.\n## Notes\nIs the lid new? Yes."
    assert (
        excerpt(text, ["lid"], 300)
        == "## Rating: 3 of 5 ... The lid is loose! ... ## Notes ... Is the lid new?"
    )
    assert excerpt(text, ["lid"], 20) == "## Rating: 3 of 5 ....."  # the cut is the same, marked (R207)
    assert (
        excerpt("## Lid notes\nAll fine.", ["lid"], 300) == "## Lid notes"
    )  # a heading holding the word, once
    assert excerpt("## Rating\nNothing here.", ["lid"], 300) == "## Rating\nNothing here."  # no match: whole
    assert "shows its headings and its sentences" in FindPassages.description


def test_a_phrase_found_by_its_hyphenated_form_is_shown_by_the_excerpt():
    assert excerpt("Cold start. A lid-seal was fitted. Nothing else.", [form_pattern("lid seal")], 300) == (
        "A lid-seal was fitted."
    )


def test_the_forms_rule_the_model_reads_is_what_the_code_does():
    words = PassageArguments.model_fields["words"].description or ""
    # R169: the space-or-hyphen rule and its example, both ways
    assert "a space or a hyphen in a phrase matches either" in words
    hyphen = re.search(r"\('([\w-]+)' finds '([\w ]+)'\)", words)
    assert hyphen is not None and found(hyphen.group(1), hyphen.group(2))
    assert found(hyphen.group(2), hyphen.group(1))
    # the example: the searched word is cut, and each form named is found
    example = re.search(r"'(\w+)' finds '(\w+)', '(\w+)' and '(\w+)'", words)
    assert example is not None
    query, *forms = example.groups()
    assert stem(query) != query and all(found(query, form) for form in forms)
    # the endings and the shortest stem named are the code's (an ending is written after a space, so a
    # hyphen inside the example phrase is none)
    assert set(re.findall(r"(?<![\w'])-(\w+)", words)) == set(_ENDINGS)
    assert f"{['', 'one', 'two', 'three', 'four', 'five'][_MIN_STEM]} letters stay" in words
    # the limits named: a shorter word, a doubled letter and a y turned to i are not joined
    assert not found("rate", "rating")
    assert not found("stopped", "stop") and not found("stops", "stopped")
    assert not found("carried", "carry")
    # the rule is stated once: the tool's description only points to the search by meaning
    assert "-ing" not in FindPassages.description
