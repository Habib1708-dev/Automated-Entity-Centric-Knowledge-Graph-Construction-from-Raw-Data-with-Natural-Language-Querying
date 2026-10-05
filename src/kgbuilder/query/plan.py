"""Query plans (R74): the closed set of primitives a model may combine to answer a question, and the code
check every plan passes before anything runs.

Role in the pipeline: the planner (planner.py) asks the model for a `QueryPlan`; `check_plan` refuses or
corrects it against the graph's schema; plan_cypher.py compiles each step to one parameterised, read-only
Cypher fragment and plan_run.py runs the steps in order. This replaces free text2cypher and the router
(layered-model Step 4), whose errors were the largest cause of wrong answers in R71 and R73.
Design: the LLM proposes, code decides. A step is one flat model (one `op` and optional fields), checked per
operation by its own rule (Strategy: `_RULES`), so a model's JSON stays simple and every refusal is a reason
the model can act on in its one retry. What a plan may name comes from the graph itself (`GraphSchema`);
direction, value types and identifiers are decided by code, never by the model. An optional filter (tone,
time) must quote the words of the question that ask for it, or it is dropped and the drop is reported.
Not here: compiling and running steps (plan_cypher.py, plan_run.py), the prompt (planner.py).
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..core.text import norm
from .graph_schema import GraphSchema, PropertyInfo

# Each primitive produces items of one kind; a step's `input` must produce a kind the step accepts.
ItemKind = Literal["record", "entity", "claim", "chunk"]
Op = Literal[
    "find_entity",
    "filter_records",
    "related",
    "find_claims",
    "read_check",
    "retrieve_chunks",
    "list",
    "count",
    "sum",
    "rank",
    "answer_from_chunks",
]
TERMINALS = frozenset({"list", "count", "sum", "rank", "answer_from_chunks"})
# the claim layer's primitives, left out of a system that may read the records only (records plus vector)
CLAIM_OPS = frozenset({"find_claims"})
Operator = Literal["=", "!=", "<", "<=", ">", ">=", "contains", "year"]
Tone = Literal["positive", "negative", "neutral"]


class PlanStep(BaseModel):
    """One step of a plan: an operation and the fields it uses (the others stay empty)."""

    op: Op = Field(description="The primitive this step runs.")
    input: int | None = Field(
        default=None, description="The index (from 0) of an earlier step whose result this step works on."
    )
    name: str | None = Field(
        default=None, description="find_entity: the name to look up, as the question writes it."
    )
    label: str | None = Field(
        default=None,
        description="find_entity: an optional node label or entity type; filter_records: the label "
        "to filter; "
        "related and rank: the label at the other end; list and count of the records claims are about: "
        "only the records of this label.",
    )
    property: str | None = Field(
        default=None,
        description="filter_records: the property compared; related: a property of the relationship; "
        "list, sum, rank: the property read.",
    )
    operator: Operator | None = Field(
        default=None,
        description="How `property` is compared with `value`; rank by the value most records share: "
        "'year' groups a date by its year.",
    )
    value: str | float | bool | None = Field(default=None, description="The value compared against.")
    relationship: str | None = Field(
        default=None, description="related and rank: the relationship type followed."
    )
    predicate: str | None = Field(default=None, description="find_claims: the claim's predicate.")
    subject_like: str | None = Field(default=None, description="find_claims: words for the claim's subject.")
    object_like: str | None = Field(default=None, description="find_claims: words for the claim's object.")
    tone: Tone | None = Field(default=None, description="find_claims: the claim's polarity.")
    tone_words: str | None = Field(default=None, description="The words of the question that ask for `tone`.")
    time_words: str | None = Field(
        default=None,
        description="find_claims: words the claim's time must contain, quoted from the question.",
    )
    include_parts: bool = Field(
        default=False, description="find_claims: also claims about the records that are parts of the input."
    )
    statement: str | None = Field(
        default=None, description="read_check: the statement each candidate must support."
    )
    what: Literal["name", "about", "subject", "object"] | None = Field(
        default=None,
        description="list: names of the items (name), or for claims the records they are about, their "
        "subjects "
        "or their objects.",
    )
    unit: Literal["items", "documents", "about"] | None = Field(
        default=None,
        description="count: count the items, their distinct documents, or the records claims are about.",
    )
    order: Literal["most", "fewest", "highest", "lowest"] | None = Field(
        default=None,
        description="rank: most/fewest related items, highest/lowest property value, or with a property "
        "most/fewest: the value the most or fewest records share.",
    )


class QueryPlan(BaseModel):
    """An ordered list of steps; the last one, and only the last one, is a terminal."""

    steps: list[PlanStep] = Field(min_length=1, description="The steps, in order; the last ends the plan.")


@dataclass
class PlanSchema:
    """What a plan may name: the graph's schema, the record labels, and whether claims may be read."""

    schema: GraphSchema
    record_labels: frozenset[str]
    claims: bool = True  # False for records plus vector RAG

    def label_properties(self, label: str) -> dict[str, PropertyInfo]:
        info = next((i for i in self.schema.labels if i.label == label), None)
        return {p.name: p for p in info.properties} if info else {}

    def relationship_types(self) -> set[str]:
        return {
            r.type
            for r in self.schema.relationships
            if r.source in self.record_labels and r.target in self.record_labels
        }

    def relationship_properties(self, relationship: str) -> dict[str, PropertyInfo]:
        return {
            p.name: p
            for r in self.schema.relationships
            if r.type == relationship and r.source in self.record_labels and r.target in self.record_labels
            for p in r.properties
        }

    def predicates(self) -> set[str]:
        return {c.predicate for c in self.schema.claims}

    def entity_types(self) -> set[str]:
        return {c.subject_type for c in self.schema.claims} | {c.object_type for c in self.schema.claims}


