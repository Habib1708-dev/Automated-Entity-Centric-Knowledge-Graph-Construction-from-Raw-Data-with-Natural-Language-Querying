"""Link the three graphs: `(Document)-[:ABOUT]->(domain node)`, `(Chunk)-[:ABOUT]->(record)`,
`(Entity)-[:REFERS_TO]->(domain node)` and `(thing)-[:HAS_OBSERVATION]->(Observation)`.

Role in the pipeline: `kg link`, after the domain graph, the lexical graph and the subject graph exist.
Design: three separated steps: read names and contexts from Neo4j, match in pure functions (unit-tested
without a database), write the links in batches. Entities are matched inside the *scope* of the domain
node their documents are about, because generic names repeat across the domain ("Legs" is an assembly of
every chair and table); only names that are unique in the whole domain graph are linked without a scope.
No LLM: links are a name contained in a file name, a near-exact fuzzy name match, a node name contained
whole-word in an entity's name within its documents' scope (R60's rule, in linking since R67), or - for a
record document or a section heading naming a record's key (R67) - the record's own key.
An observation belongs to the thing its chunk's document is ABOUT (R64) and, since R67, also to the
record its own section is ABOUT: code decides what a claim is about, never the model, and never through
a shared kind node, which is what made one product's defects reachable from another in R62.
Not here: merging entities with each other (resolver.py).
"""

from neo4j import Driver
from pydantic import BaseModel
from rapidfuzz import fuzz

from ..core.cypher import cypher_ident
from ..core.text import norm, squash
from ..structured.plan import ConstructionPlan, name_property

# Names shorter than this, once squashed, are too likely to occur inside an unrelated file name ("bed"
# in "embedded_notes") to be trusted for document linking.
_MIN_NAME_CHARS_FOR_DOCUMENT_MATCH = 4

# How far from a document's domain node an entity of that document may link, counted in domain
# relationships. 2 reaches a product's assemblies and their parts, but not the suppliers of those parts
# (3): a review names the parts it complains about, not who made them.
_SCOPE_HOPS = 2

# A record key shorter than this, once squashed, is too likely to appear in a heading by accident
# ("P1" inside "## Part 1") to be trusted for section linking.
_MIN_KEY_CHARS_FOR_SECTION_MATCH = 4


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
    by_containment: bool = False  # at least one match found by whole-word containment (R67)


class LinkReport(BaseModel):
    """Counts of one linking run. Metric names in MLflow; keep them stable."""

    documents_linked: int
    documents_total: int
    record_documents_linked: int  # documents built from a record, linked to it by key (R67)
    chunks_linked: int  # chunks ABOUT a record because their heading names its key (R67)
    entities_linked: int  # entities with at least one REFERS_TO
    entities_linked_in_scope: int
    entities_linked_by_containment: int  # linked because their name contains the node's name (R67)
    entities_ambiguous: int
    entity_links: int  # REFERS_TO relationships; above entities_linked when a name recurs across scopes


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


def contain_entity(names: list[str], nodes: list[DomainNode]) -> list[EntityMatch]:
    """All nodes whose whole name occurs word-for-word inside one of the entity's names, longest name only.

    R60's containment rule, in linking since R67: the plan names a node by one column ("CIVIC", the
    `model`) while the text writes it in full ("2016 Honda Civic"), which no fuzzy threshold can accept
    without accepting garbage too. Whole words, so "ESCAPE" never matches "escaped"; names shorter than
    the document-match bound are skipped for the same reason as there; the longest contained name wins
    ("Coffee Table" over "Table"), and several nodes tied for it mean the name is ambiguous.
    """
    entity_words = [set(norm(n).split()) for n in names if norm(n)]
    hits = [
        node
        for node in nodes
        if len(squash(node.name)) >= _MIN_NAME_CHARS_FOR_DOCUMENT_MATCH
        and any(set(norm(node.name).split()) <= words for words in entity_words)
    ]
    if not hits:
        return []
    best = max(len(squash(n.name)) for n in hits)
    # score 100: the node's full name is present verbatim, which is stronger than any fuzzy score
    return [EntityMatch(node=n, score=100.0) for n in hits if len(squash(n.name)) == best]


