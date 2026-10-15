"""The working set of the variant `agent_workset` (R211, agent/workset.py, loop.py) with a scripted model and
fake stores, without Neo4j or a network: every passage read is shown whole (the store's text, not a tool's
excerpt) and every query run with its rows uncut, in the order first returned, cut at the limit with the
rest named; the agent's own model gives one verdict per item before its answer, with the tools closed; a
reply missing a verdict or quoting an included item wrongly is sent back once and the second reply stands;
the reply is the answer, code adds and removes nothing; which replies are reviewed; the agent's turns are
the agent's own prompts (so a paired run reuses the agent's cached turns); the trace and metrics; the
variant's registration; and what the model reads names no evaluated dataset."""

import re

from pydantic import BaseModel

from kgbuilder.agent.loop import AgentLimits, AgentLoop
from kgbuilder.agent.prompt import AgentCitation, AgentStep, step_model
from kgbuilder.agent.results import ChunksResult, ChunkText
from kgbuilder.agent.structured_tools import QueryResult
from kgbuilder.agent.workset import (
    LEFT_OUT,
    PROMPT,
    SEND_BACK,
    VERDICTS,
    ItemVerdict,
    Workset,
    reply_model,
)
from kgbuilder.config import Settings
from kgbuilder.pipeline.qa_stages import _agent_metrics
from kgbuilder.pipeline.qa_systems import AGENT_VARIANTS, system_params
from kgbuilder.query.answers import AgentTrace, QueryRecord, SystemAnswer, WorksetMark

from .fakes import ScriptedLLM
from .test_agent_tools import DATASET_WORDS
from .test_query import FakeStore, chunk

# whole passages in the store; the fake search shows only their first sentence, as find_passages does
TEXTS = {
    "kettles.md#0": "## Meridian Kettle\nIt boils in two minutes. The handle stays cool.",
    "kettles.md#1": "## Solstice Kettle\nThe water was hot almost at once. The lid sticks.",
    "kettles.md#2": "## Aurora Kettle\nIt takes a long while to heat. Pretty colour.",
}
LIMITS = AgentLimits(turns=6, calls=3, prompt_chars=100_000, timeout_s=60)
QUESTION = "Which kettles heat quickly?"


def store() -> FakeStore:
    return FakeStore(chunks=[chunk(i, None, t, context="Kettle notes") for i, t in TEXTS.items()])


class IdArguments(BaseModel):
    ids: list[str]


class Excerpts:
    """A search showing each passage asked for by its first sentence only."""

    name = "search"
    description = "Find passages."
    arguments = IdArguments

    def run(self, arguments):
        shown = [
            ChunkText(chunk_id=i, context="Kettle notes", text=TEXTS[i].split(". ")[0]) for i in arguments.ids
        ]
        return ChunksResult(chunks=shown, total=len(shown))


class NoArguments(BaseModel):
    pass


class Prices:
    """A table query returning three rows, each value longer than a tool would show, with its record."""

    name = "prices"
    description = "Query the price table."
    arguments = NoArguments

    def run(self, arguments):
        rows = [["Meridian Kettle", "49 " + "x" * 400], ["Solstice Kettle", "59"], ["Aurora Kettle", "39"]]
        record = QueryRecord(query_id="q-0123456789", tool="query_tables", statement="SELECT name, price",
                             parameters="{}", sources=["kettles"], files=["kettles.csv"],
                             columns=["name", "price"], rows=rows, total=5)  # fmt: skip
        return QueryResult(query_id="q-0123456789", columns=["name", "price"], rows=rows, total=5).traced(
            record
        )


def call(*ids: str) -> AgentStep:
    return AgentStep(action="call", tool="search", arguments=f'{{"ids": {list(ids)!r}}}'.replace("'", '"'))


def answer(*names: str, cited=(), asks="things", outcome="answer", tool="search", **more) -> AgentStep:
    return AgentStep(action="answer", tool=tool, asks=asks, outcome=outcome, entities=list(names),
                     citations=[AgentCitation(id=i) for i in cited], **more)  # fmt: skip


def verdict(item: str, include: bool, quote: str = "", thing: str = "") -> ItemVerdict:
    return ItemVerdict(id=item, include=include, quote=quote, thing=thing, reason="" if include else "no")


class Model:
    """The agent's steps in order (the last again once they run out) and its working-set replies in order;
    keeps every prompt with the schema it was asked for."""

    def __init__(self, steps: list[AgentStep], replies: list[tuple[list[ItemVerdict], AgentStep]] = ()):
        self._steps = list(steps)
        self._replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, prompt, schema):
        self.prompts.append((schema.__name__, prompt))
        if schema.__name__ == "WorksetStep":
            verdicts, step = self._replies.pop(0)
            return schema(verdicts=verdicts, **step.model_dump())
        return self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]


def run_loop(model: Model, tools=None, limit: int = 30, workset: bool = True):
    llm = ScriptedLLM(model)
    rule = Workset(store(), limit) if workset else None
    loop = AgentLoop(tools or [Excerpts()], llm, "m", 0.0, LIMITS, workset=rule)
    return loop.run(QUESTION), llm


