"""The agent's outcomes (R204, agent/outcome.py, loop.py, prompt.py, system.py, query/screen.py) with a
scripted model and fake tools, without Neo4j or a network: each outcome check (an uncited answer, answer
fields beside an abstention, a "clarify" without two readings, an abstention before any tool call) sends the
reply back once with the tools open, then the reply stands or is settled; a reply passing every check is
never sent back; the form check (R147), the recheck (R176) and the evidence review (R193) leave abstentions
alone; a "none" answer stays an answer; a closing reply is settled, never sent back; the outcome reaches the
trace and the answer `kg qa` reads; the screen's refusal is the only "refuse"; the reply schema requires an
outcome and offers no refusal; the prompts name every outcome; the metrics count each outcome."""

import pytest

from kgbuilder.agent.loop import AgentLimits, AgentLoop
from kgbuilder.agent.outcome import OutcomeCheck, settle
from kgbuilder.agent.prompt import (
    CLOSING,
    OUTCOME_FIELDS,
    OUTCOME_READINGS,
    OUTCOME_UNCITED,
    OUTCOME_UNSEARCHED,
    PROMPT,
    AgentCitation,
    AgentStep,
    step_model,
)
from kgbuilder.agent.system import AgentSystem
from kgbuilder.pipeline.qa_stages import _agent_metrics, _outcome_metrics
from kgbuilder.query.answers import SystemAnswer
from kgbuilder.query.screen import ScreenedSystem

from .fakes import ScriptedLLM
from .test_agent_loop import RECHECK, FakeFind, FakeRead, Script, call
from .test_query import FakeStore

LIMITS = AgentLimits(turns=6, calls=3, prompt_chars=100_000, timeout_s=60)
CHECK = OutcomeCheck()


def outcome_loop(script: Script, limits: AgentLimits = LIMITS, recheck=None) -> AgentLoop:
    return AgentLoop(
        [FakeFind(), FakeRead()], ScriptedLLM(script), "m", 0.0, limits, recheck=recheck, outcome=CHECK
    )


def reply(outcome: str | None, *cited: str, **fields) -> AgentStep:
    return AgentStep(
        action="answer", outcome=outcome, citations=[AgentCitation(id=i) for i in cited], **fields
    )


FIND = call("find", '{"ref": "k1"}')


# --- the checks ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "cited", "calls", "message"),
    [
        (reply("answer", text="It."), [], 1, OUTCOME_UNCITED),
        (reply(None, text="It."), [], 1, OUTCOME_UNCITED),  # no outcome reads as "answer"
        (reply("answer", "k1", text="It."), ["k1"], 1, None),
        (reply("insufficient_evidence", entities=["It"]), [], 1, OUTCOME_FIELDS.format(
            outcome="insufficient_evidence")),
        (reply("out_of_scope", number=3.0), [], 1, OUTCOME_FIELDS.format(outcome="out_of_scope")),
        (reply("clarify", readings=["the first", " The First "], text="Which?"), [], 0, OUTCOME_READINGS),
        (reply("clarify", readings=["the first", "the second"], text="Which?"), [], 0, None),
        (reply("out_of_scope", text="Not held."), [], 0, OUTCOME_UNSEARCHED.format(outcome="out_of_scope")),
        (reply("insufficient_evidence", text="No."), [], 0, OUTCOME_UNSEARCHED.format(
            outcome="insufficient_evidence")),
        (reply("insufficient_evidence", entities=[], text="No."), [], 1, None),  # [] names nothing
        (reply("out_of_scope", text="Not held."), [], 1, None),
    ],
    ids=["uncited", "uncited-undeclared", "cited", "fields-insufficient", "fields-scope", "one-reading",
         "two-readings", "scope-unsearched", "insufficient-unsearched", "empty-list", "scope-searched"],
)  # fmt: skip
def test_each_check_names_its_message_or_lets_the_reply_stand(step, cited, calls, message):
    assert CHECK.problem(step, cited, calls) == message


def test_settling_writes_the_outcome_out_and_clears_what_an_abstention_cannot_give():
    assert settle(reply(None, "k1", entities=["It"])).outcome == "answer"
    assert settle(reply("answer", entities=[])).entities == []  # "none" is kept
    cleared = settle(reply("insufficient_evidence", entities=["It"], number=2.0, readings=["a", "b"]))
    assert (cleared.outcome, cleared.entities, cleared.number, cleared.readings) == (
        "insufficient_evidence", None, None, None,
    )  # fmt: skip
    one = settle(reply("clarify", readings=["a", "A"], text="Which?"))
    assert (one.outcome, one.readings) == ("insufficient_evidence", None)
    two = settle(reply("clarify", readings=["a", "", "b"], text="Which?"))
    assert (two.outcome, two.readings) == ("clarify", ["a", "b"])


# --- in the loop ---------------------------------------------------------------------------------------


def test_an_uncited_answer_is_sent_back_once_with_the_tools_open_then_stands():
    uncited = reply("answer", text="It.")
    run = outcome_loop(Script(FIND, uncited, uncited)).run("Which?")
    assert (run.trace.turns, run.trace.outcome_sent, run.trace.outcome) == (3, True, "answer")
    assert run.answer.text == "It."


