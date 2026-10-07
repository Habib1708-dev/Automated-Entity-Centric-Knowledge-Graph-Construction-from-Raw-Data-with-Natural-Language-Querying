"""Facts derived in code from the links, instead of extracted: a thing the text names belongs to the thing
its document is about.

Role in the pipeline: second half of `kg link`, after the ABOUT links exist and before `kg resolve`. For
every fact type the text schema marks `derived` (subject type S, predicate P, object type O), every S
mention in a chunk of a document ABOUT a domain node becomes an observation "S mention P (O mention named
like that node)": one per mention chunk, with the chunk's sentence that names the mention as verbatim
evidence. It is written by the subject-graph writer, so a derived claim has the shape of an extracted one.
The node stands for the claim's object, so it must be one of O's records (R109, `derives_into`): a recall
document is ABOUT its Recall record, and "seatbacks INSTALLED_IN 17V472000" made a recall number a Vehicle.
An O that names no record labels keeps the older rule, any ABOUT node.
Design: "the LLM proposes, code decides", one step further: what the document states by itself (the title
names the thing, the text names its piece) is never asked from the model, which used to spend more than
half of its output on it. The rule only reads the path Mention <-MENTIONS- Chunk -PART_OF-> Document
-ABOUT-> node, written by deterministic stages. The object is a mention of the claim's own document (R75):
the one named like the node, else the one whose name contains the node's name ("2019 Subaru Outback" for
"OUTBACK", R60), else a new mention named like the node; so the thing is one mention per document, and the
identity stage decides what it refers to like any other mention. Derived observations carry
`extractor = "derived"`, so a reader can tell them from model output. All writes are MERGE.
Not here: ABOUT links (linking.py), the extracted facts (text/extraction.py), identity (identity.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.text import norm, pick_sentence
from ..structured.plan import ConstructionPlan
from ..text.schema import EntityType, FactType, TextSchema
from ..text.subject_graph import (
    MentionRow,
    ObservationRow,
    mention_row,
    observation_row,
    write_mentions,
    write_observations,
)
from .linking import DomainNode, read_domain_nodes

# The `extractor` property of a derived fact; facts from the model carry the model id instead.
DERIVED_EXTRACTOR = "derived"


class DerivationReport(BaseModel):
    """Counts of one derivation run. Metric names in MLflow; keep them stable."""

    facts_derived: int
    # object mentions (the things) that no extracted claim of their document had written (R75; before,
    # `entities_created`: entities of the whole corpus)
    mentions_created: int
    skipped_no_evidence: int  # mentions whose chunk has no sentence naming them: no quote, no fact
    # documents whose object mention was found by containment ("2019 Subaru Outback" for "OUTBACK", R60)
    targets_by_containment: int = 0
    # mention chunks of documents ABOUT a node that is not a record of the object type (R109): no fact
    skipped_other_label: int = 0


class Candidate(BaseModel):
    """A mention of the object type in one document."""

    id: str
    name: str
    chunks: int  # chunks of the document that mention it


class _Target(BaseModel):
    """The object mention of one document's derived claims."""

    mention: MentionRow
    created: bool
    contained: bool


def containing_mention(node_name: str, candidates: list[Candidate]) -> str | None:
    """The id of the candidate whose name contains every word of the domain node's name, or None.

    The plan names a node by one column ("OUTBACK", the `model`), while the text names the same thing in
    full ("2019 Subaru Outback"); without this, derivation wrote a second thing for it (found in R54). The
    candidates are the mentions of one document ABOUT the node, so a word the node's name shares with
    another thing elsewhere cannot pull that thing in. Whole words, not substrings: "ESCAPE" must not match
    "ESCAPED". Several candidates: the most mentioned, then the shortest name and the id (deterministic).
    """
    wanted = set(norm(node_name).split())
    if not wanted:
        return None
    matching = [c for c in candidates if wanted <= set(norm(c.name).split())]
    if not matching:
        return None
    return min(matching, key=lambda c: (-c.chunks, len(c.name), c.id)).id


def derives_into(object_type: EntityType | None, label: str) -> bool:
    """True when a document ABOUT a domain node labelled `label` gets derived claims of a fact type whose
    object type is `object_type` (R109): the node stands for the claim's object, so it must be one of that
    type's records. A recall document is ABOUT its `Recall` record; with object type `Vehicle` (record label
    `Vehicle`) code wrote "seatbacks INSTALLED_IN 17V472000", a recall number as a vehicle: 97 of held-out's
    135 derived claims in R108. An object type that names no record labels (not keyed, or not in the schema)
    cannot say which nodes are its things, so it keeps the rule before R109: every ABOUT node.
    """
    if object_type is None or not object_type.record_labels:
        return True
    return label in object_type.record_labels


class DerivationSource(BaseModel):
    """One mention of a derived fact type's subject type in one chunk of a document ABOUT a domain node:
    a row of the path Mention <-MENTIONS- Chunk -PART_OF-> Document -ABOUT-> node."""

    id: str  # mention id
    name: str
    chunk_id: str
    text: str  # the chunk's text, where the evidence sentence is picked
    doc_id: str
    node: str  # the ABOUT node's id (an element id in the graph, a record ref in the audit's snapshot)


class DerivedRows(BaseModel):
    """What one fact type's derivation writes, and its counts."""

    mentions: list[MentionRow]  # the object mentions (the things), one per id
    mentioned_in: set[tuple[str, str]]  # (chunk id, object mention id): the claims' chunks mention the thing
    observations: list[ObservationRow]
    created: int  # object mentions no extracted claim of their document had written
    contained: int  # object mentions found by containment
    skipped: int
    other_label: int = 0  # sources whose ABOUT node is not a record of the object type (R109)


