"""The text schema: which entity types and fact types may be extracted from the documents.

Role in the pipeline: `kg text-schema`. The LLM proposes the schema from the text (every chunk, whole, while
the corpus fits a character budget, else an even sample: `select_context`) and the domain graph's node
descriptions; a human reviews `out/text_schema.json`; `kg extract` is then constrained to it.
Design: same shape as the structured path: pydantic models are the LLM's response schema,
`validate_text_schema` is the code gate, and the retry loop is `llm.refine.refine` with a critic pass.
`Value` (core/values.py) is a built-in object type: a fact type may end in a number with a unit without the
proposer defining it (R66).
Every entity type declares its identity class (R75, layered-model Step 5), which decides what one name in
the text stands for: a record of the structured data (`keyed`, with the plan labels its records carry), one
particular thing named only in the text (`individual`), or a kind shared by many things (`concept`). The
identity stage (resolution/) reads the classes; this module only checks them against the plan.
Not here: extraction itself (extraction.py) and deciding what a mention refers to (resolution/).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..core.values import VALUE_TYPE
from ..llm.base import LLMClient
from ..llm.refine import Critique, Refinement, refine
from ..structured.plan import ConstructionPlan
from .chunking import Chunk

_PASCAL_CASE = re.compile(r"[A-Z][A-Za-z0-9]*")  # Product, SubAssembly
_UPPER_SNAKE_CASE = re.compile(r"[A-Z][A-Z0-9_]*")  # HAS_PROBLEM


# The three identity classes (direction document, section 5): an identity is a key in the structured data,
# a particular thing named only in text (kept apart across documents until the text gives evidence), or a
# kind (merged across documents by entity resolution, with its guards).
IdentityClass = Literal["keyed", "individual", "concept"]


# The field descriptions reach the proposer as its response schema, so they are prompt text: the examples
# come from an invented domain (beekeeping), never from an evaluated dataset (prompt-engineering skill).
class EntityType(BaseModel):
    name: str = Field(description="PascalCase type, e.g. Beekeeper")
    description: str = Field(description="One sentence saying what counts as this type, and what does not")
    # "concept" by default: a schema written before R75 keeps the identity rule it was built with (one
    # node per type and name across documents, merged by entity resolution)
    identity: IdentityClass = Field(
        default="concept",
        description="How the things of this type are told apart: keyed when each one is a record of the "
        "domain graph, individual when each one is one particular person, organisation, place or object "
        "named only in the text, concept when the type names kinds, states or properties that many "
        "things share",
    )
    record_labels: list[str] = Field(
        default=[],
        description="Keyed types only: the domain graph labels whose records the things of this type are",
    )
    key_attributes: list[str] = Field(
        default=[],
        description="Keyed types only: properties of those records whose value, written next to a name in "
        "the text, tells two records with the same name apart; may be empty",
    )


class FactType(BaseModel):
    predicate: str = Field(description="UPPER_SNAKE_CASE relationship, e.g. KEPT_AT")
    subject_type: str
    object_type: str
    description: str
    # A derived fact type is computed by code in the link stage (resolution/derivation.py) from what the
    # document is about, and is never shown to the extractor: the model used to spend more than half of
    # its output on "part X is part of the product" facts that the document title already states.
    derived: bool = Field(
        default=False,
        description="True only for a relation that follows from the document itself (a thing the text "
        "names belongs to the thing the whole document is about); code derives it and the extractor never "
        "sees it",
    )
    # Entity resolution never merges a part with its whole (R75): "the gearbox" and "the gearbox casing"
    # spell alike, and one claim of a part-of fact type between them shows they are two things. Which fact
    # types state part-of is a matter of meaning, so the proposer marks them and code applies the rule.
    part_of: bool = Field(
        default=False,
        description="True when a claim of this type states that its subject is one of the pieces its object "
        "is made of",
    )


class TextSchema(BaseModel):
    entity_types: list[EntityType]
    fact_types: list[FactType]

    def entity_names(self) -> set[str]:
        return {e.name for e in self.entity_types}

    def entity_type(self, name: str) -> EntityType | None:
        """The entity type called `name`; None for the built-in `Value` and for unknown types."""
        return next((e for e in self.entity_types if e.name == name), None)

    def identity_of(self, type_name: str) -> IdentityClass:
        """The identity class of a type; a type the schema does not define (`Value`, or a stored mention
        of a type since removed) is a concept, the class that never links to records."""
        entity_type = self.entity_type(type_name)
        return entity_type.identity if entity_type is not None else "concept"

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

    def is_part_of(self, subject_type: str, predicate: str, object_type: str) -> bool:
        """True when the signature is a fact type marked `part_of`: its subject is a piece of its object."""
        fact = self.find(subject_type, predicate, object_type)
        return fact is not None and fact.part_of

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
# R58 (the system's aim is as many true, consistent, linked facts as possible; the goal sets the scope, not
# the shape): three changes, each answering a measured failure.
# - "Reuse its concepts as entity types" became "name a type like a domain node ... so that code can link":
#   on the held-out data the proposer copied the plan's record node (`Complaint`) and made it the hub of
#   every problem fact, so problems were stored on the complaint, not on the part (R53, R55: 11 misses).
# - The source rule: a statement's source (a document, a report, a message) is not a thing the statement
#   is about; provenance (chunk, evidence) already records it. Generic genre words only, no corpus word.
# - Coverage replaced "name a question of the goal each fact type answers": question-driven schemas keep
#   the goal's chain and drop the observations it starts from (R56: furniture recall 0.854 -> 0.552).
#   The goal still decides which things matter, so reviewers and their cities stay out (R14).
# R66 (the observation graph): every claim carries its polarity, so the schema describes aspects of things
# and one fact type holds the good, the bad and the neutral claim of a kind; until R66 the proposals held
# only problems, and a measure, a material or praise had no place (the R63 audit). Numbers get the
# built-in `Value` object type, so a measure is stored as a number and a unit, not as a name.
# R75 (identity, layered-model Step 5): the identity rule asks for what only meaning can tell (is a type's
# thing a record, one particular thing, or a kind?); code checks every label and property it names against
# the plan (`identity_issues`). The part-of rule lets code keep a part and its whole apart in resolution.
PROMPT = """You design the schema for extracting knowledge from unstructured text into a knowledge graph.

