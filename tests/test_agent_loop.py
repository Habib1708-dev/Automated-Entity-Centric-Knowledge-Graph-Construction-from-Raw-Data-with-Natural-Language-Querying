"""The agent loop and the agent system (R135, agent/loop.py, citations.py, prompt.py, system.py) with a
scripted model and fake tools, without Neo4j or a network: a call run and its result written into the next
prompt with the calls left; refusals (an unknown tool, arguments that are no JSON object or do not fit the
tool, a call identical to an earlier one) written back and not counted as calls; each cap (turns, calls,
prompt characters, wall-clock time) closing the tools and asking once more; citations of ids no tool
returned sent back once and then dropped; a cited claim bringing its chunk; the answer as `kg qa` reads it
(shown: chunks read, then cited chunks read from the store; citations with quotes; the trace); the agent's
metrics; its registration with its params and prompt; and the prompt free of the evaluated datasets'
words. R138: the reply model asked for names one of the loop's tools and cannot leave the tool out; an
empty answer is written back once with the tools open, and counted. R141: a passage read in full after
its excerpt keeps the full text in its first place. R142: the model's time and the loop's time in the
trace; whether the answer's names and number appear in what its citations' results hold; the metrics of
both, and the seconds per answer every system logs. R146: the tool list and the reply schema the model reads
are versioned, from a tuple of tool classes held equal to what `build_tools` builds. R147: the agent and the
reader share one answer-form rule (a number first) and the answer fields' descriptions; the reply must
declare the form it answers (`asks`); an answer that leaves its declared field empty is sent back once with
the tools open while a turn is left, then stands; an empty answer is R138's, never also sent back for its
form; the metrics count the answers sent back, and the agent's prompt version covers the text they are sent
back with. R150: a thing `rank_by_documents` counted is given its name, and its result supports its count.
R161: the prompt asks for counts and sums of entries from a tool's measure, never by counting listed rows,
and for one list less another by `!=` conditions. R163: the form rule, the `entities` field and `asks` list
several years, a month alone or a time of day as things, keep a date (a day of a month, with or without
its year) as text and a year alone as a number."""

import pytest
from pydantic import BaseModel, ValidationError

from kgbuilder.agent.citations import Ledger
from kgbuilder.agent.loop import AgentLimits, AgentLoop
from kgbuilder.agent.prompt import (
    ASKS,
    CITATION_RETRY,
    CLOSING,
    EMPTY_ANSWER,
    FORM_ANSWER,
    PROMPT,
    AgentCitation,
    AgentStep,
    form_answer,
    step_model,
    tool_list,
)
from kgbuilder.agent.results import (
    ChunksResult,
    ChunkText,
    ClaimsResult,
    MentionCount,
    MentionCountsResult,
    NodeHit,
    NodesResult,
)
from kgbuilder.agent.system import AGENT_TOOL_TYPES, AgentSystem, ToolCaps, build_tools
from kgbuilder.config import Settings
from kgbuilder.llm.base import prompt_version, schema_text
from kgbuilder.pipeline.qa_stages import _agent_metrics, _latency_metrics
from kgbuilder.pipeline.qa_systems import system_params
from kgbuilder.query import reader
from kgbuilder.query.answers import AgentTrace
from kgbuilder.query.graph_schema import GraphSchema
from kgbuilder.query.reader import ANSWER_FIELDS, FORM_RULE, ReaderAnswer

from .fakes import ScriptedLLM
from .test_agent_tools import DATASET_WORDS, SETTING, fact
from .test_query import FakeStore, chunk

LIMITS = AgentLimits(turns=6, calls=3, prompt_chars=100_000, timeout_s=60)


class RefArguments(BaseModel):
    ref: str


class FakeFind:
    """A tool returning one node per call, named after the ref asked."""

    name = "find"
    description = "Find a node."
    arguments = RefArguments

    def __init__(self):
        self.asked: list[str] = []

    def run(self, arguments):
        self.asked.append(arguments.ref)
        return NodesResult(nodes=[NodeHit(ref=arguments.ref, kind="concept", label="Thing", title="It")])


