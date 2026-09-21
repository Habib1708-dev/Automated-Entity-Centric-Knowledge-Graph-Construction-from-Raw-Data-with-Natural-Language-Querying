"""Propose a construction plan with an LLM, checked by code first and an LLM critic second."""

from typing import Literal

from pydantic import BaseModel

from . import llm
from .plan import ConstructionPlan, validate_plan
from .profiler import DataProfile

PROPOSER_PROMPT = """You are an expert at knowledge graph modeling with property graphs.
Design construction rules that turn the CSV files below into a graph serving the user's goal.

<goal>
{goal}
</goal>

The data profile below was computed exactly from the files. Trust it over your intuition:
- `is_unique` tells you which columns can be node keys. Never use a non-unique column as `unique_column`.
- `foreign_keys` lists columns whose values are contained in another file's unique column.
  Candidates with `name_match: false` on small integer columns are usually coincidences.

<profile>
{profile}
</profile>

Modeling rules:
- A file with one unique identifier is a node. Its foreign key columns become relationships
  (source_file is that same file, from_column is its own key, to_column is the foreign key column).
- A file with no unique identifier and two foreign keys is a relationship file; its other columns
  are relationship properties.
- Do not import foreign key columns as node properties.
- The schema must be one connected graph. Skip files that are irrelevant to the goal.
- No two relationships between the same pair of labels may be inverses or synonyms of each other.

{feedback}"""

CRITIC_PROMPT = """You are reviewing a proposed knowledge graph construction plan.
The plan already passed mechanical checks (columns exist, keys are unique, graph is connected),
so judge only the modeling:
- Could any node really be a relationship, or the reverse?
- Are relationship directions and types natural for the goal?
- Are obvious relationships from the foreign key candidates missing?
- Are any relationships redundant?
- Can the goal's typical questions be answered by traversing this schema?

Reply "retry" only for problems that would change the plan; otherwise "valid".

<goal>
{goal}
</goal>

<profile>
{profile}
</profile>

<plan>
{plan}
</plan>"""


class Critique(BaseModel):
    verdict: Literal["valid", "retry"]
    issues: list[str]


class SchemaResult(BaseModel):
    plan: ConstructionPlan
    rounds: int
    accepted: bool  # False when max_rounds ran out with open issues
    open_issues: list[str]


def propose_plan(goal: str, profile: DataProfile, max_rounds: int = 3, use_critic: bool = True) -> SchemaResult:
    profile_json = profile.model_dump_json(indent=1)
    feedback, plan, issues = "", None, []

    for round_number in range(1, max_rounds + 1):
        prompt = PROPOSER_PROMPT.format(goal=goal, profile=profile_json, feedback=feedback)
        plan = llm.generate(prompt, ConstructionPlan)

        issues = validate_plan(plan, profile)
        if not issues and use_critic:
            critique = llm.generate(
                CRITIC_PROMPT.format(goal=goal, profile=profile_json, plan=plan.model_dump_json(indent=1)),
                Critique,
            )
            issues = critique.issues if critique.verdict == "retry" else []
        if not issues:
            return SchemaResult(plan=plan, rounds=round_number, accepted=True, open_issues=[])

        feedback = (
            "Your previous plan is below, followed by the problems found in it. Fix every problem.\n"
            f"<previous_plan>\n{plan.model_dump_json(indent=1)}\n</previous_plan>\n"
            "<problems>\n" + "\n".join(f"- {i}" for i in issues) + "\n</problems>"
        )

    return SchemaResult(plan=plan, rounds=max_rounds, accepted=False, open_issues=issues)
