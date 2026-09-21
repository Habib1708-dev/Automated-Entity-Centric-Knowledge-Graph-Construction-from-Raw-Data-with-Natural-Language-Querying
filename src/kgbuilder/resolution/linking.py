"""Link the three graphs: `(Document)-[:ABOUT]->(domain node)` and `(Entity)-[:REFERS_TO]->(domain node)`.

Role in the pipeline: `kg link`, after the domain graph, the lexical graph and the subject graph exist.
Design: three separated steps: read names from Neo4j, match in pure functions (unit-tested without a
database), write the links in batches (one query per domain label, because labels cannot be parameters).
No LLM: links are either a name contained in a file name, or a near-exact fuzzy name match.
Not here: merging entities with each other (resolver.py).
"""

from neo4j import Driver
from pydantic import BaseModel
from rapidfuzz import fuzz

from ..core.cypher import cypher_ident
from ..core.text import norm, squash
from ..structured.plan import ConstructionPlan, NodeRule

# Names shorter than this, once squashed, are too likely to occur inside an unrelated file name ("bed"
# in "embedded_notes") to be trusted for document linking.
_MIN_NAME_CHARS_FOR_DOCUMENT_MATCH = 4


class DomainNode(BaseModel):
    """A node of the domain graph, reduced to what matching needs."""

    label: str
    key: object  # value of the label's unique property; type depends on the CSV column
    name: str


class EntityMatch(BaseModel):
    node: DomainNode
    score: float  # rapidfuzz token_sort_ratio, 0..100


class LinkReport(BaseModel):
    documents_linked: int
    documents_total: int
    entities_linked: int


def name_property(rule: NodeRule) -> str:
    """The property holding a node's human-readable name; the key when no property looks like a name."""
    for prop in [*rule.properties, rule.unique_column]:
        if "name" in prop.lower() or "title" in prop.lower():
            return prop
    return rule.unique_column


def match_document(title: str, domain: list[DomainNode]) -> DomainNode | None:
    """The domain node whose name is contained in the document title, if any.

    Example: title ``jonkoping_coffee_table_reviews`` contains both "Table" and "Coffee Table"; the
    longest name wins, so the document is about the coffee table.
    """
    stem = squash(title)
    hits = [
        n
        for n in domain
        if len(squash(n.name)) >= _MIN_NAME_CHARS_FOR_DOCUMENT_MATCH and squash(n.name) in stem
    ]
    return max(hits, key=lambda n: len(squash(n.name)), default=None)


def match_entity(names: list[str], domain: list[DomainNode], threshold: float) -> EntityMatch | None:
    """The best domain node for an entity (its name and aliases), if the score reaches `threshold`.

    token_sort_ratio ignores word order ("Chair Stockholm" = "Stockholm Chair") but not extra words, so
    with a threshold around 90 only near-exact names link, which is the intent: a wrong REFERS_TO is
    worse than a missing one.
    """
    candidates = {norm(n) for n in names if norm(n)}
    best: EntityMatch | None = None
    for node in domain:
        target = norm(node.name)
        score = max((fuzz.token_sort_ratio(c, target) for c in candidates), default=0.0)
        if score >= threshold and (best is None or score > best.score):
            best = EntityMatch(node=node, score=score)
    return best


def read_domain_nodes(driver: Driver, plan: ConstructionPlan) -> list[DomainNode]:
    """Key and display name of every domain node that has a name."""
    nodes: list[DomainNode] = []
    for rule in plan.nodes:
        key, name = cypher_ident(rule.unique_column), cypher_ident(name_property(rule))
        records, _, _ = driver.execute_query(
            f"MATCH (n:{cypher_ident(rule.label)}) RETURN n.{key} AS key, n.{name} AS name"
        )
        nodes += [
            DomainNode(label=rule.label, key=r["key"], name=str(r["name"]))
            for r in records
            if r["name"] is not None
        ]
    return nodes


def _write_links(
    driver: Driver,
    plan: ConstructionPlan,
    source_match: str,
    relationship: str,
    rows_by_label: dict[str, list[dict]],
) -> None:
    """MERGE one link per row. `source_match` is the Cypher pattern that finds the source node from `r.id`."""
    for label, rows in rows_by_label.items():
        key = cypher_ident(plan.node(label).unique_column)
        driver.execute_query(
            f"UNWIND $rows AS r MATCH {source_match}, (n:{cypher_ident(label)} {{{key}: r.key}}) "
            f"MERGE (src)-[l:{relationship}]->(n) SET l += r.props",
            rows=rows,
        )


def link_graphs(driver: Driver, plan: ConstructionPlan, threshold: float = 90.0) -> LinkReport:
    """Create ABOUT and REFERS_TO links. Idempotent; returns how many documents and entities were linked."""
    domain = read_domain_nodes(driver, plan)

    documents, _, _ = driver.execute_query("MATCH (d:Document) RETURN d.doc_id AS id, d.title AS title")
    document_rows: dict[str, list[dict]] = {}
    for d in documents:
        node = match_document(d["title"], domain)
        if node:
            document_rows.setdefault(node.label, []).append({"id": d["id"], "key": node.key, "props": {}})
    _write_links(driver, plan, "(src:Document {doc_id: r.id})", "ABOUT", document_rows)

    entities, _, _ = driver.execute_query(
        "MATCH (e:Entity) RETURN e.id AS id, e.name AS name, coalesce(e.aliases, []) AS aliases"
    )
    entity_rows: dict[str, list[dict]] = {}
    for e in entities:
        match = match_entity([e["name"], *e["aliases"]], domain, threshold)
        if match:
            row = {"id": e["id"], "key": match.node.key, "props": {"score": match.score}}
            entity_rows.setdefault(match.node.label, []).append(row)
    _write_links(driver, plan, "(src:Entity {id: r.id})", "REFERS_TO", entity_rows)

    return LinkReport(
        documents_linked=sum(len(rows) for rows in document_rows.values()),
        documents_total=len(documents),
        entities_linked=sum(len(rows) for rows in entity_rows.values()),
    )
