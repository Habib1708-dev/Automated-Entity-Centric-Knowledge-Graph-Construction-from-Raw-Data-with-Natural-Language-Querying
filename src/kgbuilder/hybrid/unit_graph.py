"""Write the retrieval units into Neo4j with their vectors, and the indexes over them (R119).

Role in the pipeline: `kg index --cards <representation>` (pipeline/index_stages.py) calls it after reading
every node's evidence and rendering its card; R120's retrievers search what it writes.
Design: the index layer is additive (graph/index_layer.py): every unit is MERGEd by its id, points at what
it stands for (`CARD_OF` a record, individual or concept; `SENTENCE_OF` an observation), and nothing in Part
1 is touched. A unit whose text, version and embedding model are all unchanged keeps its vector and is not
embedded again, so a re-index costs nothing and adding a second representation later re-embeds none of the
first's cards or of the claim sentences they share. Stale units are removed per kind: the cards of the
representation written now (never another's) and the claim sentences whose observation is gone. Element ids
are used only inside one run, to find the targets the refs name.
Not here: the evidence and the texts (unit_sources.py, cards.py, claims.py), searching (R120).
"""

import hashlib
from collections.abc import Mapping

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..core.errors import MissingInputError
from ..graph.index_layer import (
    ANALYZER,
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
from ..llm.base import Embedder
from .claims import CLAIM_VERSION, ClaimSentence
from .evidence import RenderedCard

_BATCH = 500  # units per write: each row carries a text and a vector


class UnitRow(BaseModel):
    """One unit as it is written: its id and text, what versions the text, and its target."""

    id: str  # "<representation>:<ref>" for a card, "claim:<observation id>" for a claim sentence
    text: str
    text_hash: str
    version: str
    target: str  # the element id of the node a card stands for, or the observation id of a claim sentence
    ref: str | None = None  # a card's node ref; a claim sentence has none
    evidence_hash: str | None = None  # a card's evidence hash
    embedding: list[float] | None = None  # set only for a unit embedded in this run


class UnitWrite(BaseModel):
    """What one write did."""

    written: int  # units new or changed: embedded now
    reused: int  # units unchanged: their vector kept
    stale_removed: int
    dimensions: int | None  # the vectors' length; None when the layer holds no vector at all


def card_rows(
    representation: str, version: str, cards: list[RenderedCard], targets: Mapping[str, str]
) -> list[UnitRow]:
    """The rows of one representation's cards. Raises `MissingInputError` for a card whose node the graph no
    longer holds (the graph changed while the cards were read)."""
    rows = []
    for card in cards:
        if card.ref not in targets:
            raise MissingInputError(f"no node {card.ref} in the graph for its card; run kg index again")
        rows.append(
            UnitRow(
                id=f"{representation}:{card.ref}",
                text=card.text,
                text_hash=_hash(card.text),
                version=version,
                target=targets[card.ref],
                ref=card.ref,
                evidence_hash=card.evidence_hash,
            )
        )
    return rows


def claim_rows(claims: list[ClaimSentence]) -> list[UnitRow]:
    """The rows of the claim sentences."""
    return [
        UnitRow(id=f"claim:{c.id}", text=c.text, text_hash=_hash(c.text), version=CLAIM_VERSION, target=c.id)
        for c in claims
    ]


def write_units(
    driver: Driver,
    representation: str,
    cards: list[UnitRow],
    claims: list[UnitRow],
    embedder: Embedder,
    embed_model: str,
) -> UnitWrite:
    """MERGE the cards of `representation` and the claim sentences, embedding only the new and the changed
    ones, link each to its target, and remove the stale cards of this representation and the stale claim
    sentences. Calls `embedder` once for every unit that needs a vector (it batches as it must)."""
    driver.execute_query(f"CREATE CONSTRAINT IF NOT EXISTS FOR (u:{RETRIEVAL_UNIT}) REQUIRE u.id IS UNIQUE")
    label = card_label(representation)
    kept = _kept(driver, label, embed_model)
    pending = [row for row in cards + claims if kept.get(row.id) != (row.text_hash, row.version)]
    vectors = embedder.embed([row.text for row in pending]) if pending else []
    for row, vector in zip(pending, vectors, strict=True):
        row.embedding = vector
    on_card = "MATCH (n) WHERE elementId(n) = r.target"
    _write(driver, cards, f"{NODE_CARD}:{cypher_ident(label)}", on_card, CARD_OF, representation, embed_model)
    on_claim = "MATCH (n:Observation {id: r.target})"
    _write(driver, claims, CLAIM_SENTENCE, on_claim, SENTENCE_OF, None, embed_model)
    stale = 0
    for unit_label, rows in ((cypher_ident(label), cards), (CLAIM_SENTENCE, claims)):
        removed, _, _ = driver.execute_query(
            f"MATCH (u:{unit_label}) WHERE NOT u.id IN $ids DETACH DELETE u RETURN count(u) AS n",
            ids=[r.id for r in rows],
        )
        stale += removed[0]["n"]
    return UnitWrite(
        written=len(pending),
        reused=len(cards) + len(claims) - len(pending),
        stale_removed=stale,
        dimensions=len(vectors[0]) if vectors else _stored_dimensions(driver),
    )


def _kept(driver: Driver, label: str, embed_model: str) -> dict[str, tuple[str, str]]:
    """Unit id -> (text hash, version) of every stored card of this representation and claim sentence that
    has a vector made by `embed_model`: these keep their vector when the text and version are the same."""
    rows, _, _ = driver.execute_query(
        f"MATCH (u:{RETRIEVAL_UNIT}) WHERE (u:{cypher_ident(label)} OR u:{CLAIM_SENTENCE}) "
        "AND u.embedding IS NOT NULL AND u.embed_model = $model "
        "RETURN u.id AS id, u.text_hash AS h, u.version AS v",
        model=embed_model,
    )
    return {r["id"]: (r["h"], r["v"]) for r in rows}


def _write(
    driver: Driver,
    rows: list[UnitRow],
    labels: str,
    target: str,
    edge: str,
    representation: str | None,
    model: str,
) -> None:
    """MERGE the units with their labels and properties, set a vector only where one was made now, and point
    each at its target, dropping an edge to a node it no longer stands for."""
    for start in range(0, len(rows), _BATCH):
        driver.execute_query(
            f"UNWIND $rows AS r MERGE (u:{RETRIEVAL_UNIT} {{id: r.id}}) SET u:{labels}, u.text = r.text, "
            "u.text_hash = r.text_hash, u.version = r.version, u.ref = r.ref, "
            "u.evidence_hash = r.evidence_hash, u.representation = $representation "
            # FOREACH-as-IF: a unit not embedded now keeps the vector (and the model that made it) it has
            "FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END | "
            "  SET u.embedding = r.embedding, u.embed_model = $model) "
            f"WITH u, r {target} "
            f"OPTIONAL MATCH (u)-[old:{edge}]->(m) WHERE m <> n DELETE old "
            f"MERGE (u)-[:{edge}]->(n)",
            rows=[row.model_dump() for row in rows[start : start + _BATCH]],
            representation=representation,
            model=model,
        )


def _stored_dimensions(driver: Driver) -> int | None:
    """The length of a stored unit vector, when nothing was embedded in this run."""
    rows, _, _ = driver.execute_query(
        f"MATCH (u:{RETRIEVAL_UNIT}) WHERE u.embedding IS NOT NULL RETURN size(u.embedding) AS d LIMIT 1"
    )
    return rows[0]["d"] if rows else None


def ensure_indexes(driver: Driver, representation: str, dimensions: int) -> list[str]:
    """Create the layer's indexes for `representation` that are missing, and recreate any of them whose
    label, dimensions or analyzer differ (an index Neo4j keeps from an earlier model or setting would else
    silently refuse or misrank the vectors). Waits until every index is online. Returns the recreated
    names."""
    label = card_label(representation)
    names = card_indexes(representation)
    vector = {"type": "VECTOR", "property": "embedding", "config": {"vector.dimensions": dimensions}}
    fulltext = {"type": "FULLTEXT", "property": "text", "config": {"fulltext.analyzer": ANALYZER}}
    wanted = {
        names.vector: (label, vector),
        names.fulltext: (label, fulltext),
        CLAIM_VECTOR_INDEX: (CLAIM_SENTENCE, vector),
        CLAIM_FULLTEXT_INDEX: (CLAIM_SENTENCE, fulltext),
        CHUNK_FULLTEXT_INDEX: ("Chunk", fulltext),
    }
    rows, _, _ = driver.execute_query(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, options WHERE name IN $names "
        "RETURN name, type, labelsOrTypes AS labels, options.indexConfig AS config",
        names=list(wanted),
    )
    existing = {r["name"]: r for r in rows}
    recreated = []
    for name, (on, spec) in wanted.items():
        found = existing.get(name)
        if found is not None and not _same(found, on, spec):
            driver.execute_query(f"DROP INDEX {cypher_ident(name)} IF EXISTS")
            recreated.append(name)
        driver.execute_query(_create(name, on, spec))
    driver.execute_query("CALL db.awaitIndexes(300)")
    return recreated


def _same(found: Mapping, label: str, spec: Mapping) -> bool:
    """Whether a stored index is the one wanted: its type, its label and the wanted config values."""
    config = found["config"] or {}
    return (
        found["type"] == spec["type"]
        and list(found["labels"] or []) == [label]
        and all(config.get(k) == v for k, v in spec["config"].items())
    )


def _create(name: str, label: str, spec: Mapping) -> str:
    """The DDL of one index. Index names, labels and options cannot be parameters: the names and labels are
    escaped, the options are this module's own values (an int computed from a vector, the analyzer)."""
    name, on = cypher_ident(name), cypher_ident(label)
    if spec["type"] == "VECTOR":
        dims = int(spec["config"]["vector.dimensions"])
        return (
            f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (u:{on}) ON (u.embedding) OPTIONS "
            f"{{indexConfig: {{`vector.dimensions`: {dims}, `vector.similarity_function`: 'cosine'}}}}"
        )
    return (
        f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (u:{on}) ON EACH [u.text] "
        f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{ANALYZER}'}}}}"
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
