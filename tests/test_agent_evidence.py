"""The answer-time evidence ledger of the variant `agent_evidence` (R193, agent/evidence.py, loop.py) with a
scripted model and fake stores, without Neo4j or a network: every passage the agent read is judged whole
(the store's full text, not a tool's excerpt) and none is cut at a cap; a yes stands only with a quote
verbatim in its passage, and its thing only when the passage or its document's name writes it; a row is
covered when the answer cites its passage or names its thing as whole words; the rows left out are sent back
once, each passage whole, with the tools closed, and the reply is the answer; a number answer, an empty
answer and a covered set are not sent back; the review also holds an answer given at a cap; the trace and
the metrics count it; the variant is registered with its prompts and params; and what the model reads names
no evaluated dataset."""

import re

from pydantic import BaseModel

from kgbuilder.agent.evidence import (
    PROMPT,
    SEND_BACK,
    EvidenceLedger,
    EvidenceReply,
    EvidenceRow,
    EvidenceVerdict,
    covered,
)
from kgbuilder.agent.loop import AgentLimits, AgentLoop
from kgbuilder.agent.prompt import AgentCitation, AgentStep
from kgbuilder.agent.results import ChunksResult, ChunkText
from kgbuilder.agent.semantic_filter import FilterOptions
from kgbuilder.agent.sql_tool import QuerySql
from kgbuilder.agent.structured_tools import QueryTables
from kgbuilder.config import Settings
from kgbuilder.pipeline.qa_stages import _agent_metrics
from kgbuilder.pipeline.qa_systems import AGENT_VARIANTS, system_params
from kgbuilder.query.answers import AgentTrace, SystemAnswer

from .fakes import ScriptedLLM
from .test_agent_tools import DATASET_WORDS
from .test_query import FakeStore, chunk

# whole passages in the store; the fake word search shows only their first sentence, as find_passages does
TEXTS = {
    "kettles.md#0": "## Meridian Kettle\nIt boils in two minutes. The handle stays cool.",
    "kettles.md#1": "## Solstice Kettle\nThe water was hot almost at once. The lid sticks.",
    "kettles.md#2": "## Aurora Kettle\nIt takes a long while to heat. Pretty colour.",
}
LIMITS = AgentLimits(turns=6, calls=3, prompt_chars=100_000, timeout_s=60)


def store() -> FakeStore:
    return FakeStore(chunks=[chunk(i, None, t, context="Kettle notes") for i, t in TEXTS.items()])


class IdArguments(BaseModel):
    ids: list[str]


class Excerpts:
    """A word search showing each passage asked for by its first sentence only."""

    name = "search"
    description = "Find passages."
    arguments = IdArguments

    def run(self, arguments):
        shown = [
            ChunkText(chunk_id=i, context="Kettle notes", text=TEXTS[i].split(". ")[0]) for i in arguments.ids
        ]
        return ChunksResult(chunks=shown, total=len(shown))


def says(*kept: tuple[str, str, str]):
    """A judge saying yes, with the quote and thing given, for the passages holding a kept marker; kept is
    (marker in the passage, quote, thing)."""

    def reply(prompt: str) -> EvidenceReply:
        passages = re.findall(r'<passage id="(P\d+)" document="[^"]*">\n(.*?)\n</passage>', prompt, re.S)
        verdicts = []
        for pid, text in passages:
            hit = next(((q, t) for marker, q, t in kept if marker in text), None)
            verdicts.append(EvidenceVerdict(id=pid, states=hit is not None, quote=hit[0] if hit else "",
                                            thing=hit[1] if hit else ""))  # fmt: skip
        return EvidenceReply(verdicts=verdicts)

    return reply


class Model:
    """The agent's steps in order (the last again once they run out) and the judge's replies; keeps every
    prompt with the schema it was asked for."""

    def __init__(self, steps: list[AgentStep], judge):
        self._steps = steps
        self._judge = judge
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, prompt, schema):
        self.prompts.append((schema.__name__, prompt))
        if schema is EvidenceReply:
            return self._judge(prompt)
        return self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]


def call(*ids: str) -> AgentStep:
    return AgentStep(action="call", tool="search", arguments=f'{{"ids": {list(ids)!r}}}'.replace("'", '"'))


def things(*names: str, cited=(), asks="things") -> AgentStep:
    return AgentStep(action="answer", asks=asks, entities=list(names),
                     citations=[AgentCitation(id=i) for i in cited])  # fmt: skip


def run_loop(model: Model, limits: AgentLimits = LIMITS, batch: int = 15):
    llm = ScriptedLLM(model)
    options = FilterOptions(model="judge-model", temperature=0.0, thinking="low", batch=batch)
    ledger = EvidenceLedger(llm, options, store())
    loop = AgentLoop([Excerpts()], llm, "m", 0.0, limits, evidence=ledger)
    return loop.run("Which kettles heat quickly?"), llm


FAST = [
    ("boils in two minutes", "It boils in two minutes.", "Meridian Kettle"),
    ("hot almost at once", "The water was hot almost at once.", "Solstice Kettle"),
]


