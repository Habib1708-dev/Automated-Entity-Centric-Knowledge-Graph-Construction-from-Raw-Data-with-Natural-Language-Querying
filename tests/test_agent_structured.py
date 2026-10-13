"""The agent's structured tools (R134, agent/structured_query.py, record_query.py, table_query.py, tables.py
and structured_tools.py) without Neo4j: the code check of a typed query (unknown fields, values that do not
fit a field's type, a measure without the field it needs or with one it cannot read a number from, an order
on a measure); the records' Cypher as text (every value a parameter, the related record, the three shapes);
the tables' SQL run by DuckDB over small CSVs (types mapped, text equal ignoring case, words contained, a
text holding a number compared and summed as that number, a date compared and taken by its year, the year
inside a text date, counts per group largest first, lists cut with their total); and the tools: refusals
with their reasons, a failed run reported, rows with refs, a stable query id, the schema's text, and every
text the model reads free of the evaluated datasets' words. R139: a group over a field holding several
values counts each value (a list always; a text split at the separator the query gives), and the check
refuses a separator without a group, on a field that is no text, or made of letters or digits. R140: the
operator `in` (one of a list of values, each compared as `=` compares it) in the check, both compilers and
DuckDB; a path of relationships from the related record, compiled from the queried records' side, its types
and length checked by query_records. R148: a field compared with another field of the same entry (how each
pair of types compares, the refusals of a pair that cannot, of a value given with it and of a literal
spelled as a field), compiled to Cypher with no parameter and run by DuckDB; a later page by `offset`, its
statement unchanged at the first page, and the note naming the next offset; every argument described.
R161: a cut or paged listing's note says how to count some of its entries (a group's does not), and the
query it points to runs; a group of listed rows (group_by with measure rows) is refused with the measure that
counts each group, and the query that refusal points to runs. R164: a table's examples show each shape of a
text column (years and year-months), and the year operator is described for a text holding a date. R167:
`= ''` and `!= ''` test for an empty cell on a field of any type (the check, both compilers as text, DuckDB
over a CSV with empty cells), a null value's refusal points to `''`, and `''` compared otherwise is still
refused. R168: the measure `distinct` and a `period` (year or decade) of the grouped or the counted field:
the refusals (no field, a list, a period with nothing to read or on a number or a time of day, with a split),
the Cypher as text (a date's year, its decade, a text's year through APOC with the pattern parameter, and a
grouped count unchanged with no such parameter), DuckDB over a CSV with a text column of mixed date shapes
and a date column, and the descriptions naming distinct and decade."""

import pytest

from kgbuilder.agent.record_query import compile_records
from kgbuilder.agent.structured_query import (
    YEAR_IN_TEXT,
    Condition,
    RecordQuery,
    StructuredQuery,
    TableQuery,
    check_query,
    pair_kind,
)
from kgbuilder.agent.structured_tools import QueryRecords, QueryTables, Schema
from kgbuilder.agent.table_query import compile_table
from kgbuilder.agent.tables import DuckDBTables
from kgbuilder.hybrid.unit_store import CardStart
from kgbuilder.query.graph_schema import ClaimInfo, GraphSchema, LabelInfo, PropertyInfo, RelationshipInfo
from kgbuilder.query.graph_store import ReadResult

from .graphs import PLAN
from .test_agent_tools import DATASET_WORDS, FakeReader

# R161: what a cut or paged listing's note adds, as the model reads it (structured_tools._note)
COUNT_HINT = (
    "to count only some of them, add their condition to where and give measure count, without order_by or "
    "offset; a count covers every match, shown or not"
)


def prop(name: str, kind: str) -> PropertyInfo:
    return PropertyInfo(name=name, type=kind, examples=[])


FIELDS = {p.name: p for p in [prop("id", "STRING"), prop("price", "STRING"), prop("year", "INTEGER"),
                              prop("since", "DATE"), prop("tags", "LIST<STRING>"),
                              prop("at", "LOCAL TIME")]}  # fmt: skip