class FakeRead:
    """A tool returning one chunk with one claim, and one claim found alone in another chunk."""

    name = "read"
    description = "Read passages."
    arguments = RefArguments

    def run(self, arguments):
        if arguments.ref == "claims":
            return ClaimsResult(claims=[fact("o9", "d.md#9")])
        return ChunksResult(chunks=[ChunkText(chunk_id="d.md#0", context="Doc", text="The lid is loose.",
                                              claims=[fact("o1", "d.md#0")])], total=1)  # fmt: skip


def call(tool: str, arguments: str) -> AgentStep:
    return AgentStep(action="call", tool=tool, arguments=arguments)


def answer(*cited: str, text: str = "loose") -> AgentStep:
    return AgentStep(action="answer", text=text, citations=[AgentCitation(id=i) for i in cited])


class Script:
    """A model replying with fixed steps in order, the last one again once they run out; keeps the
    prompts."""

    def __init__(self, *steps: AgentStep):
        self._steps = list(steps)
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]


def loop(script: Script, limits: AgentLimits = LIMITS, clock=None, tools=None, timer=None) -> AgentLoop:
    kwargs = {"clock": clock} if clock else {}
    if timer:
        kwargs["timer"] = timer
    return AgentLoop(tools or [FakeFind(), FakeRead()], ScriptedLLM(script), "m", 0.0, limits, **kwargs)


# --- calls and refusals ------------------------------------------------------------------------------


def test_a_call_runs_and_its_result_is_in_the_next_prompt_with_the_calls_left():
    script = Script(call("find", '{"ref": "k1"}'), answer("k1"))
    run = loop(script).run("Which thing?")
    assert [c.tool for c in run.trace.calls] == ["find"] and run.trace.calls[0].returned == ["k1"]
    assert (run.trace.turns, run.trace.stopped, run.trace.cited) == (2, "answer", ["k1"])
    assert "Tool calls left: 3." in script.prompts[0] and "Tool calls left: 2." in script.prompts[1]
    assert '<turn n="1" tool="find">' in script.prompts[1] and '"ref":"k1"' in script.prompts[1]
    assert run.trace.prompt_chars == sum(len(p) for p in script.prompts)


def test_refused_calls_are_written_back_with_their_reason_and_use_a_turn_but_no_call():
    script = Script(call("nope", "{}"), call("find", "not json"), call("find", '{"id": "k1"}'),
                    call("find", '["k1"]'), answer())  # fmt: skip
    run = loop(script).run("Which thing?")
    reasons = [c.refused for c in run.trace.calls]
    assert reasons[0] == "no tool 'nope'; the tools are find, read"
    assert reasons[1].startswith("the arguments are not a JSON object")
    assert reasons[2].startswith("the arguments do not fit find: ref: Field required")
    assert reasons[3] == "the arguments must be a JSON object"
    assert "refused: no tool 'nope'" in script.prompts[1]
    assert "Tool calls left: 3." in script.prompts[4]  # no refused call counted


def test_a_call_identical_to_an_earlier_one_is_refused_and_not_run_again():
    tool = FakeFind()
    script = Script(call("find", '{"ref": "k1"}'), call("find", '{ "ref":"k1" }'), answer())
    run = loop(script, tools=[tool]).run("Which thing?")
    assert tool.asked == ["k1"]
    assert run.trace.calls[1].refused == "identical to the call of turn 1; its result is above"


# --- the reply model and empty answers (R138) --------------------------------------------------------


def test_the_reply_model_names_one_of_the_loops_tools_and_cannot_leave_the_tool_out():
    script = Script(answer())
    loop(script).run("Which thing?")
    schema = script.schemas[0]
    assert issubclass(schema, AgentStep) and schema.model_json_schema()["properties"]["tool"]["enum"] == [
        "find",
        "read",
    ]
    assert "tool" in schema.model_json_schema()["required"]
    with pytest.raises(ValidationError):  # R136's empty tool
        schema.model_validate({"action": "call", "arguments": '{"ref": "k1"}', "asks": "statement"})
    with pytest.raises(ValidationError):
        schema.model_validate({"action": "call", "tool": "nope", "asks": "statement"})
    assert schema.model_validate({"action": "call", "tool": "find", "asks": "statement"}).tool == "find"
    with pytest.raises(ValidationError):
        step_model(["find"]).model_validate({"action": "lookup", "tool": "find", "asks": "statement"})


