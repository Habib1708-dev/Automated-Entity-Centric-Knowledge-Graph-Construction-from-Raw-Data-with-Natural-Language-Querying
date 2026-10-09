"""What the agent's graph tools read about nodes (R133): nodes by ref, a node's names and properties, its
relations, and claims with their sentences.

Role in the pipeline: the graph-walk tools (graph_tools.py) and `search_claims` (text_tools.py) depend on the
`NodeReader` protocol and are tested with a fake; `Neo4jNodeReader` runs the Cypher, tested once against
Neo4j. The chunks a node reaches come from the existing `GraphStore.reach`, not from here.
Design: Repository, read-only. A node is addressed by its stable ref, the id the target gold and the cards
use: a record by `Label:key` (a label of the plan), an individual or a concept by its canonical id. A
relation is either a record relationship of the plan (between two records) or the claims of one predicate
and direction joining the node to one other node through their mentions; both lists are sorted and cut to
the limit in the database, so a hub's thousand neighbours never leave it, and the total comes along.
Not here: caps and notes for the agent (the tools), ranking chunks (query/ranking.py).
"""

from typing import Protocol

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.identity import record_ref
from ..core.text import norm
from ..graph.canonical import canonical_id, canonical_name, record_ref_of
from ..hybrid.claims import claim_text
from ..hybrid.unit_store import CardStart
from ..structured.plan import ConstructionPlan, NodeRule, name_property
from .results import ClaimFact, NodeFacts, NodeHit, Property, Relation


class RelationPage(BaseModel):
    """A node's relation groups, record relationships first, cut to a limit, and how many there are."""

    relations: list[Relation]
    total: int


class NodeReader(Protocol):
    """What the graph tools read about nodes."""

    def nodes(self, refs: list[str]) -> list[NodeHit]:
        """The nodes these refs name, in the order given; a ref that names no node is left out."""
        ...

    def node(self, ref: str) -> NodeFacts | None:
        """The node `ref` names with its other names and properties; None when it names no node."""
        ...

    def start(self, ref: str) -> CardStart | None:
        """Where `GraphStore.reach` starts for the node: a record by element id, a kind by canonical id."""
        ...

    def relations(self, ref: str, relation: str | None, limit: int) -> RelationPage:
        """The node's relation groups (of one type or predicate, when given), at most `limit`."""
        ...

    def node_claims(self, ref: str, chunk_ids: list[str]) -> list[ClaimFact]:
        """The claims read from these chunks that the node holds or is an end of, by id."""
        ...

    def claims(self, claim_ids: list[str]) -> list[ClaimFact]:
        """The claims with these ids, in the order given; unknown ids are left out."""
        ...


class _Located(BaseModel):
    """A node found by its ref: its element id, what it is, and for a record its rule and properties."""

    element: str
    hit: NodeHit
    rule: NodeRule | None = None
    props: dict[str, str] = {}


# A claim's fields with its two ends bound to `s` and `t`: the chunk of its FROM edge, the truth fields (an
# observation written before R77 has none: an affirmed, actual claim), and the ends' canonical names and
# types, from which code writes the sentence (hybrid/claims.py)
_CLAIM_FIELDS = (
    "o.id AS id, o.predicate AS predicate, head([(o)-[:FROM]->(fc:Chunk) | fc.chunk_id]) AS chunk_id, "
    "coalesce(o.truth, 'affirmed') AS truth, coalesce(o.triple_truth, 'affirmed') AS triple_truth, "
    "coalesce(o.modality, 'actual') AS modality, coalesce(o.hedge, '') AS hedge, "
    "coalesce(o.condition, '') AS condition, "
    f"{canonical_name('s')} AS sname, s.type AS stype, {canonical_name('t')} AS tname, t.type AS ttype"
)

# The record relationships of a node: a relationship type of the plan to another record (a node of a plan
# label; chunks and documents share type names such as PART_OF but carry no plan label), sorted, then cut to
# the limit with the total kept
_RECORD_RELATIONS = (
    "MATCH (n) WHERE elementId(n) = $element "
    "MATCH (n)-[r]-(m) WHERE type(r) IN $types AND any(l IN labels(m) WHERE l IN $labels) "
    "AND ($relation IS NULL OR type(r) = $relation) "
    f"WITH DISTINCT type(r) AS type, startNode(r) = n AS outgoing, {record_ref_of('m')} AS ref, "
    "head([x IN $rules WHERE x.label IN labels(m) | coalesce(toString(m[x.name]), toString(m[x.key]))]) "
    "AS name, head([l IN labels(m) WHERE l IN $labels]) AS label "
    "ORDER BY type, NOT outgoing, name, ref "
    "WITH collect({type: type, outgoing: outgoing, ref: ref, name: name, label: label}) AS rows "
    "RETURN size(rows) AS total, rows[0..$limit] AS rows"
)