# --- the check ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "issue"),
    [
        (
            TableQuery(table="t", where=[Condition(field="colour", operator="=", value="red")]),
            "no field 'colour'",
        ),
        (TableQuery(table="t", where=[Condition(field="year", operator="=", value="old")]), "give a number"),
        (TableQuery(table="t", where=[Condition(field="since", operator="year", value="16")]), "four-digit"),
        (TableQuery(table="t", where=[Condition(field="since", operator=">", value="May")]), "YYYY-MM-DD"),
        (TableQuery(table="t", where=[Condition(field="since", operator="=", value="May")]), "YYYY-MM-DD"),
        (
            TableQuery(
                table="t", where=[Condition(field="since", operator="in", value=["2015-06-01", "May"])]
            ),
            "YYYY-MM-DD",
        ),
        (TableQuery(table="t", measure="sum"), "needs the field it measures"),
        (TableQuery(table="t", measure="count", field="year"), "takes no field"),
        (TableQuery(table="t", measure="sum", field="since"), "sum needs a number, since is DATE"),
        (TableQuery(table="t", measure="max", field="tags"), "cannot measure a list"),
        (TableQuery(table="t", measure="count", order_by="year"), "a measure is ordered largest first"),
        (TableQuery(table="t", group_by="colour", measure="count"), "group_by: no field 'colour'"),
        (TableQuery(table="t", measure="count", group_split=","), "group_split needs group_by"),
        (TableQuery(table="t", measure="count", group_by="year", group_split=","), "year is INTEGER"),
        (TableQuery(table="t", measure="count", group_by="id", group_split="x"), "no letter or digit"),
        (TableQuery(table="t", measure="count", group_by="id", group_split=""), "no letter or digit"),
        (TableQuery(table="t", where=[Condition(field="id", operator="in", value="K1")]), "in needs a list"),
        (TableQuery(table="t", where=[Condition(field="id", operator="in", value=[])]), "in needs a list"),
        (
            TableQuery(table="t", where=[Condition(field="id", operator="=", value=["K1"])]),
            "needs the operator in",
        ),
        (
            TableQuery(table="t", where=[Condition(field="year", operator="in", value=[2016, "old"])]),
            "give a number",
        ),
        # R148: a field compared with another field
        (
            TableQuery(table="t", where=[Condition(field="since", operator="=", other_field="year")]),
            "since (DATE) cannot be compared with year (INTEGER) by =; to compare the year of a date with a "
            "year, use the operator year with the date as field and the year as other_field",
        ),
        (  # a time of day has no year: refused here, not failed at run time
            TableQuery(table="t", where=[Condition(field="at", operator="year", other_field="year")]),
            "at (LOCAL TIME) cannot be compared with year (INTEGER) by year",
        ),
        (
            TableQuery(table="t", where=[Condition(field="since", operator="year", other_field="at")]),
            "cannot be compared",
        ),
        (
            TableQuery(table="t", where=[Condition(field="year", operator="year", other_field="since")]),
            "year (INTEGER) cannot be compared with since (DATE) by year",
        ),
        (
            TableQuery(table="t", where=[Condition(field="id", operator="contains", other_field="price")]),
            "cannot be compared",
        ),
        (
            TableQuery(table="t", where=[Condition(field="tags", operator="=", other_field="id")]),
            "cannot be compared",
        ),
        (  # a text against a number is refused, not read as the number the text holds
            TableQuery(table="t", where=[Condition(field="price", operator=">", other_field="year")]),
            "cannot be compared",
        ),
        (
            TableQuery(table="t", where=[Condition(field="since", operator="year", other_field="colour")]),
            "other_field: no field 'colour'",
        ),
        (
            TableQuery(
                table="t", where=[Condition(field="year", operator="=", value=2016, other_field="year")]
            ),
            "give value or other_field, not both",
        ),
        (  # R167: the hint for an empty cell, which LC05's agent asked for as null first
            TableQuery(table="t", where=[Condition(field="year", operator="=")]),
            "give value or other_field; to match an empty cell, compare with ''",
        ),
        (TableQuery(table="t", where=[Condition(field="price", operator="<", value="")]), "needs a number"),
        # R168: distinct needs a field it can count, a period a date or a text to read a year from
        (TableQuery(table="t", measure="distinct"), "distinct needs the field it measures"),
        (TableQuery(table="t", measure="distinct", field="tags"), "distinct cannot measure a list"),
        (
            TableQuery(table="t", measure="count", period="year"),
            "period needs group_by or measure distinct: it is the year or decade of the grouped field, or of "
            "the field distinct counts",
        ),
        (
            TableQuery(table="t", measure="count", group_by="year", period="decade"),
            "period decade needs a date or a text holding one, year is INTEGER",
        ),
        (TableQuery(table="t", measure="distinct", field="at", period="year"), "at is LOCAL TIME"),
        (
            TableQuery(table="t", measure="count", group_by="id", group_split=",", period="year"),
            "period and group_split do not go together: a date holds one value",
        ),
        (
            TableQuery(table="t", measure="distinct", field="id", offset=5),
            "offset pages listed rows or groups",
        ),
        (
            TableQuery(table="t", where=[Condition(field="since", operator="year", value="year")]),
            "'year' is the field 'year'; to compare with that field, give it as other_field; to match the "
            "text itself, use in with that one value",
        ),
        (
            TableQuery(table="t", where=[Condition(field="since", operator="=", value="year")]),
            "'year' is the field 'year'",
        ),
        (  # ignoring case, as = compares a text
            TableQuery(table="t", where=[Condition(field="id", operator="=", value="PRICE")]),
            "'PRICE' is the field 'price'",
        ),
        (TableQuery(table="t", offset=-1), "offset must be 0 or more"),
        (TableQuery(table="t", measure="count", offset=20), "offset pages listed rows or groups"),
        # R161: a group of listed rows is refused, not answered with the listing it would silently be
        (
            TableQuery(table="t", group_by="year"),
            "group_by needs a measure: rows lists the entries without groups; give measure count to count "
            "the entries of each value, without order_by (it sorts listed rows only)",
        ),
    ],
)
def test_the_check_refuses_names_and_values_the_source_does_not_have(query, issue):
    issues = check_query(query, FIELDS)
    assert any(issue in i for i in issues), issues


def test_the_check_accepts_a_text_measured_by_its_number_and_a_date_by_its_year():
    query = TableQuery(table="t", where=[Condition(field="since", operator="year", value=2016.0)],
                       measure="sum", field="price", group_by="year")  # fmt: skip
    assert check_query(query, FIELDS) == []