def test_the_reply_must_declare_the_form_it_answers():
    """R147: `asks` is required, as `tool` is (a model held to a schema leaves an optional field out)."""
    schema = step_model(["find"])
    asks = schema.model_json_schema()["properties"]["asks"]
    assert "asks" in schema.model_json_schema()["required"]
    assert asks["enum"] == ["things", "number", "statement"]
    with pytest.raises(ValidationError):
        schema.model_validate({"action": "answer", "tool": "find", "text": "It."})
    with pytest.raises(ValidationError):
        schema.model_validate({"action": "answer", "tool": "find", "asks": "list", "text": "It."})
    assert AgentStep(action="answer", text="It.").asks is None  # the base stays optional


def test_an_empty_answer_is_written_back_once_with_the_tools_open():
    empty = AgentStep(action="answer")
    script = Script(call("find", '{"ref": "k1"}'), empty, call("read", '{"ref": "x"}'), answer("k1"))
    run = loop(script).run("Which thing?")
    assert EMPTY_ANSWER in script.prompts[2] and "<tools>" in script.prompts[2]
    assert (run.trace.emptied, run.trace.stopped, run.trace.turns) == (True, "answer", 4)
    assert [c.tool for c in run.trace.calls] == ["find", "read"] and run.answer.text == "loose"


def test_a_second_empty_answer_is_the_answer_and_none_is_not_empty():
    empty = AgentStep(action="answer")
    run = loop(Script(empty, empty)).run("Which thing?")
    assert (run.trace.emptied, run.trace.stopped, run.trace.turns) == (True, "answer", 2)
    assert run.answer.is_empty()
    none = AgentStep(action="answer", entities=[])
    assert not none.is_empty() and not loop(Script(none)).run("Which thing?").trace.emptied


# --- the answer form (R147) --------------------------------------------------------------------------


def test_an_answer_leaving_out_the_form_it_declares_is_sent_back_once_with_the_tools_open():
    worded = AgentStep(
        action="answer", asks="things", text="It is the one.", citations=[AgentCitation(id="k1")]
    )
    named = AgentStep(action="answer", asks="things", entities=["It"], citations=[AgentCitation(id="k1")])
    script = Script(call("find", '{"ref": "k1"}'), worded, named)
    run = loop(script).run("Which thing?")
    assert form_answer(worded) in script.prompts[2] and "<tools>" in script.prompts[2]
    sent = 'asks for things but leaves `entities` empty; its text was "It is the one."; answer'
    assert sent in script.prompts[2]
    assert (run.trace.reformed, run.trace.emptied, run.trace.stopped, run.trace.turns) == (
        True,
        False,
        "answer",
        3,
    )
    assert run.answer.entities == ["It"] and run.trace.asks == "things"


def test_entities_left_empty_beside_a_text_are_sent_back_for_a_things_question():
    """R143b's model wrote [] beside its text answers; declared "things", [] with text is no answer form."""
    beside = AgentStep(action="answer", asks="things", entities=[], text="It is the one.")
    script = Script(beside, AgentStep(action="answer", asks="things", entities=["It"]))
    run = loop(script).run("Which thing?")
    assert (run.trace.reformed, run.trace.turns, run.answer.entities) == (True, 2, ["It"])
    assert "leaves `entities` empty" in script.prompts[1]


def test_a_none_answer_is_sent_back_once_with_a_way_out_and_then_stands():
    """PROMPT asks for [] and a `text` when nothing answers; the send-back names that exit."""
    none = AgentStep(action="answer", asks="things", entities=[], text="No result names any such thing.")
    script = Script(none, none)
    run = loop(script).run("Which thing?")
    exit_ = "the answer is that there are none (`entities` []), send the same answer again"
    assert exit_ in script.prompts[1]
    assert (run.trace.reformed, run.trace.stopped, run.answer.entities) == (True, "answer", [])


