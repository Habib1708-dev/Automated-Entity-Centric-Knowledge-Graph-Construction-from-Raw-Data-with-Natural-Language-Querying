"""The agent loop's observer (R221, agent/observer.py) with a scripted model and fake tools, without Neo4j or
a network: every model reply, settled call (run with its result, or refused) and send-back is reported in
the order it happens, with the turn it belongs to; the citation retry is reported as a send-back; the agent
system passes its observer to the loop; and a loop with an observer replies, calls and traces exactly as
one without."""

from kgbuilder.agent.observer import NO_OBSERVER, SendBack
from kgbuilder.agent.results import NodesResult, ToolResult
from kgbuilder.agent.system import AgentSystem
from kgbuilder.query.answers import AgentCall

from .test_agent_loop import AgentStep, Script, answer, call, loop
from .test_query import FakeStore


class Recorder:
    """An observer keeping every event as a tuple, in order."""

    def __init__(self):
        self.events: list[tuple] = []

    def model_replied(self, turn: int, ms: float) -> None:
        self.events.append(("model", turn, ms))

    def call_done(self, call: AgentCall, result: ToolResult | None) -> None:
        self.events.append(("call", call.turn, call.tool, bool(call.refused), type(result).__name__))

    def sent_back(self, turn: int, reason: SendBack) -> None:
        self.events.append(("sent_back", turn, reason))


def steps() -> Script:
    """A run, a refused call, an empty answer sent back, then the answer."""
    return Script(call("find", '{"ref": "k1"}'), call("nope", "{}"), AgentStep(action="answer"), answer("k1"))


def test_every_reply_call_and_send_back_is_reported_in_order_with_its_turn():
    ticks = iter([0.0, 0.0, 0.5, 1.0, 1.25, 2.0, 2.0, 3.0, 4.0, 9.0])  # the loop's start, each reply's two
    recorder = Recorder()
    run = loop(steps(), timer=lambda: next(ticks)).run("Which thing?", recorder)
    assert recorder.events == [
        ("model", 1, 500.0),
        ("call", 1, "find", False, "NodesResult"),
        ("model", 2, 250.0),
        ("call", 2, "nope", True, "NoneType"),
        ("model", 3, 0.0),
        ("sent_back", 3, "empty"),
        ("model", 4, 1000.0),
    ]
    assert run.trace.llm_ms == 1750.0  # the reported times add up to the trace's model time


def test_a_run_call_is_reported_with_the_result_the_tool_returned():
    results: list[ToolResult | None] = []

    class Results(Recorder):
        def call_done(self, call, result):
            results.append(result)

    loop(Script(call("find", '{"ref": "k1"}'), answer("k1"))).run("Which thing?", Results())
    [result] = results
    assert isinstance(result, NodesResult) and [n.ref for n in result.nodes] == ["k1"]


def test_the_citation_retry_is_reported_as_a_send_back_before_the_closing_reply():
    recorder = Recorder()
    script = Script(call("find", '{"ref": "k1"}'), answer("k1", "made-up"), answer("k1"))
    loop(script).run("Which thing?", recorder)
    assert [e[0] for e in recorder.events] == ["model", "call", "model", "sent_back", "model"]
    assert recorder.events[3] == ("sent_back", 2, "citations")


def test_a_loop_with_an_observer_runs_and_traces_exactly_as_one_without():
    watched, plain = steps(), steps()
    with_observer = loop(watched, timer=lambda: 0.0).run("Which thing?", Recorder())
    without = loop(plain, timer=lambda: 0.0).run("Which thing?", NO_OBSERVER)
    assert watched.prompts == plain.prompts
    assert with_observer.answer == without.answer

    def untimed(trace):  # a call's own latency is measured on the wall clock, not the injected timer
        calls = [c.model_copy(update={"latency_ms": 0}) for c in trace.calls]
        return trace.model_copy(update={"calls": calls})

    assert untimed(with_observer.trace) == untimed(without.trace)


def test_the_agent_system_passes_its_observer_to_the_loop():
    recorder = Recorder()
    system = AgentSystem(loop(Script(call("find", '{"ref": "k1"}'), answer("k1"))), FakeStore())
    assert system.answer("Q1", "Which thing?", recorder).agent is not None
    assert [e[0] for e in recorder.events] == ["model", "call", "model"]