@pytest.mark.parametrize("field", ["id", "since", "year", "tags"])
def test_the_check_accepts_the_empty_cell_test_on_a_field_of_any_type(field):
    """R167: LC05's `date = ''` was refused as no date before, and matched nothing on a text; the empty-cell
    test is its own comparison, so a date, a number or a list field takes it like a text."""
    for operator in ("=", "!="):
        assert check_query(TableQuery(table="t", where=[Condition(field=field, operator=operator, value="")]),
                           FIELDS) == []  # fmt: skip
    some = TableQuery(table="t", where=[Condition(field=field, operator="in", value=[""])])
    assert check_query(some, FIELDS) == []  # '' among in's values, compared as = compares it


def test_the_check_accepts_distinct_counts_and_periods_of_a_date_or_a_text():
    """R168: the different values of any field but a list; the year or decade of a date or of a text (the
    schema's examples tell the model whether a text holds one), grouped or distinct-counted."""
    accepted = [
        TableQuery(table="t", measure="distinct", field="since", period="decade"),
        TableQuery(table="t", measure="distinct", field="price"),  # a text's values as they are
        TableQuery(table="t", measure="count", group_by="id", period="year"),
        TableQuery(table="t", measure="distinct", field="id", group_by="since", period="decade"),
    ]
    assert [check_query(q, FIELDS) for q in accepted] == [[], [], [], []]


def test_the_check_accepts_a_field_compared_with_another_field():
    pairs = [("since", "year", "year", "year"), ("since", "year", "since", "years"),
             ("id", "=", "price", "text"), ("year", "<", "year", "value")]  # fmt: skip
    for field, operator, other, kind in pairs:
        query = TableQuery(table="t", where=[Condition(field=field, operator=operator, other_field=other)])
        assert check_query(query, FIELDS) == [], (field, operator, other)
        assert pair_kind(operator, FIELDS[field], FIELDS[other]) == kind
    assert pair_kind("=", FIELDS["since"], FIELDS["year"]) is None
    assert pair_kind("=", FIELDS["since"], prop("seen", "LOCAL DATETIME")) is None  # dates of two types
    assert pair_kind("year", prop("seen", "LOCAL DATETIME"), FIELDS["since"]) == "years"
    flag, other_flag = prop("open", "BOOLEAN"), prop("shut", "BOOLEAN")
    assert pair_kind("=", flag, other_flag) == "value" and pair_kind("<", flag, other_flag) is None
    # a word searched for by contains may be spelled as a field: it is still a word
    words = TableQuery(table="t", where=[Condition(field="id", operator="contains", value="price")])
    assert check_query(words, FIELDS) == []
    # the way out for a text that really is spelled as a field: in with that one value
    spelled = TableQuery(table="t", where=[Condition(field="id", operator="in", value=["Price"])])
    assert check_query(spelled, FIELDS) == []


# --- the records' Cypher -----------------------------------------------------------------------------


def test_a_record_query_compiles_to_parameterised_cypher_of_its_shape():
    where = [
        Condition(field="price", operator=">", value="1,000"),
        Condition(field="id", operator="=", value="K1"),
    ]
    query = RecordQuery(label="Kettle", where=where, related_to="Maker:M1", relationship="MADE_BY")
    rows = compile_records(query, FIELDS, "id", [{"label": "Kettle", "key": "id"}], "4:x:7", 5)
    assert rows.shape == "rows"
    assert rows.statement.startswith("MATCH (n:`Kettle`)-[:`MADE_BY`]-(a) WHERE elementId(a) = $anchor AND ")
    assert "ORDER BY n.`id` WITH" in rows.statement and "K1" not in rows.statement  # values are parameters
    assert rows.parameters["v0"] == 1000.0 and rows.parameters["v1"] == "K1"
    assert (rows.parameters["anchor"], rows.parameters["limit"]) == ("4:x:7", 5)
    total = compile_records(
        RecordQuery(label="Kettle", measure="sum", field="price"), FIELDS, "id", [], None, 5
    )
    assert total.shape == "value" and "RETURN sum(toFloat(apoc.text.regreplace(" in total.statement
    groups = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="year"), FIELDS, "id", [], None, 5
    )
    assert groups.shape == "groups" and "WITH n.`year` AS bucket, count(n) AS value" in groups.statement


def test_one_of_several_values_and_a_path_of_relationships_compile_to_cypher():
    query = RecordQuery(label="Kettle", where=[Condition(field="id", operator="in", value=["K1", "K3"])],
                        related_to="Shop:S1", relationship=["SOLD_AT", "MADE_BY"])  # fmt: skip
    rows = compile_records(query, FIELDS, "id", [], "4:x:7", 5)
    # the path runs from the related record (a) to the queried ones (n): written from n, its types reversed
    assert rows.statement.startswith("MATCH (n:`Kettle`)-[:`MADE_BY`]-()-[:`SOLD_AT`]-(a) WHERE ")
    one_of = "(toLower(toString(n.`id`)) = toLower($v0_0) OR toLower(toString(n.`id`)) = toLower($v0_1))"
    assert one_of in rows.statement and (rows.parameters["v0_0"], rows.parameters["v0_1"]) == ("K1", "K3")


