"""Read-only access below the planner (R203), the layer that holds even when every check above it is passed
by: the server refuses a write sent through `read_query` and through the exact route's `run_read`, and
still answers a read (these tests use the test Neo4j, marked `neo4j` through the `driver` fixture); the
DuckDB connection `query_tables` reads refuses to write a file, read a file, attach a database or change
its settings, and still answers a SELECT (no server needed); and no query-side module calls the driver
except through `read_query`, so a new reader cannot skip the READ routing unnoticed."""

from pathlib import Path

import pytest
from neo4j.exceptions import ClientError

from kgbuilder.agent.tables import DuckDBTables
from kgbuilder.graph.connection import read_query
from kgbuilder.query.graph_store import Neo4jGraphStore

SRC = Path(__file__).parents[1] / "src" / "kgbuilder"

# what answering a question reads the graph through; the build's modules (hybrid/unit_graph.py,
# unit_sources.py, every builder stage) write it and stay outside
QUERY_SIDE = [*sorted((SRC / "agent").glob("*.py")), *sorted((SRC / "query").glob("*.py")),
              SRC / "hybrid" / "unit_store.py"]  # fmt: skip

WRITES = [
    "CREATE (:Probe {id: 'p'})",
    "MERGE (:Probe {id: 'p'})",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n:Kettle) SET n.maker = 'Elm'",
]


@pytest.mark.parametrize("cypher", WRITES)
def test_the_server_refuses_a_write_sent_as_a_read(driver, cypher: str):
    driver.execute_query("CREATE (:Kettle {id: 'K1', maker: 'Ash'})")
    with pytest.raises(ClientError):
        read_query(driver, cypher)
    rows, _, _ = read_query(driver, "MATCH (n) RETURN count(n) AS n, collect(n.maker) AS makers")
    assert (rows[0]["n"], rows[0]["makers"]) == (1, ["Ash"])


@pytest.mark.parametrize("cypher", WRITES)
def test_the_exact_routes_read_refuses_a_write(driver, cypher: str):
    driver.execute_query("CREATE (:Kettle {id: 'K1', maker: 'Ash'})")
    result = Neo4jGraphStore(driver, None, hops=1).run_read(cypher, {})
    assert result.error is not None
    rows, _, _ = read_query(driver, "MATCH (n) RETURN count(n) AS n, collect(n.maker) AS makers")
    assert (rows[0]["n"], rows[0]["makers"]) == (1, ["Ash"])


def test_a_read_passes_its_parameters_even_one_called_query(driver):
    driver.execute_query("CREATE (:Kettle {id: 'K1'}), (:Kettle {id: 'K2'})")
    rows, _, _ = read_query(driver, "MATCH (n:Kettle) WHERE n.id = $query RETURN n.id AS id", query="K2")
    assert [r["id"] for r in rows] == ["K2"]


@pytest.fixture
def tables(tmp_path: Path) -> DuckDBTables:
    staging = tmp_path / "staging"
    (staging / "shop").mkdir(parents=True)
    (staging / "shop" / "kettles.csv").write_text("id,maker\nK1,Ash\nK2,Elm\n", encoding="utf-8")
    store = DuckDBTables(staging)
    yield store
    store.close()


def test_the_tables_still_answer_a_select(tables: DuckDBTables):
    result = tables.run('SELECT maker FROM "shop/kettles" WHERE id = $id', {"id": "K2"})
    assert (result.error, result.rows) == (None, [["Elm"]])


@pytest.mark.parametrize(
    "statement",
    [
        "COPY \"shop/kettles\" TO '{out}'",
        "SELECT * FROM read_csv('{source}')",
        "ATTACH '{out}.db' AS other",
        "SET enable_external_access = true",
        "SET lock_configuration = false",
    ],
)
def test_the_tables_read_no_file_write_none_and_keep_their_settings(tables, tmp_path: Path, statement: str):
    out = tmp_path / "leak.csv"
    source = tmp_path / "staging" / "shop" / "kettles.csv"
    result = tables.run(statement.format(out=out.as_posix(), source=source.as_posix()), {})
    assert result.error is not None
    assert not out.exists() and not Path(f"{out}.db").exists()


def test_no_query_side_module_calls_the_driver_except_as_a_read():
    calls = [p.relative_to(SRC).as_posix() for p in QUERY_SIDE if ".execute_query(" in p.read_text("utf-8")]
    assert calls == []
