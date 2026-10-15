"""Create the Neo4j driver, and run a read on it that the server refuses to let write.

Role in the pipeline: `open_driver` is called once by the composition root; the driver is then passed to
every stage that reads or writes the graph. `read_query` is how the query side (query/, agent/, the
retrieval stores of hybrid/) reads it (R203).
Design: Adapter boundary. `GraphDatabase.driver` is called nowhere else, so connection policy
(auth, pooling, a different database) changes in one place. `read_query` routes every query-side read as
a READ: Neo4j then refuses any write in it (`Neo.ClientError.Statement.AccessMode`), whatever the Cypher
says. Neo4j Community has no read-only users, so this access mode is the database's own guarantee that
answering a question never changes the graph, independent of the security screen (query/screen.py) and
of the checks on LLM-written Cypher (query/cypher_check.py).
Not here: queries. Each graph layer owns its own Cypher.
"""

from neo4j import Driver, EagerResult, GraphDatabase, RoutingControl


def open_driver(uri: str, username: str, password: str) -> Driver:
    """Return a Neo4j driver. The caller owns it and must close it (it is a context manager)."""
    return GraphDatabase.driver(uri, auth=(username, password))


def read_query(driver: Driver, cypher: str, /, **parameters: object) -> EagerResult:
    """Run `cypher` with `parameters` as a read: its records, summary and keys. The server refuses a write
    in it with a `ClientError`. The parameters go in as a dict, so a parameter may be called `query`,
    `database` or any name the driver uses for its own options."""
    return driver.execute_query(cypher, parameters_=parameters, routing_=RoutingControl.READ)