def test_a_group_over_a_list_or_a_split_text_counts_each_value_once_per_record():
    listed = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="tags"), FIELDS, "id", [], None, 5
    )
    assert "UNWIND n.`tags` AS bucket" in listed.statement and "WITH DISTINCT n, bucket" in listed.statement
    split = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="id", group_split=";"),
        FIELDS,
        "id",
        [],
        None,
        5,
    )
    assert "UNWIND split(n.`id`, $group_split) AS part" in split.statement
    assert split.parameters["group_split"] == ";" and "WITH DISTINCT n, part AS bucket" in split.statement


def test_distinct_counts_and_periods_compile_to_cypher_and_leave_other_statements_unchanged():
    """R168: a date's year by `.year`, a text's by APOC with the pattern parameter, a decade as text; the
    grouped count every recorded query ran keeps its statement and parameters, so its id (HC05's, among
    them) is what it was."""
    years = compile_records(
        RecordQuery(label="Kettle", measure="distinct", field="since", period="year"),
        FIELDS,
        "id",
        [],
        None,
        5,
    )
    assert years.shape == "value" and "RETURN count(DISTINCT n.`since`.year) AS value" in years.statement
    assert "year_pattern" not in years.parameters
    decades = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="since", period="decade"),
        FIELDS,
        "id",
        [],
        None,
        5,
    )
    assert "WITH toString((n.`since`.year / 10) * 10) + 's' AS bucket, count(n) AS value" in decades.statement
    text = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="id", period="year"), FIELDS, "id", [], None, 5
    )
    assert "WITH toInteger(apoc.text.regexGroups(toString(n.`id`), $year_pattern)[0][2]) AS bucket" in (
        text.statement
    )
    assert text.parameters["year_pattern"] == YEAR_IN_TEXT
    plain = compile_records(
        RecordQuery(label="Kettle", measure="distinct", field="price"), FIELDS, "id", [], None, 5
    )
    assert "RETURN count(DISTINCT n.`price`) AS value" in plain.statement  # as they are, not as numbers
    groups = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="year"), FIELDS, "id", [], None, 5
    )
    assert "WITH n.`year` AS bucket, count(n) AS value" in groups.statement
    assert "year_pattern" not in groups.parameters and "$year_pattern" not in groups.statement


def test_the_empty_cell_test_compiles_to_null_or_blank_with_no_parameter():
    """R167: a record's missing property is null, an empty text may be stored as '': both are empty."""
    empty = compile_records(
        RecordQuery(label="Kettle", where=[Condition(field="since", operator="=", value="")]),
        FIELDS, "id", [], None, 5,
    )  # fmt: skip
    assert "WHERE (n.`since` IS NULL OR toString(n.`since`) = '') WITH DISTINCT n" in empty.statement
    assert "v0" not in empty.parameters
    filled = compile_records(
        RecordQuery(label="Kettle", where=[Condition(field="year", operator="!=", value="")]),
        FIELDS, "id", [], None, 5,
    )  # fmt: skip
    assert "WHERE (n.`year` IS NOT NULL AND toString(n.`year`) <> '') WITH DISTINCT n" in filled.statement
    either = compile_records(
        RecordQuery(label="Kettle", where=[Condition(field="id", operator="in", value=["", "K1"])]),
        FIELDS, "id", [], None, 5,
    )  # fmt: skip
    assert "((n.`id` IS NULL OR toString(n.`id`) = '') OR toLower(toString(n.`id`)) = toLower($v0_1))" in (
        either.statement
    )
    assert either.parameters["v0_1"] == "K1" and "v0_0" not in either.parameters


def test_a_field_compared_with_another_field_and_a_later_page_compile_to_cypher():
    paired = [Condition(field="since", operator="year", other_field="year")]
    count = compile_records(
        RecordQuery(label="Kettle", where=paired, measure="count"), FIELDS, "id", [], None, 5
    )
    assert "WHERE n.`since`.year = n.`year` WITH DISTINCT n" in count.statement
    assert "$v0" not in count.statement and "v0" not in count.parameters  # two names, no value
    years = [Condition(field="since", operator="year", other_field="since")]
    both = compile_records(RecordQuery(label="Kettle", where=years), FIELDS, "id", [], None, 5)
    assert "WHERE n.`since`.year = n.`since`.year WITH DISTINCT n" in both.statement
    texts = [Condition(field="id", operator="!=", other_field="price")]
    differ = compile_records(RecordQuery(label="Kettle", where=texts), FIELDS, "id", [], None, 5)
    assert "toLower(toString(n.`id`)) <> toLower(toString(n.`price`))" in differ.statement
    first = compile_records(RecordQuery(label="Kettle"), FIELDS, "id", [], None, 5)
    later = compile_records(RecordQuery(label="Kettle", offset=20), FIELDS, "id", [], None, 5)
    assert "UNWIND found[$offset..$offset + $limit] AS n" in later.statement
    assert later.parameters["offset"] == 20
    # the first page is the statement it always was: its id, and every recorded one, unchanged
    assert "UNWIND found[0..$limit] AS n" in first.statement and "offset" not in first.parameters
    groups = compile_records(
        RecordQuery(label="Kettle", measure="count", group_by="year", offset=5), FIELDS, "id", [], None, 5
    )
    assert "UNWIND found[$offset..$offset + $limit] AS row" in groups.statement


# --- the tables' SQL on DuckDB -----------------------------------------------------------------------