@dataclass
class CheckedPlan:
    """A plan after the check: the plan to run (optional filters without quoted words removed), why it is
    refused (empty when it may run), and what was dropped."""

    plan: QueryPlan
    issues: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


@dataclass
class _Context:
    schema: PlanSchema
    question: str
    kinds: list[set[ItemKind]]  # the item kinds each earlier step produces
    labels: list[str | None]  # the record label each earlier step's records are known to have, if one


class _Rule:
    """The check of one primitive: what it accepts and produces, and its own field rules."""

    accepts: frozenset[ItemKind] = frozenset()  # the input kinds it works on; empty: it takes no input
    needs_input = False

    def produces(self, step: PlanStep, ctx: _Context) -> set[ItemKind]:
        return set()

    def label(self, step: PlanStep, ctx: _Context) -> str | None:
        """The record label of the records this step produces, when code can know it."""
        return None

    def check(self, step: PlanStep, ctx: _Context, dropped: list[str]) -> list[str]:
        return []


def _input_label(step: PlanStep, ctx: _Context) -> str | None:
    return ctx.labels[step.input] if step.input is not None else None


def _value_issues(where: str, prop: PropertyInfo, operator: Operator | None, value: object) -> list[str]:
    """Whether `value` can be compared with a property of `prop.type` by `operator`."""
    if operator is None or value is None:
        return [f"{where} needs both an operator and a value"]
    if operator == "year":
        return (
            [] if _is_year(value) else [f"{where}: a year comparison needs a four-digit year, got {value!r}"]
        )
    if operator in ("<", "<=", ">", ">="):
        if prop.type.startswith("DATE") or prop.type.startswith("LOCAL") or prop.type.startswith("ZONED"):
            return (
                [] if _is_iso_date(value) else [f"{where}: compare a date with 'YYYY-MM-DD', got {value!r}"]
            )
        # a number, or a text holding one ('$246'): compared as the number in it (plan_cypher.py)
        return [] if as_number(value) is not None else [f"{where}: {operator} needs a number, got {value!r}"]
    if operator == "contains" and prop.type not in ("STRING", "LIST<STRING>"):
        return [f"{where}: contains needs a text property, {prop.name} is {prop.type}"]
    if prop.type == "BOOLEAN" and not isinstance(value, bool) and str(value).lower() not in ("true", "false"):
        return [f"{where}: {prop.name} is BOOLEAN, give true or false"]
    if prop.type in ("INTEGER", "FLOAT") and as_number(value) is None:
        return [f"{where}: {prop.name} is {prop.type}, give a number"]
    return []


def _is_year(value: object) -> bool:
    text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return len(text) == 4 and text.isdigit()


def _is_iso_date(value: object) -> bool:
    text = str(value)
    return len(text) == 10 and text[4] == "-" and text[7] == "-" and text.replace("-", "").isdigit()


