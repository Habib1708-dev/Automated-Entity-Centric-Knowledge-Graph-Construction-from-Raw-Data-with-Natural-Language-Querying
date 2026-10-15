"""The outcome checks (R204): code's side of the agent's `outcome` (answer, clarify, out_of_scope,
insufficient_evidence), so the label the model proposes is borne out by what the loop saw.

Role in the pipeline: the loop (loop.py) asks `OutcomeCheck` about every reply it is about to accept with
the tools open, as it asks `AgentStep.is_empty` (R138), and writes the check's message back once as that
turn's result; every final reply, given with the tools open or closed, is then `settle`d, and the settled
outcome is what `kg qa` scores (validation/qa.py) and the metrics count (pipeline/qa_stages.py). A refusal
is not here: only the security screen refuses (query/screen.py), before any model is called.
Design: the LLM proposes, code decides. The model reads the question's meaning (is it ambiguous, is its
subject in the dataset); code cannot, but it can check what each label implies:
- "answer" rests on results: it cites at least one id a tool returned;
- a label other than "answer" gives no answer: no entity is named and no number given;
- "clarify" names at least two different readings, the readings the question back asks between;
- "out_of_scope" and "insufficient_evidence" follow at least one tool call that ran: neither can be known
  before the dataset is searched.
A reply failing a check is sent back once with the way out named (prompt.py's `OUTCOME_*`); what still
fails then is settled by `settle`, never sent back again, so the loop cannot spin: a "clarify" without two
readings becomes "insufficient_evidence" (the model found no answer and could not say why it needs one),
and answer fields beside another label are cleared (the model's own "cannot answer" outweighs a guess). An
uncited answer and an abstention without a search stand on the second reply: a right answer resting on
a result cited by its chunk alone, or an off-topic question needing no search, is not overturned by code.
Strategy pattern: the loop holds one check, or none (a test loop, or a loop built before R204), while
`settle` always applies, so a reply without an outcome reads as "answer" as before R204.
Not here: the messages' wording (prompt.py), the turn accounting (loop.py), the citation check
(citations.py).
"""

from ..query.answers import AgentOutcome
from .prompt import OUTCOME_FIELDS, OUTCOME_READINGS, OUTCOME_UNCITED, OUTCOME_UNSEARCHED, AgentStep


def outcome_of(step: AgentStep) -> AgentOutcome:
    """The outcome a reply claims: its own, or "answer" when it gives none (an older reply, a test)."""
    return step.outcome or "answer"


def readings_of(step: AgentStep) -> list[str]:
    """The reply's readings, each once (compared without case or edge spaces), blank ones left out."""
    seen: dict[str, str] = {}
    for reading in step.readings or []:
        if reading.strip():
            seen.setdefault(reading.strip().casefold(), reading.strip())
    return list(seen.values())


class OutcomeCheck:
    """The checks a reply's outcome must pass before the loop accepts it with the tools open."""

    def problem(self, step: AgentStep, cited: list[str], calls_run: int) -> str | None:
        """The message `step` is sent back with, or None when its outcome stands. `cited` holds the ids it
        cites that a tool returned; `calls_run` the tool calls the loop ran so far (refused ones aside)."""
        outcome = outcome_of(step)
        if outcome == "answer":
            return None if cited else OUTCOME_UNCITED
        # an empty `entities` list names nothing, so it is no answer beside an abstention
        if step.entities or step.number is not None:
            return OUTCOME_FIELDS.format(outcome=outcome)
        if outcome == "clarify":
            return OUTCOME_READINGS if len(readings_of(step)) < 2 else None
        return OUTCOME_UNSEARCHED.format(outcome=outcome) if calls_run == 0 else None


def settle(step: AgentStep) -> AgentStep:
    """The final reply with its outcome written out and made consistent: a "clarify" naming fewer than two
    readings becomes "insufficient_evidence"; a reply with a label other than "answer" keeps no entity, no
    number and (unless it is a "clarify") no readings. An "answer" keeps everything it gives."""
    outcome = outcome_of(step)
    readings = readings_of(step)
    if outcome == "clarify" and len(readings) < 2:
        outcome = "insufficient_evidence"
    if outcome == "answer":
        return step.model_copy(update={"outcome": outcome})
    kept = readings if outcome == "clarify" else None
    return step.model_copy(update={"outcome": outcome, "entities": None, "number": None, "readings": kept})