@pytest.fixture
def tables(tmp_path):
    (tmp_path / "kettles.csv").write_text(
        "id,name,price,made,since,noted\n"
        'K1,Meridian Kettle,"$1,289",2016,2016-05-01,seen 11/07/2016\n'
        "K2,Lark Kettle,$240,2016,2018-01-02,seen 01/02/2018\n"
        "K3,Quill Pot,$99,2019,2019-03-04,none\n",
        encoding="utf-8",
    )
    store = DuckDBTables(tmp_path)
    yield store
    store.close()


def run_table(tables, **query) -> list[list]:
    fields = {c.name: c for c in tables.tables()[0].columns}
    compiled = compile_table(TableQuery(table="kettles", **query), fields, 2)
    read = tables.run(compiled.statement, compiled.parameters)
    assert read.error is None, read.error
    return read.rows


def test_a_table_staged_in_a_subfolder_is_read_and_named_by_its_path(tmp_path):
    """A dataset whose files sit in folders stages them in folders (structured/staging.py); the store read
    only the top level, so the agent saw no table of such a dataset (found in R164: generality)."""
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "kettles.csv").write_text("id,made\nK1,2016\nK2,2019\n", encoding="utf-8")
    (tmp_path / "lids.csv").write_text("id\nL1\n", encoding="utf-8")
    store = DuckDBTables(tmp_path)
    try:
        assert [t.name for t in store.tables()] == ["lids", "shop/kettles"]  # a top-level name is unchanged
        fields = {c.name: c for c in store.tables()[1].columns}
        compiled = compile_table(TableQuery(table="shop/kettles", measure="count"), fields, 2)
        assert store.run(compiled.statement, compiled.parameters).rows == [[2]]
    finally:
        store.close()


def test_the_staged_tables_are_read_with_their_types_and_examples(tables):
    [info] = tables.tables()
    types = {c.name: c.type for c in info.columns}
    assert (info.name, info.rows) == ("kettles", 3)
    assert types == {"id": "STRING", "name": "STRING", "price": "STRING", "made": "INTEGER", "since": "DATE",
                     "noted": "STRING"}  # fmt: skip
    assert info.columns[0].examples == ["'K1'", "'K2'", "'K3'"]


def test_a_tables_examples_show_each_shape_of_a_text_column(tmp_path):
    """R164: a text column of years and year-months showed its three lowest years only, and the agent
    compared with '=' a year and lost the month-dated rows; the examples now show both forms, and a column
    of one form (the numbers here) still shows its lowest values."""
    (tmp_path / "visits.csv").write_text(
        "id,seen,count\nV1,2001,5\nV2,2002,7\nV3,2003,1\nV4,2004-06,9\nV5,2005,3\n", encoding="utf-8"
    )
    store = DuckDBTables(tmp_path)
    columns = {c.name: c for c in store.tables()[0].columns}
    store.close()
    assert (columns["seen"].type, columns["seen"].examples) == ("STRING", ["'2001'", "'2002'", "'2004-06'"])
    assert columns["count"].examples == ["1", "3", "5"]


def test_the_year_operator_is_described_for_a_text_holding_a_date():
    """R164: the year of a text that holds a date is read by both compilers (the `noted` condition above);
    the operator's description says so, for a text column whose examples show several date forms."""
    described = Condition.model_fields["operator"].description
    assert "year for the year of a date or of a text holding one" in described


def test_table_conditions_follow_the_column_types(tables):
    def ids(*conditions):
        return [row[0] for row in run_table(tables, where=[Condition(**c) for c in conditions])]

    assert ids({"field": "name", "operator": "=", "value": "meridian kettle"}) == ["K1"]
    assert ids({"field": "name", "operator": "contains", "value": "KETTLE"}) == ["K1", "K2"]
    assert ids({"field": "price", "operator": ">", "value": 200}) == ["K1", "K2"]  # '$1,289' is 1289
    assert ids({"field": "since", "operator": ">=", "value": "2018-01-02"}) == ["K2", "K3"]
    assert ids({"field": "since", "operator": "year", "value": 2016}) == ["K1"]
    assert ids({"field": "noted", "operator": "year", "value": 2018}) == ["K2"]  # the year inside a text date
    assert ids({"field": "made", "operator": "!=", "value": 2016}) == ["K3"]
    assert ids({"field": "id", "operator": "in", "value": ["k1", "K3"]}) == ["K1", "K3"]  # each ignoring case
    assert ids({"field": "made", "operator": "in", "value": [2019, 2020]}) == ["K3"]


def test_table_measures_and_groups(tables):
    assert run_table(tables, measure="sum", field="price") == [[1628.0]]
    assert run_table(tables, measure="count") == [[3]]
    assert run_table(tables, measure="count", group_by="made") == [[2016, 2, 2], [2019, 1, 2]]


