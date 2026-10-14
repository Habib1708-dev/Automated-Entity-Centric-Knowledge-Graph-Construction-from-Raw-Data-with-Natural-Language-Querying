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
Two fields of one entry (R148): a condition may name `other_field` instead of a value, and `pair_kind` is the
one place that decides how the two compare (both compilers render its answer): numbers with numbers, dates
with dates of the same type, booleans and texts (ignoring case) by = or != only, and with `year` the year of
a date against a year or another date's year (a time of day has no year). A text against a number, or texts
ordered by < <= > >=, is refused with its reason, never guessed. A literal spelled as another field's name
(ignoring case, as a text is compared) is refused with the hint to give it as `other_field`, because
compared as a literal it fails at run time (a date) or silently matches nothing (a text); `in` with that one
value still matches a text that really is spelled so. Listed rows and groups page by `offset`, which a
single measured value does not take. A group needs a measure (R161): `group_by` with `rows` is refused,
since the listing it would otherwise run as is not what was asked; its field description says so first.
An empty cell is `''` (R167): the tools print an empty or missing value as `''`, so `= ''` and `!= ''`
(and `''` among `in`'s values) test for one, on a field of any type; `is_empty_test` is the one place that
says so, and both compilers render it as "null or blank", because R166's LC05 asked `date = ''` as the
tool had shown it and was answered 0 (the cell is null to DuckDB, and a date field refused `''` as no date).
The value check (R74's `value_issues`) is asked only for other values, so the plan systems are unchanged.
Distinct counts and periods (R168): the measure `distinct` counts the different values of `field`; `period`
(year or decade) makes each group the year or the decade of the grouped field, or, with `distinct` and no
group, what is counted. `period_kind` is the one place that says how a field's year is read (a date by its
year; a text by the first four-digit run in it, `YEAR_IN_TEXT`; a number, a boolean, a list or a time of
day not at all), and both compilers render its answer. R166's HC05 grouped 29 dates and counted the years
by eye (8 for 10); LC07 grouped 58 inception texts over three pages and summed the decades by eye.
A whole word (R175): the operator `word` keeps the entries whose text holds the value as a whole word or
phrase, ignoring case, bounded on each side by the text's start or end or by anything that is no letter and
no digit (`word_pattern` is the one place that builds the pattern; both compilers run it, RE2 and Java read
it alike). R173's HD05 asked how many consequences use the word "stall" and `contains`, a substring match,
counted "installed" too (7 for 6); the operator's description says what `contains` matches inside words.
Refused on a field that is no text and for a blank value; a value spelled as a field's name is a word, as
for `contains`.
Not here: compiling (record_query.py, table_query.py), running (the tools).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..query.graph_schema import PropertyInfo
from ..query.plan import value_issues

Measure = Literal["rows", "count", "distinct", "sum", "average", "min", "max"]
Shape = Literal["rows", "value", "groups"]
Period = Literal["year", "decade"]
# how a field's year is read: a date by its year, a text by the four digits in it (R168)
PeriodKind = Literal["date", "text"]
# The year inside a text ('1859-03-01', 'seen 11/07/2016'): the first four-digit run bounded by non-digits,
# as group 2. RE2 (DuckDB) has no look-behind and Java (APOC) reads this pattern alike, so both compilers
# pass it as the parameter $year_pattern; a five-digit number is no year, a text without one gives null.
YEAR_IN_TEXT = r"(^|[^0-9])([0-9]{4})([^0-9]|$)"
# the field types a number can be read from: numbers, and texts that hold one
_MEASURABLE = ("INTEGER", "FLOAT", "STRING")
QueryOperator = Literal["=", "!=", "<", "<=", ">", ">=", "contains", "word", "year", "in"]
MAX_IN = 50  # values one `in` condition may list: enough for a node's neighbours, short enough to read
MAX_HOPS = 3  # relationships a path may follow: a record, its parts, theirs, and what supplies those
# the comparison operators as both compilers write them (SQL and Cypher share the symbols)
COMPARISONS = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
# how two fields of one entry compare: the year of a date with a year; the years of two dates; two values of
# one kind as they are; two texts ignoring case
PairKind = Literal["year", "years", "value", "text"]
_NUMBERS = ("INTEGER", "FLOAT")
# the types that have a year, by name: a time of day ("LOCAL TIME", "ZONED TIME") has none, so the prefixes
# R74 tests would let `year` through to fail at run time
_DATES = ("DATE", "LOCAL DATETIME", "ZONED DATETIME")
_PAIRED = (*COMPARISONS, "year")  # the operators that may compare a field with another field
# the field types `word` searches: a text, or a list of texts (any element may hold the word)
_TEXTS = ("STRING", "LIST<STRING>")
# What `word` escapes in the value: the characters RE2 and Java both read specially. Nothing else is escaped,
# because an escaped letter or space means different things to the two engines.
_REGEX_SPECIAL = set(r"\.^$|?*+()[]{}")
# a boundary either side of the word: no letter and no digit, in RE2's and Java's shared classes
_NOT_WORD = r"[^\p{L}\p{N}]"


class Condition(BaseModel):
    field: str = Field(description="The property or column to compare, exactly as the schema names it.")
    # year names a text holding a date too (R164), as both compilers read one: a text column whose examples
    # show '2004' and '2004-06' is matched by its year, where '=' a year misses the month-dated rows
    operator: QueryOperator = Field(
        # R175: contains is named as a substring match, with an example of no evaluated dataset, so a question
        # about a word is asked with word; the example word is one neither R160's nor R171's gold holds
        description="How to compare: = and != for equality (text ignoring case), < <= > >= for numbers and "
        "dates (YYYY-MM-DD), contains for text inside a text, inside words too ('ice' is in 'spice'), word "
        "for a whole word or phrase of a text ('ice' is not in 'spice'), year for the year of a date or of "
        "a text holding one, in for one of several values (each equal as = compares)."
    )
    value: str | float | bool | list[str | float] | None = Field(
        default=None,
        description="The value to compare with; with in, the list of values; '' with = or != tests for an "
        "empty cell (= '' keeps the empty ones, != '' the filled). Leave it out when comparing with "
        "other_field.",
    )
    other_field: str | None = Field(
        default=None,
        description="Instead of value: another property or column of the same entry, exactly as the schema "
        "names it; keeps the entries where the two compare as the operator says. A number compares with a "
        "number, a date with a date of the same type, a text with a text and a true/false with a true/false "
        "by = or != only; with year, field must be a date and its year is compared with other_field (a "
        "year, or the year of a date).",
    )


class StructuredQuery(BaseModel):
    """What both structured tools share: conditions, a measure, a group and an order."""

    where: list[Condition] = Field(default=[], description="Conditions every result must meet (all of them).")
    measure: Measure = Field(
        default="rows",
        description="rows lists the matching entries; count counts them; distinct counts the different "
        "values of `field` among them; sum, average, min and max measure `field` over them.",
    )
    field: str | None = Field(
        default=None,
        description="The field sum, average, min and max measure, or whose different values distinct counts.",
    )
    group_by: str | None = Field(
        default=None,
        description="Measure each value of this field separately (largest first), with a measure other than "
        "rows; count counts the entries of each value.",
    )
    group_split: str | None = Field(
        default=None,
        description="With group_by, for a text field holding several values joined by a separator (as its "
        "examples show): the separator; each value is then measured on its own. A list field needs none.",
    )
    # R168: the example years are of no evaluated dataset (prompt-engineering skill)
    period: Period | None = Field(
        default=None,
        description="year or decade: with group_by, each group is the year or the decade of the grouped "
        "field; without group_by, distinct counts the different years or decades of `field`. The field "
        "must be a date or a text holding one (its examples show a year, as '1740' or '1740-05-02'). A "
        "decade is named by its first year, as 1740s.",
    )
    order_by: str | None = Field(default=None, description="rows: the field to sort the entries by.")
    descending: bool = Field(default=False, description="rows: sort from the largest value down.")
    offset: int = Field(
        default=0,
        description="rows and groups: how many entries to skip, to read the ones after those a cut list "
        "showed.",
    )


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
            issues += _condition_issues(f"condition {i} ({c.field})", c, fields)
    issues += _measure_issues(query, fields) + _page_issues(query)
    for name, value in (("group_by", query.group_by), ("order_by", query.order_by)):
        if value is not None and value not in fields:
            issues.append(_unknown(name, value, fields))
    if query.order_by is not None and query.measure != "rows":
        issues.append("order_by sorts listed rows; a measure is ordered largest first")
    if query.group_by is not None and query.measure == "rows":
        # R161: both compilers read group_by only with a measure (`shape_of`), so a group of listed rows ran
        # as the plain listing, group_by unread; LB11's agent got that listing and counted it by eye. order_by
        # is named too: the corrected query would otherwise be refused again, for order_by with a measure
        issues.append(
            "group_by needs a measure: rows lists the entries without groups; give measure count to count "
            "the entries of each value, without order_by (it sorts listed rows only)"
        )
    return issues + _split_issues(query, fields) + _period_issues(query, fields)


def period_kind(field: PropertyInfo) -> PeriodKind | None:
    """How a field's year, and so its decade, is read (R168): from a date (its year), from a text (the first
    four-digit run in it, `YEAR_IN_TEXT`), or not at all (a number, a boolean, a list, a time of day: None).
    The compilers render this answer and decide nothing themselves."""
    if field.type in _DATES:
        return "date"
    return "text" if field.type == "STRING" else None


def is_empty_test(operator: QueryOperator, value: object) -> bool:
    """Whether a condition tests for an empty cell: `=` or `!=` with the value `''`, the text the tools print
    an empty or missing value as (R167). Both compilers ask this and render "null or blank" for it; no other
    comparison reads `''` specially (a `contains` of '' is every text, `<` '' no number)."""
    return operator in ("=", "!=") and value == ""


def word_pattern(value: str) -> str:
    """The pattern that matches `value` as a whole word or phrase of a lower-cased text (R175): the value
    lower-cased, its regex characters escaped, with a boundary either side that is the text's start or end or
    a character that is no letter and no digit. Unanchored: DuckDB's `regexp_matches` searches as it is, and
    the Cypher compiler wraps it for Java's whole-string `=~`."""
    escaped = "".join(f"\\{ch}" if ch in _REGEX_SPECIAL else ch for ch in value.lower())
    return f"(^|{_NOT_WORD}){escaped}({_NOT_WORD}|$)"


def pair_kind(operator: QueryOperator, left: PropertyInfo, right: PropertyInfo) -> PairKind | None:
    """How field `left` compares with field `right` of the same entry by `operator`: by the year of `left` (a
    date) against `right` (`year`: a year; `years`: a date whose year is taken), as two values of one kind,
    or as two texts ignoring case; None when they cannot be compared (contains, in, a list, a text against a
    number, texts ordered by < <= > >=, dates of different types, a time of day with year). The compilers
    render this answer and decide nothing themselves."""
    left_date, right_date = left.type in _DATES, right.type in _DATES
    if operator == "year":
        if not left_date:
            return None
        return "years" if right_date else ("year" if right.type in _NUMBERS else None)
    if operator not in COMPARISONS:
        return None
    if left.type in _NUMBERS and right.type in _NUMBERS:
        return "value"
    if left_date and left.type == right.type:
        return "value"
    # a boolean or a text has no order: only equality
    if left.type == right.type and left.type in ("BOOLEAN", "STRING") and operator in ("=", "!="):
        return "text" if left.type == "STRING" else "value"
    return None


def _condition_issues(where: str, c: Condition, fields: dict[str, PropertyInfo]) -> list[str]:
    """R74's check of a value against its field's type (`fields[c.field]`); for `in`, of each value as `=`
    takes it; for another field, whether the two can be compared."""
    prop = fields[c.field]
    if c.other_field is not None:
        return _pair_issues(where, prop, c, fields)
    if c.value is None:
        # LC05's agent sent `= null` for an empty cell before trying `= ''` (R167): the hint names the way
        return [f"{where}: give value or other_field; to match an empty cell, compare with ''"]
    # by its name ignoring case, as = and != compare a text
    named = {name.lower(): name for name in fields}
    other = named.get(c.value.lower()) if isinstance(c.value, str) else None
    if c.operator in _PAIRED and other is not None and other != c.field:
        # compared as a literal, a field's name fails at run time (a date) or matches nothing (a text); only
        # for the operators other_field takes: a word searched for by contains may be spelled as a field.
        # `in` stays the way to match a text that really is spelled as a field.
        return [
            f"{where}: {c.value!r} is the field {other!r}; to compare with that field, give it as "
            "other_field; to match the text itself, use in with that one value"
        ]
    if c.operator == "in":
        if not isinstance(c.value, list) or not 1 <= len(c.value) <= MAX_IN:
            return [f"{where}: in needs a list of 1 to {MAX_IN} values, got {c.value!r}"]
        return [issue for v in c.value for issue in _value_issues(where, prop, "=", v)]
    if c.operator == "word":
        return _word_issues(where, prop, c.value)
    if isinstance(c.value, list):
        return [f"{where}: a list of values needs the operator in"]
    return _value_issues(where, prop, c.operator, c.value)


def _word_issues(where: str, prop: PropertyInfo, value: object) -> list[str]:
    """Whether `word` can search `prop` for `value` (R175): a text field, and a word or phrase that is not
    blank (a blank pattern would match every text)."""
    if prop.type not in _TEXTS:
        return [f"{where}: word needs a text field, {prop.name} is {prop.type}"]
    if not isinstance(value, str) or not value.strip():
        return [f"{where}: word needs a word or phrase, got {value!r}"]
    return []


def _value_issues(where: str, prop: PropertyInfo, operator: QueryOperator, value: object) -> list[str]:
    """R74's check of a value against its field's type, except the empty-cell test, which any field type
    takes (R167: a date or a number field refused `''` as no date and no number)."""
    return [] if is_empty_test(operator, value) else value_issues(where, prop, operator, value)


def _pair_issues(where: str, left: PropertyInfo, c: Condition, fields: dict[str, PropertyInfo]) -> list[str]:
    """Whether field `left` can be compared with `c.other_field` by `c.operator`, with no value given."""
    if c.value is not None:
        return [f"{where}: give value or other_field, not both"]
    if c.other_field not in fields:
        return [_unknown(f"{where} other_field", c.other_field or "", fields)]
    right = fields[c.other_field]
    if pair_kind(c.operator, left, right) is not None:
        return []
    issue = f"{where}: {left.name} ({left.type}) cannot be compared with {right.name} ({right.type})"
    kinds = {_family(left), _family(right)}
    # a date against a number is most likely the year of the date against a year, asked the wrong way
    hint = (
        "; to compare the year of a date with a year, use the operator year with the date as field and the "
        "year as other_field"
    )
    return [f"{issue} by {c.operator}{hint if kinds == {'date', 'number'} else ''}"]


def _family(prop: PropertyInfo) -> str:
    """A field's kind, for a refusal's hint: a date, a number or anything else (its own type)."""
    if prop.type in _DATES:
        return "date"
    return "number" if prop.type in _NUMBERS else prop.type


def _page_issues(query: StructuredQuery) -> list[str]:
    """Whether `offset` skips entries of a list: never fewer than none, and only of listed rows or groups. A
    code check rather than a pydantic bound, so the refusal carries its reason."""
    if query.offset < 0:
        return ["offset must be 0 or more"]
    if query.offset > 0 and shape_of(query) == "value":
        return ["offset pages listed rows or groups; a single measured value has none"]
    return []


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


def _period_issues(query: StructuredQuery, fields: dict[str, PropertyInfo]) -> list[str]:
    """Whether a period has a field to read (the grouped one, else the one distinct counts), is not asked
    of a split text, and reads a field that holds a year (`period_kind`). A missing or unknown field is
    reported by the group or measure check, not twice."""
    if query.period is None:
        return []
    if query.group_by is None and query.measure != "distinct":
        return [
            "period needs group_by or measure distinct: it is the year or decade of the grouped field, or "
            "of the field distinct counts"
        ]
    if query.group_split is not None:
        return ["period and group_split do not go together: a date holds one value"]
    target = query.group_by if query.group_by is not None else query.field
    if target is None or target not in fields:
        return []
    if period_kind(fields[target]) is None:
        kind = fields[target].type
        return [f"period {query.period} needs a date or a text holding one, {target} is {kind}"]
    return []


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
