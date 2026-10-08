"""Read every node's evidence and every claim's sentence from the graph (R118).

Role in the pipeline: `kg units` (and R119's `kg index`) call it on a finished graph, after `kg attach`; its
evidence goes to a node representation (representation.py), its claim sentences become units of their own.
Design: Repository, read-only. Three kinds of node get evidence:
- a record (one per plan node rule): its key and columns as text, but its name and its prose columns (they
  are chunk text already, and would fill the card); the names its mentions write; its relationships to other
  records, grouped by type, direction and the other label; the claims it holds (`HAS_OBSERVATION`);
- an individual: the names its mentions write, the claims it holds, and the claims it is an end of, grouped
  by predicate, direction and the other end's type;
- a concept: its names and the claims it is an end of. Concepts never hold claims (attachment.py).
Claims are read with their canonical ends (graph/canonical.py) and their truth fields, then the observations
of one triple with the same modality and condition on one node become one claim with its counts of stating
and denying observations, so a card shows a disagreement instead of hiding one side. The name caps are
applied in Cypher, so a hub's thousand neighbours never leave the database; the claims cap keeps each
predicate's best supported claims first.
Not here: rendering (cards.py), embedding and writing units (R119).
"""

from collections import defaultdict
from collections.abc import Mapping

from neo4j import Driver
from pydantic import BaseModel, Field

from ..core.cypher import cypher_ident
from ..core.identity import record_ref
from ..core.text import norm
from ..graph.canonical import canonical_id, canonical_name
from ..structured.plan import ConstructionPlan, NodeRule, name_property
from .claims import ClaimSentence, claim_text
from .evidence import EvidenceClaim, Neighbours, NodeEvidence


class EvidenceCaps(BaseModel):
    """How much of a node's evidence is read: shared by every representation, so A and B see the same."""

    names: int = Field(ge=1)  # aliases, and the other ends named per relation
    claims: int = Field(ge=0)  # claims per node, each predicate's best supported first


# a claim and the two mentions it names
_ENDS = "(o:Observation)-[:SUBJECT]->(s:Mention), (o)-[:OBJECT]->(t:Mention)"
# A claim's fields as every reader of claims here needs them: its id and predicate, the chunk it was read
# from (its FROM edge), its truth fields (an observation written before R77 has none: an affirmed, actual
# claim), and its two canonical ends
_CLAIM = (
    "o.id AS id, o.predicate AS predicate, head([(o)-[:FROM]->(c:Chunk) | c.chunk_id]) AS chunk_id, "
    "coalesce(o.triple_truth, 'affirmed') AS truth, coalesce(o.modality, 'actual') AS modality, "
    "coalesce(o.hedge, '') AS hedge, coalesce(o.condition, '') AS condition, "
    f"{canonical_id('s')} AS sid, {canonical_name('s')} AS sname, s.type AS stype, "
    f"{canonical_id('t')} AS tid, {canonical_name('t')} AS tname, t.type AS ttype"
)


def read_evidence(
    driver: Driver, plan: ConstructionPlan | None, caps: EvidenceCaps, excluded: Mapping[str, set[str]]
) -> list[NodeEvidence]:
    """The evidence of every record (by the plan's node rules; none without a plan), individual and concept,
    records first in plan order, each kind sorted by ref. `excluded` maps a record label to the columns its
    cards leave out (the prose columns)."""
    records, by_element = _records(driver, plan, caps, excluded)
    held = _held_claims(driver, by_element, caps)
    kinds = _kinds(driver, caps)
    return [
        e.model_copy(update={"claims": held[e.ref][0], "claims_total": held[e.ref][1]})
        for e in records + kinds
    ]


def read_targets(driver: Driver, plan: ConstructionPlan | None) -> dict[str, str]:
    """Ref -> element id of every record (by the plan's node rules), individual and concept: where a card
    points (R119). Element ids change on every rebuild (R113), so they are read anew in each run and never
    stored as a ref."""
    out = {}
    for rule in plan.nodes if plan else []:
        rows, _, _ = driver.execute_query(
            f"MATCH (n:{cypher_ident(rule.label)}) "
            f"RETURN elementId(n) AS id, toString(n.{cypher_ident(rule.unique_column)}) AS key"
        )
        out |= {record_ref(rule.label, r["key"]): r["id"] for r in rows}
    rows, _, _ = driver.execute_query(
        "MATCH (n) WHERE n:Concept OR n:Individual RETURN n.id AS ref, elementId(n) AS id"
    )
    return out | {r["ref"]: r["id"] for r in rows}