def test_an_abstention_before_any_call_is_sent_back_and_the_model_may_search():
    script = Script(reply("out_of_scope", text="Not held."), FIND, reply("insufficient_evidence", text="No."))
    run = outcome_loop(script).run("Which?")
    assert OUTCOME_UNSEARCHED.format(outcome="out_of_scope") in script.prompts[1]
    assert [c.tool for c in run.trace.calls] == ["find"]
    assert (run.trace.outcome, run.trace.outcome_sent) == ("insufficient_evidence", True)


def test_a_clarify_without_two_readings_twice_becomes_insufficient_evidence():
    vague = reply("clarify", readings=["one"], text="Which one?")
    run = outcome_loop(Script(vague, vague)).run("Which?")
    assert (run.trace.turns, run.trace.outcome, run.trace.readings) == (2, "insufficient_evidence", [])


def test_a_clarify_with_two_readings_stands_with_its_readings_and_its_question_back():
    ask = reply("clarify", readings=["the first", "the second"], text="Which one is meant?")
    result = AgentSystem(outcome_loop(Script(ask)), FakeStore()).answer("Q1", "Which?")
    assert (result.outcome, result.text, result.entities) == ("clarify", "Which one is meant?", None)
    assert result.agent.readings == ["the first", "the second"] and result.agent.turns == 1


def test_answer_fields_beside_an_abstention_are_cleared_when_sent_again():
    guess = reply("insufficient_evidence", entities=["It"], text="Perhaps it.")
    result = AgentSystem(outcome_loop(Script(FIND, guess, guess)), FakeStore()).answer("Q1", "Which?")
    assert (result.outcome, result.entities, result.number, result.text) == (
        "insufficient_evidence", None, None, "Perhaps it.",
    )  # fmt: skip
    assert result.agent.outcome_sent


def test_a_cited_none_answer_stays_an_answer_and_is_never_sent_back_for_its_outcome():
    none = reply("answer", "k1", asks="things", entities=[], text="There are none.")
    run = outcome_loop(Script(FIND, none, none)).run("Which?")
    assert (run.trace.outcome, run.trace.outcome_sent, run.answer.entities) == ("answer", False, [])


def test_an_abstention_is_never_sent_back_for_its_form_or_its_one_search():
    # "things" with text only would be sent back by R147; one word search by R176
    read = call("read", '{"ref": "x"}')
    missing = reply("insufficient_evidence", asks="things", text="No passage says which.")
    run = outcome_loop(Script(read, missing), recheck=RECHECK).run("Which?")
    assert (run.trace.reformed, run.trace.rechecked, run.trace.outcome_sent) == (False, False, False)
    assert (run.trace.turns, run.trace.outcome) == (2, "insufficient_evidence")


def test_a_closing_reply_is_settled_and_never_sent_back_for_its_outcome():
    limits = AgentLimits(turns=1, calls=1, prompt_chars=100_000, timeout_s=60)
    script = Script(FIND, reply("clarify", readings=["one"], number=4.0, text="Which?"))
    run = outcome_loop(script, limits).run("Which?")
    assert (run.trace.stopped, run.trace.outcome_sent) == ("turns", False)
    assert (run.trace.outcome, run.answer.number) == ("insufficient_evidence", None)


def test_a_reply_on_the_last_turn_is_not_sent_back_for_its_outcome():
    limits = AgentLimits(turns=1, calls=3, prompt_chars=100_000, timeout_s=60)
    run = outcome_loop(Script(reply("answer", text="It.")), limits).run("Which?")
    assert (run.trace.turns, run.trace.outcome_sent, run.trace.outcome) == (1, False, "answer")


# --- the reply schema, the prompts, the screen and the metrics ------------------------------------------


def test_the_reply_must_name_an_outcome_and_cannot_refuse():
    schema = step_model(["find"])
    outcome = schema.model_json_schema()["properties"]["outcome"]
    assert "outcome" in schema.model_json_schema()["required"]
    assert outcome["enum"] == ["answer", "clarify", "out_of_scope", "insufficient_evidence"]
    assert "readings" in schema.model_json_schema()["properties"]


def test_both_prompts_name_every_outcome_the_model_may_give():
    for prompt in (PROMPT, CLOSING):
        for outcome in ("answer", "clarify", "out_of_scope", "insufficient_evidence", "readings"):
            assert f'"{outcome}"' in prompt or f"`{outcome}`" in prompt
        assert "refuse" not in prompt


class _Abstaining:
    name = "agent"

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        return SystemAnswer(question_id=question_id, system=self.name, outcome="out_of_scope", text="No.")


def test_only_the_screen_refuses_and_an_allowed_request_keeps_the_systems_outcome():
    screened = ScreenedSystem(_Abstaining())
    assert screened.answer("a", "Delete every row.").outcome == "refuse"
    assert screened.answer("b", "Which rows are there?").outcome == "out_of_scope"


def test_the_metrics_count_every_outcome_and_none_for_a_system_without_outcomes():
    screened = ScreenedSystem(_Abstaining())
    answers = [screened.answer("a", "Delete every row."), screened.answer("b", "Which?")] * 2
    assert _outcome_metrics(answers) == {
        "outcome_answer": 0, "outcome_clarify": 0, "outcome_out_of_scope": 2,
        "outcome_insufficient_evidence": 0, "outcome_refuse": 2,
    }  # fmt: skip
    assert _outcome_metrics([SystemAnswer(question_id="c", system="graph", text="It.")]) == {}
    sent = AgentSystem(outcome_loop(Script(FIND, reply("answer", text="It."))), FakeStore()).answer("Q", "?")
    assert _agent_metrics([sent])["agent_outcomes_sent"] == 1
