"""One individual across documents (R75 part b2), pure: name variants, the nominated pairs, the code check of
an adjudication's quotes, and joining (a "same" without verified quotes refused, two records never one, the
same name alone never enough), plus the adjudication prompt's corpus-language guard. R100: what each side is
shown (every sentence naming it with its neighbours, a record unit's data, the records both sides name), a
quote checked against exactly those lines, "unsure" kept apart, a failed call kept apart, and a side no
sentence names never asked. No Neo4j."""

import json

import pytest

from kgbuilder.core.errors import LLMResponseError
from kgbuilder.resolution.identity_evidence import EvidenceLine, build_evidence, side_lines
from kgbuilder.resolution.individuals import (
    IDENTITY_PROMPT,
    SameIndividual,
    Unit,
    display_name,
    join,
    llm_adjudicator,
    nominate,
    verified,
)
from kgbuilder.resolution.mentions import MentionText
from kgbuilder.resolution.record_choice import CandidateView
from kgbuilder.resolution.records import RecordCandidate, RecordLink, RecordMatch, match_record
from kgbuilder.resolution.variants import compatible, name_words, without_title

from .evaluation_corpora import quoted_four_grams


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Jonathan Pike", "Dr. Jonathan Pike"),  # a title says nothing about who
        ("Jonathan Pike", "J. Pike"),  # an initial
        ("Jonathan Pike", "Jon Pike"),  # a short form
        ("Dr. J. Pike", "Dr Jonathan Pike"),
        ("Jonathan Pike", "Jonathan A. Pike"),  # middle names are left out as often as written
        ("Judith Pike", "J. Pike"),  # which is why a variant only nominates a pair
    ],
)
def test_names_that_could_name_one_individual_are_variants(a, b):
    assert compatible(a, b) and compatible(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Jonathan Pike", "Judith Pike"),
        ("Jonathan Pike", "Jonathan Pine"),
        ("Pike", "Jonathan Pike"),  # one word says too little
        ("Jo Pike", "Jonathan Pike"),  # two letters match too much to be a short form
        ("", "Jonathan Pike"),
    ],
)
def test_names_of_other_individuals_are_no_variants(a, b):
    assert not compatible(a, b)


def test_only_leading_titles_are_dropped():
    assert (
        name_words("Prof. Dr. Ada Lin") == ["ada", "lin"]
        and without_title("Dr Jonathan Pike") == "jonathan pike"
    )
    assert name_words("Dr") == ["dr"]  # a single word stays, whatever it is


def record(element_id: str, name: str, key: str, **attributes: str) -> RecordCandidate:
    return RecordCandidate(element_id=element_id, label="Staff", name=name, key=key, attributes=attributes)


STAFF = [
    record("s131", "Jonathan Pike", "S-131", team="Soil Ecology"),
    record("s104", "Maria Lopez", "S-104", team="Soil Ecology"),
]


def test_a_titled_name_matches_its_record_without_the_title():
    match = match_record("Dr Jonathan Pike", [], STAFF, [], threshold=90)
    assert match.link.record.element_id == "s131" and match.link.reason == "name"


def test_a_name_variant_matches_a_record_only_with_its_attribute_in_the_sentence():
    with_team = match_record("Dr. J. Pike", ["Talk by Dr. J. Pike (Soil Ecology)."], STAFF, [], threshold=90)
    assert with_team.link.record.element_id == "s131" and with_team.link.reason == "variant_attribute"
    assert match_record("J. Pike", ["J. Pike explained the overrun."], STAFF, [], threshold=90).link is None


def unit(uid: str, *names: str, record: str | None = None, chunks: int = 1) -> Unit:
    return Unit(id=uid, type="Person", names=list(names), mentions=[uid], record=record, chunks=chunks)


PIKE = unit("a", "Jonathan Pike", record="Staff:S-131", chunks=3)
JON = unit("b", "Jon Pike")
JUDITH = unit("c", "Judith Pike")
TEXTS = {
    "a": ["Jonathan Pike leads the Soil Ecology group."],
    "b": ["Jon Pike (chair) of Soil Ecology opened the meeting."],
    "c": ["Judith Pike wrote about Harbour Street."],
}
UNITS = {u.id: u for u in (PIKE, JON, JUDITH)}
# what each unit is shown: its sentences naming it, with their neighbours (one sentence each here)
SHOWN = {uid: side_lines(UNITS[uid].names, [("doc", text) for text in texts]) for uid, texts in TEXTS.items()}


def test_pairs_are_nominated_by_variant_or_spelling_only_within_a_type():
    lin = unit("d", "Ada Lin").model_copy(update={"type": "Visitor"})
    pairs = {(a.id, b.id, signal) for a, b, signal in nominate([PIKE, JON, JUDITH, lin], 90, set())}
    assert pairs == {("a", "b", "variant")}  # Judith and Jonathan are no variants; Ada is another type