def test_an_answer_on_the_last_turn_is_not_sent_back_for_its_form():
    """A send-back with no turn left would end as a "turns" stop through the closing prompt."""
    limits = AgentLimits(turns=2, calls=10, prompt_chars=100_000, timeout_s=60)
    worded = AgentStep(action="answer", asks="number", text="Over 40")
    script = Script(call("find", '{"ref": "a"}'), worded)
    run = loop(script, limits).run("How many?")
    assert (run.trace.stopped, run.trace.reformed, run.trace.turns, len(script.prompts)) == (
        "answer",
        False,
        2,
        2,
    )


def test_one_question_can_be_sent_back_as_empty_and_then_for_its_form():
    empty = AgentStep(action="answer", asks="number")
    worded = AgentStep(action="answer", asks="number", text="Over 40")
    counted = AgentStep(action="answer", asks="number", number=40.0, text="over 40")
    script = Script(empty, worded, counted)
    run = loop(script).run("How many?")
    assert EMPTY_ANSWER in script.prompts[1] and "leaves `number` empty" in script.prompts[2]
    assert (run.trace.emptied, run.trace.reformed, run.trace.turns, run.trace.stopped) == (
        True,
        True,
        3,
        "answer",
    )
    assert run.answer.number == 40.0


def test_a_number_given_in_words_is_sent_back_once_then_the_second_reply_is_the_answer():
    worded = AgentStep(action="answer", asks="number", text="Over 40")
    script = Script(worded, worded)
    run = loop(script).run("How many?")
    assert "asks for a number but leaves `number` empty" in script.prompts[1]
    assert (run.trace.reformed, run.trace.turns, run.trace.stopped) == (True, 2, "answer")
    assert run.answer.number is None and run.answer.text == "Over 40"  # once: the second reply stands


@pytest.mark.parametrize(
    "step",
    [
        AgentStep(action="answer", asks="statement", text="It was loose."),
        AgentStep(action="answer", asks="things", entities=[]),  # "none"
        AgentStep(action="answer", asks="things", entities=["It"], text="It, the first one."),
        AgentStep(action="answer", asks="number", number=3.0, text="about 3"),
        # declared "things" for a number: sending it back could move a right number into `entities`
        AgentStep(action="answer", asks="things", number=2019.0, text="It is 2019."),
        AgentStep(action="answer", text="It was loose."),  # a model off the schema declares nothing
    ],
    ids=["statement", "none", "things", "number", "things-with-number", "undeclared"],
)
def test_an_answer_in_its_declared_form_or_a_statement_is_never_sent_back(step):
    run = loop(Script(step)).run("Which thing?")
    assert (run.trace.reformed, run.trace.emptied, run.trace.turns) == (False, False, 1)
    assert run.answer == step and run.trace.asks == step.asks


def test_an_empty_answer_is_sent_back_as_empty_and_not_again_for_its_form():
    empty = AgentStep(action="answer", asks="things")
    script = Script(empty, empty)
    run = loop(script).run("Which thing?")
    assert (run.trace.emptied, run.trace.reformed, run.trace.turns) == (True, False, 2)
    assert EMPTY_ANSWER in script.prompts[1] and "leaves `entities` empty" not in "".join(script.prompts)


def test_a_closing_answer_is_never_sent_back_for_its_form():
    limits = AgentLimits(turns=1, calls=10, prompt_chars=100_000, timeout_s=60)
    worded = AgentStep(action="answer", asks="number", text="Over 40")
    script = Script(call("find", '{"ref": "a"}'), worded)
    run = loop(script, limits).run("How many?")
    assert (run.trace.stopped, run.trace.reformed, len(script.prompts)) == ("turns", False, 2)
    assert run.answer.text == "Over 40"


def test_the_agent_and_the_reader_share_one_answer_form_rule():
    for prompt in (PROMPT, CLOSING):
        assert FORM_RULE.format(source="the tools") in prompt
    assert FORM_RULE.format(source="the chunks") in reader.PROMPT
    assert (
        "never by a ref" in PROMPT and "An empty `entities` list means the chunks name none." in reader.PROMPT
    )
    # the rule before R147 sent everything but "which things" to text
    assert all("which things" not in p and "Otherwise answer in a short" not in p
               for p in (PROMPT, CLOSING, EMPTY_ANSWER, reader.PROMPT))  # fmt: skip
    fields = step_model(["t"]).model_fields
    for key, description in ANSWER_FIELDS.items():
        assert fields[key].description == f"answer: {description}."
        assert ReaderAnswer.model_fields[key].description == f"{description[:1].upper()}{description[1:]}."