# The claims a node is an end of, through the mentions that refer to it: the claim's other end (its other
# SUBJECT or OBJECT edge) by canonical id, one group per predicate, direction and other end, sorted and cut
_CLAIM_RELATIONS = (
    "MATCH (n) WHERE elementId(n) = $element "
    "MATCH (n)<-[:REFERS_TO]-(:Mention)<-[side:SUBJECT|OBJECT]-(o:Observation)-[other:SUBJECT|OBJECT]->"
    "(there:Mention) WHERE type(side) <> type(other) AND ($relation IS NULL OR o.predicate = $relation) "
    f"WITH o, type(side) = 'SUBJECT' AS outgoing, {canonical_id('there')} AS ref, "
    f"{canonical_name('there')} AS name, there.type AS label "
    "WITH o.predicate AS type, outgoing, ref, min(name) AS name, min(label) AS label, "
    "collect(DISTINCT o.id) AS ids "
    "ORDER BY type, NOT outgoing, name, ref "
    "WITH collect({type: type, outgoing: outgoing, ref: ref, name: name, label: coalesce(label, ''), "
    "count: size(ids), claims: apoc.coll.sort(ids)[0..3]}) AS rows "
    "RETURN size(rows) AS total, rows[0..$limit] AS rows"
)

# The claims a node holds (attachment's HAS_OBSERVATION) or is an end of (through its mentions), read from
# the given chunks
_NODE_CLAIMS = (
    "MATCH (n) WHERE elementId(n) = $element "
    "OPTIONAL MATCH (n)-[:HAS_OBSERVATION]->(held:Observation) "
    "WITH n, collect(held) AS held "
    "OPTIONAL MATCH (n)<-[:REFERS_TO]-(:Mention)<-[:SUBJECT|OBJECT]-(end:Observation) "
    "WITH held, collect(end) AS ends UNWIND held + ends AS o "
    "WITH DISTINCT o "
    "MATCH (o)-[:FROM]->(c:Chunk) WHERE c.chunk_id IN $chunks "
    "MATCH (o)-[:SUBJECT]->(s:Mention), (o)-[:OBJECT]->(t:Mention) "
    f"RETURN {_CLAIM_FIELDS} ORDER BY id"
)

_CLAIMS = (
    "MATCH (o:Observation) WHERE o.id IN $ids "
    f"MATCH (o)-[:SUBJECT]->(s:Mention), (o)-[:OBJECT]->(t:Mention) RETURN {_CLAIM_FIELDS}"
)


