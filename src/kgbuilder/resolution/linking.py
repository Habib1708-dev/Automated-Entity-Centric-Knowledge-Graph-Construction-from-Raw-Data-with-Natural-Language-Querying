"""Link the text graph to the domain graph: `(Document)-[:ABOUT]->(domain node)` and
`(Chunk)-[:ABOUT]->(record)`.

Role in the pipeline: `kg link`, after the domain graph, the lexical graph and the subject graph exist, and
before `kg resolve` (R75): the identity stage matches mentions to records inside the scope of the things
their documents are ABOUT, so those links come first.
Design: read names and contexts from Neo4j, match in pure functions (unit-tested without a database), write
the links in batches. No LLM: a document is ABOUT the domain node whose name its file name contains, or -
for a record document - the record of its own key; a section whose heading names a record's key is ABOUT
that record (R67).
Not here: what a mention refers to (records.py and identity.py, R75; until R75 entities were linked here),
which things a claim is about (attachment.py, after `kg resolve`, R76; until R76 observations were attached
here, from these links alone), and the ABOUT links a document's text gives (attachment.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.text import norm, squash
from ..structured.plan import ConstructionPlan, name_property

# Names shorter than this, once squashed, are too likely to occur inside an unrelated file name ("bed"
# in "embedded_notes") to be trusted for document linking.
_MIN_NAME_CHARS_FOR_DOCUMENT_MATCH = 4

# A record key shorter than this, once squashed, is too likely to appear in a heading by accident
# ("P1" inside "## Part 1") to be trusted for section linking.
_MIN_KEY_CHARS_FOR_SECTION_MATCH = 4


class DomainNode(BaseModel):
    """A node of the domain graph, reduced to what matching needs."""

    element_id: str  # Neo4j elementId: stable while the node exists, so read and write agree within a run
    label: str
    name: str
    # the unique column's value as text, the key of `record_ref` (read_domain_nodes reads it); "" where the
    # caller addresses records by their ref already (the audit snapshot)
    key: str = ""


class LinkReport(BaseModel):
    """Counts of one linking run. Metric names in MLflow; keep them stable."""

    documents_linked: int
    documents_total: int
    record_documents_linked: int  # documents built from a record, linked to it by key (R67)
    chunks_linked: int  # chunks ABOUT a record because their heading names its key (R67)


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
    """Element id, label, display name and key of every domain node that has a name."""
    nodes: list[DomainNode] = []
    for rule in plan.nodes:
        label, name = cypher_ident(rule.label), cypher_ident(name_property(rule))
        key = cypher_ident(rule.unique_column)
        # the key as text, as `read_record_keys` reads it: a number key 11440802 is the record "11440802"
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) RETURN elementId(n) AS id, n.{name} AS name, toString(n.{key}) AS key"
        )
        nodes += [
            DomainNode(element_id=r["id"], label=rule.label, name=str(r["name"]), key=r["key"] or "")
            for r in records
            if r["name"] is not None
        ]
    return nodes


def link_graphs(driver: Driver, plan: ConstructionPlan) -> LinkReport:
    """Recompute all ABOUT links of documents and chunks. Idempotent; returns counts of what was linked."""
    domain = read_domain_nodes(driver, plan)
    # Links are derived data: recomputing them from scratch keeps a rerun (after a new plan, a resolve or
    # an undo) from keeping links that the current graph no longer supports. The ABOUT links a document's
    # text gives (attachment.py) go too: they rest on identity decisions, so `kg attach` writes them again
    # after `kg resolve`.
    driver.execute_query("MATCH (:Document)-[l:ABOUT]->() DELETE l")
    driver.execute_query("MATCH (:Chunk)-[l:ABOUT]->() DELETE l")

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
    record_keys = read_record_keys(driver, plan)
    chunks, _, _ = driver.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.text AS text")
    chunk_rows = [
        {"src": c["id"], "dst": r.element_id, "props": {"name": r.name}}
        for c in chunks
        # `or ""`: a chunk stored without text (hand-built graphs) simply has no headings
        for r in match_chunk_records(c["text"] or "", record_keys)
    ]
    _write_links(driver, "(src:Chunk {chunk_id: r.src})", "ABOUT", chunk_rows)

    return LinkReport(
        documents_linked=len(document_rows) + records_linked,
        documents_total=len(documents),
        record_documents_linked=records_linked,
        chunks_linked=len({row["src"] for row in chunk_rows}),
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


def read_record_keys(driver: Driver, plan: ConstructionPlan) -> list[RecordKey]:
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