def test_the_prompt_asks_for_counts_and_list_differences_from_the_tools():
    """R161: LB11's agent counted 40 listed rows by eye (11 for 13) and FB10's subtracted one list from
    another in its head and dropped one entry; the rule sends both to what a tool can do: a measure with
    conditions, and `!=` conditions over the ids another result returned."""
    rule = (
        "- Take a count or a sum of entries from a tool's `measure`, with their conditions in `where`, "
        "never by\n  counting listed rows yourself; to leave out the entries another result returned, add "
        "one `!=` condition\n  per id it returned."
    )
    assert rule in PROMPT and rule not in CLOSING  # with no tool left, nothing could be asked of one


def test_a_trace_from_before_the_answer_form_still_loads():
    old = AgentTrace.model_validate({"calls": [], "turns": 1, "stopped": "answer", "prompt_chars": 10})
    assert (old.reformed, old.asks) == (False, None)


def test_the_form_rule_takes_one_number_before_named_things():
    """A measure asked with "what" or "how deep" is one number; a "values" clause before it would take it."""
    rule = FORM_RULE.format(source="the tools")
    assert rule.index("one number") < rule.index("named things") < rule.index("anything else")
    assert "values that are neither one number nor a date" in rule
    assert "it never replaces them" in rule


def _flat(text: str) -> str:
    """`text` with its line breaks and indents as single spaces: a prompt wraps where a phrase may fall."""
    return " ".join(text.split())


def _form_clauses() -> tuple[str, str, str]:
    """The number, entities and text clauses of the agent's rendered form rule, each on one line."""
    _, number, things, rest = FORM_RULE.format(source="the tools").split("\n  - ")
    return _flat(number), _flat(things), _flat(rest.split("\n  `")[0])  # up to the closing `text` line


def _asks_part(form: str) -> str:
    """The words `asks` gives one form ("number", "things" or "statement"), on one line."""
    after = ASKS.split(f'"{form}"')[1]
    return _flat(after.split('"things"')[0].split('"statement"')[0])


@pytest.mark.parametrize("case", ["several years", "a month alone or a time of day"])
def test_the_form_rule_lists_several_years_a_month_and_a_time_of_day_as_things(case):
    """R163: R157's agent declared "statement" and answered in `text` for two years (LB39), a month (GB05)
    and a time of day (GB33), whose gold is a set scored as entities: R147's rule named a year alone and a
    full date, and put "when" in `text`. The rule, the `entities` field and `asks` name the same cases."""
    number, things, text = _form_clauses()
    assert case in things and case not in number and case not in text
    assert case in _flat(ANSWER_FIELDS["entities"])
    assert case in _asks_part("things") and case not in _asks_part("statement")


def test_a_date_with_or_without_its_year_stays_text_and_one_year_stays_a_number():
    """R163 keeps a date in `text`, a year alone in `number`: a day of a month without its year is a date
    too (earlier gold dated "On 28 March" is text), never a month alone listed in `entities`; "when" no
    longer sends a month or a time to `text` (GB05, GB33)."""
    date = "a day of a month, with or without its year"
    number, things, text = _form_clauses()
    assert date in text and date not in things and "when" not in text
    assert "a year" in number and "nor a date" in things
    assert date in _flat(ANSWER_FIELDS["text"]) and date not in _flat(ANSWER_FIELDS["entities"])
    assert date in _asks_part("statement") and date not in _asks_part("things")
    assert "a single year" in ANSWER_FIELDS["number"] and "a single year" in _asks_part("number")


# --- the caps ----------------------------------------------------------------------------------------


def test_at_the_calls_cap_the_tools_close_and_the_model_answers_once_more():
    script = Script(call("find", '{"ref": "a"}'), call("find", '{"ref": "b"}'), call("find", '{"ref": "c"}'),
                    answer("a"))  # fmt: skip
    run = loop(script).run("Which thing?")
    assert run.trace.stopped == "calls" and len(run.trace.calls) == 3
    assert script.prompts[-1].startswith(CLOSING.split("{")[0][:60])  # the closing prompt, no tools
    assert "<tools>" not in script.prompts[-1] and run.answer.text == "loose"


