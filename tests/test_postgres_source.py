"""The PostgreSQL source of tables (R114).

Without a database: staging from a `TableSource` (a fake), the skip report, unsafe table names, an
unreadable source, the profile stage's param, and the adapter's password-free name. Against the tests' own
PostgreSQL (marked `postgres`): typed columns, views, a missing schema, and the furniture tables giving the
same profile as the CSV files; the last test also needs Neo4j and builds the same graph from both.
"""

import csv
import json
from pathlib import Path

import pytest
from neo4j import Driver
from psycopg import sql

from kgbuilder.config import Settings
from kgbuilder.core.errors import DataSourceError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline import stages as st
from kgbuilder.structured.importer import construct_domain_graph
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.structured.postgres import PostgresTables
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured

from .fakes import FakeTables, RecordingTracker
from .pg_load import PgSchema, load_csv_dir

ROOT = Path(__file__).parent.parent
DOMAIN_PLAN = ROOT / "tests" / "gold" / "domain_plan.json"


class DownTables(FakeTables):
    """A `TableSource` whose database cannot be reached."""

    def tables(self) -> list[str]:
        raise DataSourceError("fake db: connection refused")


def staged_names(staging: Path) -> list[str]:
    return sorted(p.name for p in staging.glob("*.csv"))


def test_a_table_source_replaces_the_data_dirs_files_and_reports_them_skipped(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "t.csv").write_text("id\n1\n", encoding="utf-8")
    (data / "rows.ndjson").write_text('{"id": 1}\n', encoding="utf-8")
    (data / "notes.md").write_text("# a document, not a table\n", encoding="utf-8")
    source = FakeTables({"products": "product_id\nP1\n", "orders": "order_id,product_id\nO1,P1\n"})

    report = stage_structured(data, tmp_path / "staging", source)

    assert report.tables == ["orders", "products"]
    assert staged_names(report.staged_dir) == ["orders.csv", "products.csv"]
    # the files are reported, not silently dropped; a document is not a table and is not reported
    assert {s.file: s.reason for s in report.skipped} == {
        "rows.ndjson": "the tables come from fake db schema shop",
        "t.csv": "the tables come from fake db schema shop",
    }


def test_a_table_name_unusable_as_a_file_name_is_skipped_not_written(tmp_path):
    source = FakeTables({"../escape": "id\n1\n", "a/b": "id\n1\n", ".": "id\n1\n", "order items": "id\n1\n"})

    report = stage_structured(tmp_path / "empty", tmp_path / "out" / "staging", source)

    assert report.tables == ["order items"]
    assert sorted(s.file for s in report.skipped) == [".", "../escape", "a/b"]
    assert not (tmp_path / "out" / "escape.csv").exists()


def test_an_unreadable_source_leaves_the_last_staging_in_place(tmp_path):
    staging = tmp_path / "staging"
    stage_structured(tmp_path, staging, FakeTables({"products": "product_id\nP1\n"}))

    with pytest.raises(DataSourceError, match="connection refused"):
        stage_structured(tmp_path, staging, DownTables({}))
    assert staged_names(staging) == ["products.csv"]


def test_the_profile_stage_logs_where_the_tables_came_from(data_dir, tmp_path):
    for tables, expected in [(None, "files"), (FakeTables({"p": "id\n1\n"}), "fake db schema shop")]:
        tracker = RecordingTracker()
        ctx = PipelineContext(
            settings=Settings(_env_file=None),
            driver=None,
            out=tmp_path / "out",
            tracker=tracker,
            tables=tables,
        )
        run_stages(ctx, PipelineState(data_dir=data_dir), [st.ProfileStage()])
        assert tracker.run("profile").logged_params["structured_source"] == expected
    assert [f.file for f in profile_directory(tmp_path / "out" / "staging").files] == ["p.csv"]


def test_the_postgres_source_is_named_without_the_password():
    tables = PostgresTables("postgresql://alice:s3cret@db.example:6543/shop", "sales")
    assert tables.describe() == "postgres db.example:6543/shop schema sales"


def test_an_unreachable_server_is_a_data_source_error_without_the_password():
    # `.invalid` never resolves (RFC 2606), so this fails at once; a closed port would fail only at the
    # connect timeout on Windows, where a refused connection is not reported to psycopg
    tables = PostgresTables("postgresql://alice:s3cret@db.invalid:5432/shop", "sales")
    with pytest.raises(DataSourceError) as raised:
        tables.tables()
    assert "db.invalid:5432/shop" in str(raised.value)
    assert "s3cret" not in str(raised.value)


