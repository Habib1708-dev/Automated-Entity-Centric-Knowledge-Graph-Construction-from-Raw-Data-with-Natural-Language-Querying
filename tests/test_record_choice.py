"""Tiers 2 and 3 of record matching (R95b, resolution/record_choice.py): the near misses of a mention no rule
linked, and an LLM's choice among them, linked only when code verifies it: a listed id, a quote that stands
in the mention's chunk and names it, and no other listed record of the same name. Without an LLM, too long a
list or no sentence to quote, nothing is asked and nothing is linked. The LLM is a ScriptedLLM; no Neo4j.
The records are an invented telescope; the near-miss test also replays R93's real wrong and lost links. A word
key inside a longer name nominates its record (R108)."""

import json

from kgbuilder.core.errors import LLMResponseError
from kgbuilder.resolution.names import near_name, same_name
from kgbuilder.resolution.record_choice import (
    CHOICE_PROMPT,
    CandidateView,
    ChoiceRequest,
    RecordChoice,
    choice_lines,
    choice_prompt,
    choose_records,
    chosen_links,
    near_misses,
    relation_line,
)
from kgbuilder.resolution.records import RecordCandidate, RecordLink, RecordMatch
from tests.fakes import ScriptedLLM

from .evaluation_corpora import quoted_four_grams


def record(element_id: str, name: str, key: str, label: str = "Unit") -> RecordCandidate:
    return RecordCandidate(element_id=element_id, label=label, name=name, key=key)


FOCUSER = record("e1", "Focuser", "U-1")
KNOB = record("e2", "Focuser Knob", "U-2")
EYEPIECE = record("e3", "Eyepiece", "U-3")
TRIPOD = record("e4", "Tripod", "U-4", label="Mount")
SCOPE = [FOCUSER, KNOB, EYEPIECE, TRIPOD]


def test_a_near_miss_shares_a_word_up_to_its_ending_or_is_spelled_alike():
    # R93's lost and wrong links are all near misses: an LLM tells them apart, code never links one alone
    assert near_name("weighted base", "Base", borderline=80)
    assert near_name("pre-drilled holes for the drawer handle", "Drawer Handle", borderline=80)
    assert near_name("drawer", "Drawer Unit", borderline=80)  # the record's name holds the mention's
    assert near_name("legs", "Leg", borderline=80)  # an ending from a three-letter stem
    assert near_name("drawer slides", "Drawer Sides", borderline=80)  # a shared word, and 96 alike
    assert near_name("Eyepeice", "Eyepiece", borderline=80)  # a misspelling inside the word: 88 alike
    # no shared word and not alike; a two-letter word is no evidence; a word with a digit must be equal
    assert not near_name("focuser", "Tripod", borderline=80)
    assert not near_name("ring of light", "Of", borderline=80)
    assert not near_name("model 2019", "Release 2018", borderline=80)


def test_twins_are_records_of_one_name():
    assert same_name("Focuser Knob", "knob focuser") and same_name("Focuser-Knob", "Focuser Knob")
    assert not same_name("Focuser", "Focusers") and not same_name("", "")


def test_near_misses_exist_only_where_code_decided_nothing_and_only_inside_a_scope():
    nothing = RecordMatch()
    # both focuser records share the word; the eyepiece and the tripod do not; each record once, by label/key
    assert near_misses("the focuser", nothing, [SCOPE, [FOCUSER]], borderline=80, domain=SCOPE) == [
        FOCUSER,
        KNOB,
    ]
    # no scope: a near name in the domain vouches for nothing (R94); R99's strict candidates are tested below
    assert near_misses("the focuser", nothing, [], borderline=80, domain=SCOPE) == []
    linked = RecordMatch(link=RecordLink(record=FOCUSER, reason="name", score=100, evidence="", scoped=True))
    assert near_misses("focuser", linked, [SCOPE], borderline=80, domain=SCOPE) == []
    assert (
        near_misses("focuser", RecordMatch(tied=[FOCUSER, KNOB]), [SCOPE], borderline=80, domain=SCOPE) == []
    )


# R99: outside a scope, an invented observatory's staff and instruments
LENA = RecordCandidate(
    element_id="o1", label="Staff", name="Helena Marsh", key="O-1", attributes={"team": "Optics"}
)
HUGO = RecordCandidate(
    element_id="o2", label="Staff", name="Hugo Marsh", key="O-2", attributes={"team": "Optics"}
)
ORION = record("i1", "Dome scope", "I-1", label="Instrument").model_copy(
    update={"attributes": {"model": "Orion R-7"}}
)
VEGA_A = record("i2", "North scope", "I-2", label="Instrument").model_copy(
    update={"attributes": {"model": "Vega 80"}}
)
VEGA_B = record("i3", "South scope", "I-3", label="Instrument").model_copy(
    update={"attributes": {"model": "Vega 80"}}
)
DOMAIN = [LENA, HUGO, ORION, VEGA_A, VEGA_B]


