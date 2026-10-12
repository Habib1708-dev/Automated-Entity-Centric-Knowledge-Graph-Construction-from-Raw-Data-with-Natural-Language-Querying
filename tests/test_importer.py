"""Domain graph import against a live Neo4j: reconciliation, idempotence, dangling references, and node rules
keyed by several columns (R155a)."""

import pytest

from kgbuilder.anchor import read_staged, record_nodes
from kgbuilder.audit.inputs import read_records
from kgbuilder.resolution.linking import read_record_keys
from kgbuilder.structured.importer import construct_domain_graph
from kgbuilder.structured.plan import ConstructionPlan, NodeRule
from kgbuilder.validation.qa_gold import RecordEvidence

from .sample_plans import FRAME_FILES, FRAME_PLAN, FRAME_RULE, GOOD_PLAN, node, rel, write_files

pytestmark = pytest.mark.neo4j


def count(driver, query):
    return driver.execute_query(query)[0][0]["c"]


def test_import_is_clean_and_idempotent(driver, data_dir):
    for _ in range(2):
        report = construct_domain_graph(driver, data_dir, GOOD_PLAN)
        assert report.clean
    assert count(driver, "MATCH (n:Assembly) RETURN count(n) AS c") == 4
    assert count(driver, "MATCH (:Product)-[r:CONTAINS]->(:Assembly) RETURN count(r) AS c") == 4
    assert count(driver, "MATCH (:Assembly)-[r:SUPPLIED_BY]->(:Supplier) RETURN count(r) AS c") == 4
    # values keep their CSV-inferred types instead of becoming strings
    assert count(driver, "MATCH (p:Product {product_id: 'P1'}) RETURN p.price AS c") == 199.5


def test_dangling_references_are_reported(driver, data_dir):
    plan = ConstructionPlan(
        nodes=[node("products.csv", "Product", "product_id")],
        relationships=[rel("dirty.csv", "ABOUT", "Product", "product_id", "Product", "product_id")],
    )
    report = construct_domain_graph(driver, data_dir, plan)
    assert not report.clean
    assert report.rules[1].rows_unmatched == 1  # the P9 row


def test_a_built_key_gives_one_node_per_row_and_relates_it_to_its_parent(driver, tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    for _ in range(2):  # MERGE on the built key: the second import changes nothing
        report = construct_domain_graph(driver, staged, FRAME_PLAN)
        assert report.clean
        assert [r.rows_written for r in report.rules] == [2, 5, 5]
        assert count(driver, "MATCH (n:Frame) RETURN count(n) AS c") == 5
        assert count(driver, "MATCH (:Frame)-[r:FRAME_OF]->(:Hive) RETURN count(r) AS c") == 5
    # the key is text; the properties keep their CSV-inferred types
    frame = driver.execute_query("MATCH (n:Frame {frame_key: '1|3'})-[:FRAME_OF]->(h:Hive) RETURN n, h")[0][0]
    assert frame["n"]["frame_no"] == 3 and frame["h"]["hive_id"] == 1


def test_the_graph_holds_the_built_keys_the_offline_readers_compute(driver, tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    construct_domain_graph(driver, staged, FRAME_PLAN)

    graph = {r["k"] for r in driver.execute_query("MATCH (n:Frame) RETURN toString(n.frame_key) AS k")[0]}
    offline = {r.key for r in read_records(staged, FRAME_PLAN) if r.label == "Frame"}
    placed = record_nodes(
        RecordEvidence(file="frames.csv", row={"hive_id": "2"}),
        FRAME_PLAN,
        read_staged(staged, {"frames.csv"}),
        {f"Frame:{k}" for k in graph},
    )
    assert graph == offline == {"1|1", "1|2", "1|3", "2|1", "2|3"}
    assert placed == ["Frame:2|1", "Frame:2|3"]
    # section matching reads plain keys only: a built key squashed ("13") could match a heading by chance
    assert sorted(k.key for k in read_record_keys(driver, FRAME_PLAN)) == ["1", "2"]


def test_built_keys_import_without_any_property_also_as_both_ends(driver, tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    bare = FRAME_RULE.model_copy(update={"properties": []})
    # a second rule on the same rows, keyed by the same cells in the other order
    slot = NodeRule(
        source_file="frames.csv",
        label="Slot",
        unique_column="slot_key",
        key_columns=["frame_no", "hive_id"],
        properties=[],
        description="",
    )
    plan = ConstructionPlan(
        nodes=[bare, slot],
        relationships=[rel("frames.csv", "IN_SLOT", "Frame", "frame_key", "Slot", "slot_key")],
    )
    report = construct_domain_graph(driver, staged, plan)
    assert report.clean and [r.rows_written for r in report.rules] == [5, 5, 5]
    assert (
        count(
            driver,
            "MATCH (:Frame {frame_key: '1|3'})-[r:IN_SLOT]->(:Slot {slot_key: '3|1'}) RETURN count(r) AS c",
        )
        == 1
    )


def test_a_row_with_an_empty_key_part_is_skipped_and_counted(driver, tmp_path):
    # a hand-edited plan reaches the importer even when validation would refuse it: the row is counted
    staged = write_files(tmp_path, {**FRAME_FILES, "frames.csv": "hive_id,frame_no,note\n1,1,a\n1,,b\n"})
    report = construct_domain_graph(driver, staged, FRAME_PLAN)
    frames, frame_of = report.rules[1], report.rules[2]
    assert (frames.rows_read, frames.rows_skipped_null_key, frames.rows_written) == (2, 1, 1)
    assert (frame_of.rows_skipped_null_key, frame_of.rows_written) == (1, 1)
    assert not report.clean