def read_claim_sentences(driver: Driver) -> list[ClaimSentence]:
    """One sentence per observation, by id."""
    rows, _, _ = driver.execute_query(f"MATCH {_ENDS} RETURN {_CLAIM} ORDER BY id")
    return [ClaimSentence(id=r["id"], text=_sentence(r), chunk_id=r["chunk_id"]) for r in rows]


def _sentence(row: Mapping[str, str]) -> str:
    """The sentence of a claim row read with `_CLAIM`."""
    return claim_text(row["sname"], row["stype"], row["predicate"], row["tname"], row["ttype"])


def _records(
    driver: Driver, plan: ConstructionPlan | None, caps: EvidenceCaps, excluded: Mapping[str, set[str]]
) -> tuple[list[NodeEvidence], dict[str, str]]:
    """Every record's evidence but its claims, and element id -> ref (the claims' holders are read by
    element id). A record without a name is titled by its key."""
    if plan is None:
        return [], {}
    aliases, _, _ = driver.execute_query(
        "MATCH (m:Mention)-[:REFERS_TO {kind: 'record'}]->(t) "
        "RETURN elementId(t) AS id, apoc.coll.sort(collect(DISTINCT m.name)) AS names"
    )
    alias_of = {r["id"]: r["names"] for r in aliases}
    relations = _record_relations(driver, plan, caps)
    out, by_element = [], {}
    for rule in plan.nodes:
        name_col, skip = name_property(rule), excluded.get(rule.label, set())
        rows, _, _ = driver.execute_query(
            f"MATCH (n:{cypher_ident(rule.label)}) "
            f"RETURN elementId(n) AS id, toString(n.{cypher_ident(rule.unique_column)}) AS key, "
            "properties(n) AS props"
        )
        for r in rows:
            ref = record_ref(rule.label, r["key"])
            by_element[r["id"]] = ref
            title = str(r["props"].get(name_col) or r["key"])
            columns = [c for c in (rule.unique_column, *rule.properties) if c != name_col and c not in skip]
            props = {c: str(r["props"][c]) for c in sorted(columns) if r["props"].get(c) is not None}
            out.append(
                NodeEvidence(
                    ref=ref, kind="record", label=rule.label, title=title,
                    aliases=_aliases(title, alias_of.get(r["id"], []), caps.names), properties=props,
                    relations=relations.get(r["id"], []), claims=[], claims_total=0,
                )
            )  # fmt: skip
    return sorted(out, key=lambda e: ([n.label for n in plan.nodes].index(e.label), e.ref)), by_element


def _record_relations(
    driver: Driver, plan: ConstructionPlan, caps: EvidenceCaps
) -> dict[str, list[Neighbours]]:
    """Element id -> its relationships to other records, one entry per relationship rule and direction."""
    rules = {rule.label: rule for rule in plan.nodes}
    out: dict[str, list[Neighbours]] = defaultdict(list)
    for rel in plan.relationships:
        if rel.from_label not in rules or rel.to_label not in rules:
            continue
        pattern = (
            f"(a:{cypher_ident(rel.from_label)})-[:{cypher_ident(rel.relationship_type)}]->"
            f"(b:{cypher_ident(rel.to_label)})"
        )
        for outgoing, here, there, label in (
            (True, "a", "b", rel.to_label),
            (False, "b", "a", rel.from_label),
        ):
            # the other ends' names sorted, cut to the cap in the database: a hub's names never all leave it
            rows, _, _ = driver.execute_query(
                f"MATCH {pattern} WITH {here}, {there}, {_display(there, rules[label])} AS name "
                f"WITH {here}, count(DISTINCT {there}) AS n, apoc.coll.sort(collect(DISTINCT name)) AS names "
                f"RETURN elementId({here}) AS id, n, names[0..$cap] AS names",
                cap=caps.names,
            )
            for r in rows:
                out[r["id"]].append(
                    Neighbours(type=rel.relationship_type, outgoing=outgoing, label=label, count=r["n"],
                               names=r["names"])
                )  # fmt: skip
    return {k: sorted(v, key=lambda n: (n.type, not n.outgoing, n.label)) for k, v in out.items()}


def _display(variable: str, rule: NodeRule) -> str:
    """Cypher for a record's display name: its name column, else its key, as text."""
    name, key = cypher_ident(name_property(rule)), cypher_ident(rule.unique_column)
    return f"coalesce(toString({variable}.{name}), toString({variable}.{key}))"


