"""The semantic filter of the variant `agent_semfilter` (R184, agent/semantic_filter.py) with a scripted model
and fake stores, without Neo4j or a network: the candidates are the passages nearest the statement, those
its words score highest and every passage holding a given word, fused and cut at the cap with the cut said;
the model reads them in batches under short ids at the filter's own thinking level; a yes stands only with
a quote verbatim in its passage, an unknown id is ignored, a repeated id's first verdict counts and a
passage given no verdict is counted; the kept passages are shown as their quotes, counted with their
documents, cut at the display cap, citable, and their quotes faithful; what the model reads names no
evaluated dataset."""

import re

from kgbuilder.agent.citations import Ledger
from kgbuilder.agent.results import ChunkText
from kgbuilder.agent.semantic_filter import (
    PROMPT,
    FilterArguments,
    FilterOptions,
    FilterPassages,
    FilterReply,
    PassageJudge,
    PassageVerdict,
)
from kgbuilder.core.text import norm

from .fakes import ScriptedLLM
from .test_agent_text_reading import FakeText
from .test_agent_tools import DATASET_WORDS, SplitUnitStore
from .test_query import FakeStore, FixedEmbedder, chunk

TEXTS = {
    "a.md#0": "The lamp hums at night. It glows softly.",
    "a.md#1": "Setup took an hour.",
    "b.md#0": "A faint buzz comes from the base whenever it is on.",
    "c.md#0": "Silent, and the light glowing warm.",
}


def stored(chunk_id: str):
    return chunk(chunk_id, None, TEXTS[chunk_id], context=chunk_id.split("#")[0])


def says(*kept: tuple[str, str]):
    """A model saying yes, with the quote given, for the passages whose text holds a kept marker, no for the
    rest: kept is (marker in the passage, quote)."""

    def reply(prompt: str, schema) -> FilterReply:
        passages = re.findall(r'<passage id="(P\d+)" document="[^"]*">\n(.*?)\n</passage>', prompt, re.S)
        verdicts = []
        for pid, text in passages:
            quote = next((q for marker, q in kept if marker in text), "")
            verdicts.append(PassageVerdict(id=pid, states=bool(quote), quote=quote))
        return FilterReply(verdicts=verdicts)

    return reply


def make(llm, *, nearest=(), lexical=(), documents=None, candidates=10, batch=15, limit=30):
    store = FakeStore(chunks=[stored(i) for i in TEXTS], nearest=list(nearest))
    text = FakeText(documents or {})
    options = FilterOptions(model="filter-model", temperature=0.0, thinking="low", batch=batch)
    tool = FilterPassages(
        PassageJudge(llm, options), store, SplitUnitStore(chunks=list(lexical)), text, FixedEmbedder(), 60,
        candidates, limit,
    )  # fmt: skip
    return tool, text


def test_the_candidates_are_the_nearest_the_best_by_words_and_every_passage_holding_a_given_word():
    llm = ScriptedLLM(says())
    documents = {"c.md": [ChunkText(chunk_id="c.md#0", context="c.md", text=TEXTS["c.md#0"])]}
    tool, text = make(llm, nearest=["a.md#0"], lexical=["b.md#0"], documents=documents)
    result = tool.run(FilterArguments(statement="it makes a noise", words=["glow"]))
    assert result.considered == 3
    # the given word is searched in its forms, as find_passages searches it (R162): "glowing" is found
    assert text.asked[0][0] == "matching" and re.search(text.asked[0][1][0], "glowing")
    assert "every passage holding 'glow'" in result.note


class Recorded:
    """A model script that keeps every prompt it is shown."""

    def __init__(self, script):
        self._script = script
        self.prompts: list[str] = []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        return self._script(prompt, schema)


def test_a_cut_at_the_cap_keeps_the_best_of_every_list_and_says_how_many_were_not_read():
    script = Recorded(says())
    tool, _ = make(
        ScriptedLLM(script), nearest=["a.md#0", "a.md#1"], lexical=["b.md#0", "c.md#0"], candidates=2
    )
    result = tool.run(FilterArguments(statement="it makes a noise"))
    assert result.considered == 2
    assert TEXTS["a.md#0"] in script.prompts[0] and TEXTS["b.md#0"] in script.prompts[0]  # each list's head
    assert "2 more candidates were past the cap and not read." in result.note