def workset_prompts(model: Model) -> list[str]:
    return [p for s, p in model.prompts if s == "WorksetStep"]


FAST = [verdict("kettles.md#0", True, "It boils in two minutes.", "Meridian Kettle"),
        verdict("kettles.md#1", True, "The water was hot almost at once.", "Solstice Kettle"),
        verdict("kettles.md#2", False)]  # fmt: skip
BOTH = answer("Meridian Kettle", "Solstice Kettle", cited=("kettles.md#0", "kettles.md#1"))


# --- what the agent reads ------------------------------------------------------------------------------


def test_every_passage_read_is_shown_whole_from_the_store_with_the_draft_and_the_tools_closed():
    model = Model([call(*TEXTS), answer("Meridian Kettle", cited=("kettles.md#0",))], [(FAST, BOTH)])
    run, llm = run_loop(model)
    [prompt] = workset_prompts(model)
    # the excerpt the tool showed stops at the first sentence; the working set shows every sentence
    assert all(text in prompt for text in TEXTS.values())
    assert '<item id="kettles.md#1" kind="passage" document="Kettle notes">' in prompt
    assert f"<question>{QUESTION}</question>" in prompt and "No tool can be called any more" in prompt
    assert '<draft>{"outcome":"answer","entities":["Meridian Kettle"]' in prompt
    # the agent's own model, as the loop's other calls: no separate judge
    assert ("WorksetStep", "m") in llm.calls
    assert run.trace.workset_items == list(TEXTS) and run.trace.workset_left_out == []


def test_query_rows_are_shown_uncut_with_their_statement_and_how_many_were_returned():
    steps = [AgentStep(action="call", tool="prices", arguments="{}"),
             answer("Aurora Kettle", cited=("q-0123456789",), tool="prices")]  # fmt: skip
    reply = [verdict("q-0123456789", True, "Aurora Kettle | 39", "Aurora Kettle")]
    model = Model(steps, [(reply, answer("Aurora Kettle", cited=("q-0123456789",), tool="prices"))])
    run, _ = run_loop(model, tools=[Prices()])
    [prompt] = workset_prompts(model)
    assert '<item id="q-0123456789" kind="query" source="query_tables over kettles">' in prompt
    assert "<statement>SELECT name, price</statement>" in prompt
    assert "49 " + "x" * 400 in prompt and "(3 of 5 rows returned)" in prompt
    assert run.trace.workset_marks[0].quote_found is True


def test_items_follow_the_order_first_returned_and_the_rest_past_the_limit_are_named():
    steps = [call("kettles.md#2"), call("kettles.md#0", "kettles.md#1"), BOTH]
    model = Model(steps, [([verdict("kettles.md#2", False), *FAST[:1]], BOTH)])
    run, _ = run_loop(model, limit=2)
    [prompt] = workset_prompts(model)
    assert prompt.index('id="kettles.md#2"') < prompt.index('id="kettles.md#0"')
    assert 'id="kettles.md#1" kind' not in prompt
    assert LEFT_OUT.format(count=1, ids="kettles.md#1") in prompt
    assert run.trace.workset_items == ["kettles.md#2", "kettles.md#0"]
    assert run.trace.workset_left_out == ["kettles.md#1"]


# --- the check and the send-back ---------------------------------------------------------------------


def test_a_reply_missing_a_verdict_is_sent_back_once_and_the_second_reply_stands():
    first = (FAST[:2], BOTH)  # no verdict on kettles.md#2
    model = Model([call(*TEXTS), answer("Meridian Kettle", cited=("kettles.md#0",))], [first, first])
    run, _ = run_loop(model)
    prompts = workset_prompts(model)
    assert len(prompts) == 2
    assert SEND_BACK.format(problems="no verdict for the items kettles.md#2") in prompts[1]
    assert run.trace.workset_sent and run.trace.workset_problems == ["no verdict for the items kettles.md#2"]
    assert run.answer.entities == ["Meridian Kettle", "Solstice Kettle"]


def test_an_included_item_whose_quote_is_not_in_it_is_sent_back():
    wrong = [*FAST[:1], verdict("kettles.md#1", True, "It heats fast.", "Solstice Kettle"), FAST[2]]
    model = Model([call(*TEXTS), BOTH], [(wrong, BOTH), (FAST, BOTH)])
    run, _ = run_loop(model)
    assert "the quote of the included items kettles.md#1 is not verbatim" in workset_prompts(model)[1]
    assert run.trace.workset_sent and run.trace.workset_problems == []
    assert [m.quote_found for m in run.trace.workset_marks] == [True, True, None]


def test_the_reply_is_the_answer_code_adds_no_member_and_the_change_is_recorded():
    # the agent includes the Solstice passage yet answers with one kettle: code keeps the agent's answer
    one = answer("Meridian Kettle", cited=("kettles.md#0",))
    model = Model([call(*TEXTS), one], [(FAST, one)])
    run, _ = run_loop(model)
    assert run.answer.entities == ["Meridian Kettle"] and not run.trace.workset_changed
    model = Model([call(*TEXTS), one], [(FAST, BOTH)])
    run, _ = run_loop(model)
    assert run.answer.entities == ["Meridian Kettle", "Solstice Kettle"] and run.trace.workset_changed
    assert run.trace.cited == ["kettles.md#0", "kettles.md#1"] and run.trace.stopped == "answer"


