"""The agent's structured tools (R134, agent/structured_query.py, record_query.py, table_query.py, tables.py
and structured_tools.py) without Neo4j: the code check of a typed query (unknown fields, values that do not
fit a field's type, a measure without the field it needs or with one it cannot read a number from, an order
on a measure); the records' Cypher as text (every value a parameter, the related record, the three shapes);
the tables' SQL run by DuckDB over small CSVs (types mapped, text equal ignoring case, words contained, a
text holding a number compared and summed as that number, a date compared and taken by its year, the year
inside a text date, counts per group largest first, lists cut with their total); and the tools: refusals
with their reasons, a failed run reported, rows with refs, a stable query id, the schema's text, and every
text the model reads free of the evaluated datasets' words."""

import pytest

from kgbuilder.agent.record_query import compile_records
from kgbuilder.agent.structured_query import Condition, RecordQuery, TableQuery, check_query
from kgbuilder.agent.structured_tools import QueryRecords, QueryTables, Schema
from kgbuilder.agent.table_query import compile_table
from kgbuilder.agent.tables import DuckDBTables
from kgbuilder.hybrid.unit_store import CardStart
from kgbuilder.query.graph_schema import ClaimInfo, GraphSchema, LabelInfo, PropertyInfo, RelationshipInfo
from kgbuilder.query.graph_store import ReadResult

from .graphs import PLAN
from .test_agent_tools import DATASET_WORDS, FakeReader


def prop(name: str, kind: str) -> PropertyInfo:
    return PropertyInfo(name=name, type=kind, examples=[])


FIELDS = {p.name: p for p in [prop("id", "STRING"), prop("price", "STRING"), prop("year", "INTEGER"),
                              prop("since", "DATE"), prop("tags", "LIST<STRING>")]}  # fmt: skip


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
        (TableQuery(table="t", measure="sum"), "needs the field it measures"),
        (TableQuery(table="t", measure="count", field="year"), "takes no field"),
        (TableQuery(table="t", measure="sum", field="since"), "sum needs a number, since is DATE"),
        (TableQuery(table="t", measure="max", field="tags"), "cannot measure a list"),
        (TableQuery(table="t", measure="count", order_by="year"), "a measure is ordered largest first"),
        (TableQuery(table="t", group_by="colour", measure="count"), "group_by: no field 'colour'"),
    ],
)
def test_the_check_refuses_names_and_values_the_source_does_not_have(query, issue):
    issues = check_query(query, FIELDS)
    assert any(issue in i for i in issues), issues


def test_the_check_accepts_a_text_measured_by_its_number_and_a_date_by_its_year():
    query = TableQuery(table="t", where=[Condition(field="since", operator="year", value=2016.0)],
                       measure="sum", field="price", group_by="year")  # fmt: skip
    assert check_query(query, FIELDS) == []


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


def test_the_staged_tables_are_read_with_their_types_and_examples(tables):
    [info] = tables.tables()
    types = {c.name: c.type for c in info.columns}
    assert (info.name, info.rows) == ("kettles", 3)
    assert types == {"id": "STRING", "name": "STRING", "price": "STRING", "made": "INTEGER", "since": "DATE",
                     "noted": "STRING"}  # fmt: skip
    assert info.columns[0].examples == ["'K1'", "'K2'", "'K3'"]


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


def test_table_measures_and_groups(tables):
    assert run_table(tables, measure="sum", field="price") == [[1628.0]]
    assert run_table(tables, measure="count") == [[3]]
    assert run_table(tables, measure="count", group_by="made") == [[2016, 2, 2], [2019, 1, 2]]


def test_table_rows_are_cut_with_their_total(tables):
    rows = run_table(tables, order_by="made", descending=True)
    assert [r[0] for r in rows] == ["K3", "K1"] and rows[0][-1] == 3  # the window total before the cut


# --- the tools ---------------------------------------------------------------------------------------


def test_query_tables_answers_rows_with_their_total_and_a_stable_id(tables):
    tool = QueryTables(tables, limit=2, value_chars=6)
    result = tool.run(TableQuery(table="kettles", order_by="id"))
    assert result.columns == ["id", "name", "price", "made", "since", "noted"]
    assert result.rows[0][:3] == ["K1", "Meridi...", "$1,289"]  # a long value cut
    assert (result.total, result.note) == (3, "2 of 3 shown")
    again = tool.run(TableQuery(table="kettles", order_by="id"))
    assert result.query_id == again.query_id and result.query_id.startswith("q-")
    assert result.cites() == [result.query_id]
    value = tool.run(TableQuery(table="kettles", measure="average", field="made"))
    assert (value.rows, value.total) == ([["2017.0"]], 1)


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


@pytest.mark.parametrize("tool", [Schema, QueryRecords, QueryTables], ids=lambda t: t.name)
def test_what_the_model_reads_of_a_structured_tool_names_no_evaluated_dataset(tool):
    models = [tool.arguments, Condition]
    texts = [tool.description] + [f.description or "" for m in models for f in m.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
