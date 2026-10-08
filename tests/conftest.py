"""Shared fixtures: a temp data dir with small CSVs (one deliberately dirty file), an empty Neo4j
database, and a fresh PostgreSQL schema (R114); every test that uses a database is marked `neo4j` or
`postgres` here, so none can forget the marker.

The databases are the tests' own servers (`neo4j-test` and `postgres-test` in docker-compose.yml), never
the working graph the pipeline writes: the `driver` fixture empties Neo4j before every test (R112)."""

import uuid

import psycopg
import pytest
from psycopg import sql

from kgbuilder.config import Settings
from kgbuilder.graph.connection import open_driver

from .pg_load import TEST_POSTGRES_URL, PgSchema

# the `neo4j-test` service of docker-compose.yml: bolt on 7688, its own fixed credentials
TEST_NEO4J_URI = "bolt://localhost:7688"
TEST_NEO4J_AUTH = ("neo4j", "password123")

FILES = {
    "products.csv": "product_id,product_name,price\nP1,Table,199.5\nP2,Chair,89\nP3,Lamp,35\n",
    "assemblies.csv": (
        "assembly_id,assembly_name,quantity,product_id\n"
        "A1,Table Top,1,P1\nA2,Table Legs,4,P1\nA3,Seat,1,P2\nA4,Shade,1,P3\n"
    ),
    "suppliers.csv": "supplier_id,name,country\nS1,Nordic Wood,SE\nS2,Lux Metal,DE\n",
    "assembly_supplier.csv": (
        "assembly_id,supplier_id,lead_time_days\nA1,S1,10\nA2,S2,7\nA3,S1,12\nA1,S2,20\n"
    ),
    # duplicate id and a dangling product reference
    "dirty.csv": "item_id,product_id\nX1,P1\nX1,P2\nX2,P9\n",
}


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects: the marker must exist when it is read
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test that uses the `driver` fixture as `neo4j`.

    The fixture wipes the database, so a Neo4j test without the marker also runs under
    `pytest -m "not neo4j"` and deletes the working graph (found in R40: one test did).
    """
    for item in items:
        if "driver" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.neo4j)
        if "pg_schema" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.postgres)


@pytest.fixture
def data_dir(tmp_path):
    for name, content in FILES.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def driver():
    """An empty Neo4j database: the tests' own server. Skips the test when it is down; wipes it first.

    Fails, without touching any database, when the pipeline's NEO4J_URI points at the test server, because
    the wipe would then delete the working graph.
    """
    if Settings().neo4j_uri == TEST_NEO4J_URI:
        pytest.fail(
            f"NEO4J_URI is the test database {TEST_NEO4J_URI}: the tests would wipe the working graph"
        )
    d = open_driver(TEST_NEO4J_URI, *TEST_NEO4J_AUTH)
    try:
        d.verify_connectivity()
    except Exception:  # any connection failure means "no database available", which is a skip, not an error
        d.close()
        pytest.skip("the test Neo4j is not running (docker compose up -d neo4j-test)")
    d.execute_query("MATCH (n) DETACH DELETE n")
    yield d
    d.close()


@pytest.fixture(scope="session")
def pg_reachable() -> bool:
    """Whether the test PostgreSQL answers, asked once per session.

    Once, because on Windows a connection to a closed port fails only at the connect timeout (2 s, the
    shortest libpq takes), which per test would add seconds to every skipped test.
    """
    try:
        psycopg.connect(TEST_POSTGRES_URL, connect_timeout=2).close()
    except psycopg.OperationalError:  # no server to connect to: the tests skip, they do not fail
        return False
    return True


@pytest.fixture
def pg_schema(pg_reachable):
    """A fresh, empty schema in the tests' own PostgreSQL, dropped after the test. Skips when it is down.

    It touches nothing but the schema it creates, so unlike `driver` it needs no guard against pointing at
    the working database.
    """
    if not pg_reachable:
        pytest.skip("the test PostgreSQL is not running (docker compose up -d postgres-test)")
    conn = psycopg.connect(TEST_POSTGRES_URL, autocommit=True)
    name = f"t_{uuid.uuid4().hex[:12]}"
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield PgSchema(conn=conn, name=name)
    finally:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
        conn.close()
