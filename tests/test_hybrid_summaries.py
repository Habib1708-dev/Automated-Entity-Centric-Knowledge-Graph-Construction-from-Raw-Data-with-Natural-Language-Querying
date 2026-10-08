"""LLM node summaries (R123, hybrid/summaries.py, representation B) with a scripted model, without Neo4j or a
network: the numbered facts in the template's formats with their tags, each rule of the code grounding check,
a grounded reply used as it is with the evidence hash of A's card, a refused reply retried once with its
issues, a node refused twice given its template card marked `fallback`, the order kept while nodes are asked
in parallel, no rendering without a model, the cache answering a second render, a version that changes with
every knob, and a prompt that quotes no evaluated corpus."""

import json
import re

import pytest

from kgbuilder.core.errors import LLMUnavailableError
from kgbuilder.hybrid import Neighbours, NodeEvidence, evidence_hash
from kgbuilder.hybrid.cards import TemplateCards
from kgbuilder.hybrid.summaries import (
    FACT,
    PROMPT,
    NodeSummary,
    SummaryCards,
    SummaryOptions,
    facts,
    grounding_issues,
    summary_prompt,
    summary_version,
)
from kgbuilder.llm.base import prompt_version
from kgbuilder.llm.cache import CachedLLM

from .evaluation_corpora import quoted_four_grams
from .fakes import ScriptedLLM
from .test_hybrid_cards import PRESS

OPTIONS = SummaryOptions(model="writer", temperature=0.0, thinking="low", max_chars=400)
GOOD = (
    "Quill Press is a press from 2019, made by Norcast. 7 parts are part of it, among them Gear, Pin and "
    "Spindle. 2 sources state that its Spindle wobbles and 1 denies it. Its Gear grinds only when cold. A "
    "rattling Pin is denied. It may hum."
)
# a concept: no properties, no claims, one relation; its label is one word of two capitals
HUM = NodeEvidence(
    ref="k-hum", kind="concept", label="NoiseKind", title="hums", aliases=[], properties={},
    relations=[
        Neighbours(type="HAS_CONDITION", outgoing=False, label="Press", count=1, names=["Quill Press"])
    ],
    claims=[], claims_total=0,
)  # fmt: skip


def summary(text: str, used: list[str] | None = None) -> NodeSummary:
    return NodeSummary(text=text, facts_used=used if used is not None else ["F2", "F3"])


def cards(llm) -> SummaryCards:
    return SummaryCards(OPTIONS, llm, TemplateCards(1500))


def issues(text: str, used: list[str] | None = None, e: NodeEvidence = PRESS, max_chars: int = 400):
    return grounding_issues(summary(text, used), e, facts(e), max_chars)


def test_the_facts_are_numbered_in_the_templates_formats_with_their_tags():
    prompt = summary_prompt(PRESS, facts(PRESS), 400)
    assert "The node: Quill Press (Press)" in prompt and "Write at most 400 characters" in prompt
    numbered = [line for line in prompt.splitlines() if re.match(r"F\d+\. ", line)][:10]  # then the example
    assert numbered == [
        "F1. Also called: the press",
        "F2. press_id: P1",
        "F3. year: 2019",
        "F4. CONCERNS <- Ticket (1): T-1",
        "F5. MADE_BY -> Maker (1): Norcast",
        "F6. PART_OF <- Part (7): Gear, Pin, Spindle (+4 more)",
        "F7. Spindle (Part) has condition wobbles (Condition) [stated 2, denied 1]",
        "F8. Gear (Part) has condition grinds (Condition) [conditional: when cold]",
        "F9. Pin (Part) has condition rattles (Condition) [denied]",
        "F10. Quill Press (Press) has condition hums (Condition) [hedged]",
    ]
    bare = HUM.model_copy(update={"relations": []})
    assert "one numbered fact per line:\n(none)\n" in summary_prompt(bare, facts(bare), 400)


def test_a_grounded_summary_passes_the_check():
    assert issues(GOOD, ["F3", "F5", "F6", "F7", "F8", "F9", "F10"]) == []
    # an alias names the node too; a possessive and a capitalised first word are ordinary
    assert issues("The press's maker is Norcast.", ["F1", "F5"]) == []
    # a label written in words is found in the evidence ("Noise Kind" for NoiseKind)
    assert issues("hums is a Noise Kind that Quill Press has.", [], e=HUM) == []


@pytest.mark.parametrize(
    "text, used, expected",
    [
        ("", None, "the text is empty"),
        ("A press from 2019, made by Norcast.", None, 'does not name the node: write "Quill Press"'),
        (GOOD, ["F3", "F11", "F0"], "facts_used cites F0, F11, which is not a fact id"),
        ("Quill Press dates from 2021 and has 12 parts.", None, "the numbers 12, 2021 are in no fact"),
        ("Quill Press is made by Norcast in Sheffield (F5).", None, "the words F5, Sheffield are in no fact"),
        (GOOD + " " + "It is sturdy. " * 30, None, "characters; write at most 400"),
    ],
)
def test_each_grounding_rule_refuses_its_failure(text, used, expected):
    found = issues(text, used)
    assert len(found) == 1 and expected in found[0]


