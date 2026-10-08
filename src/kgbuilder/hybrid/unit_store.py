"""What the hybrid retrievers read from the retrieval layer (R120): cards, claims and chunks found by vector
or by full text, a card's start node, and the state of the indexes.

Role in the pipeline: the retrievers (retrievers.py) depend on the `UnitStore` protocol and are tested with
a fake; `Neo4jUnitStore` runs the index queries against the layer `kg index` wrote (unit_graph.py, names in
graph/index_layer.py).
Design: Repository, read-only. A claim search joins the hit to its observation in the same query, reads the
truth fields there (one source of truth: a claim sentence's text never says whether it is denied), applies
the optional `ClaimFilter` after the index search, and over-fetches so the depth still fills when the filter
drops hits. Each claim comes with its siblings: the observations of the same canonical subject, predicate
and object whose triple has the opposite truth, so a denial is never read without the statement it denies,
nor the other way round.
Not here: ranking, round-robin and fusion (retrievers.py, R120b), the chunks' own store
(query/graph_store.py).
"""

from typing import Literal, Protocol

from neo4j import Driver
from pydantic import BaseModel

from ..graph.canonical import canonical_id
from ..graph.index_layer import (
    CARD_OF,
    CHUNK_FULLTEXT_INDEX,
    CLAIM_FULLTEXT_INDEX,
    CLAIM_SENTENCE,
    CLAIM_VECTOR_INDEX,
    NODE_CARD,
    RETRIEVAL_UNIT,
    SENTENCE_OF,
    card_indexes,
    card_label,
)
from ..query.answers import ClaimHit

# A filtered claim search asks the index for this many times the hits it needs: a filter keeping one claim
# in four still fills the depth (furniture: 64 of 685 claims are denied, R110)
_OVERFETCH = 4


class ClaimFilter(BaseModel):
    """Which claims a caller that knows the question's polarity wants; None keeps every value."""

    triple_truth: Literal["affirmed", "negated"] | None = None
    modality: Literal["actual", "possible", "conditional"] | None = None

    @property
    def active(self) -> bool:
        return self.triple_truth is not None or self.modality is not None


class CardStart(BaseModel):
    """Where the traversal starts for a card's node: a thing (a record) by element id, a kind (an individual
    or a concept) by canonical id, as `GraphStore.reach` takes them."""

    kind: Literal["thing", "kind"]
    node_id: str


class IndexState(BaseModel):
    """What the layer holds for one representation: its card and claim versions and embedding models, and
    which of its indexes are online. The hybrid source refuses a layer that does not fit (R120b)."""

    cards: int
    card_versions: list[str]
    claims: int
    claim_versions: list[str]
    embed_models: list[str]
    online: list[str]  # the layer's index names that exist and are online


class UnitStore(Protocol):
    """What the hybrid retrievers read from the retrieval layer."""

    def nearest_cards(self, representation: str, vector: list[float], k: int) -> list[str]:
        """The refs of the `k` cards nearest `vector`, nearest first."""
        ...

    def search_cards(self, representation: str, query: str, k: int) -> list[str]:
        """The refs of the `k` cards a Lucene query scores highest, best first."""
        ...

    def nearest_claims(self, vector: list[float], k: int, claim_filter: ClaimFilter) -> list[ClaimHit]:
        """The `k` claims nearest `vector` that pass the filter, nearest first, with their siblings."""
        ...

    def search_claims(self, query: str, k: int, claim_filter: ClaimFilter) -> list[ClaimHit]:
        """The `k` claims a Lucene query scores highest that pass the filter, with their siblings."""
        ...

    def search_chunks(self, query: str, k: int) -> list[str]:
        """The ids of the `k` chunks a Lucene query scores highest, best first."""
        ...

    def card_starts(self, refs: list[str]) -> dict[str, CardStart]:
        """Ref -> where the traversal starts for that card's node; refs without a card are left out."""
        ...

    def index_state(self, representation: str) -> IndexState:
        """What the layer holds for `representation` and the claim sentences."""
        ...


# After an index search yields `node` (a claim sentence) and `score`: its observation and truth fields, the
# filter, the opposite-truth siblings of the same canonical triple, then the best `$k`. Siblings are found
# by predicate first (an observation property), then by their canonical ends.
_CLAIM_HITS = (
    f"MATCH (node)-[:{SENTENCE_OF}]->(o:Observation)-[:SUBJECT]->(s:Mention), (o)-[:OBJECT]->(t:Mention) "
    "WITH node, score, o, s, t, coalesce(o.triple_truth, 'affirmed') AS tt, "
    "coalesce(o.modality, 'actual') AS md "
    "WHERE ($truth IS NULL OR tt = $truth) AND ($modality IS NULL OR md = $modality) "
    f"WITH score, o, tt, md, {canonical_id('s')} AS sid, {canonical_id('t')} AS tid "
    "ORDER BY score DESC LIMIT $k "
    "OPTIONAL MATCH (x:Observation {predicate: o.predicate})-[:SUBJECT]->(xs:Mention), "
    "(x)-[:OBJECT]->(xt:Mention) "
    f"WHERE x <> o AND coalesce(x.triple_truth, 'affirmed') <> tt AND {canonical_id('xs')} = sid "
    f"AND {canonical_id('xt')} = tid "
    "WITH score, o, tt, md, x ORDER BY x.id "
    "WITH score, o, tt, md, collect(x.id) AS sibs, "
    "collect(head([(x)-[:FROM]->(c:Chunk) | c.chunk_id])) AS sib_chunks "
    "RETURN o.id AS id, head([(o)-[:FROM]->(c:Chunk) | c.chunk_id]) AS chunk_id, "
    "coalesce(o.truth, 'affirmed') AS truth, coalesce(o.negation, '') AS negation, md AS modality, "
    "coalesce(o.hedge, '') AS hedge, coalesce(o.condition, '') AS condition, tt AS triple_truth, "
    "sibs AS siblings, sib_chunks AS sibling_chunks ORDER BY score DESC, id"
)


