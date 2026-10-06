"""Frozen query decisions (R80): the plans and text2cypher queries of an earlier `kg qa` run, replayed on a
changed graph instead of being written anew.

Role in the pipeline: `kg qa --plans DIR` (pipeline/qa_stages.py) reads `DIR/answers_<system>.jsonl` into
one `FrozenQuery` per question; the plan system (systems.py) runs them.
Why: the planner's prompt carries the graph's schema text, so a rebuilt graph misses the planner cache and
every question is planned anew. In R77 parts b and f every changed answer came with a new plan, and the
graph change could not be told apart from the new plans. With the queries frozen only execution reruns:
the graph's content, read_check and the reader.
Not here: running the queries (systems.py, exact.py), the answers file format (answers.py).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..core.errors import FrozenPlansError
from .answers import ExactAttempt, SystemAnswer, load_system_answers
from .plan import QueryPlan


class FrozenQuery(BaseModel):
    """How one question was answered before: the plan that ran to its end, else the text2cypher query that
    answered, else neither (the question was read from text)."""

    plan: QueryPlan | None = None
    cypher: ExactAttempt | None = None
    # the form the query's first column answers in; the answers file keeps it only implicitly (below)
    answer_form: Literal["entities", "number"] = "entities"


def frozen_query(answer: SystemAnswer) -> FrozenQuery:
    """The query decisions of one plan-system answer. Raises `FrozenPlansError` when it has no plan trace
    (an answer of the vector baseline, or of a run before R74)."""
    if answer.plan is None:
        raise FrozenPlansError([f"{answer.question_id}: the answer has no plan trace to freeze"])
    ran = [attempt.plan for attempt in answer.plan.attempts if attempt.steps and not attempt.issues]
    if ran:
        return FrozenQuery(plan=ran[-1])
    if answer.plan.fallback == "text2cypher" and answer.exact is not None and answer.exact.answered:
        # the answered query is the last attempt; its form is not stored, but code wrote `entities` (a list,
        # maybe empty) exactly when the form was "entities" (exact.rows_to_answer), so it is recovered exactly
        form = "entities" if answer.entities is not None else "number"
        return FrozenQuery(cypher=answer.exact.attempts[-1], answer_form=form)
    return FrozenQuery()


def load_frozen(path: Path, system: str, question_ids: list[str]) -> dict[str, FrozenQuery]:
    """Question id -> its frozen query, from an answers file of `system`. Raises `FrozenPlansError` naming
    every line of another system and every question the file does not answer: a frozen run that planned
    some questions anew would mix the two kinds of change again."""
    answers = load_system_answers(path)
    issues = [
        f"{a.question_id}: answered by '{a.system}', not '{system}'" for a in answers if a.system != system
    ]
    by_id = {a.question_id: a for a in answers}
    issues += [f"{q}: not in {path}" for q in question_ids if q not in by_id]
    if issues:
        raise FrozenPlansError(issues)
    return {q: frozen_query(by_id[q]) for q in question_ids}
