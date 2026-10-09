"""The one interface every tool of the agent has (R133): a name, what it does, its arguments and `run`.

Role in the pipeline: the agent loop (R135) lists the tools to the model by name, description and the
arguments' JSON schema, parses each call it proposes into the tool's arguments model (code refuses a call
that does not parse), runs it, and keeps the ids the result offers for citation.
Design: Strategy, one class per tool (graph_tools.py, text_tools.py; the structured tools in R134). The
description and the arguments' field descriptions are text the model reads, so they follow the
`prompt-engineering` skill: structural words only, no word of a dataset under evaluation. Every tool caps
its own result where it is built, so no call can return a hub's whole neighbourhood.
Not here: the loop, its caps on steps, calls, tokens and cost, and the citation check (R135).
"""

from typing import Protocol

from pydantic import BaseModel

from .results import ToolResult


class Tool(Protocol):
    """A tool the agent may call."""

    name: str  # how the model calls it
    description: str  # what it does and when to use it, as the model reads it
    arguments: type[BaseModel]  # the call's arguments; their field descriptions are part of what it reads

    def run(self, arguments: BaseModel) -> ToolResult:
        """The tool's result for parsed `arguments` (an instance of `arguments`); a ref that names nothing
        is answered with a note in the result, never with an exception."""
        ...
