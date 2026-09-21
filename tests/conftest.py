"""Shared fixtures: a temp data dir with small CSVs, including one deliberately dirty file."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.graph.connection import open_driver

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


@pytest.fixture
def data_dir(tmp_path):
    for name, content in FILES.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


@pytest.fixture
def driver():
    """An empty Neo4j database. Skips the test when Neo4j is down; wipes the database first."""
    s = Settings()
    d = open_driver(s.neo4j_uri, s.neo4j_username, s.neo4j_password)
    try:
        d.verify_connectivity()
    except Exception:  # any connection failure means "no database available", which is a skip, not an error
        d.close()
        pytest.skip("Neo4j is not running (docker compose up -d)")
    d.execute_query("MATCH (n) DETACH DELETE n")
    yield d
    d.close()