def test_outside_a_scope_a_variant_of_a_records_name_is_a_candidate():
    nothing = RecordMatch()
    # a short form and an initial with a title: Helena only; the initial "H." fits Hugo as well
    assert near_misses("Lena Marsh", nothing, [], borderline=80, domain=DOMAIN) == []  # not a prefix
    assert near_misses("Hel Marsh", nothing, [], borderline=80, domain=DOMAIN) == [LENA]
    assert near_misses("Dr. H. Marsh", nothing, [], borderline=80, domain=DOMAIN) == [LENA, HUGO]
    # a shared word is no candidate outside a scope, however alike
    assert near_misses("the dome", nothing, [], borderline=80, domain=DOMAIN) == []


def test_outside_a_scope_the_one_record_holding_the_name_as_key_attribute_is_a_candidate():
    nothing = RecordMatch()
    assert near_misses("orion r-7", nothing, [], borderline=80, domain=DOMAIN) == [ORION]
    # an attribute twin: two instruments of one model; the value names a kind of record, not one
    assert near_misses("Vega 80", nothing, [], borderline=80, domain=DOMAIN) == []
    assert near_misses("Optics", nothing, [], borderline=80, domain=DOMAIN) == []  # a team of two
    # a scoped document keeps tier 2 as it was: the domain's strict candidates are not added
    assert near_misses("Orion R-7", nothing, [[LENA]], borderline=80, domain=DOMAIN) == []


def test_a_choice_outside_a_scope_is_linked_as_unscoped():
    request = ChoiceRequest(
        mention="m1",
        name="Hel Marsh",
        lines=["[Notes] Hel Marsh aligned the mirror."],
        texts=["Hel Marsh aligned the mirror."],
        candidates=[LENA],
        scoped=False,
    )
    llm = ScriptedLLM(
        lambda prompt, schema: RecordChoice(record="Staff:O-1", quote="Hel Marsh aligned the mirror.")
    )
    decisions = choose_records([request], {"o1": CandidateView(cells={}, relations=[])}, llm, "model-x")
    [link] = chosen_links(decisions, [request]).values()
    assert decisions[0].action == "chosen" and link.record is LENA and not link.scoped


def test_the_lines_are_the_sentences_naming_the_mention_with_their_document_once_each():
    chunks = [
        ("Lyra Telescope Notes", "The brass focuser sticks. The tripod is fine. The brass focuser sticks."),
        ("Lyra Telescope Notes", "Nothing here."),
    ]
    assert choice_lines("brass focuser", chunks) == ["[Lyra Telescope Notes] The brass focuser sticks."]
    many = [("D", " ".join(f"Focuser note {i}." for i in range(9)))]
    assert len(choice_lines("focuser", many)) == 5


def test_the_choice_prompt_speaks_no_corpus_language():
    text = CHOICE_PROMPT + json.dumps(RecordChoice.model_json_schema())
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "pump", "part")
    words = set(text.lower().replace('"', " ").split())
    assert not [w for w in banned if w in words or f"{w}s" in words]
    assert quoted_four_grams(text) == []


VIEWS = {
    "e1": CandidateView(
        cells={"finish": "brass"}, relations=[relation_line("FITS", True, "Mount:U-4", "Tripod")]
    ),
    "e2": CandidateView(cells={}, relations=[relation_line("PART_OF", True, "Unit:U-1", "Focuser")]),
    "e3": CandidateView(cells={}, relations=[]),
    "e4": CandidateView(cells={}, relations=[relation_line("FITS", False, "Unit:U-1", "Focuser")]),
}
TEXT = "Lyra notes. The brass focuser sticks in the cold. The focuser knob turns freely."


def request(name: str = "brass focuser", candidates=(FOCUSER, KNOB), lines=None) -> ChoiceRequest:
    return ChoiceRequest(
        mention=f"m-{name}",
        name=name,
        lines=lines if lines is not None else choice_lines(name, [("Lyra Telescope Notes", TEXT)]),
        texts=[TEXT],
        candidates=list(candidates),
    )


