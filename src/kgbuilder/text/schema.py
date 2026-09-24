"""The text schema: which entity types and fact types may be extracted from the documents.

Role in the pipeline: `kg text-schema`. The LLM proposes the schema from the text (every chunk, whole, while
the corpus fits a character budget, else an even sample: `select_context`) and the domain graph's node
descriptions; a human reviews `out/text_schema.json`; `kg extract` is then constrained to it.
Design: same shape as the structured path: pydantic models are the LLM's response schema,
`validate_text_schema` is the code gate, and the retry loop is `llm.refine.refine` with a critic pass.
Not here: extraction itself (extraction.py).
"""

import re

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ..llm.refine import Critique, Refinement, refine
from ..structured.plan import ConstructionPlan
from .chunking import Chunk

_PASCAL_CASE = re.compile(r"[A-Z][A-Za-z0-9]*")  # Product, SubAssembly
_UPPER_SNAKE_CASE = re.compile(r"[A-Z][A-Z0-9_]*")  # HAS_PROBLEM


class EntityType(BaseModel):
    name: str = Field(description="PascalCase type, e.g. Component")
    description: str = Field(description="One sentence saying what counts as this type, and what does not")


class FactType(BaseModel):
    predicate: str = Field(description="UPPER_SNAKE_CASE relationship, e.g. CAUSES_PROBLEM_WITH")
    subject_type: str
    object_type: str
    description: str
    # A derived fact type is computed by code in the link stage (resolution/derivation.py) from what the
    # document is about, and is never shown to the extractor: the model used to spend more than half of
    # its output on "part X is part of the product" facts that the document title already states.
    derived: bool = Field(
        default=False,
        description="True only for a relation that follows from the document itself (a named part belongs "
        "to the product the document is about); code derives it and the extractor never sees it",
    )


class PathStep(BaseModel):
    """One fact type on the way from what a goal question starts with to what it asks for."""

    subject_type: str
    predicate: str
    object_type: str


class GoalQuestion(BaseModel):
    """A question the goal asks of the text, with the chain of fact types that answers it (R56)."""

    question: str = Field(description="A concrete question the user's goal asks of this text")
    path: list[PathStep] = Field(
        description="The fact types, in order, that lead from what the question starts with to what it asks "
        "for; consecutive steps share an entity type"
    )


class TextSchema(BaseModel):
    # first, so that the model writes the questions before it designs the types (R56: goal-first design);
    # empty in the reviewed schemas pinned before R56, which stay valid
    goal_questions: list[GoalQuestion] = Field(
        default=[],
        description="3 to 6 questions of the goal, each with the path of fact types that answers it",
    )
    entity_types: list[EntityType]
    fact_types: list[FactType]

    def entity_names(self) -> set[str]:
        return {e.name for e in self.entity_types}

    def find(self, subject_type: str, predicate: str, object_type: str) -> FactType | None:
        """The fact type with this signature, derived or not; None when the schema has none."""
        return next(
            (
                f
                for f in self.fact_types
                if f.predicate == predicate
                and f.subject_type == subject_type
                and f.object_type == object_type
            ),
            None,
        )

    def allows(self, subject_type: str, predicate: str, object_type: str) -> bool:
        """True when the signature is one of the schema's fact types, derived or not: a stored fact may be
        either."""
        return self.find(subject_type, predicate, object_type) is not None

    def allows_extraction(self, subject_type: str, predicate: str, object_type: str) -> bool:
        """True when the extractor may return this signature: in the schema and not derived by code."""
        fact = self.find(subject_type, predicate, object_type)
        return fact is not None and not fact.derived

    def extractable(self) -> list[FactType]:
        """The fact types the extractor is asked for."""
        return [f for f in self.fact_types if not f.derived]

    def derived(self) -> list[FactType]:
        """The fact types code derives in the link stage."""
        return [f for f in self.fact_types if f.derived]


