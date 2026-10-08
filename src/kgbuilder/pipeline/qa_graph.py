"""The check that the loaded graph is the one a QA gold file was written on (R117).

Role in the pipeline: `kg qa` (qa_stages.py) and `kg retrieve-eval` (retrieval_stages.py) call it first,
before any model or embedding call, and log the digest it returns as the param `graph_digest`.
Design: one read of the graph (graph/digest.py) serves both purposes: the digest that lets two runs be
paired only on the same graph, and the chunk ids the gold's evidence must be among.
Not here: the digest's Cypher (graph/digest.py), scoring (validation/).
"""

from neo4j import Driver

from ..core.errors import EvaluationError
from ..graph.digest import GraphDigest, graph_digest
from ..validation.qa_gold import QAGold


def check_graph(driver: Driver, gold: QAGold) -> GraphDigest:
    """The loaded graph's digest, once the graph is known to hold every chunk the gold cites as evidence.

    Raises `EvaluationError` naming the missing chunks: the graph is then not the build the gold was written
    on (another dataset, other chunk settings), and a system would be scored against chunks it can never
    return, since `GraphStore.chunks` leaves unknown ids out without a word. Read-only.
    """
    graph = graph_digest(driver)
    cited = {c.chunk_id for q in gold.questions for c in q.chunks}
    if missing := sorted(cited - graph.chunk_ids):
        raise EvaluationError(
            [
                f"{len(missing)} of the gold's {len(cited)} evidence chunks are not in the loaded graph "
                f"(first: {missing[:3]}): load the build the gold was written on"
            ]
        )
    return graph
