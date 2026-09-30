"""The router: the model labels a question exact or retrieval; code keeps a fallback when exact fails.

Role in the pipeline: the first step of the graph system (systems.py `RoutedGraph`); its label is scored
against the gold set's expected route (`route_accuracy`, validation/qa.py).
Design: the LLM proposes the route; the graph system still falls back to retrieval whenever the exact route
cannot answer, so a wrong label costs an extra step, not an answer.
Not here: the routes themselves (exact.py, systems.py).
"""

from typing import Literal

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..validation.qa_gold import Route

# The router's prompt: the two routes described by what they can do, in domain-neutral words (the question
# types of the benchmark are not listed, so the router is not tuned to them). The schema tells the model
# which record fields exist, so "filter on a field" can be judged.
PROMPT = """Decide how a question about the graph below is best answered.

- "exact": a database query answers it: it asks to count, to rank, to list every thing that meets a
  condition, or to filter on a field of the structured records.
- "retrieval": reading a few passages of text answers it: it asks what the texts say about something, or
  how, why or when something happened.

<graph>
{schema}
</graph>

<question>{question}</question>"""


class RouteChoice(BaseModel):
    route: Literal["exact", "retrieval"] = Field(description="How the question is best answered.")
    reason: str = Field(description="One sentence on why.")


class Router:
    """Labels questions with one model call each."""

    def __init__(self, llm: LLMClient, model: str, schema_text: str, temperature: float = 0.0):
        self._llm = llm
        self._model = model
        self._schema_text = schema_text
        self._temperature = temperature

    def route(self, question: str) -> Route:
        """The route the model chose. Raises `LLMResponseError` when the model keeps failing."""
        choice = self._llm.generate(
            PROMPT.format(schema=self._schema_text, question=question),
            RouteChoice,
            model=self._model,
            temperature=self._temperature,
        )
        return Route(choice.route)