# The domain graph summary is passed in so that a type like "Assembly" keeps the meaning it has in the
# structured data. The prompt names no example entity types on purpose: the system must work for any data,
# and an example list steers the model. Until R14 it listed "products, parts, problems, materials, locations,
# people/roles", and the stronger models then extracted every reviewer and their city (70 of ~150 facts on
# data/). Relevance is decided by the goal alone, which the user states per run; the text stays in the
# lexical graph, so facts a schema leaves out can still be extracted later under another goal.
# R56, goal first: until R55 the goal was one rule ("name a question each fact type helps answer"), which
# an indirect design passes, while "reuse the domain concepts" came first. On the held-out data the
# proposer made the plan's record node (Complaint) the hub of every fact, so a problem reached its part only
# through the report that mentions both (R53, R55). Now the model writes the goal's questions and the path
# of fact types that answers each (checked in code, `_goal_path_issues`), and one general rule keeps the
# source of a text out of the paths: provenance is already stored (chunk, evidence) outside the schema.
# Disclosed: the rule was motivated by a held-out failure; it names no domain and is measured on furniture
# before one held-out run.
PROMPT = """You design the schema for extracting knowledge from unstructured text into a knowledge graph.
The user's goal decides the design: the graph must answer the goal's questions by following facts.

<goal>
{goal}
</goal>

The graph already contains this structured (domain) data. Name an entity type like a domain node when the
text talks about the same things, so that code can link the two; keep the meaning of each type distinct:
<domain_graph>
{domain}
</domain_graph>

Representative text chunks:
<chunks>
{chunks}
</chunks>

Work in this order:
1. goal_questions: write the 3 to 6 concrete questions the goal asks of this text.
2. For each question, its path: the fact types, in order, that lead from what the question starts with to
   what it asks for. Consecutive steps share an entity type.
3. Define the entity types and fact types the paths use, plus any fact type the text states often and the
   goal needs.

Rules:
- A path follows what the text states. When the text says that something happens to or in a thing,
  connect the two directly; do not route the connection through the source the text comes from (a
  document, a report, a message, a post): where a statement comes from is recorded outside the schema.
- Do not make types for ratings, dates or generic adjectives.
- Every fact type must reference only entity types you defined. Keep both lists small and precise
  (at most 12 entity types and 20 fact types).

{feedback}"""

# The critic checks what code cannot: whether types overlap in meaning, whether the facts serve the goal,
# and whether the sample text actually supports them. Naming and references are already verified in code.
CRITIC_PROMPT = """You are reviewing a proposed schema for extracting facts from text into a knowledge graph.
The schema already passed mechanical checks (naming, no duplicates, fact types reference defined entity
types), so judge only the modeling:
- Do two entity types overlap so that an extractor could not choose between them?
- Does any entity type clash in meaning with a node of the domain graph that has the same name?
- Are there fact types the sample text clearly supports and the goal needs, but that are missing?
- Are there fact types the sample text gives no evidence for?
- Is there a fact type that answers none of the goal's questions?
- Does a goal question's path pass through the source a text comes from (a document, a report, a message)
  where the text itself connects the two things directly?

Reply "retry" only for problems that would change the schema; otherwise "valid".

<goal>
{goal}
</goal>

<domain_graph>
{domain}
</domain_graph>

<chunks>
{chunks}
</chunks>

<schema>
{schema}
</schema>"""


def validate_text_schema(schema: TextSchema) -> list[str]:
    """Check naming, duplicates and references. Returns a list of problems; empty means valid."""
    issues = []
    names = [e.name for e in schema.entity_types]
    for name in sorted({n for n in names if names.count(n) > 1}):
        issues.append(f"entity type '{name}' defined more than once")
    for name in names:
        if not _PASCAL_CASE.fullmatch(name):
            issues.append(f"entity type '{name}' must be PascalCase")

    seen = set()
    for fact in schema.fact_types:
        if not _UPPER_SNAKE_CASE.fullmatch(fact.predicate):
            issues.append(f"predicate '{fact.predicate}' must be UPPER_SNAKE_CASE")
        for type_name in (fact.subject_type, fact.object_type):
            if type_name not in names:
                issues.append(f"fact {fact.predicate}: entity type '{type_name}' is not defined")
        key = (fact.subject_type, fact.predicate, fact.object_type)
        if key in seen:
            issues.append(f"fact {key} defined more than once")
        seen.add(key)

    if not schema.entity_types:
        issues.append("no entity types")
    if not schema.fact_types:
        issues.append("no fact types")
    return issues + _goal_path_issues(schema)


# A longer chain is no longer "following what the text states": four steps reach from a thing through its
# problem and part to a record about the part, which is the longest route the thesis goals need.
_MAX_PATH_STEPS = 4