# R100: an invented observatory's log, where the evidence stands next to the name
LOG = (
    "Night log of the north dome. "  # a heading-like first sentence, as R75's prompt showed alone
    "The shutter jammed at midnight. "
    "L. Brandt freed it by hand. "
    "She is the dome technician. "
    "The sky cleared at two."
)
BRANDT = Unit(id="m1", type="Person", names=["L. Brandt"], mentions=["m1"], chunks=1)
LENA = Unit(id="m2", type="Person", names=["Lena Brandt"], mentions=["m2"], record="Staff:O-1", chunks=1)


def test_a_side_is_shown_every_sentence_naming_it_with_its_neighbours():
    lines = side_lines(["L. Brandt"], [("Night log", LOG)])
    assert [(line.sentence, line.names_it) for line in lines] == [
        ("The shutter jammed at midnight.", False),
        ("L. Brandt freed it by hand.", True),
        ("She is the dome technician.", False),
    ]  # the heading and the last sentence are two away: not shown
    many = side_lines(["Brandt"], [("D", " ".join(f"Brandt note {i}." for i in range(9)))])
    # four naming sentences at most, with their neighbours: notes 0-3 and note 4, never note 5
    assert [line.sentence for line in many] == [f"Brandt note {i}." for i in range(5)]


def test_evidence_shows_a_record_units_data_and_the_records_both_sides_name():
    shutter = RecordCandidate(element_id="e9", label="Device", name="North shutter", key="D-9")
    rows = [
        MentionText(mention="m1", document="Night log", chunk_id="log#0", text=LOG),
        MentionText(mention="m3", document="Night log", chunk_id="log#0", text=LOG),
        MentionText(
            mention="m2", document="Staff page", chunk_id="staff#0", text="Lena Brandt runs the dome."
        ),
        MentionText(
            mention="m4", document="Staff page", chunk_id="staff#0", text="Lena Brandt runs the dome."
        ),
    ]
    link = RecordLink(record=shutter, reason="name", score=100, evidence="", scoped=False)
    matches = {"m1": RecordMatch(), "m3": RecordMatch(link=link), "m4": RecordMatch(link=link)}
    views = {
        "Staff:O-1": CandidateView(cells={"role": "dome technician"}, relations=[]),
        "Device:D-9": CandidateView(cells={"place": "north dome"}, relations=[]),
    }
    shutter_unit = Unit(id="m3", type="Device", names=["shutter"], mentions=["m3", "m4"], chunks=2)
    evidence = build_evidence([BRANDT, LENA, shutter_unit], rows, matches, views)
    assert evidence.sides["m2"].view == views["Staff:O-1"] and evidence.sides["m1"].view is None
    assert evidence.shared(BRANDT, LENA) == ["Device:D-9"]  # both texts name the shutter record
    prompts: list[str] = []

    class Capture:
        def generate(self, prompt, schema, *, model, temperature=0.0, thinking=""):
            prompts.append(prompt)
            return SameIndividual(answer="unsure")

    llm_adjudicator(Capture(), "m", evidence)(BRANDT, LENA)
    assert "- * [Night log] L. Brandt freed it by hand." in prompts[0]
    assert "-   [Night log] She is the dome technician." in prompts[0]
    assert "Staff:O-1 in the data: role = dome technician; relations: (none)" in prompts[0]
    assert "- Device:D-9: place = north dome; relations: (none)" in prompts[0]


def test_evidence_only_in_the_neighbouring_sentence_joins():
    shown = {
        "m1": side_lines(["L. Brandt"], [("Night log", LOG)]),
        "m2": side_lines(["Lena Brandt"], [("Staff page", "Lena Brandt is the dome technician.")]),
    }
    reply = SameIndividual(
        answer="same", quote_a="She is the dome technician.", quote_b="Lena Brandt is the dome technician."
    )
    assert verified(reply, shown["m1"], shown["m2"])  # A's quote does not name A: its neighbour does
    joining = join([BRANDT, LENA], [(BRANDT, LENA, "variant")], lambda a, b: reply, shown, "judge")
    assert joining.groups == [["m1", "m2"]] and joining.decisions[0].action == "joined"


def test_a_quote_counts_only_among_the_lines_shown_for_its_own_side():
    good = SameIndividual(
        answer="same",
        quote_a="Jonathan Pike leads the Soil Ecology group.",
        quote_b="Jon Pike (chair) of Soil Ecology",
    )
    assert verified(good, SHOWN["a"], SHOWN["b"])
    copied = good.model_copy(update={"quote_a": "[doc] Jonathan Pike leads the Soil Ecology group."})
    assert verified(copied, SHOWN["a"], SHOWN["b"])  # the line copied with its document is the same line
    swapped = good.model_copy(update={"quote_a": good.quote_b, "quote_b": good.quote_a})
    assert not verified(swapped, SHOWN["a"], SHOWN["b"])  # each quote from the other side's lines
    invented = good.model_copy(update={"quote_b": "Jon Pike is the group leader."})
    assert not verified(invented, SHOWN["a"], SHOWN["b"])  # not in B's lines
    # a sentence of the log two away from the name is in the chunk but was not shown: it shows nothing
    log = side_lines(["L. Brandt"], [("Night log", LOG)])
    outside = SameIndividual(answer="same", quote_a="The sky cleared at two.", quote_b=good.quote_b)
    assert not verified(outside, log, SHOWN["b"])