# --- what the judge reads -----------------------------------------------------------------------------


def test_every_passage_read_is_judged_whole_from_the_store_not_as_the_excerpt_shown():
    model = Model([call(*TEXTS), things("Meridian Kettle", "Solstice Kettle")], says(*FAST))
    run, llm = run_loop(model)
    judged = [p for s, p in model.prompts if s == "EvidenceReply"]
    assert len(judged) == 1 and "<question>Which kettles heat quickly?</question>" in judged[0]
    # the excerpt the tool showed stops at the first sentence; the judge reads every sentence
    assert all(text in judged[0] for text in TEXTS.values())
    assert ("EvidenceReply", "judge-model") in llm.calls and "low" in llm.thinking
    assert (run.trace.evidence_read, run.trace.evidence_rows, run.trace.evidence_sent) == (3, 2, [])


def test_no_passage_is_cut_at_a_cap_every_one_is_judged_in_batches():
    model = Model([call(*TEXTS), things("Meridian Kettle", "Solstice Kettle")], says(*FAST))
    run, _ = run_loop(model, batch=2)
    judged = [p for s, p in model.prompts if s == "EvidenceReply"]
    assert len(judged) == 2 and 'id="P1"' in judged[1]  # each batch numbers its passages from P1
    assert run.trace.evidence_read == 3


def test_a_yes_stands_only_with_a_verbatim_quote_and_its_thing_only_when_written():
    judge = says(
        ("boils in two minutes", "It boils in two minutes.", "Meridian Kettle"),
        ("hot almost at once", "Hot water instantly.", "Solstice Kettle"),  # quote not in the passage
        ("long while", "It takes a long while to heat.", "Zenith Kettle"),  # thing not written
    )
    ledger = EvidenceLedger(ScriptedLLM(lambda p, s: judge(p)), FilterOptions(model="j", temperature=0.0,
                            thinking="low", batch=15), store())  # fmt: skip
    review = ledger.review("Q", things(), list(TEXTS), [])
    assert [(r.chunk_id, r.thing) for r in review.rows] == [("kettles.md#0", "Meridian Kettle"),
                                                           ("kettles.md#2", "")]  # fmt: skip
    assert review.unverified == 1 and review.rows[0].text == TEXTS["kettles.md#0"]


def test_an_unknown_id_is_ignored_and_a_passage_given_no_verdict_is_counted():
    reply = EvidenceReply(verdicts=[EvidenceVerdict(id="P9", states=True, quote="x", thing="y"),
                                    EvidenceVerdict(id=" P1 ", states=False)])  # fmt: skip
    ledger = EvidenceLedger(ScriptedLLM(lambda p, s: reply), FilterOptions(model="j", temperature=0.0,
                            thinking="low", batch=15), store())  # fmt: skip
    review = ledger.review("Q", things(), list(TEXTS), [])
    assert (review.rows, review.unanswered) == ([], 2)


# --- what the answer covers ---------------------------------------------------------------------------


def row(thing: str, chunk_id: str = "kettles.md#0") -> EvidenceRow:
    return EvidenceRow(chunk_id=chunk_id, context="Kettle notes", text="t", thing=thing, quote="t")


def test_a_row_is_covered_by_its_citation_or_by_its_thing_named_as_whole_words():
    assert covered(row("Meridian Kettle"), {"kettles.md#0"}, [])
    assert covered(row("Meridian Kettle"), set(), ["Meridian"])  # a shorter name within the thing's
    assert covered(row("Meridian"), set(), ["the Meridian Kettle"])  # and the other way round
    assert not covered(row("Kit"), set(), ["it"]) and not covered(row("Meridian Kettle"), set(), ["Merid"])
    assert not covered(row(""), set(), ["Meridian Kettle"])  # no checked thing: only its citation covers it


# --- the send-back ------------------------------------------------------------------------------------


def test_a_left_out_member_is_sent_back_once_whole_with_the_tools_closed_and_the_reply_is_the_answer():
    model = Model(
        [call(*TEXTS), things("Meridian Kettle"), things("Meridian Kettle", "Solstice Kettle")], says(*FAST)
    )
    run, _ = run_loop(model)
    steps = [p for s, p in model.prompts if s != "EvidenceReply"]
    sent = steps[-1]
    assert "No tool can be called any more" in sent  # the closing prompt: the tools are closed
    assert "your answer neither names that thing nor cites the passage" in sent
    tag = '<passage id="kettles.md#1" document="Kettle notes" thing="Solstice Kettle">'
    assert f"{tag}\n{TEXTS['kettles.md#1']}\n</passage>" in sent  # the passage whole, not its excerpt
    assert "kettles.md#0" not in sent.split("<passages>")[-1]  # a covered row is not sent back
    assert run.answer.entities == ["Meridian Kettle", "Solstice Kettle"]
    assert (run.trace.evidence_sent, run.trace.turns, run.trace.stopped) == (["kettles.md#1"], 2, "answer")
    assert len([p for s, p in model.prompts if s == "EvidenceReply"]) == 1  # reviewed once