def test_a_table_counts_distinct_values_and_groups_by_the_year_or_decade_of_a_date_or_a_text(tmp_path):
    """R168: HC05 asked how many different years a date column holds (the agent grouped 29 dates and
    counted by eye); LC07 which decade a text column of mixed shapes names most. A text without a year is in
    no group and no count; a year read from a text is a number, a decade a text in both columns."""
    rows = ["id,founded,since", "K1,1680,2016-05-01", "K2,1859-03-01,2018-01-02", "K3,,2019-03-04",
            "K4,none,2019-07-07", "K5,seen 11/07/2016,"]  # fmt: skip
    (tmp_path / "pots.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    store = DuckDBTables(tmp_path)
    fields = {c.name: c for c in store.tables()[0].columns}
    assert (fields["founded"].type, fields["since"].type) == ("STRING", "DATE")

    def run(**query):
        compiled = compile_table(TableQuery(table="pots", **query), fields, 5)
        read = store.run(compiled.statement, compiled.parameters)
        assert read.error is None, read.error
        return read.rows

    try:
        assert run(measure="distinct", field="founded") == [[4]]  # '' is NULL; 'none' is a value
        assert run(measure="distinct", field="founded", period="year") == [[3]]  # 1680, 1859, 2016
        assert run(measure="distinct", field="since", period="year") == [[3]]  # 2016, 2018, 2019
        assert run(measure="count", group_by="founded", period="decade") == [
            ["1680s", 1, 3], ["1850s", 1, 3], ["2010s", 1, 3]
        ]  # fmt: skip
        assert run(measure="count", group_by="since", period="year") == [
            [2019, 2, 3],
            [2016, 1, 3],
            [2018, 1, 3],
        ]
    finally:
        store.close()


def test_a_table_empty_cell_is_matched_by_the_empty_text_on_a_text_and_on_a_date_column(tmp_path):
    """R167: DuckDB reads an empty CSV cell as NULL, which `= ''` compared as a text never matched; LC05's
    count of the undated entries came back 0 for 9. Both cells here are NULL (a date column takes no blank
    text), and the test names both NULL and the blank so a stored '' counts too."""
    (tmp_path / "pots.csv").write_text(
        "id,since,note\nK1,2016-05-01,seen\nK2,,\nK3,2019-03-04,\n", encoding="utf-8"
    )
    store = DuckDBTables(tmp_path)
    fields = {c.name: c for c in store.tables()[0].columns}
    assert (fields["since"].type, fields["note"].type) == ("DATE", "STRING")

    def count(field, operator, value):
        condition = Condition(field=field, operator=operator, value=value)
        query = TableQuery(table="pots", measure="count", where=[condition])
        compiled = compile_table(query, fields, 5)
        assert "v0" not in compiled.parameters or operator == "in"  # the empty test names no parameter
        read = store.run(compiled.statement, compiled.parameters)
        assert read.error is None, read.error
        return read.rows[0][0]

    try:
        assert (count("since", "=", ""), count("since", "!=", "")) == (1, 2)
        assert (count("note", "=", ""), count("note", "!=", "")) == (2, 1)
        assert count("note", "in", ["", "SEEN"]) == 3  # the empty ones, or a text as = compares it
    finally:
        store.close()


def test_a_table_group_over_a_text_holding_several_values_counts_each_value(tmp_path):
    (tmp_path / "pots.csv").write_text(
        'id,metals\nK1,"steel, glass"\nK2,glass\nK3,"steel,copper,steel"\nK4,\n', encoding="utf-8"
    )
    store = DuckDBTables(tmp_path)
    fields = {c.name: c for c in store.tables()[0].columns}
    whole = compile_table(TableQuery(table="pots", measure="count", group_by="metals"), fields, 10)
    each = compile_table(
        TableQuery(table="pots", measure="count", group_by="metals", group_split=","), fields, 10
    )
    assert len(store.run(whole.statement, whole.parameters).rows) == 3  # each whole text its own group
    read = store.run(each.statement, each.parameters)
    store.close()
    assert read.error is None, read.error
    # K3 names steel twice and counts once; K4's empty value is no group
    assert read.rows == [["glass", 2, 3], ["steel", 2, 3], ["copper", 1, 3]]


def test_table_rows_are_cut_with_their_total(tables):
    rows = run_table(tables, order_by="made", descending=True)
    assert [r[0] for r in rows] == ["K3", "K1"] and rows[0][-1] == 3  # the window total before the cut


def test_table_conditions_compare_two_columns_of_one_row(tmp_path):
    (tmp_path / "trips.csv").write_text(
        "id,opened,closed,made,label,alias\n"
        "T1,2016-05-01,2016-05-01,2016,Quill,quill\n"
        "T2,2017-02-03,2017-03-01,2016,Lark,Wren\n"
        "T3,2019-01-01,2019-01-09,2019,Fen,FEN\n",
        encoding="utf-8",
    )
    store = DuckDBTables(tmp_path)
    fields = {c.name: c for c in store.tables()[0].columns}

    def rows(field, operator, other, measure="rows"):
        paired = Condition(field=field, operator=operator, other_field=other)
        query = TableQuery(table="trips", where=[paired], measure=measure)
        assert check_query(query, fields) == []
        compiled = compile_table(query, fields, 10)
        read = store.run(compiled.statement, compiled.parameters)
        assert read.error is None, read.error
        return [row[0] for row in read.rows] if measure == "rows" else read.rows

    assert rows("opened", "=", "closed") == ["T1"]
    assert rows("opened", "year", "made") == ["T1", "T3"]
    assert rows("opened", "year", "made", measure="count") == [[2]]  # how many in one call, no listing
    assert rows("opened", "year", "closed") == ["T1", "T2", "T3"]  # the year of another date
    assert rows("label", "=", "alias") == ["T1", "T3"]  # ignoring case
    assert rows("closed", ">", "opened") == ["T2", "T3"]
    store.close()


# --- the tools ---------------------------------------------------------------------------------------


def test_query_tables_answers_rows_with_their_total_and_a_stable_id(tables):
    tool = QueryTables(tables, limit=2, value_chars=6)
    result = tool.run(TableQuery(table="kettles", order_by="id"))
    assert result.columns == ["id", "name", "price", "made", "since", "noted"]
    assert result.rows[0][:3] == ["K1", "Meridi...", "$1,289"]  # a long value cut
    assert (result.total, result.note) == (3, f"1-2 of 3 shown; offset 2 shows the next ones; {COUNT_HINT}")
    again = tool.run(TableQuery(table="kettles", order_by="id"))
    assert result.query_id == again.query_id and result.query_id.startswith("q-")
    assert result.cites() == [result.query_id]
    value = tool.run(TableQuery(table="kettles", measure="average", field="made"))
    assert (value.rows, value.total) == ([["2017.0"]], 1)


def test_query_tables_pages_a_cut_list_by_offset(tables):
    assert check_query(TableQuery(table="t", offset=20), FIELDS) == []
    assert check_query(TableQuery(table="t", measure="count", group_by="year", offset=5), FIELDS) == []
    tool = QueryTables(tables, limit=2, value_chars=60)
    first = tool.run(TableQuery(table="kettles", order_by="id"))
    assert first.note == f"1-2 of 3 shown; offset 2 shows the next ones; {COUNT_HINT}"
    last = tool.run(TableQuery(table="kettles", order_by="id", offset=2))
    assert ([r[0] for r in last.rows], last.total, last.note) == (["K3"], 3, f"3-3 of 3 shown; {COUNT_HINT}")
    assert last.query_id != first.query_id  # a page is a run of its own, cited by its own id
    past = tool.run(TableQuery(table="kettles", order_by="id", offset=5))
    assert (past.rows, past.note) == ([], "Nothing past the first 5.")
    assert run_table(tables, measure="count", group_by="made", offset=1) == [[2019, 1, 2]]
    whole = tool.run(TableQuery(table="kettles", measure="count", group_by="made"))
    assert whole.note == ""  # every group shown at once: nothing to say


def test_a_cut_or_paged_listing_points_to_the_count_and_a_group_does_not(tables):
    """R161: LB11's agent paged through 40 listed rows and counted them by eye (11 for 13); a listing cut or
    paged says the count measures every match. Groups are a measure already, and a list shown whole needs
    no count to be read."""
    tool = QueryTables(tables, limit=1, value_chars=60)
    cut = tool.run(TableQuery(table="kettles", order_by="id"))
    assert cut.note == f"1-1 of 3 shown; offset 1 shows the next ones; {COUNT_HINT}"
    groups = tool.run(TableQuery(table="kettles", measure="count", group_by="made"))
    assert groups.note == "1-1 of 2 shown; offset 1 shows the next ones"
    paged = tool.run(TableQuery(table="kettles", measure="count", group_by="made", offset=1))
    assert paged.note == "2-2 of 2 shown"
    whole = QueryTables(tables, limit=5, value_chars=60).run(TableQuery(table="kettles"))
    assert (whole.total, whole.note) == (3, "")
    assert not DATASET_WORDS.search(COUNT_HINT)


def test_the_count_the_note_points_to_runs_from_an_ordered_paged_listing(tables):
    """R161: the note's own total already counts every match, so the hint names the step that counts some of
    them: their condition in `where` and measure count, with order_by and offset dropped, since the check
    refuses both with a measure. Followed as worded from an ordered later page, the count runs."""
    tool = QueryTables(tables, limit=1, value_chars=60)
    page = tool.run(TableQuery(table="kettles", order_by="id", offset=1))
    assert page.note == f"2-2 of 3 shown; offset 2 shows the next ones; {COUNT_HINT}"
    some = [Condition(field="made", operator="=", value=2016)]
    followed = TableQuery(table="kettles", where=some, measure="count")  # order_by and offset left out
    counted = tool.run(followed)
    assert (counted.issues, counted.rows) == ([], [["2"]])


def test_a_group_of_listed_rows_is_refused_by_the_tool_not_answered_with_the_listing(tables):
    """R161: LB11's agent asked for listed rows grouped by a column and got the plain listing back, group_by
    unread by the compiler; refused, the reason names the measure that counts each group and that order_by
    goes, so the query it points to runs at the next call instead of meeting the order_by refusal."""
    tool = QueryTables(tables, limit=5, value_chars=60)
    refused = tool.run(TableQuery(table="kettles", group_by="made", order_by="id"))
    assert refused.rows == [] and refused.cites() == []
    assert refused.issues == [
        "group_by needs a measure: rows lists the entries without groups; give measure count to count "
        "the entries of each value, without order_by (it sorts listed rows only)"
    ]
    followed = tool.run(TableQuery(table="kettles", group_by="made", measure="count"))
    assert (followed.issues, followed.rows) == ([], [["2016", "2"], ["2019", "1"]])


def test_query_tables_refuses_an_unknown_table_and_a_query_that_fails_the_check(tables):
    tool = QueryTables(tables, limit=2, value_chars=60)
    unknown = tool.run(TableQuery(table="pots"))
    assert unknown.issues == ["no table 'pots'; the tables are kettles"] and unknown.cites() == []
    refused = tool.run(TableQuery(table="kettles", measure="sum", field="since"))
    assert refused.issues == ["sum needs a number, since is DATE"]
    assert refused.note == "The query was refused; correct it and ask again."


class FakeCypher:
    """`CypherStore` answering every read with fixed rows; records the statements."""

    def __init__(self, result: ReadResult):
        self._result = result
        self.reads: list[tuple[str, dict]] = []

    def run_read(self, cypher, parameters):
        self.reads.append((cypher, parameters))
        return self._result


SCHEMA = GraphSchema(
    labels=[LabelInfo(label="Press", count=2,
                      properties=[prop("press_id", "STRING"), prop("year", "INTEGER")]),
            LabelInfo(label="Part", count=1, properties=[prop("part_id", "STRING")]),
            LabelInfo(label="Chunk", count=9, properties=[prop("chunk_id", "STRING")])],
    relationships=[RelationshipInfo(source="Part", type="PART_OF", target="Press", count=1),
                   RelationshipInfo(source="Chunk", type="PART_OF", target="Document", count=9)],
    claims=[ClaimInfo(subject_type="Component", predicate="HAS_CONDITION", object_type="Condition", count=3)],
)  # fmt: skip


def query_records(result=None, reader=None):
    store = FakeCypher(result or ReadResult())
    return QueryRecords(store, SCHEMA, PLAN, reader or FakeReader(), limit=2, value_chars=60), store


@pytest.mark.parametrize(
    ("query", "issue"),
    [
        (RecordQuery(label="Chunk"), "no record label 'Chunk'; the labels are Part, Press"),
        (RecordQuery(label="Part", related_to="Press:P1"), "related_to and relationship go together"),
        (
            RecordQuery(label="Part", related_to="Press:P1", relationship="MADE_BY"),
            "no relationship 'MADE_BY'",
        ),
        (RecordQuery(label="Part", related_to="k-1", relationship="PART_OF"), "names no record"),
        (
            RecordQuery(label="Part", related_to="Press:P1", relationship=["PART_OF", "MADE_BY"]),
            "no relationship 'MADE_BY'",
        ),
        (
            RecordQuery(label="Part", related_to="Press:P1", relationship=["PART_OF"] * 4),
            "at most 3 relationships",
        ),
        (RecordQuery(label="Part", related_to="Press:P1", relationship=[]), "related_to and relationship"),
    ],
)
def test_query_records_refuses_what_the_record_layer_does_not_hold(query, issue):
    tool, store = query_records(reader=FakeReader(starts={"k-1": CardStart(kind="kind", node_id="k-1")}))
    result = tool.run(query)
    assert any(issue in i for i in result.issues), result.issues
    assert store.reads == []  # nothing runs before the check passes


def test_query_records_lists_records_with_their_refs_and_reports_a_failed_run():
    found = [[5, "Part:S1", {"part_id": "S1"}], [5, "Part:S2", {"part_id": "S2"}]]
    rows = ReadResult(keys=["total", "ref", "props"], rows=found)
    reader = FakeReader(starts={"Press:P1": CardStart(kind="thing", node_id="4:x:1")})
    tool, store = query_records(rows, reader)
    result = tool.run(RecordQuery(label="Part", related_to="Press:P1", relationship="PART_OF"))
    assert (result.rows, result.refs, result.total) == ([["S1"], ["S2"]], ["Part:S1", "Part:S2"], 5)
    assert result.names == ["S1", "S2"]  # no name in the rows: the key the ref ends in
    assert result.cites() == [result.query_id, "Part:S1", "Part:S2"]
    assert store.reads[0][1]["anchor"] == "4:x:1"
    failed, _ = query_records(ReadResult(error="the timeout"))
    result = failed.run(RecordQuery(label="Press"))
    assert (result.issues, result.note, result.cites()) == (["the timeout"], "The query failed to run.", [])


def test_the_schema_shows_the_record_layer_the_claim_patterns_and_the_tables(tables):
    text = Schema(SCHEMA, PLAN, tables).run(Schema.arguments()).text
    assert ":Press (2): press_id (STRING)" in text and "(:Part)-[:PART_OF]->(:Press)" in text
    assert "Chunk" not in text and "Document" not in text  # the text layer is the text tools'
    assert "- Component HAS_CONDITION Condition (3)" in text
    assert "- kettles (3): id (STRING) e.g. 'K1', 'K2', 'K3'" in text


def test_the_measure_and_the_period_are_described_with_distinct_and_decade():
    """R168: the model learns the two from their descriptions alone (no example in PROMPT)."""
    fields = StructuredQuery.model_fields
    assert "distinct counts the different values of `field`" in (fields["measure"].description or "")
    assert "whose different values distinct counts" in (fields["field"].description or "")
    assert "the year or the decade of the grouped field" in (fields["period"].description or "")
    assert "A decade is named by its first year, as 1740s" in (fields["period"].description or "")
    assert all("different values" in t.description for t in (QueryRecords, QueryTables))
    assert all("year or decade" in t.description for t in (QueryRecords, QueryTables))


@pytest.mark.parametrize("tool", [Schema, QueryRecords, QueryTables], ids=lambda t: t.name)
def test_what_the_model_reads_of_a_structured_tool_names_no_evaluated_dataset(tool):
    models = [tool.arguments, Condition]
    texts = [tool.description] + [f.description or "" for m in models for f in m.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
    assert all(f.description for m in models for f in m.model_fields.values())  # every argument explained