def as_number(value: object) -> float | None:
    """The number `value` is or spells ("1,289", "$246"), or None; a bool is no number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip().lstrip("$€£"))
    except ValueError:
        return None


class _FindEntity(_Rule):
    def produces(self, step, ctx):
        # a record label finds records, an entity type finds entities (kinds); no label finds both, except
        # in a system without the claim layer, whose only names are records
        if step.label in ctx.schema.record_labels:
            return {"record"}
        if step.label is not None:
            return {"entity"}
        return {"record", "entity"} if ctx.schema.claims else {"record"}

    def label(self, step, ctx):
        return step.label if step.label in ctx.schema.record_labels else None

    def check(self, step, ctx, dropped):
        issues = [] if step.name and step.name.strip() else ["find_entity needs a name"]
        allowed = set(ctx.schema.record_labels) | (ctx.schema.entity_types() if ctx.schema.claims else set())
        if step.label is not None and step.label not in allowed:
            issues.append(f"find_entity: unknown label or type {step.label!r}; use one of {sorted(allowed)}")
        return issues


class _FilterRecords(_Rule):
    accepts = frozenset({"record"})

    def produces(self, step, ctx):
        return {"record"}

    def label(self, step, ctx):
        return step.label

    def check(self, step, ctx, dropped):
        if step.label not in ctx.schema.record_labels:
            return [
                f"filter_records: unknown label {step.label!r}; use one of {sorted(ctx.schema.record_labels)}"
            ]
        if step.property is None:  # every record of the label
            return [] if step.operator is None else ["filter_records: an operator needs a property"]
        props = ctx.schema.label_properties(step.label)
        if step.property not in props:
            return [f"filter_records: {step.label} has no property {step.property!r}; it has {sorted(props)}"]
        return _value_issues(f"filter_records {step.label}.{step.property}", props[step.property],
                             step.operator, step.value)  # fmt: skip


class _Related(_Rule):
    accepts = frozenset({"record"})
    needs_input = True

    def produces(self, step, ctx):
        return {"record"}

    def label(self, step, ctx):
        return step.label

    def check(self, step, ctx, dropped):
        types = ctx.schema.relationship_types()
        if step.relationship not in types:
            return [f"related: unknown relationship {step.relationship!r}; use one of {sorted(types)}"]
        issues = []
        if step.label is not None and step.label not in ctx.schema.record_labels:
            issues.append(f"related: unknown label {step.label!r}")
        if step.property is not None:
            props = ctx.schema.relationship_properties(step.relationship)
            if step.property not in props:
                issues.append(
                    f"related: {step.relationship} has no property {step.property!r}; it has {sorted(props)}"
                )
            else:
                issues += _value_issues(f"related {step.relationship}.{step.property}", props[step.property],
                                        step.operator, step.value)  # fmt: skip
        return issues


class _FindClaims(_Rule):
    accepts = frozenset({"record", "entity"})

    def produces(self, step, ctx):
        return {"claim"}

    def check(self, step, ctx, dropped):
        if not ctx.schema.claims:
            return ["find_claims is not available: this system reads the records only"]
        issues = []
        if step.predicate is not None and step.predicate not in ctx.schema.predicates():
            issues.append(
                f"find_claims: unknown predicate {step.predicate!r}; "
                f"use one of {sorted(ctx.schema.predicates())}"
            )
        if step.input is None and not (step.predicate or step.subject_like or step.object_like):
            issues.append("find_claims needs an input, a predicate, subject_like or object_like")
        # an optional filter stays only when the question asks for it in its own words (G12's invented tone)
        question = norm(ctx.question)
        if step.tone is not None and not (step.tone_words and norm(step.tone_words) in question):
            dropped.append(f"tone {step.tone!r}: {step.tone_words!r} is not words of the question")
            step.tone = None
        if step.time_words is not None and norm(step.time_words) not in question:
            dropped.append(f"time {step.time_words!r}: not words of the question")
            step.time_words = None
        return issues


class _ReadCheck(_Rule):
    accepts = frozenset({"record", "entity", "claim", "chunk"})
    needs_input = True

    def produces(self, step, ctx):
        return set(ctx.kinds[step.input]) if step.input is not None else set()

    def label(self, step, ctx):
        return _input_label(step, ctx)

    def check(self, step, ctx, dropped):
        return [] if step.statement and step.statement.strip() else ["read_check needs a statement"]


class _RetrieveChunks(_Rule):
    accepts = frozenset({"record", "entity", "claim"})

    def produces(self, step, ctx):
        return {"chunk"}


class _List(_Rule):
    accepts = frozenset({"record", "entity", "claim"})
    needs_input = True

    def check(self, step, ctx, dropped):
        kinds = ctx.kinds[step.input] if step.input is not None else set()
        if step.property is not None:
            label = _input_label(step, ctx)
            if kinds != {"record"} or label is None:
                return ["list: a property can be read only from records of one known label"]
            if step.property not in ctx.schema.label_properties(label):
                return [f"list: {label} has no property {step.property!r}"]
        if step.what in ("about", "subject", "object") and kinds != {"claim"}:
            return [f"list: what={step.what!r} needs claims as input"]
        if kinds == {"claim"} and step.what in (None, "about"):
            return _about_label_issues("list", step, ctx)
        return []


class _Count(_Rule):
    accepts = frozenset({"record", "entity", "claim", "chunk"})
    needs_input = True

    def check(self, step, ctx, dropped):
        kinds = ctx.kinds[step.input] if step.input is not None else set()
        if step.unit == "about" and kinds != {"claim"}:
            return ["count: unit 'about' needs claims as input"]
        return _about_label_issues("count", step, ctx) if step.unit == "about" else []


def _about_label_issues(op: str, step: PlanStep, ctx: _Context) -> list[str]:
    """A label narrowing the records claims are about must be a record label (R79: "how many complaints"
    counts the complaints, not the vehicles the same claims hang on)."""
    if step.label is None or step.label in ctx.schema.record_labels:
        return []
    return [f"{op}: unknown record label {step.label!r}; use one of {sorted(ctx.schema.record_labels)}"]


class _Sum(_Rule):
    accepts = frozenset({"record"})
    needs_input = True

    def check(self, step, ctx, dropped):
        label = _input_label(step, ctx)
        if label is None or step.property not in ctx.schema.label_properties(label):
            return ["sum needs records of one known label and one of its properties"]
        return []


class _Rank(_Rule):
    accepts = frozenset({"record", "claim"})
    needs_input = True

    def check(self, step, ctx, dropped):
        kinds = ctx.kinds[step.input] if step.input is not None else set()
        if step.order is None:
            return ["rank needs an order"]
        if step.property is not None:
            label = _input_label(step, ctx)
            props = ctx.schema.label_properties(label) if label is not None else {}
            if step.property not in props:
                return ["rank by property needs records of one known label and one of its properties"]
            if step.order in ("highest", "lowest"):
                return []
            # most / fewest: the value the most or fewest records share (R79), by year only for a date
            if step.operator == "year" and not props[step.property].type.startswith(
                ("DATE", "LOCAL", "ZONED")
            ):
                return [f"rank: operator 'year' needs a date property, {step.property} is not one"]
            if step.operator not in (None, "year"):
                return ["rank by the value records share takes no operator but 'year'"]
            return []
        if step.order not in ("most", "fewest"):
            return ["rank by related items orders most or fewest"]
        if step.relationship is not None:
            if kinds != {"record"} or step.relationship not in ctx.schema.relationship_types():
                return [f"rank: records and a known relationship needed, got {step.relationship!r}"]
            return []
        return (
            []
            if kinds == {"claim"}
            else ["rank needs a property, a relationship, or claims (grouped by record)"]
        )


class _AnswerFromChunks(_Rule):
    accepts = frozenset({"record", "entity", "claim", "chunk"})


_RULES: dict[str, _Rule] = {
    "find_entity": _FindEntity(),
    "filter_records": _FilterRecords(),
    "related": _Related(),
    "find_claims": _FindClaims(),
    "read_check": _ReadCheck(),
    "retrieve_chunks": _RetrieveChunks(),
    "list": _List(),
    "count": _Count(),
    "sum": _Sum(),
    "rank": _Rank(),
    "answer_from_chunks": _AnswerFromChunks(),
}


def check_plan(plan: QueryPlan, schema: PlanSchema, question: str) -> CheckedPlan:
    """Check `plan` for `question` against `schema`: every name exists, every value fits its property's type,
    every input points back to a step whose items the step accepts, exactly the last step is a terminal.
    Optional filters without the question's own words are removed from the returned plan, and reported.
    The issues are worded for the model's retry."""
    plan = plan.model_copy(deep=True)
    ctx = _Context(schema=schema, question=question, kinds=[], labels=[])
    issues: list[str] = []
    dropped: list[str] = []
    last = len(plan.steps) - 1
    for index, step in enumerate(plan.steps):
        where = f"step {index} ({step.op})"
        rule = _RULES[step.op]
        if (step.op in TERMINALS) != (index == last):
            issues.append(f"{where}: the last step, and only the last, must be one of {sorted(TERMINALS)}")
        if step.input is not None and not 0 <= step.input < index:
            issues.append(f"{where}: input {step.input} is not an earlier step")
            step.input = None
        if step.input is None and rule.needs_input:
            issues.append(f"{where}: needs an input step")
        elif step.input is not None and not (ctx.kinds[step.input] & rule.accepts):
            got = sorted(ctx.kinds[step.input])
            issues.append(f"{where}: cannot work on {got} items; it takes {sorted(rule.accepts)}")
        issues += [f"{where}: {issue}" for issue in rule.check(step, ctx, dropped)]
        ctx.kinds.append(rule.produces(step, ctx))
        ctx.labels.append(rule.label(step, ctx))
    return CheckedPlan(plan=plan, issues=issues, dropped=dropped)