class Neo4jNodeReader:
    """`NodeReader` over the pipeline's Neo4j graph. `plan` gives the record labels, keys and names (None for
    a text-only dataset: then every ref names an individual or a concept)."""

    def __init__(self, driver: Driver, plan: ConstructionPlan | None):
        self._driver = driver
        self._plan = plan
        self._rules = {rule.label: rule for rule in plan.nodes} if plan else {}

    def nodes(self, refs: list[str]) -> list[NodeHit]:
        found = self._locate_all(refs)
        return [found[ref].hit for ref in refs if ref in found]

    def node(self, ref: str) -> NodeFacts | None:
        located = self._locate(ref)
        if located is None:
            return None
        rows, _, _ = self._driver.execute_query(
            "MATCH (m:Mention)-[:REFERS_TO]->(n) WHERE elementId(n) = $element "
            "RETURN apoc.coll.sort(collect(DISTINCT m.name)) AS names",
            element=located.element,
        )
        title = located.hit.title
        aliases = [name for name in rows[0]["names"] if norm(name) != norm(title)] if rows else []
        return NodeFacts(**located.hit.model_dump(), aliases=aliases, properties=_properties(located))

    def start(self, ref: str) -> CardStart | None:
        located = self._locate(ref)
        if located is None:
            return None
        if located.rule is not None:
            return CardStart(kind="thing", node_id=located.element)
        return CardStart(kind="kind", node_id=ref)

    def relations(self, ref: str, relation: str | None, limit: int) -> RelationPage:
        located = self._locate(ref)
        if located is None:
            return RelationPage(relations=[], total=0)
        params = {"element": located.element, "relation": relation, "limit": limit}
        record_rows, record_total = self._page(_RECORD_RELATIONS, **params, **self._record_params())
        claim_rows, claim_total = self._page(_CLAIM_RELATIONS, **params)
        relations = [Relation(via="record", count=1, claims=[], **r) for r in record_rows]
        relations += [Relation(via="claim", **r) for r in claim_rows]
        return RelationPage(relations=relations[:limit], total=record_total + claim_total)

    def node_claims(self, ref: str, chunk_ids: list[str]) -> list[ClaimFact]:
        located = self._locate(ref)
        if located is None or not chunk_ids:
            return []
        rows, _, _ = self._driver.execute_query(_NODE_CLAIMS, element=located.element, chunks=chunk_ids)
        return [_claim(r) for r in rows]

    def claims(self, claim_ids: list[str]) -> list[ClaimFact]:
        rows, _, _ = self._driver.execute_query(_CLAIMS, ids=claim_ids)
        found = {r["id"]: _claim(r) for r in rows}
        return [found[i] for i in claim_ids if i in found]

    def _locate(self, ref: str) -> _Located | None:
        """The node `ref` names, or None."""
        return self._locate_all([ref]).get(ref)

    def _locate_all(self, refs: list[str]) -> dict[str, _Located]:
        """Ref -> the node it names: a record when its prefix is a plan label (one query per label), else an
        individual or a concept (one query for all); refs that name no node are left out."""
        keys: dict[str, list[str]] = {}
        kinds = []
        for ref in refs:
            label, _, key = ref.partition(":")
            if key and label in self._rules:
                keys.setdefault(label, []).append(key)
            else:
                kinds.append(ref)
        found = {}
        for label, wanted in keys.items():
            found |= self._records(self._rules[label], wanted)
        if kinds:
            rows, _, _ = self._driver.execute_query(
                "MATCH (n) WHERE (n:Concept OR n:Individual) AND n.id IN $refs "
                "RETURN n.id AS ref, elementId(n) AS element, n.name AS name, coalesce(n.type, '') AS type, "
                "n:Individual AS individual",
                refs=kinds,
            )
            for r in rows:
                kind = "individual" if r["individual"] else "concept"
                hit = NodeHit(ref=r["ref"], kind=kind, label=r["type"], title=r["name"])
                found[r["ref"]] = _Located(element=r["element"], hit=hit)
        return found

    def _records(self, rule: NodeRule, keys: list[str]) -> dict[str, _Located]:
        """Ref -> the record of `rule` with each key, titled by its name column, else its key."""
        # the key compared as text, as `record_ref` writes it, so a number key matches its ref too
        rows, _, _ = self._driver.execute_query(
            f"MATCH (n:{cypher_ident(rule.label)}) "
            f"WITH n, toString(n.{cypher_ident(rule.unique_column)}) AS key WHERE key IN $keys "
            "RETURN key, elementId(n) AS element, properties(n) AS props",
            keys=keys,
        )
        found = {}
        for r in rows:
            props = {k: str(v) for k, v in r["props"].items() if v is not None}
            ref = record_ref(rule.label, r["key"])
            title = props.get(name_property(rule)) or r["key"]
            hit = NodeHit(ref=ref, kind="record", label=rule.label, title=title)
            found[ref] = _Located(element=r["element"], hit=hit, rule=rule, props=props)
        return found

    def _record_params(self) -> dict[str, object]:
        """The plan's record labels, relationship types, and each label's key and name column for Cypher."""
        rules = [
            {"label": rule.label, "key": rule.unique_column, "name": name_property(rule)}
            for rule in self._rules.values()
        ]
        types = sorted({rel.relationship_type for rel in self._plan.relationships}) if self._plan else []
        return {"rules": rules, "labels": list(self._rules), "types": types}

    def _page(self, query: str, **params: object) -> tuple[list[dict], int]:
        rows, _, _ = self._driver.execute_query(query, **params)
        return (rows[0]["rows"], rows[0]["total"]) if rows else ([], 0)


def _properties(located: _Located) -> list[Property]:
    """A record's plan columns with their values and source, sorted by name; the name column is its title."""
    rule = located.rule
    if rule is None:
        return []
    key = located.props.get(rule.unique_column, "")
    source = f"{rule.source_file} row {rule.unique_column}={key}"
    columns = {rule.unique_column, *rule.properties} - {name_property(rule)}
    return [
        Property(name=c, value=located.props[c], source=source) for c in sorted(columns) if c in located.props
    ]


def _claim(row) -> ClaimFact:
    """A claim row read with `_CLAIM_FIELDS`, its sentence written by code."""
    sentence = claim_text(row["sname"], row["stype"], row["predicate"], row["tname"], row["ttype"])
    return ClaimFact(
        id=row["id"], sentence=sentence, chunk_id=row["chunk_id"] or "", truth=row["truth"],
        triple_truth=row["triple_truth"], modality=row["modality"], hedge=row["hedge"],
        condition=row["condition"],
    )  # fmt: skip
