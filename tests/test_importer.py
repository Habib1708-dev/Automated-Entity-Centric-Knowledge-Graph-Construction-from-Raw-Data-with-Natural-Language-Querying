"""Domain graph import against a live Neo4j: reconciliation, idempotence, dangling references."""

import pytest

from kgbuilder.structured.importer import construct_domain_graph
from kgbuilder.structured.plan import ConstructionPlan

from .sample_plans import GOOD_PLAN, node, rel

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
