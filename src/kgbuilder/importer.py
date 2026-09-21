"""Rule-based import of a construction plan into Neo4j, with a reconciliation report."""

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import duckdb
from neo4j import Driver, GraphDatabase
from pydantic import BaseModel

from .config import settings
from .plan import ConstructionPlan, NodeRule, RelationshipRule
from .profiler import quote_ident, read_csv

BATCH_SIZE = 1000


class RuleReport(BaseModel):
    rule: str
    rows_read: int
    rows_skipped_null_key: int
    rows_written: int  # nodes merged, or relationship rows whose endpoints were both found

    @property
    def rows_unmatched(self) -> int:
        return self.rows_read - self.rows_skipped_null_key - self.rows_written


class ImportReport(BaseModel):
    rules: list[RuleReport]

    @property
    def clean(self) -> bool:
        return all(r.rows_unmatched == 0 and r.rows_skipped_null_key == 0 for r in self.rules)


def get_driver() -> Driver:
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))


def cypher_ident(name: str) -> str:
    # labels, types and property keys cannot be query parameters, so escape them instead
    return "`" + name.replace("`", "``") + "`"


def _to_neo4j(value):
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (bool, int, float, str, date, datetime, time)):
        return value
    return str(value)


def _read_rows(data_dir: Path, source_file: str, columns: list[str]) -> list[dict]:
    con = duckdb.connect()
    select = ", ".join(quote_ident(c) for c in dict.fromkeys(columns))
    relation = read_csv(con, data_dir / source_file).query("src", f"SELECT {select} FROM src")
    names = relation.columns
    return [{n: _to_neo4j(v) for n, v in zip(names, row)} for row in relation.fetchall()]


def _batches(rows: list[dict]):
    for i in range(0, len(rows), BATCH_SIZE):
        yield rows[i : i + BATCH_SIZE]


def import_nodes(driver: Driver, data_dir: Path, rule: NodeRule) -> RuleReport:
    label, key = cypher_ident(rule.label), cypher_ident(rule.unique_column)
    driver.execute_query(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    rows = _read_rows(data_dir, rule.source_file, [rule.unique_column, *rule.properties])
    payload = [
        {"key": r[rule.unique_column], "props": {p: r[p] for p in rule.properties if r[p] is not None}}
        for r in rows
        if r[rule.unique_column] is not None
    ]
    written = 0
    for batch in _batches(payload):
        records, _, _ = driver.execute_query(
            f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.key}}) SET n += row.props RETURN count(n) AS c",
            rows=batch,
        )
        written += records[0]["c"]
    return RuleReport(
        rule=f"node {rule.label}",
        rows_read=len(rows),
        rows_skipped_null_key=len(rows) - len(payload),
        rows_written=written,
    )


def import_relationships(
    driver: Driver, data_dir: Path, rule: RelationshipRule, plan: ConstructionPlan
) -> RuleReport:
    from_node, to_node = plan.node(rule.from_label), plan.node(rule.to_label)
    # match each endpoint on its own key property; the column in this file may be named differently
    query = (
        "UNWIND $rows AS row "
        f"MATCH (a:{cypher_ident(from_node.label)} {{{cypher_ident(from_node.unique_column)}: row.from}}) "
        f"MATCH (b:{cypher_ident(to_node.label)} {{{cypher_ident(to_node.unique_column)}: row.to}}) "
        f"MERGE (a)-[r:{cypher_ident(rule.relationship_type)}]->(b) "
        "SET r += row.props RETURN count(r) AS c"
    )
    rows = _read_rows(data_dir, rule.source_file, [rule.from_column, rule.to_column, *rule.properties])
    payload = [
        {
            "from": r[rule.from_column],
            "to": r[rule.to_column],
            "props": {p: r[p] for p in rule.properties if r[p] is not None},
        }
        for r in rows
        if r[rule.from_column] is not None and r[rule.to_column] is not None
    ]
    written = 0
    for batch in _batches(payload):
        records, _, _ = driver.execute_query(query, rows=batch)
        written += records[0]["c"]
    return RuleReport(
        rule=f"relationship {rule.relationship_type} ({rule.from_label}->{rule.to_label})",
        rows_read=len(rows),
        rows_skipped_null_key=len(rows) - len(payload),
        rows_written=written,
    )


def construct_domain_graph(data_dir: Path, plan: ConstructionPlan, driver: Driver | None = None) -> ImportReport:
    """Import all nodes, then all relationships. Safe to rerun: everything is MERGEd."""
    data_dir = Path(data_dir)
    own_driver = driver is None
    driver = driver or get_driver()
    try:
        reports = [import_nodes(driver, data_dir, rule) for rule in plan.nodes]
        reports += [import_relationships(driver, data_dir, rule, plan) for rule in plan.relationships]
    finally:
        if own_driver:
            driver.close()
    return ImportReport(rules=reports)
