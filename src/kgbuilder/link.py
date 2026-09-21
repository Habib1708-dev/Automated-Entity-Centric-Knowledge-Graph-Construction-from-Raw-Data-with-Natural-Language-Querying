"""Link the three graphs: Document -ABOUT-> domain node, and Entity -REFERS_TO-> domain node."""

from neo4j import Driver
from pydantic import BaseModel
from rapidfuzz import fuzz

from .core.cypher import cypher_ident
from .core.text import norm
from .core.text import squash as _squash
from .structured.plan import ConstructionPlan, NodeRule


class LinkReport(BaseModel):
    documents_linked: int
    documents_total: int
    entities_linked: int


def name_property(rule: NodeRule) -> str:
    """The property that holds a node's human-readable name."""
    for p in [*rule.properties, rule.unique_column]:
        if "name" in p.lower() or "title" in p.lower():
            return p
    return rule.unique_column


def link_graphs(driver: Driver, plan: ConstructionPlan, threshold: float = 90.0) -> LinkReport:
    domain: list[tuple[str, str, str]] = []  # (label, key value, name)
    for rule in plan.nodes:
        prop = name_property(rule)
        rows, _, _ = driver.execute_query(
            f"MATCH (n:{cypher_ident(rule.label)}) "
            f"RETURN n.{cypher_ident(rule.unique_column)} AS k, n.{cypher_ident(prop)} AS name"
        )
        domain += [(rule.label, r["k"], str(r["name"])) for r in rows if r["name"] is not None]
    keys = {n.label: n.unique_column for n in plan.nodes}

    # documents: the file name mentions the domain node it is about (stockholm_chair_reviews.md -> Stockholm Chair)
    docs, _, _ = driver.execute_query("MATCH (d:Document) RETURN d.doc_id AS id, d.title AS title")
    linked_docs = 0
    for d in docs:
        stem = _squash(d["title"])
        hits = [
            (label, k, name) for label, k, name in domain if len(_squash(name)) >= 4 and _squash(name) in stem
        ]
        if not hits:
            continue
        best = max(
            hits, key=lambda h: len(_squash(h[2]))
        )  # longest name wins, so "Coffee Table" beats "Table"
        driver.execute_query(
            f"MATCH (d:Document {{doc_id: $id}}), (n:{cypher_ident(best[0])} {{{cypher_ident(keys[best[0]])}: $k}}) "
            "MERGE (d)-[:ABOUT]->(n)",
            id=d["id"],
            k=best[1],
        )
        linked_docs += 1

    # entities: near-exact name match with a domain node
    ents, _, _ = driver.execute_query(
        "MATCH (e:Entity) RETURN e.id AS id, e.name AS name, coalesce(e.aliases, []) AS aliases"
    )
    linked_ents = 0
    for e in ents:
        names = {norm(n) for n in [e["name"], *e["aliases"]]}
        best, best_score = None, 0.0
        for label, k, dname in domain:
            score = max(fuzz.token_sort_ratio(n, norm(dname)) for n in names)
            if score > best_score:
                best, best_score = (label, k), score
        if best and best_score >= threshold:
            driver.execute_query(
                f"MATCH (e:Entity {{id: $id}}), (n:{cypher_ident(best[0])} {{{cypher_ident(keys[best[0]])}: $k}}) "
                "MERGE (e)-[r:REFERS_TO]->(n) SET r.score = $score",
                id=e["id"],
                k=best[1],
                score=best_score,
            )
            linked_ents += 1
    return LinkReport(documents_linked=linked_docs, documents_total=len(docs), entities_linked=linked_ents)
