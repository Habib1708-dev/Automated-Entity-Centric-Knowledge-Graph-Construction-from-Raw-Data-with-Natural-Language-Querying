"""Deterministic node cards (R118, hybrid/cards.py) from hand-made evidence, without Neo4j: the layout line by
line, a hub's "(+k more)", the qualifier tags and a disagreement's counts, truncation (claims first, then the
smallest relations, then a cut at a space), determinism and the evidence hash on every card, a card grounded
by construction (each of its words is the template's or the evidence's), a template free of corpus words,
the claim sentence, and the representations registry."""

import pytest

from kgbuilder.core.errors import ConfigurationError
from kgbuilder.hybrid import (
    REPRESENTATIONS,
    EvidenceClaim,
    Neighbours,
    NodeEvidence,
    claim_text,
    evidence_hash,
    predicate_words,
    representation,
)
from kgbuilder.hybrid.cards import CARD_TEMPLATE, TemplateCards
from kgbuilder.llm.base import prompt_version

from .evaluation_corpora import quoted_four_grams, words


def claim(sentence: str, stated: int = 1, denied: int = 0, modality: str = "actual", condition: str = ""):
    return EvidenceClaim(
        id=f"o-{sentence[:4]}", predicate="HAS_CONDITION", sentence=sentence, stated=stated, denied=denied,
        modality=modality,
        hedge="may" if modality == "possible" else "", condition=condition, chunk_id="notes.md#0",
    )  # fmt: skip


PRESS = NodeEvidence(
    ref="Press:P1",
    kind="record",
    label="Press",
    title="Quill Press",
    aliases=["the press"],
    properties={"press_id": "P1", "year": "2019"},
    relations=[
        Neighbours(type="CONCERNS", outgoing=False, label="Ticket", count=1, names=["T-1"]),
        Neighbours(type="MADE_BY", outgoing=True, label="Maker", count=1, names=["Norcast"]),
        Neighbours(type="PART_OF", outgoing=False, label="Part", count=7, names=["Gear", "Pin", "Spindle"]),
    ],
    claims=[
        claim("Spindle (Part) has condition wobbles (Condition)", stated=2, denied=1),
        claim("Gear (Part) has condition grinds (Condition)", modality="conditional", condition="when cold"),
        claim("Pin (Part) has condition rattles (Condition)", stated=0, denied=1),
        claim("Quill Press (Press) has condition hums (Condition)", modality="possible"),
    ],
    claims_total=6,
)


def test_a_card_reads_title_names_properties_relations_then_claims_with_their_tags():
    card = TemplateCards(max_chars=1500).render([PRESS])[0]
    assert card.text.splitlines() == [
        "Quill Press (Press)",
        "Also called: the press",
        "press_id: P1",
        "year: 2019",
        "CONCERNS <- Ticket (1): T-1",
        "MADE_BY -> Maker (1): Norcast",
        "PART_OF <- Part (7): Gear, Pin, Spindle (+4 more)",  # a hub reads as a hub
        "Claims (4 of 6):",
        "- Spindle (Part) has condition wobbles (Condition) [stated 2, denied 1]",
        "- Gear (Part) has condition grinds (Condition) [conditional: when cold]",
        "- Pin (Part) has condition rattles (Condition) [denied]",
        "- Quill Press (Press) has condition hums (Condition) [hedged]",
    ]
    assert (card.ref, card.truncated, card.evidence_hash) == ("Press:P1", False, evidence_hash(PRESS))


def test_a_conditional_claim_without_its_words_and_a_node_with_nothing_but_a_name():
    bare = NodeEvidence(
        ref="k-hum", kind="concept", label="Condition", title="hums", aliases=[], properties={},
        relations=[], claims=[claim("Gear (Part) hums (Condition)", modality="conditional")], claims_total=1,
    )  # fmt: skip
    assert (
        TemplateCards(1500).render([bare])[0].text.splitlines()[-1]
        == "- Gear (Part) hums (Condition) [conditional]"
    )
    alone = bare.model_copy(update={"claims": [], "claims_total": 0})
    assert TemplateCards(1500).render([alone])[0].text == "hums (Condition)"


def test_the_length_cap_drops_claims_from_the_last_then_the_smallest_relations():
    full = TemplateCards(1500).render([PRESS])[0].text
    lines = full.splitlines()
    # one claim too many: the last (least supported) goes, and the header counts what is shown
    cut = TemplateCards(len(full) - 1).render([PRESS])[0]
    assert cut.truncated and "hums" not in cut.text and "Claims (3 of 6):" in cut.text
    # no claim left: then the smallest relation goes first (of equal ones the last), the hub stays
    no_claims = "\n".join(lines[:7])
    smaller = TemplateCards(len(no_claims) - 1).render([PRESS])[0].text
    assert "Claims" not in smaller and "MADE_BY" not in smaller and "CONCERNS" in smaller
    assert "PART_OF <- Part (7)" in smaller
    # a head longer than the cap is cut at a space, never inside a word
    head = TemplateCards(15).render([PRESS])[0].text
    assert head == "Quill Press" and len(head) <= 15


def test_cards_are_deterministic_and_carry_the_hash_of_their_evidence():
    cards = TemplateCards(1500)
    assert cards.render([PRESS]) == cards.render([PRESS.model_copy(deep=True)])
    changed = PRESS.model_copy(update={"claims_total": 7})
    assert evidence_hash(changed) != evidence_hash(PRESS) and len(evidence_hash(PRESS)) == 16
    assert cards.render([changed])[0].evidence_hash == evidence_hash(changed)


def test_every_word_of_a_card_is_the_templates_or_the_evidences():
    """Grounded by construction: code adds the template's words and the counts it computes from the evidence
    (how many names a hub leaves out, how many claims are shown), nothing else."""
    text = TemplateCards(1500).render([PRESS])[0].text
    computed = {str(n.count - len(n.names)) for n in PRESS.relations} | {str(len(PRESS.claims))}
    allowed = set(words(CARD_TEMPLATE)) | set(words(PRESS.model_dump_json())) | computed
    assert set(words(text)) - allowed == set()


def test_the_template_quotes_no_corpus():
    assert quoted_four_grams(CARD_TEMPLATE) == []


def test_a_claim_sentence_names_both_ends_with_their_types_and_the_predicate_in_words():
    assert predicate_words("HAS_CONDITION") == "has condition" and predicate_words("MADE_BY") == "made by"
    assert claim_text("Spindle", "Part", "HAS_CONDITION", "wobbles", "Condition") == (
        "Spindle (Part) has condition wobbles (Condition)"
    )


def test_a_representation_is_chosen_by_name_and_versioned_by_its_template():
    assert set(REPRESENTATIONS) == {"template"}
    cards = representation("template", 900)
    assert cards.name == "template" and cards.version == prompt_version(CARD_TEMPLATE)
    with pytest.raises(ConfigurationError, match="choose from template"):
        representation("summary", 900)