<goal>
{goal}
</goal>

The graph already contains this structured (domain) data. When the text talks about the same kind of
thing as a domain node, name the entity type like that node, so that code can link the two, and keep the
meaning of each type distinct from these:
<domain_graph>
{domain}
</domain_graph>

Representative text chunks:
<chunks>
{chunks}
</chunks>

Rules:
- Derive the entity types from the goal and the text: the things the text makes statements about that
  the goal cares about. Do not make types for ratings, dates or generic adjectives.
- Do not make an entity type for the source a statement comes from (a document, a report, a message, a
  post), and do not connect facts through such a source: where each fact comes from is recorded outside
  the schema. Connect the things a statement is about directly to each other.
- Propose fact types as (subject_type, PREDICATE, object_type) for every kind of claim the text makes
  about those things, not only the claims one question needs: a claim the schema cannot hold is lost.
- Describe aspects of the things, not only what goes wrong: what they are made of, how they are
  measured or rated, what they do well and what they do badly. Each extracted claim records whether it is
  positive, negative or neutral, so one fact type holds all three; do not split a fact type by tone.
- When the object of a claim is a number with a unit (a measure, a weight, a limit, a price), use the
  built-in object type {value_type}. It needs no entity type of its own.
- Give every entity type its identity. "keyed": each thing of the type is one record of the domain
  graph; name its labels in record_labels, and in key_attributes the record properties the text writes
  next to a name to tell two records of the same name apart. "individual": each thing is one particular
  person, organisation, place or object that the text names and the domain graph does not hold.
  "concept": the type names kinds, states or properties that many things share.
- Mark a fact type part_of when its claims state that the subject is one of the pieces the object is
  made of.
- Every fact type must reference only entity types you defined, and every entity type must be used by a
  fact type. Keep both lists small and precise (at most 12 entity types and 20 fact types).

{feedback}"""

# The critic checks what code cannot: whether types overlap in meaning, whether a type stands for a source
# instead of a thing (R58), whether the text's claims have a place, and whether the text supports the fact
# types. Naming, references and unused types are already verified in code.
CRITIC_PROMPT = """You are reviewing a proposed schema for extracting facts from text into a knowledge graph.
The schema already passed mechanical checks (naming, no duplicates, fact types reference defined entity
types, every entity type is used), so judge only the modeling. {value_type} is a built-in object type for a
number with a unit, and every extracted claim records whether it is positive, negative or neutral.
- Do two entity types overlap so that an extractor could not choose between them?
- Does any entity type clash in meaning with a node of the domain graph that has the same name?
- Does an entity type stand for the source of a statement (a document, a report, a message) instead of a
  thing the statement is about, or does a fact type connect two things through such a source?