def derive_rows(
    fact_type: FactType,
    object_type: EntityType | None,
    sources: list[DerivationSource],
    candidates: dict[str, list[Candidate]],
    nodes: dict[str, DomainNode],
) -> DerivedRows:
    """The rows `derive_facts` writes for one fact type, without touching the graph. Pure, so the graph audit
    (audit/snapshot.py, R87) rebuilds the derived claims offline with this very code, `derives_into` included.

    `object_type` is the schema's entity type named by the fact type's object (None when the schema lacks
    it); `sources` in write order (by mention id, then chunk id); `candidates` are the object type's mentions
    per document; `nodes` the named domain nodes by id (a node without a name derives nothing).
    """
    targets: dict[tuple[str, str], _Target] = {}
    rows: list[tuple[DerivationSource, MentionRow, str]] = []
    skipped = other_label = 0
    for r in sources:
        node = nodes.get(r.node)  # None: the ABOUT node has no name in the plan
        if node is not None and not derives_into(object_type, node.label):
            other_label += 1
            continue
        sentence = pick_sentence(r.text or "", [r.name]) if node is not None else None
        if node is None or sentence is None:
            skipped += 1
            continue
        key = (r.doc_id, r.node)
        if key not in targets:
            targets[key] = _target(fact_type, node.name, r.chunk_id, candidates.get(r.doc_id, []))
        target = targets[key].mention
        if target.id == r.id:  # the thing itself: "OUTBACK PART_OF OUTBACK" says nothing
            skipped += 1
            continue
        rows.append((r, target, sentence))
    return DerivedRows(
        # the chunk mentions the thing too: its document is about it, which is what the claim rests on;
        # without the mention the provenance check would report the thing's mention as sourceless
        mentions=list({target.id: target for _, target, _ in rows}.values()),
        mentioned_in={(r.chunk_id, target.id) for r, target, _ in rows},
        observations=[
            # the claim's wording is the two mentions' names: what the chunk and the plan call them
            observation_row(fact_type.predicate, r.id, target.id, r.chunk_id, sentence, (r.name, target.name))
            for r, target, sentence in rows
        ],
        created=sum(t.created for t in targets.values()),
        contained=sum(t.contained for t in targets.values()),
        skipped=skipped,
        other_label=other_label,
    )


def derive_facts(driver: Driver, schema: TextSchema, plan: ConstructionPlan) -> DerivationReport:
    """Write every fact the schema marks `derived`. Idempotent; returns the counts."""
    nodes = {n.element_id: n for n in read_domain_nodes(driver, plan)}
    facts = created = skipped = contained = other_label = 0
    for fact_type in schema.derived():
        records, _, _ = driver.execute_query(
            # ORDER BY keeps the write order, and so the report, deterministic
            "MATCH (m:Mention {type: $stype})<-[:MENTIONS]-(c:Chunk)-[:PART_OF]->(d:Document)-[:ABOUT]->(n) "
            "RETURN m.id AS id, m.name AS name, c.chunk_id AS chunk_id, c.text AS text, d.doc_id AS doc_id, "
            "elementId(n) AS node ORDER BY id, chunk_id",
            stype=fact_type.subject_type,
        )
        sources = [
            DerivationSource(
                id=r["id"], name=r["name"], chunk_id=r["chunk_id"], text=r["text"] or "", doc_id=r["doc_id"],
                node=r["node"],
            )
            for r in records
        ]  # fmt: skip
        object_type = schema.entity_type(fact_type.object_type)
        derived = derive_rows(
            fact_type, object_type, sources, _candidates(driver, fact_type.object_type), nodes
        )
        write_mentions(driver, derived.mentions, derived.mentioned_in)
        write_observations(driver, derived.observations, DERIVED_EXTRACTOR)
        facts += len(derived.observations)
        created += derived.created
        contained += derived.contained
        skipped += derived.skipped
        other_label += derived.other_label
    return DerivationReport(
        facts_derived=facts,
        mentions_created=created,
        skipped_no_evidence=skipped,
        targets_by_containment=contained,
        skipped_other_label=other_label,
    )


def _target(fact_type: FactType, node_name: str, chunk_id: str, candidates: list[Candidate]) -> _Target:
    """The object mention for a document ABOUT the node named `node_name`: one named like it, else one
    containing its name, else a new mention named like it."""
    new = mention_row(fact_type.object_type, node_name, chunk_id)
    if any(c.id == new.id for c in candidates):  # the text names the thing exactly like the plan
        return _Target(mention=new, created=False, contained=False)
    found = containing_mention(node_name, candidates)
    if found is None:
        return _Target(mention=new, created=True, contained=False)
    name = next(c.name for c in candidates if c.id == found)
    return _Target(mention=new.model_copy(update={"id": found, "name": name}), created=False, contained=True)


def _candidates(driver: Driver, entity_type: str) -> dict[str, list[Candidate]]:
    """Document id -> the mentions of `entity_type` in that document, with their chunk counts."""
    records, _, _ = driver.execute_query(
        "MATCH (m:Mention {type: $etype}) OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(m) "
        "RETURN m.doc_id AS doc_id, m.id AS id, m.name AS name, count(c) AS chunks ORDER BY doc_id, id",
        etype=entity_type,
    )
    out: dict[str, list[Candidate]] = {}
    for r in records:
        out.setdefault(r["doc_id"], []).append(Candidate(id=r["id"], name=r["name"], chunks=r["chunks"]))
    return out
