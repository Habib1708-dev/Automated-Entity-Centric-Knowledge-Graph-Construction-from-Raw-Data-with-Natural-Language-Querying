"""Rule-based import of a construction plan into Neo4j, with a reconciliation report.

Role in the pipeline: `kg build`. Executes the reviewed plan against the staged CSVs; no LLM involved.
Design: every write is a batched, parameterised `MERGE`, so the import is idempotent and safe to rerun.
Every rule reports rows read / skipped / written, so silent data loss (dangling foreign keys, null
keys) shows up as numbers instead of as a smaller graph.
Not here: deciding what to import (proposer.py, plan.py).
"""

from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Literal

import duckdb
from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from .plan import ConstructionPlan, NodeRule, RelationshipRule
from .profiler import quote_ident, read_csv

# Rows per UNWIND. Large enough to amortise the round trip, small enough to keep transactions light.
BATCH_SIZE = 1000


class RuleReport(BaseModel):
    """Reconciliation numbers for one node or relationship rule."""

    kind: Literal["node", "relationship"]
    rule: str
    rows_read: int
    rows_skipped_null_key: int
    rows_written: int  # nodes merged, or relationship rows whose endpoints were both found

    @property
    def rows_unmatched(self) -> int:
        """Rows with keys that matched nothing: a relationship endpoint that does not exist."""
        return self.rows_read - self.rows_skipped_null_key - self.rows_written


class ImportReport(BaseModel):
    rules: list[RuleReport]

    @property
    def clean(self) -> bool:
        """True when every source row ended up in the graph."""
        return all(r.rows_unmatched == 0 and r.rows_skipped_null_key == 0 for r in self.rules)

    def written(self, kind: Literal["node", "relationship"]) -> int:
        return sum(r.rows_written for r in self.rules if r.kind == kind)

    @property
    def rows_dropped(self) -> int:
        return sum(r.rows_unmatched + r.rows_skipped_null_key for r in self.rules)


def _to_neo4j(value: object) -> object:
    """Map a DuckDB value to a type the Neo4j driver accepts, keeping numbers and dates typed."""
    if isinstance(value, Decimal):
        return float(value)  # Neo4j has no decimal type
    if value is None or isinstance(value, (bool, int, float, str, date, datetime, time)):
        return value
    return str(value)


def _read_rows(data_dir: Path, source_file: str, columns: list[str]) -> list[dict]:
    """Read the given columns with DuckDB's type inference, the same one the profiler used."""
    con = duckdb.connect()
    select = ", ".join(quote_ident(c) for c in dict.fromkeys(columns))  # dedupe, keep order
    relation = read_csv(con, data_dir / source_file).query("src", f"SELECT {select} FROM src")
    names = relation.columns
    return [{n: _to_neo4j(v) for n, v in zip(names, row, strict=True)} for row in relation.fetchall()]


def _batches(rows: list[dict]) -> Iterator[list[dict]]:
    for start in range(0, len(rows), BATCH_SIZE):
        yield rows[start : start + BATCH_SIZE]


def _write(driver: Driver, query: str, payload: list[dict]) -> int:
    """Run `query` per batch; the query must `RETURN count(...) AS c`. Returns the total."""
    written = 0
    for batch in _batches(payload):
        records, _, _ = driver.execute_query(query, rows=batch)
        written += records[0]["c"]
    return written


def import_nodes(driver: Driver, data_dir: Path, rule: NodeRule) -> RuleReport:
    """MERGE one node per row on the rule's key. Rows with a null key are skipped and counted."""
    label, key = cypher_ident(rule.label), cypher_ident(rule.unique_column)
    # the constraint doubles as the index that makes MERGE and the relationship MATCHes fast
    driver.execute_query(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    rows = _read_rows(data_dir, rule.source_file, [rule.unique_column, *rule.properties])
    payload = [
        # null properties are left out: SET n += {p: null} would delete a value from an earlier run
        {"key": r[rule.unique_column], "props": {p: r[p] for p in rule.properties if r[p] is not None}}
        for r in rows
        if r[rule.unique_column] is not None
    ]
    query = (
        f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.key}}) SET n += row.props RETURN count(n) AS c"
    )
    return RuleReport(
        kind="node",
        rule=f"node {rule.label}",
        rows_read=len(rows),
        rows_skipped_null_key=len(rows) - len(payload),
        rows_written=_write(driver, query, payload),
    )


def import_relationships(
    driver: Driver, data_dir: Path, rule: RelationshipRule, plan: ConstructionPlan
) -> RuleReport:
    """MERGE one relationship per row whose two endpoints exist. Unmatched rows show up in the report."""
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
    return RuleReport(
        kind="relationship",
        rule=f"relationship {rule.relationship_type} ({rule.from_label}->{rule.to_label})",
        rows_read=len(rows),
        rows_skipped_null_key=len(rows) - len(payload),
        rows_written=_write(driver, query, payload),
    )


def construct_domain_graph(driver: Driver, data_dir: Path, plan: ConstructionPlan) -> ImportReport:
    """Import all nodes, then all relationships (which need their endpoints). Safe to rerun."""
    data_dir = Path(data_dir)
    reports = [import_nodes(driver, data_dir, rule) for rule in plan.nodes]
    reports += [import_relationships(driver, data_dir, rule, plan) for rule in plan.relationships]
    return ImportReport(rules=reports)