def link_entity(
    names: list[str], scopes: list[list[DomainNode]], domain: list[DomainNode], threshold: float
) -> EntityLinks:
    """Link one entity: inside each scope of its documents first, else in the whole domain graph.

    A scope is the neighbourhood of a node the entity's documents are ABOUT. An entity mentioned in the
    reviews of two products gets one link per product it matches in, so "legs" in the chair reviews and
    "legs" in the table reviews point at the chair's and the table's legs. Inside a scope, a name that no
    fuzzy match finds may still contain a node's whole name (`contain_entity`); outside every scope, only
    a name that is unique in the domain graph is linked, and containment is not trusted at all (a scope
    vouches that the document is about the node's neighbourhood, the whole domain vouches for nothing).
    """
    matches: dict[str, EntityMatch] = {}
    ambiguous = False
    by_containment = False
    for scope in scopes:
        found = match_entity(names, scope, threshold)
        if not found:  # fuzzy said nothing at all; an ambiguous fuzzy tie is not overridden
            found = contain_entity(names, scope)
            by_containment |= len(found) == 1
        if len(found) == 1:
            matches.setdefault(found[0].node.element_id, found[0])
        ambiguous |= len(found) > 1
    if matches:
        return EntityLinks(
            matches=list(matches.values()), scoped=True, ambiguous=False, by_containment=by_containment
        )

    found = match_entity(names, domain, threshold)
    if len(found) == 1:
        return EntityLinks(matches=found, scoped=False, ambiguous=False)
    return EntityLinks(matches=[], scoped=False, ambiguous=ambiguous or len(found) > 1)


class RecordKey(BaseModel):
    """One record's identity, reduced to what section matching needs."""

    element_id: str
    key: str  # the unique-column value as text
    name: str  # display name, carried onto the ABOUT link


def heading_tokens(text: str) -> set[str]:
    """The squashed tokens of every markdown heading line in `text`.

    Only headings: a key cited in running text ("similar to complaint 11440802") states a relation, not
    what the section is about. Tokens are squashed so "P-1000" and "P-1000:" both yield "p1000".
    """
    tokens: set[str] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            tokens |= {squash(token) for token in norm(line).split()}
    return tokens - {""}


def match_chunk_records(text: str, records: list[RecordKey]) -> list[RecordKey]:
    """The records whose key appears as a whole token in one of the chunk's headings (R67).

    "## Complaint 11440801: brakes failed" names record 11440801; a key inside a longer token
    ("111440801") does not match. Keys below the length bound are never matched: too likely to appear in
    a heading by accident.
    """
    tokens = heading_tokens(text)
    return [
        r
        for r in records
        if len(squash(r.key)) >= _MIN_KEY_CHARS_FOR_SECTION_MATCH and squash(r.key) in tokens
    ]


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
    driver.execute_query("MATCH (:Chunk)-[l:ABOUT]->() DELETE l")
    driver.execute_query("MATCH (:Entity)-[l:REFERS_TO]->() DELETE l")

    documents, _, _ = driver.execute_query(
        "MATCH (d:Document) RETURN d.doc_id AS id, d.title AS title, d.record_label AS record_label, "
        "d.record_key_property AS record_key_property, d.record_key AS record_key"
    )
    # a document built from a record is linked to that record by key, never by its title: its title is
    # the record's display name and could match a different, similarly named node
    file_docs = [d for d in documents if d["record_label"] is None]
    record_docs = [d for d in documents if d["record_label"] is not None]
    document_rows = [
        # the thing's display name travels on the link, so a reader of the text graph can name what a
        # document is about without knowing which property the plan names each label by
        {"src": d["id"], "dst": node.element_id, "props": {"name": node.name}}
        for d in file_docs
        if (node := match_document(d["title"], domain))
    ]
    _write_links(driver, "(src:Document {doc_id: r.src})", "ABOUT", document_rows)
    records_linked = _link_record_documents(driver, record_docs)

    # a section whose heading names a record's key is ABOUT that record (R67): the vehicle document's
    # "## Complaint 11440801" sections reach their Complaint records this way
    record_keys = _read_record_keys(driver, plan)
    chunks, _, _ = driver.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.text AS text")
    chunk_rows = [
        {"src": c["id"], "dst": r.element_id, "props": {"name": r.name}}
        for c in chunks
        # `or ""`: a chunk stored without text (hand-built graphs) simply has no headings
        for r in match_chunk_records(c["text"] or "", record_keys)
    ]
    _write_links(driver, "(src:Chunk {chunk_id: r.src})", "ABOUT", chunk_rows)

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
        {
            "src": eid,
            "dst": m.node.element_id,
            "props": {"score": m.score, "scoped": r.scoped, "contained": r.by_containment},
        }
        for eid, r in results.items()
        for m in r.matches
    ]
    _write_links(driver, "(src:Entity {id: r.src})", "REFERS_TO", entity_rows)

    return LinkReport(
        documents_linked=len(document_rows) + records_linked,
        documents_total=len(documents),
        record_documents_linked=records_linked,
        chunks_linked=len({row["src"] for row in chunk_rows}),
        entities_linked=sum(bool(r.matches) for r in results.values()),
        entities_linked_in_scope=sum(r.scoped for r in results.values()),
        entities_linked_by_containment=sum(r.by_containment for r in results.values()),
        entities_ambiguous=sum(r.ambiguous for r in results.values()),
        entity_links=len(entity_rows),
    )