def test_at_the_turns_cap_the_tools_close():
    limits = AgentLimits(turns=2, calls=10, prompt_chars=100_000, timeout_s=60)
    script = Script(call("nope", "{}"), call("nope", "{}"), answer())
    run = loop(script, limits).run("Which thing?")
    assert (run.trace.stopped, run.trace.turns, len(script.prompts)) == ("turns", 2, 3)


def test_the_prompt_budget_stops_the_loop_before_a_prompt_would_break_it():
    limits = AgentLimits(turns=10, calls=10, prompt_chars=len(PROMPT) * 2 + 1500, timeout_s=60)
    script = Script(call("find", '{"ref": "a"}'), call("find", '{"ref": "b"}'), answer())
    run = loop(script, limits).run("Which thing?")
    assert run.trace.stopped == "prompt_chars"
    assert run.trace.prompt_chars <= limits.prompt_chars  # the closing prompt is not counted


def test_the_timeout_closes_the_tools_before_the_next_turn():
    times = iter([0.0, 1.0, 61.0, 61.0])
    script = Script(call("find", '{"ref": "a"}'), answer())
    run = loop(script, clock=lambda: next(times)).run("Which thing?")
    assert (run.trace.stopped, run.trace.turns) == ("timeout", 1)


# --- citations ---------------------------------------------------------------------------------------


def test_an_answer_citing_unknown_ids_is_sent_back_once_then_they_are_dropped():
    script = Script(call("find", '{"ref": "k1"}'), answer("k1", "made-up"), answer("k1", "made-up"))
    run = loop(script).run("Which thing?")
    assert "Your answer cited ids no tool returned: made-up." in script.prompts[-1]
    assert (run.trace.retried, run.trace.cited, run.trace.dropped) == (True, ["k1"], ["made-up"])


def test_a_cited_claim_brings_its_chunk_and_a_chunk_citation_keeps_its_quote():
    ledger = Ledger()
    ledger.add(FakeRead().run(RefArguments(ref="x")))
    ledger.add(FakeRead().run(RefArguments(ref="claims")))
    checked = ledger.check([AgentCitation(id="o9"), AgentCitation(id="d.md#0", quote="lid is loose")])
    assert checked.kept == ["o9", "d.md#0", "d.md#9"] and checked.unknown == []
    assert [(c.chunk_id, c.quote) for c in checked.chunks] == [("d.md#0", "lid is loose")]
    assert [c.chunk_id for c in ledger.read()] == ["d.md#0"]  # d.md#9 was named, never read


def test_a_passage_read_in_full_after_its_excerpt_keeps_the_full_text_and_its_place():
    ledger = Ledger()
    ledger.add(
        ChunksResult(
            chunks=[
                ChunkText(chunk_id="d.md#0", context="Doc", text="loose."),
                ChunkText(chunk_id="d.md#1", context="Doc", text="Late."),
            ],
            total=2,
        )
    )
    ledger.add(FakeRead().run(RefArguments(ref="x")))  # d.md#0 in full: "The lid is loose."
    ledger.add(ChunksResult(chunks=[ChunkText(chunk_id="d.md#0", context="Doc", text="lid")], total=1))
    assert [(c.chunk_id, c.text) for c in ledger.read()] == [
        ("d.md#0", "The lid is loose."),
        ("d.md#1", "Late."),
    ]


# --- time and support (R142) ------------------------------------------------------------------------


def test_the_trace_times_the_models_calls_and_the_whole_loop():
    # the loop's start, then each reply's start and end, then the end of the loop
    ticks = iter([0.0, 1.0, 3.0, 4.0, 7.0, 10.0])
    run = loop(Script(call("find", '{"ref": "k1"}'), answer("k1")), timer=lambda: next(ticks)).run("Which?")
    assert (run.trace.llm_ms, run.trace.wall_ms) == (5000.0, 10000.0)