def _goal_path_issues(schema: TextSchema) -> list[str]:
    """Every goal question's path must be a chain of the schema's own fact types (R56).

    A step names a fact type by its signature; consecutive steps share an entity type (in either
    direction, since a question may follow a fact backwards). The model's claim that its schema answers a
    question is thereby checked in code, not taken on trust.
    """
    issues = []
    for q in schema.goal_questions:
        if not q.path:
            issues.append(f"goal question '{q.question}': the path is empty")
            continue
        if len(q.path) > _MAX_PATH_STEPS:
            issues.append(f"goal question '{q.question}': the path has more than {_MAX_PATH_STEPS} steps")
        for step in q.path:
            if schema.find(step.subject_type, step.predicate, step.object_type) is None:
                signature = f"{step.subject_type} -[{step.predicate}]-> {step.object_type}"
                issues.append(f"goal question '{q.question}': {signature} is not a fact type of the schema")
        for before, after in zip(q.path, q.path[1:], strict=False):
            if not {before.subject_type, before.object_type} & {after.subject_type, after.object_type}:
                gap = f"step {before.predicate} does not connect to {after.predicate}"
                issues.append(f"goal question '{q.question}': {gap}")
    return issues


def _proposal_issues(schema: TextSchema) -> list[str]:
    """What a proposal must satisfy beyond a valid schema: it states the goal's questions and their paths."""
    missing = (
        [] if schema.goal_questions else ["no goal questions: write the goal's questions and their paths"]
    )
    return validate_text_schema(schema) + missing


def domain_summary(plan: ConstructionPlan | None) -> str:
    """The domain graph as prompt text: node labels with their descriptions, and the relationships."""
    if plan is None:
        return "(none)"
    lines = [f"- node {n.label}: {n.description}" for n in plan.nodes]
    lines += [f"- {r.from_label} -[{r.relationship_type}]-> {r.to_label}" for r in plan.relationships]
    return "\n".join(lines)


def select_context(chunks: list[Chunk], budget_chars: int) -> list[Chunk]:
    """The chunks the proposer sees: all of them, whole, when their text fits `budget_chars`; else an even
    sample of whole chunks over the corpus, as many as fit.

    R55: a fixed sample of 12 chunks, each cut at 1,200 characters, showed the proposer 17 % of the furniture
    text and 48 % of the held-out text, so a relation stated only in unseen chunks could not enter the
    schema. Small corpora now go in whole; the budget keeps a large one from overflowing the prompt.
    Deterministic (no randomness), so the prompt and its cache key are stable.
    """
    total = sum(len(c.text) for c in chunks)
    if total <= budget_chars:
        return chunks
    n = max(1, len(chunks) * budget_chars // total)
    while n > 1:
        # n comes from the average chunk; unevenly long chunks can still overshoot, so shrink until it fits
        sample = [chunks[int(i * len(chunks) / n)] for i in range(n)]
        if sum(len(c.text) for c in sample) <= budget_chars:
            return sample
        n -= 1
    return chunks[:1]


def propose_text_schema(
    goal: str,
    chunks: list[Chunk],
    llm: LLMClient,
    model: str,
    plan: ConstructionPlan | None = None,
    temperature: float = 0.0,
    max_rounds: int = 3,
    use_critic: bool = True,
) -> Refinement[TextSchema]:
    """Ask `llm` for a schema until it passes code validation and the critic, or `max_rounds` is used up.

    `chunks` is the context, shown whole to the proposer and the critic; choose it with `select_context`.
    """
    domain = domain_summary(plan)
    body = "\n\n".join(f"[{c.chunk_id}]\n{c.text}" for c in chunks)

    def propose(feedback: str) -> TextSchema:
        prompt = PROMPT.format(goal=goal, domain=domain, chunks=body, feedback=feedback)
        return llm.generate(prompt, TextSchema, model=model, temperature=temperature)

    def critique(schema: TextSchema) -> list[str]:
        prompt = CRITIC_PROMPT.format(
            goal=goal, domain=domain, chunks=body, schema=schema.model_dump_json(indent=1)
        )
        reply = llm.generate(prompt, Critique, model=model, temperature=temperature)
        return reply.issues if reply.verdict == "retry" else []

    return refine(propose, _proposal_issues, critique if use_critic else None, max_rounds)
