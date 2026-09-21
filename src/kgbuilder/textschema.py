"""Propose entity types and fact types (subject-predicate-object) for the unstructured text."""

import re

from pydantic import BaseModel, Field

from . import llm
from .config import settings
from .lexical import Chunk
from .plan import ConstructionPlan


class EntityType(BaseModel):
    name: str = Field(description="PascalCase type, e.g. Component")
    description: str = Field(description="One sentence saying what counts as this type, and what does not")


class FactType(BaseModel):
    predicate: str = Field(description="UPPER_SNAKE_CASE relationship, e.g. CAUSES_PROBLEM_WITH")
    subject_type: str
    object_type: str
    description: str


class TextSchema(BaseModel):
    entity_types: list[EntityType]
    fact_types: list[FactType]

    def entity_names(self) -> set[str]:
        return {e.name for e in self.entity_types}

    def allows(self, subject_type: str, predicate: str, object_type: str) -> bool:
        return any(
            f.predicate == predicate and f.subject_type == subject_type and f.object_type == object_type
            for f in self.fact_types
        )


PROMPT = """You design the schema for extracting knowledge from unstructured text into a knowledge graph.

<goal>
{goal}
</goal>

The graph already contains this structured (domain) data. Reuse its concepts as entity types where the
text talks about the same things, and keep the meaning of each type distinct from these:
<domain_graph>
{domain}
</domain_graph>

Representative text chunks:
<chunks>
{chunks}
</chunks>

Rules:
- Propose entity types that are things a reader would want as nodes (products, parts, problems, materials,
  locations, people/roles...). Do not make types for ratings, dates or generic adjectives.
- Propose fact types as (subject_type, PREDICATE, object_type) that appear in the text and serve the goal.
- Every fact type must reference only entity types you defined. Keep both lists small and precise
  (at most 12 entity types and 20 fact types).

{feedback}"""


def validate_text_schema(ts: TextSchema) -> list[str]:
    issues = []
    names = [e.name for e in ts.entity_types]
    for n in {n for n in names if names.count(n) > 1}:
        issues.append(f"entity type '{n}' defined more than once")
    for n in names:
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", n):
            issues.append(f"entity type '{n}' must be PascalCase")
    seen = set()
    for f in ts.fact_types:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", f.predicate):
            issues.append(f"predicate '{f.predicate}' must be UPPER_SNAKE_CASE")
        for t in (f.subject_type, f.object_type):
            if t not in names:
                issues.append(f"fact {f.predicate}: entity type '{t}' is not defined")
        key = (f.subject_type, f.predicate, f.object_type)
        if key in seen:
            issues.append(f"fact {key} defined more than once")
        seen.add(key)
    if not ts.entity_types:
        issues.append("no entity types")
    if not ts.fact_types:
        issues.append("no fact types")
    return issues


def domain_summary(plan: ConstructionPlan | None) -> str:
    if plan is None:
        return "(none)"
    lines = [f"- node {n.label}: {n.description}" for n in plan.nodes]
    lines += [f"- {r.from_label} -[{r.relationship_type}]-> {r.to_label}" for r in plan.relationships]
    return "\n".join(lines)


def sample_chunks(chunks: list[Chunk], n: int = 12) -> list[Chunk]:
    if len(chunks) <= n:
        return chunks
    step = len(chunks) / n
    return [chunks[int(i * step)] for i in range(n)]


def propose_text_schema(
    goal: str, chunks: list[Chunk], plan: ConstructionPlan | None = None, max_rounds: int = 3
) -> tuple[TextSchema, int, list[str]]:
    """Returns (schema, rounds used, open issues). Code validation gates every round."""
    body = "\n\n".join(f"[{c.chunk_id}]\n{c.text[:1200]}" for c in sample_chunks(chunks))
    feedback, schema, issues = "", None, []
    for round_number in range(1, max_rounds + 1):
        prompt = PROMPT.format(goal=goal, domain=domain_summary(plan), chunks=body, feedback=feedback)
        schema = llm.generate(prompt, TextSchema, model=settings.schema_model)
        issues = validate_text_schema(schema)
        if not issues:
            return schema, round_number, []
        feedback = (
            f"Your previous schema:\n{schema.model_dump_json(indent=1)}\nProblems, fix all of them:\n"
            + "\n".join(f"- {i}" for i in issues)
        )
    return schema, max_rounds, issues
