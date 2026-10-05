"""The exact route (text2cypher): the model writes one Cypher query, code checks it, the graph answers.

Role in the pipeline: since R74 the logged fallback of the plan systems (systems.py `PlanSystem`), used
when no plan could run; when it cannot answer either, the question falls back to reading.
Design: the LLM proposes, code decides (fixed decision 6). A proposal is checked by its text
(cypher_check.py) and by the database's plan (`CypherStore.explain`), and runs only in a read transaction
with a timeout. A rejected or failing query gets one retry with the reasons; a second failure gives up.
The answer is the rows, read by code: the first column's distinct values when the question asks which
things, its single number when it asks how many. The same route serves the records-plus-vector system
(R73) over the record layer alone: its prompt leaves out the text and claim layer, its schema shows only
the plan's labels, and code refuses any query that names a label or type outside them.
Not here: the query plans (plan.py), the retrieval route (systems.py), the checks' rules (cypher_check.py).
"""

import math
from typing import Literal

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from .answers import ExactAttempt, ExactTrace
from .cypher_check import check_text, excluded_name_issues
from .graph_schema import GraphSchema
from .graph_store import CypherStore

# The text2cypher prompt. Rule by rule: the schema lists what exists, and the database refuses anything
# else (EXPLAIN), so the model is told to keep to it; values as parameters is the injection rule code
# enforces; the type rule because a year stored as an INTEGER never equals '2015' and a DATE has no text
# prefix (R71's held-out run: five filters ran and returned nothing); case-insensitive CONTAINS and the
# aliases are how the graph's names are written (entity
# resolution keeps other spellings as aliases); the first column is what code reads as the answer. The
# paragraph on the pipeline's fixed nodes describes this project's graph shape, the same for any dataset.
_TASK = """You write one read-only Cypher query for Neo4j that answers a question from the graph below.

"""
_TEXT_LAYER = """\
The graph holds structured records (the labels of the domain) and what documents state about them:
- (:Document)<-[:PART_OF]-(:Chunk {{chunk_id}}); a chunk or a document may be ABOUT a record.
- Each claim a text makes is an (:Observation {{predicate, polarity, subject_name, object_name}}) with
  [:SUBJECT] and [:OBJECT] to (:Entity {{name, type, aliases}}) nodes and [:FROM] to its (:Chunk). The record
  a claim is about has [:HAS_OBSERVATION] to it. An :Entity may [:REFERS_TO] the record it names.
- `polarity` is "positive", "negative" or "neutral": the claim's tone.

"""
# The record layer alone (R73, records plus vector RAG): one sentence in place of the text layer's paragraph
_RECORD_LAYER = """The graph holds structured records: the labels, relationships and fields listed below.

"""
_RULES = """{schema}

Rules:
- Use only the labels, relationship types and property keys listed above.
- Pass every value you compare against as a parameter (`$name`) and give it in `parameters`; never quote
  a text value inside the query.
- Compare with the property's own type, shown in brackets: give a number or true/false as such, not as
  text; compare a DATE with `date($p)` and `$p` as 'YYYY-MM-DD', or its year with `.year`.
- Compare names case-insensitively and allow other wordings: `toLower(x) CONTAINS toLower($p)`, and look
  in `aliases` too where a node has them.
- Return the answer in the first column: the names of the things when the question asks which things
  (`answer_form` "entities"), or one number when it asks how many or how much (`answer_form` "number").
- Read only: no clause that writes, no CALL, no LOAD.

<question>{question}</question>"""
PROMPT = _TASK + _TEXT_LAYER + _RULES  # the graph system's prompt (R71)
RECORDS_PROMPT = _TASK + _RECORD_LAYER + _RULES  # the records-plus-vector system's prompt (R73)

# Appended for the one retry: the refused query and the reasons, so the model can repair it.
RETRY = """

Your previous query was refused:
<query>{cypher}</query>
Reasons: {reasons}
Write a corrected query."""