def test_typed_columns_are_staged_in_a_form_the_profiler_types(pg_schema: PgSchema, tmp_path):
    table = sql.Identifier(pg_schema.name, "typed")
    pg_schema.conn.execute(
        sql.SQL(
            "CREATE TABLE {} (id integer PRIMARY KEY, amount numeric(8,2), active boolean, day date, "
            "at timestamptz, note text, meta jsonb, tags text[])"
        ).format(table)
    )
    rows = [
        (
            1,
            25,
            True,
            "2024-01-05",
            "2024-01-05 10:00:00+02",
            'a, "quoted"\nsecond line',
            '{"k": 1}',
            ["a", "b"],
        ),
        (2, 3.5, False, "2024-02-01", "2024-02-01 11:30:00+00", "", '{"k": 2}', []),
        (3, None, None, None, None, None, None, None),
    ]
    with pg_schema.conn.cursor() as cur:
        cur.executemany(sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, %s, %s, %s, %s, %s)").format(table), rows)

    staged = stage_structured(
        tmp_path / "empty", tmp_path / "staging", PostgresTables(pg_schema.url, pg_schema.name)
    )
    profile = profile_directory(staged.staged_dir).file("typed.csv")

    assert {c.name: c.dtype for c in profile.columns} == {
        "id": "BIGINT",
        "amount": "DOUBLE",
        "active": "BOOLEAN",
        "day": "DATE",
        "at": "TIMESTAMP WITH TIME ZONE",
        "note": "VARCHAR",
        "meta": "VARCHAR",
        "tags": "VARCHAR",
    }
    assert profile.column("amount").null_count == 1
    with (staged.staged_dir / "typed.csv").open(encoding="utf-8", newline="") as f:
        first, second, third = csv.DictReader(f)
    assert first["at"] == "2024-01-05 08:00:00+00"  # UTC, whatever the server's time zone
    assert first["note"] == 'a, "quoted"\nsecond line'
    assert (first["tags"], second["tags"], third["tags"]) == ('["a","b"]', "[]", "")  # lists as JSON text


def test_views_are_staged_and_other_schemas_are_not(pg_schema: PgSchema, tmp_path):
    other = f"{pg_schema.name}_other"
    conn = pg_schema.conn
    conn.execute(sql.SQL("CREATE TABLE {} (id text)").format(sql.Identifier(pg_schema.name, "items")))
    conn.execute(
        sql.SQL("CREATE VIEW {} AS SELECT id FROM {}").format(
            sql.Identifier(pg_schema.name, "chosen_items"), sql.Identifier(pg_schema.name, "items")
        )
    )
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(other)))
    try:
        conn.execute(sql.SQL("CREATE TABLE {} (id text)").format(sql.Identifier(other, "elsewhere")))
        tables = PostgresTables(pg_schema.url, pg_schema.name)
        assert tables.tables() == ["chosen_items", "items"]
    finally:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(other)))


def test_a_missing_or_empty_schema_is_an_error(pg_schema: PgSchema):
    for schema in [pg_schema.name, f"{pg_schema.name}_missing"]:
        with pytest.raises(DataSourceError, match="does not exist or holds no tables"):
            PostgresTables(pg_schema.url, schema).tables()


def test_the_furniture_tables_give_the_same_profile_from_postgres_as_from_files(
    pg_schema: PgSchema, tmp_path
):
    load_csv_dir(pg_schema.conn, pg_schema.name, ROOT / "data")
    from_files = stage_structured(ROOT / "data", tmp_path / "files").staged_dir
    from_postgres = stage_structured(
        tmp_path / "empty", tmp_path / "postgres", PostgresTables(pg_schema.url, pg_schema.name)
    ).staged_dir

    # Everything but the sample values: those differ between two profiles of the very same files (an open
    # issue, "Profile samples are not deterministic" in REFACTOR_PLAN.md), so they cannot be compared here.
    without_samples = {"files": {"__all__": {"columns": {"__all__": {"samples"}}}}}
    from_postgres_profile = profile_directory(from_postgres).model_dump(exclude=without_samples)
    assert from_postgres_profile == profile_directory(from_files).model_dump(exclude=without_samples)


def graph_snapshot(driver: Driver) -> list[str]:
    """Every node and relationship with its labels or type and its properties, as sorted JSON lines."""
    nodes = driver.execute_query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props")[0]
    # a relationship is told apart by its two ends' properties: element ids differ between the two builds
    rels = driver.execute_query(
        "MATCH (a)-[r]->(b) "
        "RETURN type(r) AS type, properties(r) AS props, properties(a) AS a, properties(b) AS b"
    )[0]
    lines = [json.dumps(record.data(), sort_keys=True, default=str) for record in [*nodes, *rels]]
    return sorted(lines)


def test_the_furniture_graph_is_the_same_from_postgres_as_from_files(pg_schema: PgSchema, driver, tmp_path):
    plan = ConstructionPlan.model_validate_json(DOMAIN_PLAN.read_text(encoding="utf-8"))
    load_csv_dir(pg_schema.conn, pg_schema.name, ROOT / "data")

    assert construct_domain_graph(
        driver, stage_structured(ROOT / "data", tmp_path / "files").staged_dir, plan
    ).clean
    from_files = graph_snapshot(driver)
    driver.execute_query("MATCH (n) DETACH DELETE n")
    source = PostgresTables(pg_schema.url, pg_schema.name)
    assert construct_domain_graph(
        driver, stage_structured(tmp_path, tmp_path / "pg", source).staged_dir, plan
    ).clean

    assert graph_snapshot(driver) == from_files
    assert len(from_files) > 100  # the whole furniture domain graph, not an empty one twice
