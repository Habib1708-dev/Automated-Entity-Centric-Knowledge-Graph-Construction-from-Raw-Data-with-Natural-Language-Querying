"""The text schema: which entity types and fact types may be extracted from the documents.

Role in the pipeline: `kg text-schema`. The LLM proposes the schema from sample chunks and the domain
graph's node descriptions; a human reviews `out/text_schema.json`; `kg extract` is then constrained to it.
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

# How much text the proposer sees: enough variety to find the recurring fact patterns, small enough to
# keep the prompt cheap. Chunks are sampled evenly across the corpus, not taken from the first document.
_SAMPLE_CHUNKS = 12
_SAMPLE_CHARS_PER_CHUNK = 1200


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


class TextSchema(BaseModel):
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
- Derive the entity types from the goal, the domain graph and the text: things the goal's questions are
  about. Do not make types for ratings, dates or generic adjectives.
- Propose fact types as (subject_type, PREDICATE, object_type) that appear in the text and serve the goal:
  for each one, you should be able to name a question of the goal that it helps answer.
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
    return issues


def domain_summary(plan: ConstructionPlan | None) -> str:
    """The domain graph as prompt text: node labels with their descriptions, and the relationships."""
    if plan is None:
        return "(none)"
    lines = [f"- node {n.label}: {n.description}" for n in plan.nodes]
    lines += [f"- {r.from_label} -[{r.relationship_type}]-> {r.to_label}" for r in plan.relationships]
    return "\n".join(lines)


def sample_chunks(chunks: list[Chunk], n: int = _SAMPLE_CHUNKS) -> list[Chunk]:
    """`n` chunks spread evenly over the corpus. Deterministic, so the prompt and its cache key are stable."""
    if len(chunks) <= n:
        return chunks
    step = len(chunks) / n
    return [chunks[int(i * step)] for i in range(n)]


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
    """Ask `llm` for a schema until it passes code validation and the critic, or `max_rounds` is used up."""
    domain = domain_summary(plan)
    body = "\n\n".join(f"[{c.chunk_id}]\n{c.text[:_SAMPLE_CHARS_PER_CHUNK]}" for c in sample_chunks(chunks))

    def propose(feedback: str) -> TextSchema:
        prompt = PROMPT.format(goal=goal, domain=domain, chunks=body, feedback=feedback)
        return llm.generate(prompt, TextSchema, model=model, temperature=temperature)

    def critique(schema: TextSchema) -> list[str]:
        prompt = CRITIC_PROMPT.format(
            goal=goal, domain=domain, chunks=body, schema=schema.model_dump_json(indent=1)
        )
        reply = llm.generate(prompt, Critique, model=model, temperature=temperature)
        return reply.issues if reply.verdict == "retry" else []

    return refine(propose, validate_text_schema, critique if use_critic else None, max_rounds)