def test_the_passages_are_read_in_batches_under_short_ids_at_the_filters_own_level():
    script = Recorded(says(("hums", "The lamp hums at night.")))
    llm = ScriptedLLM(script)
    tool, _ = make(llm, nearest=["a.md#0", "a.md#1", "b.md#0"], batch=2)
    tool.run(FilterArguments(statement="it makes a noise"))
    assert llm.calls == [("FilterReply", "filter-model")] * 2 and llm.thinking == ["low", "low"]
    assert 'id="P1" document="a.md"' in script.prompts[0] and 'id="P2"' in script.prompts[0]
    assert 'id="P1" document="b.md"' in script.prompts[1]  # each batch numbers its passages from P1
    assert "<statement>it makes a noise</statement>" in script.prompts[0]


def test_a_yes_stands_only_with_a_quote_verbatim_in_its_passage():
    llm = ScriptedLLM(says(("hums", "The lamp hums at night."), ("buzz", "A loud buzz.")))
    tool, _ = make(llm, nearest=["a.md#0", "b.md#0"])
    result = tool.run(FilterArguments(statement="it makes a noise"))
    assert [c.chunk_id for c in result.chunks] == ["a.md#0"]
    assert result.unverified == 1 and "1 whose quote is not in the passage" in result.note


def test_an_unknown_id_is_ignored_a_repeated_ids_first_verdict_counts_and_a_silent_passage_is_counted():
    def reply(prompt, schema):
        return FilterReply(
            verdicts=[
                PassageVerdict(id="P9", states=True, quote="hums"),
                PassageVerdict(id=" P1 ", states=True, quote="The lamp hums"),
                PassageVerdict(id="P1", states=False),
            ]
        )

    tool, _ = make(ScriptedLLM(reply), nearest=["a.md#0", "b.md#0"])
    result = tool.run(FilterArguments(statement="it makes a noise"))
    assert [c.chunk_id for c in result.chunks] == ["a.md#0"]
    assert "0 whose quote is not in the passage, 1 given no verdict" in result.note


def test_the_kept_passages_are_counted_with_their_documents_shown_as_quotes_and_cut_at_the_display_cap():
    llm = ScriptedLLM(says(("hums", "The lamp hums at night."), ("buzz", "A faint buzz comes from the base")))
    tool, _ = make(llm, nearest=["a.md#0", "b.md#0", "a.md#1"], limit=1)
    result = tool.run(FilterArguments(statement="it makes a noise"))
    assert (result.total, result.documents, result.document_count) == (2, ["a.md", "b.md"], 2)
    assert result.chunks == [ChunkText(chunk_id="a.md#0", context="a.md", text="The lamp hums at night.")]
    assert result.note.startswith("2 of 3 passages read state it") and "1 of the 2 are shown." in result.note


def test_a_kept_passage_is_citable_with_its_quote_and_its_count_supports_a_number_answer():
    llm = ScriptedLLM(says(("hums", "The lamp hums at night."), ("buzz", "A faint buzz comes from the base")))
    tool, _ = make(llm, nearest=["a.md#0", "b.md#0"])
    ledger = Ledger()
    ledger.add(tool.run(FilterArguments(statement="it makes a noise")))
    assert [c.chunk_id for c in ledger.read()] == ["a.md#0", "b.md#0"]
    assert norm("The lamp hums at night.") in norm(ledger.read()[0].text)  # the citation check's comparison
    assert ledger.support(None, 2, ["a.md#0"]).number_stated
    assert not ledger.support(None, 7, ["a.md#0"]).number_stated


def test_what_the_model_reads_of_the_filter_names_no_evaluated_dataset():
    fields = [*FilterArguments.model_fields.values(), *PassageVerdict.model_fields.values()]
    texts = [FilterPassages.description, PROMPT, *(f.description or "" for f in fields)]
    texts += [f.description or "" for f in FilterReply.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
    assert all(f.description for f in FilterArguments.model_fields.values())