def _kinds(driver: Driver, caps: EvidenceCaps) -> list[NodeEvidence]:
    """Every individual's and concept's evidence but the claims it holds: individuals first, by ref."""
    rows, _, _ = driver.execute_query(
        "MATCH (n) WHERE n:Concept OR n:Individual "
        "OPTIONAL MATCH (m:Mention)-[:REFERS_TO]->(n) "
        "WITH n, apoc.coll.sort(collect(DISTINCT m.name)) AS names "
        "RETURN n.id AS id, n.name AS name, n.type AS type, n:Individual AS individual, names"
    )
    relations = _claim_relations(driver, caps)
    out = [
        NodeEvidence(
            ref=r["id"], kind="individual" if r["individual"] else "concept", label=r["type"] or "",
            title=r["name"], aliases=_aliases(r["name"], r["names"], caps.names), properties={},
            relations=relations.get(r["id"], []), claims=[], claims_total=0,
        )
        for r in rows
    ]  # fmt: skip
    return sorted(out, key=lambda e: (e.kind != "individual", e.ref))


def _claim_relations(driver: Driver, caps: EvidenceCaps) -> dict[str, list[Neighbours]]:
    """Canonical id -> the claims an individual or a concept is an end of, grouped by predicate, direction
    and the other end's type, with the other ends' canonical names (capped in the database)."""
    out: dict[str, list[Neighbours]] = defaultdict(list)
    for outgoing, here, there in ((True, "s", "t"), (False, "t", "s")):
        rows, _, _ = driver.execute_query(
            f"MATCH {_ENDS}, ({here})-[:REFERS_TO]->(n) WHERE n:Concept OR n:Individual "
            f"WITH n.id AS id, o.predicate AS type, {there}.type AS label, "
            f"{canonical_id(there)} AS other, {canonical_name(there)} AS name "
            "WITH id, type, label, count(DISTINCT other) AS n, "
            "apoc.coll.sort(collect(DISTINCT name)) AS names "
            "RETURN id, type, label, n, names[0..$cap] AS names",
            cap=caps.names,
        )
        for r in rows:
            out[r["id"]].append(
                Neighbours(type=r["type"], outgoing=outgoing, label=r["label"] or "", count=r["n"],
                           names=r["names"])
            )  # fmt: skip
    return {k: sorted(v, key=lambda n: (n.type, not n.outgoing, n.label)) for k, v in out.items()}


def _held_claims(
    driver: Driver, records: Mapping[str, str], caps: EvidenceCaps
) -> defaultdict[str, tuple[list[EvidenceClaim], int]]:
    """Ref -> the claims the node holds, in `_by_predicate` order and capped, and how many it holds in all.
    A holder is a record (by element id) or an individual (by its id)."""
    rows, _, _ = driver.execute_query(
        f"MATCH (n)-[:HAS_OBSERVATION]->{_ENDS} "
        f"RETURN elementId(n) AS holder, CASE WHEN n:Individual THEN n.id END AS individual, {_CLAIM} "
        "ORDER BY id"
    )
    groups: dict[str, dict[tuple[str, ...], list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        ref = r["individual"] or records.get(r["holder"])
        if ref is not None:
            groups[ref][(r["sid"], r["predicate"], r["tid"], r["modality"], r["condition"])].append(r)
    out: defaultdict[str, tuple[list[EvidenceClaim], int]] = defaultdict(lambda: ([], 0))
    for ref, by_claim in groups.items():
        claims = _by_predicate([_claim(group) for group in by_claim.values()])
        out[ref] = (claims[: caps.claims], len(claims))
    return out


def _by_predicate(claims: list[EvidenceClaim]) -> list[EvidenceClaim]:
    """The best supported claim of each predicate first, then the second of each, and so on; within a
    predicate by support, then sentence. A capped card thus shows several kinds of fact: by support alone, a
    record's three lines were its derived "part of" claims, which its relationship lines already say (R118,
    the furniture product cards)."""
    ranked = sorted(claims, key=lambda c: (-(c.stated + c.denied), c.sentence, c.id))
    by_predicate: dict[str, list[EvidenceClaim]] = defaultdict(list)
    for c in ranked:  # predicates in the order of their best claim
        by_predicate[c.predicate].append(c)
    rounds = max((len(group) for group in by_predicate.values()), default=0)
    return [group[i] for i in range(rounds) for group in by_predicate.values() if i < len(group)]


def _claim(group: list) -> EvidenceClaim:
    """The claim a group of observations of one triple stands for: the first one's words and qualifiers."""
    first = group[0]
    denied = sum(r["truth"] == "negated" for r in group)
    return EvidenceClaim(
        id=first["id"],
        predicate=first["predicate"],
        sentence=_sentence(first),
        stated=len(group) - denied,
        denied=denied,
        modality=first["modality"],
        hedge=first["hedge"],
        condition=first["condition"],
        chunk_id=first["chunk_id"],
    )


def _aliases(title: str, names: list[str], cap: int) -> list[str]:
    """The names other than the title (ignoring case and accents), sorted, at most `cap`."""
    return [n for n in names if norm(n) != norm(title)][:cap]
