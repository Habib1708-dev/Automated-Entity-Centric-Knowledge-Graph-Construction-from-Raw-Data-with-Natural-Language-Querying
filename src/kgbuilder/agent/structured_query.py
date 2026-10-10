"""The typed query the agent's structured tools take (R134), the code check every query passes, and the
compiled statement both compilers return.

Role in the pipeline: the agent (R135) proposes a `RecordQuery` (`query_records`) or a `TableQuery`
(`query_tables`); `check_query` refuses it unless every field it names exists in the source and every value
fits its field's type, and only then does record_query.py or table_query.py compile it.
Design: the LLM proposes, code decides. One flat model per tool, with a closed set of operators and
measures, so a query is a choice among fields the schema lists, never a statement the model writes. Field
types are R74's (`PropertyInfo.type`: INTEGER, FLOAT, STRING, DATE, BOOLEAN, LIST<...>), and values are
checked by R74's own rule (`query.plan.value_issues`), so a year must be four digits and a date compared as
YYYY-MM-DD in both tools. A text field may be measured: its number is read out of it ("$246"), the rule by
which R74 compares such a field. A group over a field holding several values counts each value (R139): a
list always, a text when the query gives the separator its values are joined by. The separator is the
model's proposal, read from the schema's examples, because no source declares one and a comma inside one
name ("Ash, Birch and Co") is as common as a comma between names; code checks it is punctuation.
Branching walks (R140): the operator `in` keeps the entries equal to one of a list of values, each value
checked and compared as `=` checks and compares it, so one query reads what took one call per value; and
`relationship` may be a path of up to `MAX_HOPS` relationship types from the related record, so the records
several relationships away are one query. R74's shared operators are unchanged: `in` is the agent's own.
Not here: compiling (record_query.py, table_query.py), running (the tools).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..query.graph_schema import PropertyInfo
from ..query.plan import value_issues

Measure = Literal["rows", "count", "sum", "average", "min", "max"]
Shape = Literal["rows", "value", "groups"]
# the field types a number can be read from: numbers, and texts that hold one
_MEASURABLE = ("INTEGER", "FLOAT", "STRING")
QueryOperator = Literal["=", "!=", "<", "<=", ">", ">=", "contains", "year", "in"]
MAX_IN = 50  # values one `in` condition may list: enough for a node's neighbours, short enough to read
MAX_HOPS = 3  # relationships a path may follow: a record, its parts, theirs, and what supplies those


class Condition(BaseModel):
    field: str = Field(description="The property or column to compare, exactly as the schema names it.")
    operator: QueryOperator = Field(
        description="How to compare: = and != for equality (text ignoring case), < <= > >= for numbers and "
        "dates (YYYY-MM-DD), contains for words inside a text, year for the year of a date, in for one of "
        "several values (each equal as = compares)."
    )
    value: str | float | bool | list[str | float] = Field(
        description="The value to compare with; with in, the list of values."
    )


class StructuredQuery(BaseModel):
    """What both structured tools share: conditions, a measure, a group and an order."""

    where: list[Condition] = Field(default=[], description="Conditions every result must meet (all of them).")
    measure: Measure = Field(
        default="rows",
        description="rows lists the matching entries; count counts them; sum, average, min and max measure "
        "`field` over them.",
    )
    field: str | None = Field(default=None, description="The field sum, average, min and max measure.")
    group_by: str | None = Field(
        default=None, description="Measure each value of this field separately (largest first)."
    )
    group_split: str | None = Field(
        default=None,
        description="With group_by, for a text field holding several values joined by a separator (as its "
        "examples show): the separator; each value is then measured on its own. A list field needs none.",
    )
    order_by: str | None = Field(default=None, description="rows: the field to sort the entries by.")
    descending: bool = Field(default=False, description="rows: sort from the largest value down.")


class RecordQuery(StructuredQuery):
    label: str = Field(description="The record label to query, exactly as the schema names it.")
    related_to: str | None = Field(
        default=None,
        description="Only records related to this record: its ref, exactly as a tool returned it.",
    )
    relationship: str | list[str] | None = Field(
        default=None,
        description="With related_to: the relationship type joining the two, as the schema names it; or "
        f"the types along a path of several relationships, in order from the related record (at most "
        f"{MAX_HOPS}).",
    )

    def path(self) -> list[str]:
        """The relationship types from the related record to these records; [] without one."""
        if self.relationship is None:
            return []
        return [self.relationship] if isinstance(self.relationship, str) else list(self.relationship)


class TableQuery(StructuredQuery):
    table: str = Field(description="The source table to query, exactly as the schema names it.")


class Compiled(BaseModel):
    """A statement ready to run: its text, its parameters, and what its rows hold (`rows`: total, ref and
    properties; `value`: one value; `groups`: total, bucket (the group) and value)."""

    statement: str
    parameters: dict[str, Any]  # Any: the values a query compares with (texts, numbers, booleans, lists)
    shape: Shape


def shape_of(query: StructuredQuery) -> Shape:
    """What a query's rows hold: listed entries, one measured value, or one value per group."""
    if query.measure == "rows":
        return "rows"
    return "groups" if query.group_by is not None else "value"