def test_a_join_needs_a_yes_with_verified_quotes_and_never_puts_two_records_together():
    other = unit("e", "J. Pike", record="Staff:S-999")
    replies = {
        frozenset("ab"): SameIndividual(
            answer="same", quote_a="Jonathan Pike leads the Soil Ecology group.", quote_b="Jon Pike (chair)"
        ),
        frozenset("bc"): SameIndividual(answer="same", quote_a="Jon Pike (chair)", quote_b="made up"),
        frozenset("be"): SameIndividual(answer="same", quote_a="Jon Pike (chair)", quote_b="J. Pike"),
    }
    asked: list[frozenset] = []

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        asked.append(frozenset((a.id, b.id)))
        return replies.get(frozenset((a.id, b.id)), SameIndividual(answer="different"))

    pairs = [
        (PIKE, JON, "variant"),
        (JON, JUDITH, "variant"),
        (JON, other, "variant"),
        (PIKE, other, "variant"),
    ]
    shown = {**SHOWN, "e": side_lines(["J. Pike"], [("doc", "Talk by J. Pike.")])}
    joining = join([PIKE, JON, JUDITH, other], pairs, adjudicate, shown, "judge-model")
    actions = {(d.a, d.b): d.action for d in joining.decisions}
    assert actions == {
        ("a", "b"): "joined",
        ("b", "c"): "quote_not_verified",  # a yes whose quote is not among Judith's lines
        ("b", "e"): "different_records",  # verified, but the group already holds another record
        ("a", "e"): "different_records",  # two records: never asked
    }
    assert frozenset("ae") not in asked
    assert sorted(joining.groups) == [["a", "b"], ["c"], ["e"]]
    [joined] = [d for d in joining.decisions if d.action == "joined"]
    assert joined.by == "judge-model" and joined.evidence.startswith("A: Jonathan Pike leads")


def test_unsure_and_different_keep_the_pair_apart_and_are_counted_apart():
    for answer, action in (("unsure", "unsure"), ("different", "apart")):
        joining = join(
            [PIKE, JON],
            [(PIKE, JON, "variant")],
            lambda a, b, answer=answer: SameIndividual(answer=answer),
            SHOWN,
            "judge",
        )
        assert joining.groups == [["a"], ["b"]] and joining.decisions[0].action == action


def test_a_side_no_sentence_names_is_not_asked():
    asked: list[str] = []

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        asked.append(a.id)
        return SameIndividual(answer="same")

    shown = {**SHOWN, "b": [EvidenceLine(document="doc", sentence="Opened the meeting.", names_it=False)]}
    joining = join([PIKE, JON], [(PIKE, JON, "variant")], adjudicate, shown, "judge")
    assert asked == [] and joining.decisions[0].action == "no_sentence" and joining.decisions[0].by == "code"


def test_a_failed_adjudication_keeps_its_pair_apart_and_never_fails_the_others():
    """R100: the provider kept failing (or its reply did not parse) for one pair: that pair is logged as
    `failed` and stays apart, the other pairs are decided as usual, and nothing is raised."""

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        if {a.id, b.id} == {"b", "c"}:
            raise LLMResponseError("judge-model failed 3 times for SameIndividual")
        return SameIndividual(
            answer="same", quote_a="Jonathan Pike leads the Soil Ecology group.", quote_b="Jon Pike (chair)"
        )

    pairs = [(PIKE, JON, "variant"), (JON, JUDITH, "variant")]
    joining = join([PIKE, JON, JUDITH], pairs, adjudicate, SHOWN, "judge-model")
    actions = {(d.a, d.b): (d.action, d.by) for d in joining.decisions}
    assert actions == {("a", "b"): ("joined", "judge-model"), ("b", "c"): ("failed", "judge-model")}
    assert sorted(joining.groups) == [["a", "b"], ["c"]]


def test_without_an_adjudicator_nominated_pairs_stay_apart():
    joining = join([PIKE, JON], [(PIKE, JON, "variant")], None, SHOWN, "m")
    assert joining.groups == [["a"], ["b"]] and joining.decisions[0].action == "skipped"


def test_a_unit_is_named_by_its_fullest_name():
    assert display_name(unit("x", "J. Pike", "Jonathan Pike", "Jon Pike")) == "Jonathan Pike"


def test_the_identity_prompt_speaks_no_corpus_language():
    text = IDENTITY_PROMPT + json.dumps(SameIndividual.model_json_schema())
    banned = (
        "product",
        "review",
        "vehicle",
        "complaint",
        "recall",
        "drawer",
        "defect",
        "pump",
        "staff",
        "pike",
    )
    words = set(text.lower().replace('"', " ").split())
    assert not [w for w in banned if w in words or f"{w}s" in words]
    assert quoted_four_grams(text) == []
