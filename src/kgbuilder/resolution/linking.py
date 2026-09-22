"""Link the three graphs: `(Document)-[:ABOUT]->(domain node)` and `(Entity)-[:REFERS_TO]->(domain node)`.

Role in the pipeline: `kg link`, after the domain graph, the lexical graph and the subject graph exist.
Design: three separated steps: read names and contexts from Neo4j, match in pure functions (unit-tested
without a database), write the links in batches. Entities are matched inside the *scope* of the domain
node their documents are about, because generic names repeat across the domain ("Legs" is an assembly of
every chair and table); only names that are unique in the whole domain graph are linked without a scope.
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

# How far from a document's domain node an entity of that document may link, counted in domain
# relationships. 2 reaches a product's assemblies and their parts, but not the suppliers of those parts
# (3): a review names the parts it complains about, not who made them.
_SCOPE_HOPS = 2


class DomainNode(BaseModel):
    """A node of the domain graph, reduced to what matching needs."""

    element_id: str  # Neo4j elementId: stable while the node exists, so read and write agree within a run
    label: str
    name: str


class EntityMatch(BaseModel):
    node: DomainNode
    score: float  # rapidfuzz token_sort_ratio, 0..100


class EntityLinks(BaseModel):
    """The outcome of linking one entity: its links, and how they were found."""

    matches: list[EntityMatch]  # one per scope it matched in; at most one when not scoped
    scoped: bool  # found inside the scope of the entity's documents
    ambiguous: bool  # a match existed but several nodes tied for it, so nothing was linked


class LinkReport(BaseModel):
    """Counts of one linking run. Metric names in MLflow; keep them stable."""

    documents_linked: int
    documents_total: int
    entities_linked: int  # entities with at least one REFERS_TO
    entities_linked_in_scope: int
    entities_ambiguous: int
    entity_links: int  # REFERS_TO relationships; above entities_linked when a name recurs across scopes


def name_property(rule: NodeRule) -> str:
    """The property holding a node's human-readable name.

    The plan's `name_column` when set. Otherwise a guess: the first name-like column, else the key. The
    guess is what made R11 necessary: `sub_assembly_name` comes before `part_name`, so parts were matched
    by their sub-assembly code.
    """
    if rule.name_column is not None:
        return rule.name_column
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


def match_entity(names: list[str], nodes: list[DomainNode], threshold: float) -> list[EntityMatch]:
    """All nodes that share the best score for an entity (its name and aliases), if it reaches `threshold`.

    One result is a match; several mean the name is ambiguous among `nodes`; none means no match.
    token_sort_ratio ignores word order ("Chair Stockholm" = "Stockholm Chair") but not extra words, so
    with a threshold around 90 only near-exact names link, which is the intent: a wrong REFERS_TO is
    worse than a missing one.
    """
    candidates = {norm(n) for n in names if norm(n)}
    if not candidates:
        return []
    scored = [
        EntityMatch(node=node, score=max(fuzz.token_sort_ratio(c, norm(node.name)) for c in candidates))
        for node in nodes
    ]
    best = max((m.score for m in scored), default=0.0)
    return [m for m in scored if m.score == best] if best >= threshold else []


def link_entity(
    names: list[str], scopes: list[list[DomainNode]], domain: list[DomainNode], threshold: float
) -> EntityLinks:
    """Link one entity: inside each scope of its documents first, else in the whole domain graph.

    A scope is the neighbourhood of a node the entity's documents are ABOUT. An entity mentioned in the
    reviews of two products gets one link per product it matches in, so "legs" in the chair reviews and
    "legs" in the table reviews point at the chair's and the table's legs. Outside every scope, only a
    name that is unique in the domain graph is linked.
    """
    matches: dict[str, EntityMatch] = {}
    ambiguous = False
    for scope in scopes:
        found = match_entity(names, scope, threshold)
        if len(found) == 1:
            matches.setdefault(found[0].node.element_id, found[0])
        ambiguous |= len(found) > 1
    if matches:
        return EntityLinks(matches=list(matches.values()), scoped=True, ambiguous=False)

    found = match_entity(names, domain, threshold)
    if len(found) == 1:
        return EntityLinks(matches=found, scoped=False, ambiguous=False)
    return EntityLinks(matches=[], scoped=False, ambiguous=ambiguous or len(found) > 1)


def read_domain_nodes(driver: Driver, plan: ConstructionPlan) -> list[DomainNode]:
    """Element id, label and display name of every domain node that has a name."""
    nodes: list[DomainNode] = []
    for rule in plan.nodes:
        label, name = cypher_ident(rule.label), cypher_ident(name_property(rule))
        records, _, _ = driver.execute_query(f"MATCH (n:{label}) RETURN elementId(n) AS id, n.{name} AS name")
        nodes += [
            DomainNode(element_id=r["id"], label=rule.label, name=str(r["name"]))
            for r in records
            if r["name"] is not None
        ]
    return nodes


def read_scopes(driver: Driver, anchor_ids: list[str], labels: list[str]) -> dict[str, set[str]]:
    """For each anchor node, the element ids of the domain nodes within `_SCOPE_HOPS` of it (itself included).

    Every node on the path must be a domain node: otherwise a path could run through a Document or an
    Entity (anchor <-ABOUT- document, or <-REFERS_TO- entity -REFERS_TO-> elsewhere) and leak scopes.
    """
    if not anchor_ids:
        return {}
    records, _, _ = driver.execute_query(
        # the hop bound cannot be a parameter; it is our own integer constant, not user input
        f"MATCH (a) WHERE elementId(a) IN $ids MATCH p = (a)-[*0..{_SCOPE_HOPS}]-(n) "
        "WHERE all(x IN nodes(p) WHERE any(l IN labels(x) WHERE l IN $labels)) "
        "RETURN elementId(a) AS anchor, collect(DISTINCT elementId(n)) AS scope",
        ids=anchor_ids,
        labels=labels,
    )
    return {r["anchor"]: set(r["scope"]) for r in records}


def link_graphs(driver: Driver, plan: ConstructionPlan, threshold: float = 90.0) -> LinkReport:
    """Recompute all ABOUT and REFERS_TO links. Idempotent; returns counts of what was linked."""
    domain = read_domain_nodes(driver, plan)
    # Links are derived data: recomputing them from scratch keeps a rerun (after a new plan, a resolve or
    # an undo) from keeping links that the current graph no longer supports.
    driver.execute_query("MATCH (:Document)-[l:ABOUT]->() DELETE l")
    driver.execute_query("MATCH (:Entity)-[l:REFERS_TO]->() DELETE l")

    documents, _, _ = driver.execute_query("MATCH (d:Document) RETURN d.doc_id AS id, d.title AS title")
    document_rows = [
        {"src": d["id"], "dst": node.element_id, "props": {}}
        for d in documents
        if (node := match_document(d["title"], domain))
    ]
    _write_links(driver, "(src:Document {doc_id: r.src})", "ABOUT", document_rows)

    # the documents each entity is mentioned in, reduced to the domain nodes they are ABOUT
    entities, _, _ = driver.execute_query(
        "MATCH (e:Entity) "
        "OPTIONAL MATCH (e)<-[:MENTIONS]-(:Chunk)-[:PART_OF]->(:Document)-[:ABOUT]->(a) "
        "RETURN e.id AS id, e.name AS name, coalesce(e.aliases, []) AS aliases, "
        "collect(DISTINCT elementId(a)) AS anchors"
    )
    anchor_ids = sorted({a for e in entities for a in e["anchors"]})
    scope_ids = read_scopes(driver, anchor_ids, [rule.label for rule in plan.nodes])
    scopes = {anchor: [n for n in domain if n.element_id in ids] for anchor, ids in scope_ids.items()}

    results = {
        e["id"]: link_entity(
            [e["name"], *e["aliases"]], [scopes.get(a, []) for a in e["anchors"]], domain, threshold
        )
        for e in entities
    }
    entity_rows = [
        {"src": eid, "dst": m.node.element_id, "props": {"score": m.score, "scoped": r.scoped}}
        for eid, r in results.items()
        for m in r.matches
    ]
    _write_links(driver, "(src:Entity {id: r.src})", "REFERS_TO", entity_rows)

    return LinkReport(
        documents_linked=len(document_rows),
        documents_total=len(documents),
        entities_linked=sum(bool(r.matches) for r in results.values()),
        entities_linked_in_scope=sum(r.scoped for r in results.values()),
        entities_ambiguous=sum(r.ambiguous for r in results.values()),
        entity_links=len(entity_rows),
    )


def _write_links(driver: Driver, source_match: str, relationship: str, rows: list[dict]) -> None:
    """MERGE one link per row. `source_match` is the Cypher pattern that finds the source node from `r.src`.

    The target is found by element id, so one query serves every domain label.
    """
    if rows:
        driver.execute_query(
            f"UNWIND $rows AS r MATCH {source_match} MATCH (n) WHERE elementId(n) = r.dst "
            f"MERGE (src)-[l:{relationship}]->(n) SET l += r.props",
            rows=rows,
        )