class Neo4jUnitStore:
    """`UnitStore` over the retrieval layer `kg index` wrote into the pipeline's Neo4j graph."""

    def __init__(self, driver: Driver):
        self._driver = driver

    def nearest_cards(self, representation: str, vector: list[float], k: int) -> list[str]:
        return self._refs(
            "CALL db.index.vector.queryNodes($index, $k, $vector) YIELD node, score",
            index=card_indexes(representation).vector, k=k, vector=vector,
        )  # fmt: skip

    def search_cards(self, representation: str, query: str, k: int) -> list[str]:
        return self._refs(
            "CALL db.index.fulltext.queryNodes($index, $query, {limit: $k}) YIELD node, score",
            index=card_indexes(representation).fulltext, query=query, k=k,
        )  # fmt: skip

    def nearest_claims(self, vector: list[float], k: int, claim_filter: ClaimFilter) -> list[ClaimHit]:
        return self._claims(
            "CALL db.index.vector.queryNodes($index, $fetch, $vector) YIELD node, score",
            claim_filter, k, index=CLAIM_VECTOR_INDEX, vector=vector,
        )  # fmt: skip

    def search_claims(self, query: str, k: int, claim_filter: ClaimFilter) -> list[ClaimHit]:
        return self._claims(
            "CALL db.index.fulltext.queryNodes($index, $query, {limit: $fetch}) YIELD node, score",
            claim_filter, k, index=CLAIM_FULLTEXT_INDEX, query=query,
        )  # fmt: skip

    def search_chunks(self, query: str, k: int) -> list[str]:
        rows, _, _ = self._driver.execute_query(
            "CALL db.index.fulltext.queryNodes($index, $query, {limit: $k}) YIELD node, score "
            "RETURN node.chunk_id AS id ORDER BY score DESC, id",
            index=CHUNK_FULLTEXT_INDEX, query=query, k=k,
        )  # fmt: skip
        return [r["id"] for r in rows]

    def card_starts(self, refs: list[str]) -> dict[str, CardStart]:
        # the card of any representation names the same node; a kind is addressed by its canonical id
        rows, _, _ = self._driver.execute_query(
            f"MATCH (u:{NODE_CARD})-[:{CARD_OF}]->(n) WHERE u.ref IN $refs "
            "RETURN DISTINCT u.ref AS ref, elementId(n) AS element, n.id AS id, "
            "(n:Concept OR n:Individual) AS kind",
            refs=refs,
        )
        return {
            r["ref"]: CardStart(kind="kind", node_id=r["id"])
            if r["kind"]
            else CardStart(kind="thing", node_id=r["element"])
            for r in rows
        }

    def index_state(self, representation: str) -> IndexState:
        names = card_indexes(representation)
        rows, _, _ = self._driver.execute_query(
            # the labels as values: on a graph without the layer, naming them would make the server warn
            "MATCH (u) WHERE $layer IN labels(u) "
            "WITH u, CASE WHEN $card IN labels(u) THEN 'card' "
            "WHEN $claim IN labels(u) THEN 'claim' END AS unit "
            "WHERE unit IS NOT NULL "
            "RETURN unit, count(*) AS n, collect(DISTINCT u.version) AS versions, "
            "collect(DISTINCT u.embed_model) AS models",
            layer=RETRIEVAL_UNIT, card=card_label(representation), claim=CLAIM_SENTENCE,
        )  # fmt: skip
        by_unit = {r["unit"]: r for r in rows}
        indexes, _, _ = self._driver.execute_query(
            "SHOW INDEXES YIELD name, state WHERE name IN $names AND state = 'ONLINE' RETURN name",
            names=[
                names.vector,
                names.fulltext,
                CLAIM_VECTOR_INDEX,
                CLAIM_FULLTEXT_INDEX,
                CHUNK_FULLTEXT_INDEX,
            ],
        )
        card, claim = by_unit.get("card"), by_unit.get("claim")
        return IndexState(
            cards=card["n"] if card else 0,
            card_versions=sorted(card["versions"]) if card else [],
            claims=claim["n"] if claim else 0,
            claim_versions=sorted(claim["versions"]) if claim else [],
            embed_models=sorted({m for r in rows for m in r["models"]}),
            online=sorted(r["name"] for r in indexes),
        )

    def _refs(self, search: str, **params: object) -> list[str]:
        rows, _, _ = self._driver.execute_query(
            f"{search} RETURN node.ref AS ref ORDER BY score DESC, ref", **params
        )
        return [r["ref"] for r in rows]

    def _claims(self, search: str, claim_filter: ClaimFilter, k: int, **params: object) -> list[ClaimHit]:
        fetch = k * _OVERFETCH if claim_filter.active else k
        rows, _, _ = self._driver.execute_query(
            f"{search} {_CLAIM_HITS}",
            k=k, fetch=fetch, truth=claim_filter.triple_truth, modality=claim_filter.modality, **params,
        )  # fmt: skip
        return [ClaimHit(**r.data()) for r in rows]