def test_a_covered_set_a_number_answer_and_an_empty_answer_are_not_sent_back():
    covered_set = Model([call(*TEXTS), things("Meridian Kettle", cited=["kettles.md#1"])], says(*FAST))
    run, _ = run_loop(covered_set)
    assert (run.trace.evidence_rows, run.trace.evidence_sent) == (2, [])
    number = Model([call(*TEXTS), AgentStep(action="answer", asks="number", number=2)], says(*FAST))
    run, _ = run_loop(number)
    assert run.trace.evidence_read == 0 and all(s != "EvidenceReply" for s, _ in number.prompts)
    empty = Model([call(*TEXTS), AgentStep(action="answer", asks="things")], says(*FAST))
    run, _ = run_loop(empty)  # sent back once as empty (R138), then it stands, naming no set to complete
    assert (run.trace.emptied, run.trace.evidence_read) == (True, 0)


def test_an_answer_of_none_is_a_set_answer_and_is_reviewed():
    model = Model([call(*TEXTS), things(), things("Meridian Kettle", "Solstice Kettle")], says(*FAST))
    run, _ = run_loop(model)
    assert run.trace.evidence_sent == ["kettles.md#0", "kettles.md#1"]
    assert run.answer.entities == ["Meridian Kettle", "Solstice Kettle"]


def test_an_answer_given_at_a_cap_is_reviewed_too():
    """HS01 (R192): the call cap closed the tools and the closing answer dropped what had been read."""
    limits = AgentLimits(turns=6, calls=1, prompt_chars=100_000, timeout_s=60)
    model = Model(
        [call(*TEXTS), things("Meridian Kettle"), things("Meridian Kettle", "Solstice Kettle")], says(*FAST)
    )
    run, _ = run_loop(model, limits)
    assert (run.trace.stopped, run.trace.evidence_sent) == ("calls", ["kettles.md#1"])
    assert run.answer.entities == ["Meridian Kettle", "Solstice Kettle"]


def test_the_metrics_count_the_review():
    trace = AgentTrace(calls=[], turns=2, stopped="answer", prompt_chars=10, evidence_read=3, evidence_rows=2,
                       evidence_sent=["kettles.md#1"])  # fmt: skip
    plain = AgentTrace(calls=[], turns=1, stopped="answer", prompt_chars=10)
    answers = [
        SystemAnswer(question_id=q, system="agent_evidence", agent=t) for q, t in (("a", trace), ("b", plain))
    ]
    metrics = _agent_metrics(answers)
    assert (metrics["agent_evidence_read"], metrics["agent_evidence_rows"]) == (3, 2)
    assert (metrics["agent_answers_evidenced"], metrics["agent_evidence_sent"]) == (1, 1)


# --- registration and wording -------------------------------------------------------------------------


def test_the_variant_has_the_agents_tools_logs_its_judge_and_versions_its_prompts():
    variants = {v.name: v for v in AGENT_VARIANTS}
    evidence, agent = variants["agent_evidence"], variants["agent"]
    assert evidence.tool_types == agent.tool_types and evidence.wider == agent.wider
    assert set(evidence.prompts) == {"agent_evidence", "agent_evidence_reply"}
    params = system_params(Settings(_env_file=None), "agent_evidence")
    assert {"agent_filter_batch", "agent_filter_thinking", "agent_evidence_prompt_version"} <= set(params)
    assert "agent_evidence_prompt_version" not in system_params(Settings(_env_file=None), "agent")


def test_the_combined_variant_is_the_sql_agent_with_the_ledger():
    variants = {v.name: v for v in AGENT_VARIANTS}
    best, sql, ledger = variants["agent_best"], variants["agent_sql"], variants["agent_evidence"]
    assert best.tool_types == sql.tool_types
    assert QuerySql in best.tool_types and QueryTables not in best.tool_types
    assert best.evidence is ledger.evidence and best.prompts == ledger.prompts and best.wider == ledger.wider
    settings = Settings(_env_file=None)
    params = {n: system_params(settings, n) for n in ("agent", "agent_sql", "agent_evidence", "agent_best")}
    # the tool list is versioned as the SQL agent's and the ledger's prompt as the ledger agent's, so MLflow
    # tells the arms apart by these params
    tools = "agent_tools_prompt_version"
    assert params["agent_best"][tools] == params["agent_sql"][tools] != params["agent"][tools]
    ledger_key = "agent_evidence_prompt_version"
    assert params["agent_best"][ledger_key] == params["agent_evidence"][ledger_key]
    assert ledger_key not in params["agent_sql"]


def test_what_the_model_reads_of_the_ledger_names_no_evaluated_dataset():
    texts = [PROMPT, SEND_BACK, *(f.description or "" for f in EvidenceVerdict.model_fields.values())]
    texts += [f.description or "" for f in EvidenceReply.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