def _link_record_documents(driver: Driver, docs: list[dict]) -> int:
    """MERGE `(Document)-[:ABOUT]->(record)` for documents built from a record, matched by the record's
    key. Returns how many documents found their record; a missing record leaves its document unlinked,
    which the documents_linked/documents_total gap makes visible.

    Keys are compared as text: the document carries the staged CSV's string while the importer stored
    the typed value. That comparison cannot use the key index, which is fine for the few record
    documents a dataset has (29 recalls on NHTSA). The link's name is the record's display name, taken
    from the document title (record_documents.py set it from the plan's name column).
    """
    linked = 0
    groups: dict[tuple[str, str], list[dict]] = {}
    for d in docs:
        groups.setdefault((d["record_label"], d["record_key_property"]), []).append(d)
    for (label, key_prop), group in groups.items():
        records, _, _ = driver.execute_query(
            "UNWIND $rows AS r MATCH (src:Document {doc_id: r.src}) "
            f"MATCH (n:{cypher_ident(label)}) WHERE toString(n.{cypher_ident(key_prop)}) = r.key "
            "MERGE (src)-[l:ABOUT]->(n) SET l.name = r.name RETURN count(DISTINCT src) AS c",
            rows=[{"src": d["id"], "key": d["record_key"], "name": d["title"]} for d in group],
        )
        linked += records[0]["c"]
    return linked


def _read_record_keys(driver: Driver, plan: ConstructionPlan) -> list[RecordKey]:
    """Element id, key (as text) and display name of every domain node, for section matching."""
    keys: list[RecordKey] = []
    for rule in plan.nodes:
        label, key = cypher_ident(rule.label), cypher_ident(rule.unique_column)
        name = cypher_ident(name_property(rule))
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) WHERE n.{key} IS NOT NULL "
            f"RETURN elementId(n) AS id, toString(n.{key}) AS key, "
            f"coalesce(toString(n.{name}), toString(n.{key})) AS name"
        )
        keys += [RecordKey(element_id=r["id"], key=r["key"], name=r["name"]) for r in records]
    return keys


def attach_observations(driver: Driver) -> int:
    """Recompute `(thing)-[:HAS_OBSERVATION {name}]->(o)` for every observation, from the ABOUT links of
    its chunk's document AND of the chunk itself. Returns how many observations are attached.

    Claims hang on both things (R67, decided with the user): the document's thing keeps the R65 questions
    and path_truth meaningful, the section's record makes the claim reachable from the record. Runs after
    derivation, so derived observations are attached like extracted ones. Recomputed from scratch, like
    the other links, so a changed plan or an undone merge leaves no stale attachment.
    """
    driver.execute_query("MATCH ()-[h:HAS_OBSERVATION]->(:Observation) DELETE h")
    driver.execute_query(
        "MATCH (o:Observation)-[:FROM]->(:Chunk)-[:PART_OF]->(:Document)-[a:ABOUT]->(n) "
        "MERGE (n)-[h:HAS_OBSERVATION]->(o) SET h.name = a.name"
    )
    driver.execute_query(
        "MATCH (o:Observation)-[:FROM]->(:Chunk)-[a:ABOUT]->(n) "
        "MERGE (n)-[h:HAS_OBSERVATION]->(o) SET h.name = a.name"
    )
    records, _, _ = driver.execute_query(
        "MATCH ()-[:HAS_OBSERVATION]->(o:Observation) RETURN count(DISTINCT o) AS n"
    )
    return records[0]["n"]


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