def test_an_answer_is_supported_when_its_cited_results_hold_its_names_and_number():
    ledger = Ledger()
    ledger.add(NodesResult(nodes=[NodeHit(ref="k1", kind="record", label="Thing", title="Lárk Kettle")]))
    ledger.add(ChunksResult(chunks=[ChunkText(chunk_id="d.md#0", context="Doc",
                                              text="12 pots were sold in 2010.")], total=1))  # fmt: skip
    found = ledger.support(["lark kettle"], 12.0, ["k1", "d.md#0"])
    assert (found.entities_grounded, found.number_stated, found.ungrounded) == (True, True, [])
    one_missing = ledger.support(["Lark Kettle", "Quill Pot"], None, ["k1"])
    assert (one_missing.entities_grounded, one_missing.number_stated, one_missing.ungrounded) == (
        False,
        None,
        ["Quill Pot"],
    )
    assert ledger.support(["Lark Kettle"], None, ["d.md#0"]).entities_grounded is False  # held, not cited
    assert ledger.support(None, 10.0, ["d.md#0"]).number_stated is False  # 10 is no token of 2010
    assert ledger.support([], None, ["k1"]).entities_grounded is None  # "none" names nothing


def test_the_agents_answer_carries_its_support():
    reply = AgentStep(action="answer", entities=["k1", "Lark Kettle"], citations=[AgentCitation(id="k1")])
    result = AgentSystem(loop(Script(call("find", '{"ref": "k1"}'), reply)), FakeStore()).answer(
        "Q1", "Which?"
    )
    assert (result.agent.entities_grounded, result.agent.ungrounded) == (False, ["Lark Kettle"])


def test_the_seconds_per_answer_and_the_questions_are_logged_for_every_system():
    answers = [
        AgentSystem(loop(Script(answer())), FakeStore())
        .answer(f"Q{i}", "Which?")
        .model_copy(update={"latency_s": s})
        for i, s in enumerate([4.0, 1.0, 10.0, 2.0, 3.0])
    ]
    assert _latency_metrics(answers) == {
        "questions": 5,
        "answer_latency_s_mean": 4.0,
        "answer_latency_s_p50": 3.0,
        "answer_latency_s_p90": 10.0,
        "answer_latency_s_max": 10.0,
    }
    untimed = [a.model_copy(update={"latency_s": None}) for a in answers]
    assert _latency_metrics(untimed) == {"questions": 5}


# --- the answer kg qa reads --------------------------------------------------------------------------


def test_the_agents_answer_shows_the_chunks_it_read_then_the_cited_ones_it_saw_by_id():
    cited = [AgentCitation(id="d.md#0", quote="lid is loose"), AgentCitation(id="o9")]
    reply = AgentStep(action="answer", entities=["lid"], citations=cited)
    script = Script(call("read", '{"ref": "x"}'), call("read", '{"ref": "claims"}'), reply)
    store = FakeStore(chunks=[chunk("d.md#9", None, "Another passage.")])
    result = AgentSystem(loop(script), store).answer("Q1", "Which part is loose?")
    assert [c.chunk_id for c in result.shown] == ["d.md#0", "d.md#9"] == result.retrieved
    assert result.shown[1].text == "Another passage."  # read from the store for the judge
    assert result.entities == ["lid"] and [c.chunk_id for c in result.citations] == ["d.md#0"]
    assert (result.system, result.agent.stopped, result.agent.cited) == (
        "agent",
        "answer",
        ["d.md#0", "o9", "d.md#9"],
    )


def test_the_agents_metrics_count_turns_calls_refusals_and_stops():
    script = Script(call("find", '{"ref": "a"}'), call("nope", "{}"), answer("a"))
    one = AgentSystem(loop(script), FakeStore()).answer("Q1", "Which?")
    metrics = _agent_metrics([one, one.model_copy(update={"agent": None})])
    assert (metrics["agent_turns_mean"], metrics["agent_calls_mean"], metrics["agent_calls_refused"]) == (
        3,
        1,
        1,
    )
    assert metrics["agent_calls_find"] == 1 and metrics["agent_stopped_answer"] == 1
    assert metrics["agent_stopped_calls"] == 0 and _agent_metrics([]) == {}
    emptied = one.model_copy(update={"agent": one.agent.model_copy(update={"emptied": True})})
    assert _agent_metrics([one, emptied])["agent_answers_emptied"] == 1
    reformed = one.model_copy(update={"agent": one.agent.model_copy(update={"reformed": True})})
    assert _agent_metrics([one, reformed])["agent_answers_reformed"] == 1
    assert metrics["agent_answers_reformed"] == 0
    timed = one.agent.model_copy(
        update={"llm_ms": 3000.0, "wall_ms": 5000.0, "entities_grounded": True, "number_stated": False}
    )
    both = _agent_metrics([one, one.model_copy(update={"agent": timed})])
    assert (both["agent_llm_s_mean"], both["agent_wall_s_mean"]) == (1.5, 2.5)
    assert (both["agent_entity_answers"], both["agent_entity_answers_grounded"]) == (1, 1)
    assert (both["agent_number_answers"], both["agent_number_answers_stated"]) == (1, 0)


