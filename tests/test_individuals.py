"""One individual across documents (R75 part b2), pure: name variants, the nominated pairs, the code check of
an adjudication's quotes, and joining (a "same" without verified quotes refused, two records never one, the
same name alone never enough), plus the adjudication prompt's corpus-language guard. No Neo4j."""

import json

import pytest

from kgbuilder.resolution.individuals import (
    IDENTITY_PROMPT,
    SameIndividual,
    Unit,
    display_name,
    join,
    nominate,
    verified,
)
from kgbuilder.resolution.records import RecordCandidate, match_record
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


def test_pairs_are_nominated_by_variant_or_spelling_only_within_a_type():
    lin = unit("d", "Ada Lin").model_copy(update={"type": "Visitor"})
    pairs = {(a.id, b.id, signal) for a, b, signal in nominate([PIKE, JON, JUDITH, lin], 90, set())}
    assert pairs == {("a", "b", "variant")}  # Judith and Jonathan are no variants; Ada is another type


def test_a_quote_counts_only_in_its_own_sides_chunks_and_naming_that_side():
    good = SameIndividual(
        same=True,
        quote_a="Jonathan Pike leads the Soil Ecology group.",
        quote_b="Jon Pike (chair) of Soil Ecology",
    )
    assert verified(good, PIKE, JON, TEXTS)
    swapped = good.model_copy(update={"quote_a": good.quote_b, "quote_b": good.quote_a})
    assert not verified(swapped, PIKE, JON, TEXTS)  # each quote from the other side's document
    invented = good.model_copy(update={"quote_b": "Jon Pike is the group leader."})
    assert not verified(invented, PIKE, JON, TEXTS)  # not in B's text
    unnamed = good.model_copy(update={"quote_b": "opened the meeting"})
    assert not verified(unnamed, PIKE, JON, TEXTS)  # in B's text, but it does not name B


def test_a_join_needs_a_yes_with_verified_quotes_and_never_puts_two_records_together():
    other = unit("e", "J. Pike", record="Staff:S-999")
    replies = {
        frozenset("ab"): SameIndividual(
            same=True, quote_a="Jonathan Pike leads the Soil Ecology group.", quote_b="Jon Pike (chair)"
        ),
        frozenset("bc"): SameIndividual(same=True, quote_a="Jon Pike (chair)", quote_b="made up"),
        frozenset("be"): SameIndividual(same=True, quote_a="Jon Pike (chair)", quote_b="J. Pike"),
    }
    asked: list[frozenset] = []

    def adjudicate(a: Unit, b: Unit) -> SameIndividual:
        asked.append(frozenset((a.id, b.id)))
        return replies.get(frozenset((a.id, b.id)), SameIndividual(same=False))

    pairs = [
        (PIKE, JON, "variant"),
        (JON, JUDITH, "variant"),
        (JON, other, "variant"),
        (PIKE, other, "variant"),
    ]
    texts = {**TEXTS, "e": ["Talk by J. Pike."]}
    joining = join([PIKE, JON, JUDITH, other], pairs, adjudicate, texts, "judge-model")
    actions = {(d.a, d.b): d.action for d in joining.decisions}
    assert actions == {
        ("a", "b"): "joined",
        ("b", "c"): "quote_not_verified",  # a yes whose quote is not in Judith's text
        ("b", "e"): "different_records",  # verified, but the group already holds another record
        ("a", "e"): "different_records",  # two records: never asked
    }
    assert frozenset("ae") not in asked
    assert sorted(joining.groups) == [["a", "b"], ["c"], ["e"]]
    [joined] = [d for d in joining.decisions if d.action == "joined"]
    assert joined.by == "judge-model" and joined.evidence.startswith("A: Jonathan Pike leads")


def test_without_an_adjudicator_nominated_pairs_stay_apart():
    joining = join([PIKE, JON], [(PIKE, JON, "variant")], None, TEXTS, "m")
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