# --- which replies are reviewed ----------------------------------------------------------------------


def test_numbers_and_abstentions_after_reading_are_reviewed():
    number = answer(asks="number", number=2.0, cited=("kettles.md#0",))
    model = Model([call(*TEXTS), number], [(FAST, number)])
    assert run_loop(model)[0].trace.workset_items == list(TEXTS)
    missing = AgentStep(
        action="answer", tool="search", asks="things", outcome="insufficient_evidence", text="Not stated."
    )
    model = Model([call(*TEXTS), missing], [(FAST, BOTH)])
    run, _ = run_loop(model)
    assert run.trace.workset_items and run.answer.entities == ["Meridian Kettle", "Solstice Kettle"]


def test_a_question_back_a_statement_an_empty_read_and_the_plain_agent_are_not_reviewed():
    back = AgentStep(action="answer", asks="things", outcome="clarify", readings=["a", "b"], text="Which?")
    statement = answer(asks="statement", text="They are kettles.", cited=("kettles.md#0",))
    for step in (back, statement):
        model = Model([call(*TEXTS), step])
        assert run_loop(model)[0].trace.workset_items == [] and not workset_prompts(model)
    model = Model([AgentStep(action="call", tool="prices", arguments="{}"), answer("x")])
    run, _ = run_loop(model, tools=[_Empty()])
    assert not workset_prompts(model) and run.trace.workset_items == []
    model = Model([call(*TEXTS), BOTH])
    run, _ = run_loop(model, workset=False)
    assert not workset_prompts(model) and run.trace.workset_items == []


class _Empty:
    """A tool returning nothing to read."""

    name = "prices"
    description = "Query the price table."
    arguments = NoArguments

    def run(self, arguments):
        return QueryResult(issues=["refused"])


def test_the_agents_turns_are_its_own_prompts_so_a_paired_run_reuses_its_cached_turns():
    steps = [call("kettles.md#0"), call("kettles.md#1", "kettles.md#2"), BOTH]
    plain = Model(list(steps))
    run_loop(plain, workset=False)
    worked = Model(list(steps), [(FAST, BOTH)])
    run_loop(worked)
    turns = [(s, p) for s, p in worked.prompts if s != "WorksetStep"]
    assert turns == plain.prompts


# --- the reply model, trace, metrics, registration and wording ----------------------------------------


def test_the_reply_asks_for_the_verdicts_before_every_field_of_the_loops_reply():
    step = step_model(["search"])
    schema = reply_model(step)
    assert list(schema.model_fields)[0] == "verdicts"
    assert set(step.model_fields) < set(schema.model_fields)
    assert schema.model_fields["asks"].is_required() and schema.model_fields["outcome"].is_required()


def test_the_metrics_count_the_review():
    mark = [
        WorksetMark(id="a", include=True, quote="q", quote_found=True),
        WorksetMark(id="b", include=False),
    ]
    trace = AgentTrace(calls=[], turns=2, stopped="answer", prompt_chars=10, workset_items=["a", "b"],
                       workset_left_out=["c"], workset_marks=mark, workset_sent=True,
                       workset_problems=["no verdict"], workset_changed=True)  # fmt: skip
    plain = AgentTrace(calls=[], turns=1, stopped="answer", prompt_chars=10)
    answers = [
        SystemAnswer(question_id=q, system="agent_workset", agent=t) for q, t in (("a", trace), ("b", plain))
    ]
    metrics = _agent_metrics(answers)
    assert (metrics["agent_workset_reviewed"], metrics["agent_workset_items"]) == (1, 2)
    assert (metrics["agent_workset_left_out"], metrics["agent_workset_included"]) == (1, 1)
    assert (metrics["agent_workset_sent"], metrics["agent_workset_unresolved"]) == (1, 1)
    assert metrics["agent_workset_changed"] == 1


def test_the_variant_has_the_agents_tools_and_prompts_and_versions_its_own():
    variants = {v.name: v for v in AGENT_VARIANTS}
    worked, agent = variants["agent_workset"], variants["agent"]
    assert worked.tool_types == agent.tool_types and worked.wider == agent.wider
    assert set(worked.prompts) == {"agent_workset", "agent_workset_reply"}
    settings = Settings(_env_file=None)
    params, plain = system_params(settings, "agent_workset"), system_params(settings, "agent")
    assert params["agent_workset_items"] == 30 and "agent_workset_prompt_version" in params
    assert "agent_workset_prompt_version" not in plain and "agent_workset_items" not in plain
    shared = [k for k in plain if k.endswith("_prompt_version")]
    assert shared and all(params[k] == plain[k] for k in shared)


def test_what_the_model_reads_of_the_working_set_names_no_evaluated_dataset():
    texts = [PROMPT, LEFT_OUT, SEND_BACK, VERDICTS]
    texts += [f.description or "" for f in ItemVerdict.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
    assert re.search(r"ONE contiguous quote copied\s+verbatim", PROMPT)
