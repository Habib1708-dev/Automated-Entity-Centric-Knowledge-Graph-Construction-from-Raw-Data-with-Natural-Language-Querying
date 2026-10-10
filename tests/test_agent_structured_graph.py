"""`query_records` against Neo4j (R134, agent/record_query.py run by structured_tools.py), on the shared
hand-made graph with the claims of test_hybrid_units.py and the schema read from it: the records related to
one record, by key and cut with their total; a count under an integer condition; a sum; the year of a date;
counts per value, largest first; text equal ignoring case; and the schema's record layer leaving the text
layer out. R139: counts per value of a list and of a text split at a separator, each value on its own.
Needs Neo4j."""

from kgbuilder.agent import Neo4jNodeReader
from kgbuilder.agent.structured_query import Condition, RecordQuery
from kgbuilder.agent.structured_tools import QueryRecords
from kgbuilder.query.graph_schema import read_graph_schema
from kgbuilder.query.graph_store import Neo4jGraphStore

from .test_hybrid_units import RELATED_PLAN, load


def tool(driver, limit=3) -> QueryRecords:
    load(driver)
    store = Neo4jGraphStore(driver, RELATED_PLAN, hops=2)
    reader = Neo4jNodeReader(driver, RELATED_PLAN)
    return QueryRecords(store, read_graph_schema(driver), RELATED_PLAN, reader, limit=limit, value_chars=60)


def test_records_related_to_one_record_are_listed_by_key_with_their_total(driver):
    result = tool(driver).run(RecordQuery(label="Part", related_to="Press:P1", relationship="PART_OF"))
    assert result.issues == []
    assert result.refs == ["Part:S1", "Part:S2", "Part:S3"] and result.total == 4  # four parts, three shown
    assert result.columns == ["name", "part_id"] and result.rows[0] == ["Spindle", "S1"]
    assert result.names == ["Spindle", "Gear", "Pin"]


def test_counts_sums_and_dates_run_on_the_records(driver):
    query = tool(driver)
    newer = query.run(RecordQuery(label="Press", where=[Condition(field="year", operator=">", value=2017)],
                                  measure="count"))  # fmt: skip
    assert newer.rows == [["1"]]
    assert query.run(RecordQuery(label="Press", measure="sum", field="year")).rows == [["4035"]]
    since = query.run(
        RecordQuery(label="Press", where=[Condition(field="since", operator="year", value=2020)])
    )
    assert since.refs == ["Press:P1"]
    named = query.run(
        RecordQuery(label="Press", where=[Condition(field="name", operator="=", value="lark press")])
    )
    assert named.refs == ["Press:P2"]


def test_counts_per_value_come_largest_first(driver):
    result = tool(driver).run(RecordQuery(label="Press", measure="count", group_by="active"))
    assert result.columns == ["group", "value"] and sorted(result.rows) == [["False", "1"], ["True", "1"]]
    assert result.total == 2


def test_a_group_over_a_list_or_a_split_text_counts_each_value(driver):
    load(driver)
    driver.execute_query(
        "MATCH (p:Press {press_id: 'P1'}) SET p.tags = ['oil', 'steel', 'oil'], p.notes = 'oil; steel' "
        "WITH 1 AS one MATCH (p:Press {press_id: 'P2'}) SET p.tags = ['oil'], p.notes = 'oil'"
    )
    store = Neo4jGraphStore(driver, RELATED_PLAN, hops=2)
    reader = Neo4jNodeReader(driver, RELATED_PLAN)
    query = QueryRecords(store, read_graph_schema(driver), RELATED_PLAN, reader, limit=5, value_chars=60)
    listed = query.run(RecordQuery(label="Press", measure="count", group_by="tags"))
    split = query.run(RecordQuery(label="Press", measure="count", group_by="notes", group_split=";"))
    assert listed.issues == [] and listed.rows == [["oil", "2"], ["steel", "1"]] and listed.total == 2
    assert split.issues == [] and split.rows == [["oil", "2"], ["steel", "1"]]