def test_the_agent_is_registered_with_its_caps_and_its_prompt_version():
    params = system_params(Settings(), "agent")
    assert (params["agent_max_calls"], params["agent_nodes"], params["seed_candidates"]) == (10, 15, 25)
    assert "agent_text_rrf_k" not in params
    # R147: the text a form send-back writes is versioned with the prompts
    assert params["agent_prompt_version"] == prompt_version(
        PROMPT + CLOSING + CITATION_RETRY + EMPTY_ANSWER + FORM_ANSWER
    )
    # R146: the tool list and the reply schema are versioned too, from the same text the model reads
    names = [t.name for t in AGENT_TOOL_TYPES]
    assert params["agent_tools_prompt_version"] == prompt_version(tool_list(AGENT_TOOL_TYPES))
    assert params["agent_reply_prompt_version"] == prompt_version(schema_text(step_model(names)))


class NoTables:
    """A table store with no table: the schema tool reads the tables when it is built."""

    def tables(self) -> list:
        return []


def test_the_versioned_tool_list_is_the_list_the_agent_is_built_with():
    """R146: a tool added to `build_tools` and not to `AGENT_TOOL_TYPES` would reach the model unversioned."""
    caps = ToolCaps(
        nodes=1, relations=1, chunks=1, claims=1, rows=1, value_chars=1, names=1, document_chunks=1,
        matches=1, text_rrf_k=0,
    )  # fmt: skip
    stand_in = object()  # the constructors keep their stores; only Schema reads its schema and tables
    schema = GraphSchema(labels=[], relationships=[], claims=[])
    tools = build_tools(
        stand_in, stand_in, stand_in, stand_in, stand_in, NoTables(), schema, None, SETTING, caps
    )  # fmt: skip
    assert [type(t) for t in tools] == list(AGENT_TOOL_TYPES)
    assert tool_list(tools) == tool_list(AGENT_TOOL_TYPES)


def test_what_the_model_reads_of_the_agent_names_no_evaluated_dataset():
    texts = [PROMPT, CLOSING, EMPTY_ANSWER, FORM_ANSWER] + [
        f.description or "" for m in (step_model(["t"]), AgentCitation) for f in m.model_fields.values()
    ]
    assert all(not DATASET_WORDS.search(text) for text in texts)


def test_an_entity_written_as_a_ref_a_tool_returned_is_given_that_nodes_name():
    reply = AgentStep(action="answer", entities=["k1", "Lark Kettle"], citations=[AgentCitation(id="k1")])
    script = Script(call("find", '{"ref": "k1"}'), reply)
    result = AgentSystem(loop(script), FakeStore()).answer("Q1", "Which thing?")
    assert result.entities == ["It", "Lark Kettle"]  # FakeFind titles every node "It"; a name stays


def test_a_counted_things_ref_is_given_its_name_and_supports_its_count():
    """R150: a thing `rank_by_documents` returned is named like any node, and its result holds its count."""
    ledger = Ledger()
    thing = MentionCount(ref="i-x", kind="individual", label="Person", name="Ada Brook", documents=4,
                         spellings=["Ada", "Ada Brook"])  # fmt: skip
    ledger.add(MentionCountsResult(things=[thing], total=1))
    assert ledger.name_of("i-x") == "Ada Brook"
    found = ledger.support(["Ada Brook"], 4.0, ["i-x"])
    assert (found.entities_grounded, found.number_stated) == (True, True)
