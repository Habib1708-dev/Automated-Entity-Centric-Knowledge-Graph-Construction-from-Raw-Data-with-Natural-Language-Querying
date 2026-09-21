"""Create the Neo4j driver.

Role in the pipeline: called once by the composition root; the driver is then passed to every stage that
reads or writes the graph.
Design: Adapter boundary. `GraphDatabase.driver` is called nowhere else, so connection policy
(auth, pooling, a different database) changes in one place.
Not here: queries. Each graph layer owns its own Cypher.
"""

from neo4j import Driver, GraphDatabase


def open_driver(uri: str, username: str, password: str) -> Driver:
    """Return a Neo4j driver. The caller owns it and must close it (it is a context manager)."""
    return GraphDatabase.driver(uri, auth=(username, password))