- Is there a kind of claim the text makes several times about the things the goal cares about that no
  fact type can hold, good and neutral claims included?
- Are there fact types the sample text gives no evidence for?
- Is there a fact type about things the goal does not care about?
- Is an identity wrong: a type whose things are records of the domain graph not keyed, a type of kinds or
  states keyed or individual, or a type of particular named things a concept?

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


def validate_text_schema(schema: TextSchema, plan: ConstructionPlan | None = None) -> list[str]:
    """Check naming, duplicates, references and, against `plan`, the identity classes. Returns a list of
    problems; empty means valid. Without a plan (a text-only dataset) no type can be keyed."""
    issues = identity_issues(schema, plan)
    names = [e.name for e in schema.entity_types]
    for name in sorted({n for n in names if names.count(n) > 1}):
        issues.append(f"entity type '{name}' defined more than once")
    for name in names:
        if not _PASCAL_CASE.fullmatch(name):
            issues.append(f"entity type '{name}' must be PascalCase")

    if VALUE_TYPE in names:
        issues.append(f"entity type '{VALUE_TYPE}' is built in; do not define it")
    seen = set()
    for fact in schema.fact_types:
        if not _UPPER_SNAKE_CASE.fullmatch(fact.predicate):
            issues.append(f"predicate '{fact.predicate}' must be UPPER_SNAKE_CASE")
        if fact.subject_type == VALUE_TYPE:
            issues.append(f"fact {fact.predicate}: a number cannot be a subject")
        # the built-in Value may end a fact type without a definition; a subject Value is reported above
        for type_name in (fact.subject_type, fact.object_type):
            if type_name not in names and type_name != VALUE_TYPE:
                issues.append(f"fact {fact.predicate}: entity type '{type_name}' is not defined")
        key = (fact.subject_type, fact.predicate, fact.object_type)
        if key in seen:
            issues.append(f"fact {key} defined more than once")
        seen.add(key)

    # an entity type no fact type uses can never hold an entity: entities exist only as ends of facts
    used = {t for f in schema.fact_types for t in (f.subject_type, f.object_type)}
    for name in names:
        if name not in used:
            issues.append(f"entity type '{name}' is used by no fact type")

    if not schema.entity_types:
        issues.append("no entity types")
    if not schema.fact_types:
        issues.append("no fact types")
    return issues


def identity_issues(schema: TextSchema, plan: ConstructionPlan | None) -> list[str]:
    """The problems of the identity classes (R75): a keyed type names at least one plan label, and each key
    attribute is a column of one of its labels; other types name neither; a type named like a plan label
    is keyed, because the schema prompt asks for that name only so that code can link the two."""
    issues = []
    columns = {n.label: {n.unique_column, *n.properties} for n in plan.nodes} if plan else {}
    for e in schema.entity_types:
        if e.identity != "keyed":
            if e.record_labels or e.key_attributes:
                issues.append(f"entity type '{e.name}' is {e.identity}: only a keyed type names records")
            if e.name in columns:
                issues.append(f"entity type '{e.name}' has the name of a domain label but is not keyed")
            continue
        if not e.record_labels:
            issues.append(f"keyed entity type '{e.name}' names no record label")
        for label in e.record_labels:
            if label not in columns:
                issues.append(f"keyed entity type '{e.name}': '{label}' is not a label of the domain graph")
        known = set().union(*(columns.get(label, set()) for label in e.record_labels))
        for attribute in e.key_attributes:
            if attribute not in known:
                issues.append(f"keyed entity type '{e.name}': key attribute '{attribute}' is not a property")
    return issues


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
        prompt = PROMPT.format(
            goal=goal, domain=domain, chunks=body, feedback=feedback, value_type=VALUE_TYPE
        )
        return llm.generate(prompt, TextSchema, model=model, temperature=temperature)

    def critique(schema: TextSchema) -> list[str]:
        prompt = CRITIC_PROMPT.format(
            goal=goal,
            domain=domain,
            chunks=body,
            schema=schema.model_dump_json(indent=1),
            value_type=VALUE_TYPE,
        )
        reply = llm.generate(prompt, Critique, model=model, temperature=temperature)
        return reply.issues if reply.verdict == "retry" else []

    def validate(schema: TextSchema) -> list[str]:
        return validate_text_schema(schema, plan)

    return refine(propose, validate, critique if use_critic else None, max_rounds)