def test_a_grounded_reply_is_the_card_with_its_cited_facts_and_the_evidence_hash_of_as_card():
    llm = ScriptedLLM(lambda prompt, schema: summary(GOOD, ["F3", "F5"]))
    card = cards(llm).render([PRESS])[0]
    template = TemplateCards(1500).render([PRESS])[0]
    assert (card.text, card.facts_used, card.fallback, card.rejected) == (GOOD, ["F3", "F5"], False, [])
    assert card.evidence_hash == template.evidence_hash == evidence_hash(PRESS) and not card.truncated
    assert llm.calls == [("NodeSummary", "writer")] and llm.thinking == ["low"]


def test_a_refused_reply_is_retried_once_with_its_issues_listed():
    prompts = []

    def script(prompt, schema):
        prompts.append(prompt)
        return summary("It is made by Norcast in Sheffield.") if len(prompts) == 1 else summary(GOOD)

    card = cards(ScriptedLLM(script)).render([PRESS])[0]
    assert card.text == GOOD and card.fallback is False and len(prompts) == 2
    assert prompts[1].startswith(prompts[0]) and "<previous_answer>" in prompts[1]
    assert 'does not name the node: write "Quill Press"' in prompts[1] and "Sheffield" in prompts[1]
    [rejected] = card.rejected
    assert rejected.text == "It is made by Norcast in Sheffield." and len(rejected.issues) == 2


def test_a_node_refused_twice_gets_its_template_card_marked_fallback():
    llm = ScriptedLLM(lambda prompt, schema: summary("A machine from 1850."))
    card = cards(llm).render([PRESS])[0]
    assert card.text == TemplateCards(1500).render([PRESS])[0].text and card.fallback is True
    assert card.facts_used == [] and len(card.rejected) == 2 and len(llm.calls) == 2
    assert card.evidence_hash == evidence_hash(PRESS)


def test_the_cards_come_back_in_the_order_of_the_evidence():
    nodes = [PRESS.model_copy(update={"ref": f"Press:P{i}", "title": f"Press {i}"}) for i in range(20)]

    def script(prompt, schema):
        title = re.search(r"The node: (.+) \(", prompt).group(1)  # the node's own line comes first
        return summary(f"{title} is a press.", [])

    rendered = cards(ScriptedLLM(script)).render(nodes)
    assert [c.ref for c in rendered] == [n.ref for n in nodes]
    assert all(c.text == f"{n.title} is a press." for c, n in zip(rendered, nodes, strict=True))


def test_without_a_model_the_version_is_known_but_nothing_is_rendered():
    no_model = cards(None)
    assert no_model.version == cards(ScriptedLLM(lambda p, s: summary(GOOD))).version
    assert no_model.prompts() == {"summary": PROMPT}
    assert no_model.params() == {
        "summary_model": "writer", "summary_temperature": 0.0, "summary_thinking": "low",
        "summary_max_chars": 400, "summary_prompt_version": prompt_version(PROMPT),
    }  # fmt: skip
    with pytest.raises(LLMUnavailableError, match="kg index --cards summary"):
        no_model.render([PRESS])


def test_a_second_render_is_answered_by_the_cache(tmp_path):
    inner = ScriptedLLM(lambda prompt, schema: summary(GOOD))
    hits = []
    cached = CachedLLM(inner, tmp_path, hits.append)
    first = cards(cached).render([PRESS])
    assert cards(cached).render([PRESS]) == first and len(inner.calls) == 1 and len(hits) == 1


@pytest.mark.parametrize(
    "change",
    [{"model": "other"}, {"temperature": 0.5}, {"thinking": "high"}, {"max_chars": 401}],
)
def test_the_version_changes_with_every_knob(change):
    base = summary_version(OPTIONS, TemplateCards.version)
    assert summary_version(OPTIONS.model_copy(update=change), TemplateCards.version) != base


def test_the_version_hashes_the_prompt_the_reply_schema_the_options_and_the_fallback_cards():
    material = [PROMPT, FACT, NodeSummary.model_json_schema(), OPTIONS.model_dump(mode="json"), "v1"]
    assert summary_version(OPTIONS, "v1") == prompt_version(json.dumps(material, sort_keys=True))
    assert summary_version(OPTIONS, "v1") != summary_version(OPTIONS, "v2")


def test_the_prompt_and_its_field_descriptions_quote_no_corpus():
    descriptions = " ".join(f.description or "" for f in NodeSummary.model_fields.values())
    assert quoted_four_grams(PROMPT) == [] and quoted_four_grams(descriptions) == []