def check_query(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> list[str]:
    """Why `query` cannot run over a source with `fields` (name -> type); [] when it can. The source itself
    (label, table, relationship) is checked by the tool, which knows which ones exist."""
    issues = []
    for i, c in enumerate(query.where):
        if c.field not in fields:
            issues.append(_unknown(f"condition {i}", c.field, fields))
        else:
            issues += _condition_issues(f"condition {i} ({c.field})", fields[c.field], c)
    issues += _measure_issues(query, fields)
    for name, value in (("group_by", query.group_by), ("order_by", query.order_by)):
        if value is not None and value not in fields:
            issues.append(_unknown(name, value, fields))
    if query.order_by is not None and query.measure != "rows":
        issues.append("order_by sorts listed rows; a measure is ordered largest first")
    return issues + _split_issues(query, fields)


def _condition_issues(where: str, prop: PropertyInfo, c: Condition) -> list[str]:
    """R74's check of a value against its field's type; for `in`, of each value as `=` takes it."""
    if c.operator == "in":
        if not isinstance(c.value, list) or not 1 <= len(c.value) <= MAX_IN:
            return [f"{where}: in needs a list of 1 to {MAX_IN} values, got {c.value!r}"]
        return [issue for v in c.value for issue in value_issues(where, prop, "=", v)]
    if isinstance(c.value, list):
        return [f"{where}: a list of values needs the operator in"]
    return value_issues(where, prop, c.operator, c.value)


def _split_issues(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> list[str]:
    """Whether a separator comes with a group over a text field and is punctuation: a letter or a digit
    would cut values apart inside their words."""
    if query.group_split is None:
        return []
    if query.group_by is None:
        return ["group_split needs group_by: it splits the grouped field's values"]
    issues = []
    if query.group_by in fields and fields[query.group_by].type != "STRING":
        issues.append(f"group_split splits a text, {query.group_by} is {fields[query.group_by].type}")
    if not query.group_split.strip() or any(ch.isalnum() for ch in query.group_split):
        issues.append(f"group_split must be punctuation, no letter or digit, got {query.group_split!r}")
    return issues


def _measure_issues(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> list[str]:
    """Whether the measure has the field it needs, and a field it can read a number (or for min and max, an
    order) from."""
    if query.measure in ("rows", "count"):
        if query.field is None:
            return []
        return [f"{query.measure} takes no field; give sum, average, min or max"]
    if query.field is None:
        return [f"{query.measure} needs the field it measures"]
    if query.field not in fields:
        return [_unknown("field", query.field, fields)]
    kind = fields[query.field].type
    if query.measure in ("sum", "average") and kind not in _MEASURABLE:
        return [f"{query.measure} needs a number, {query.field} is {kind}"]
    if kind.startswith("LIST"):
        return [f"{query.measure} cannot measure a list, {query.field} is {kind}"]
    return []


def _unknown(where: str, name: str, fields: dict[str, PropertyInfo]) -> str:
    return f"{where}: no field {name!r}; the fields are {', '.join(sorted(fields))}"