# A parameter's value in the type the property holds. bool comes first: pydantic would otherwise read a
# true as the number 1, and an INTEGER year given as 2015 stays an int.
ParameterValue = bool | int | float | str | list[str]


class CypherParameter(BaseModel):
    name: str = Field(description="The parameter's name, as written after $ in the query.")
    value: ParameterValue = Field(description="The value the query compares against, in the property's type.")


class CypherProposal(BaseModel):
    """The model's query, its parameters, and what form its first column answers in."""

    cypher: str = Field(description="One read-only Cypher query.")
    parameters: list[CypherParameter] = Field(default=[], description="Every $parameter the query uses.")
    answer_form: Literal["entities", "number"] = Field(
        description="entities: the first column names the things; number: it holds one number."
    )


class ExactOutcome(BaseModel):
    entities: list[str] | None = None
    number: float | None = None
    trace: ExactTrace


class ExactRoute:
    """text2cypher with code checks and one retry, over a `CypherStore`."""

    def __init__(
        self,
        store: CypherStore,
        llm: LLMClient,
        model: str,
        limit: int,
        temperature: float = 0.0,
        *,
        schema: GraphSchema | None = None,
        prompt: str = PROMPT,
        excluded: frozenset[str] = frozenset(),
    ):
        """Reads the graph's schema once (or takes `schema`, a part of it): it goes into every prompt.
        `excluded` names the labels and relationship types a query may not use (cypher_check.py)."""
        self._store = store
        self._llm = llm
        self._model = model
        self._limit = limit
        self._temperature = temperature
        self._prompt = prompt
        self._excluded = excluded
        self.schema_text = (schema or store.schema()).text()

    def answer(self, question: str) -> ExactOutcome:
        """The rows' answer, or `answered=False` after two refused or failing proposals.

        Raises `LLMResponseError` when the model keeps failing to reply (llm/retry.py).
        """
        prompt = self._prompt.format(schema=self.schema_text, question=question)
        attempts: list[ExactAttempt] = []
        for _ in range(2):  # the proposal and one retry
            proposal = self._llm.generate(
                prompt, CypherProposal, model=self._model, temperature=self._temperature
            )
            attempt, rows = self._try(proposal)
            attempts.append(attempt)
            if not attempt.issues:
                entities, number = rows_to_answer(rows, proposal.answer_form)
                trace = ExactTrace(attempts=attempts, answered=True, rows=len(rows))
                return ExactOutcome(entities=entities, number=number, trace=trace)
            prompt += RETRY.format(cypher=attempt.cypher, reasons="; ".join(attempt.issues))
        return ExactOutcome(trace=ExactTrace(attempts=attempts, answered=False))

    def _try(self, proposal: CypherProposal) -> tuple[ExactAttempt, list[list[object]]]:
        """Check the proposal's text, then its plan, then run it; the first failure stops it."""
        parameters = {p.name: p.value for p in proposal.parameters}
        checked = check_text(proposal.cypher, parameters, self._limit)
        issues = checked.issues + excluded_name_issues(checked.cypher, self._excluded)
        issues = issues or self._store.explain(checked.cypher, parameters)
        rows: list[list[object]] = []
        if not issues:
            result = self._store.run_read(checked.cypher, parameters)
            rows = result.rows
            issues = [f"it failed when run: {result.error}"] if result.error else []
        return ExactAttempt(cypher=checked.cypher, parameters=parameters, issues=issues), rows


def rows_to_answer(
    rows: list[list[object]], form: Literal["entities", "number"]
) -> tuple[list[str] | None, float | None]:
    """The answer the rows give: the distinct first-column values as names (lists flattened, nulls
    skipped), or the first row's first value as a number (None when it is not one)."""
    first = [row[0] for row in rows if row]
    if form == "number":
        value = first[0] if first else None
        # a bool is an int in Python, but "true" is no count
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
            return None, float(value)
        return None, None
    names: list[str] = []
    for value in first:
        for item in value if isinstance(value, list) else [value]:
            if item is not None and str(item) not in names:
                names.append(str(item))
    return names, None
