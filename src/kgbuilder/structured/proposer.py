"""Propose a construction plan with an LLM, checked by code first and by an LLM critic second.

Role in the pipeline: `kg plan`. Input is the exact data profile; output is a `ConstructionPlan` that a
human reviews in `out/plan.json` before `kg build` executes it.
Design: the retry loop is `llm.refine.refine` (Template Method); this module only supplies the prompts
and the three steps. The LLM never sees raw data, only the profile computed by DuckDB.
Not here: plan validation rules (plan.py) and execution (importer.py).
"""

from ..llm.base import LLMClient
from ..llm.refine import Critique, Refinement, refine
from .plan import ConstructionPlan, validate_plan
from .profiler import DataProfile

# The proposer is told to trust the profile because models otherwise "recognise" id columns by name and
# pick non-unique keys. The modeling rules encode the two table shapes (entity table, link table) and the
# failure modes seen in practice: FK columns duplicated as properties, disconnected islands, inverse pairs.
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

# The critic is told what code already guarantees, so it spends its judgement on modeling only, and it
# must answer "valid" unless a problem would change the plan; otherwise critics nitpick forever.
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


def propose_plan(
    goal: str,
    profile: DataProfile,
    llm: LLMClient,
    model: str,
    temperature: float = 0.0,
    max_rounds: int = 3,
    use_critic: bool = True,
) -> Refinement[ConstructionPlan]:
    """Ask `llm` for a plan until it passes `validate_plan` and the critic, or `max_rounds` is used up."""
    profile_json = profile.model_dump_json(indent=1)

    def propose(feedback: str) -> ConstructionPlan:
        prompt = PROPOSER_PROMPT.format(goal=goal, profile=profile_json, feedback=feedback)
        return llm.generate(prompt, ConstructionPlan, model=model, temperature=temperature)

    def critique(plan: ConstructionPlan) -> list[str]:
        prompt = CRITIC_PROMPT.format(goal=goal, profile=profile_json, plan=plan.model_dump_json(indent=1))
        reply = llm.generate(prompt, Critique, model=model, temperature=temperature)
        return reply.issues if reply.verdict == "retry" else []

    return refine(
        propose,
        validate=lambda plan: validate_plan(plan, profile),
        critique=critique if use_critic else None,
        max_rounds=max_rounds,
    )
