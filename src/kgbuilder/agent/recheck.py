"""The single-search recheck (R176): a set answer resting on one word search is sent back once, the tools
open, so the model searches the idea's other wordings or reads the document before it answers.

Role in the pipeline: the loop (loop.py) asks this rule about every answer the model gives while a turn is
left, as it asks `AgentStep.is_empty` (R138) and `AgentStep.misses_form` (R147); when the rule applies, the
loop writes its message back as that turn's result and asks again.
Design: code decides when the model is asked to look further; the model decides what it finds. The rule
fires for an answer declaring "things" whose loop ran exactly one call of the word-search tool and none of
the tools that search by meaning or read a document, and whose every cited id that one call returned: the
answer rests on one literal wording. R173's ND11 searched ["R44", "rolled"], found one accident and answered
with nine calls unused, where the other accident says "dynamic rollover"; R166's LC08 ("fog bell"), R157's
FB38 ("wobble") and HB41 ("notified") stopped the same way. Once per question (loop.py's flag), and the same
answer sent again stands, so a complete search costs one turn and is never forced to find more. The tools'
names are given at the composition root (pipeline/qa_systems.py), so the loop stays ignorant of its tools
and a test names fakes. Strategy pattern: the loop holds one rule, or none.
Not here: the loop's turn accounting (loop.py), the message's wording (prompt.py).
"""

from ..query.answers import AgentCall
from .prompt import RECHECK_ANSWER, AgentStep


class SingleSearchRecheck:
    """The rule: `search` is the word-search tool's name, `wider` the names of the tools that search by
    meaning or read a document (any one of them run means the model already looked further)."""

    def __init__(self, search: str, wider: tuple[str, ...]):
        self._search = search
        self._wider = wider

    def applies(self, step: AgentStep, calls: list[AgentCall]) -> AgentCall | None:
        """The one search `step` rests on, when the rule fires; None otherwise. Only run calls count: a
        refused call read nothing."""
        if step.asks != "things":
            return None
        run = [c for c in calls if not c.refused]
        searches = [c for c in run if c.tool == self._search]
        if len(searches) != 1 or any(c.tool in self._wider for c in run):
            return None
        returned = set(searches[0].returned)
        if any(c.id not in returned for c in step.citations):
            return None
        return searches[0]

    def message(self, search: AgentCall) -> str:
        """`RECHECK_ANSWER` for the one search the answer rests on."""
        return RECHECK_ANSWER.format(
            search=search.tool, arguments=search.arguments, wider=", ".join(self._wider)
        )