def test_the_prompt_shows_each_near_miss_with_its_id_name_cells_and_relations():
    prompt = choice_prompt(request(), VIEWS)
    assert "[Lyra Telescope Notes] The brass focuser sticks in the cold." in prompt
    assert "- Unit:U-1: Focuser\n  data: finish = brass\n  relations: FITS -> Mount:U-4 (Tripod)" in prompt
    assert "- Unit:U-2: Focuser Knob\n  data: (none)\n  relations: PART_OF -> Unit:U-1 (Focuser)" in prompt
    assert relation_line("FITS", False, "Unit:U-1", "Focuser") == "Unit:U-1 (Focuser) FITS -> this"


def answering(record_id: str, quote: str = "The brass focuser sticks in the cold.") -> ScriptedLLM:
    return ScriptedLLM(lambda prompt, schema: RecordChoice(record=record_id, quote=quote))


def test_a_listed_choice_with_a_verified_quote_links_and_says_which_model_chose_it():
    llm = answering("Unit:U-1")
    [decision] = choose_records([request()], VIEWS, llm, "model-x")
    assert (decision.action, decision.record, decision.by) == ("chosen", "Unit:U-1", "model-x")
    assert decision.candidates == ["Unit:U-1", "Unit:U-2"] and llm.calls == [("RecordChoice", "model-x")]
    link = chosen_links([decision], [request()])["m-brass focuser"]
    assert (link.record, link.reason, link.score, link.by) == (FOCUSER, "chosen", None, "model-x")
    assert link.evidence == "The brass focuser sticks in the cold." and link.scoped


def test_none_an_unlisted_id_or_an_unverified_quote_is_no_link():
    cases = {
        "none": answering("none", quote=""),
        "not_listed": answering("Mount:U-4"),  # a real record, but not one of this mention's near misses
        "quote_not_verified": answering(
            "Unit:U-1", quote="The brass focuser was replaced."
        ),  # not in the text
    }
    for action, llm in cases.items():
        [decision] = choose_records([request()], VIEWS, llm, "model-x")
        assert (decision.action, decision.record, decision.evidence) == (action, None, "")
    # a sentence of the chunk that does not name the mention shows nothing about it
    [other] = choose_records([request()], VIEWS, answering("Unit:U-1", "The focuser knob turns freely."), "m")
    assert other.action == "quote_not_verified"
    assert chosen_links([other], [request()]) == {}


def test_a_choice_between_twins_is_refused():
    twin = record("e5", "Focuser", "U-5")
    [decision] = choose_records([request(candidates=(FOCUSER, twin))], VIEWS | {"e5": VIEWS["e1"]},
                                answering("Unit:U-1"), "m")  # fmt: skip
    assert decision.action == "twin"


def test_nothing_is_asked_without_an_llm_a_sentence_to_quote_or_a_short_enough_list():
    llm = answering("Unit:U-1")
    crowd = [record(f"c{i}", f"Focuser {i}", f"U-{100 + i}") for i in range(21)]
    views = VIEWS | {c.element_id: CandidateView(cells={}, relations=[]) for c in crowd}
    unasked = [request(lines=[]), request(candidates=crowd)]
    assert [d.action for d in choose_records(unasked, views, llm, "m")] == ["no_sentence", "too_many"]
    assert llm.calls == [] and all(d.by == "code" for d in choose_records(unasked, views, llm, "m"))
    [skipped] = choose_records([request()], VIEWS, None, "m")
    assert (skipped.action, skipped.by) == ("skipped", "code")


def test_a_failed_call_is_no_link_and_no_failure_of_the_rest():
    def script(prompt, schema):
        if '"brass focuser"' in prompt:
            raise LLMResponseError("the reply did not parse")
        return RecordChoice(record="Unit:U-2", quote="The focuser knob turns freely.")

    decisions = choose_records([request(), request("focuser knob")], VIEWS, ScriptedLLM(script), "m")
    assert [d.action for d in decisions] == ["failed", "chosen"]


def test_a_word_key_in_a_longer_name_nominates_its_record_inside_a_scope_and_outside():
    """R108: a key that is a plain word no longer links inside a longer name ("Corvid Voyager" for the key
    "CORVID"), so it nominates: the chooser tells a version of the record from a line of its own."""
    corvid = record("w1", "Wide-field refractor", "CORVID", label="Telescope")
    nothing = RecordMatch()
    assert near_misses("Corvid Voyager", nothing, [[corvid, TRIPOD]], borderline=80, domain=SCOPE) == [corvid]
    assert near_misses("Corvid Voyager", nothing, [], borderline=80, domain=[corvid, *SCOPE]) == [corvid]
