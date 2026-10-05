"""Attachment: which records and individuals each claim is about, written as
`(thing)-[:HAS_OBSERVATION {name, how, evidence}]->(Observation)` (R76, layered-model Step 6).

Role in the pipeline: `kg attach`, after `kg resolve`: two of its routes read the identity edges, which
resolve writes. Readers find a claim's things through these edges: the query stage (find_claims, the
traversal patterns), path truth (validation/paths.py) and the fact reader.
Why: a claim's grammatical subject ("mechanical seal") and the thing it is about (pump HP40-1183) are two
things. Until R76 the second came only from the document's ABOUT link, which a file name had to give, so
the documents of the generality corpus hung on nothing (0 of 32 in R75).
Design: code decides from the text, never the model (the extractor is not changed). Each attachment names
the route (`how`) that justified it and the evidence code found:
  1. `key_in_sentence`: a name of a record or an individual that a mention of the claim's own document
     refers to (the mention's wording, the record's key, the canonical name) stands in a sentence of the
     claim's quote that names one of the claim's ends: "the mechanical seal of pump HP40-1183 failed" (a
     quote may span several lines of a note, and a name on another line says nothing about the claim);
  2. `part_of`: the claim's subject is a part of a thing, by another claim of the same document of a fact
     type the schema marks `part_of` ("mechanical seal COMPONENT_OF HP40-1183");
  3. `section`: the claim's chunk is ABOUT a record, because its heading names the record's key (R67);
  4. `document`: the claim's document is ABOUT the thing: by its file name or its record (kg link), or,
     for a document neither gives a thing, by its text: the record whose names its sentences write
     clearly more often than any other's (written here as `ABOUT {how: 'text'}`).
Precedence (the user's decision, 2026-10-05): the most specific route wins **per kind of thing** (a record's
label, an individual's type): a quote naming one staff member replaces the staff member the whole document
is about, but a complaint's section record and the vehicle of the document are two kinds, so a held-out
claim keeps both (R67's decision). Several things of one kind hold a claim when one route finds several:
a quote naming two pumps attaches to both.
Concepts never hold claims: a kind is shared by many documents, and attaching through it is what made one
product's defects reachable from another in R62.
Not here: ABOUT links from file names, records and headings (linking.py), what a mention refers to
(identity.py), scoring the attachments (validation/paths.py).
"""

from collections import defaultdict
from typing import Literal

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import document_of
from ..core.text import claim_sentences, contains_words, split_sentences, squash
from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from .linking import read_record_keys

AttachRoute = Literal["key_in_sentence", "part_of", "section", "document"]
# Most specific first: what the claim's own sentence says beats what its document is about.
ROUTES: tuple[AttachRoute, ...] = ("key_in_sentence", "part_of", "section", "document")

# The `how` of an ABOUT link this stage writes. The identity stage ignores such links when it reads a
# document's scope (mentions.py): the links come from identity decisions, so reading them back would make
# a rerun of `kg resolve` depend on whether `kg attach` ran before it.
TEXT_ABOUT = "text"

# A name shorter than this, once squashed, is too likely to stand in a quote by accident even as whole
# words ("GE", "A1"); three keeps short real names such as "Kia".
_MIN_NAME_CHARS = 3

# A document is about a record by its text when the record is named in at least this many of its sentences
# and in at least `_DOMINANCE` times as many as the next record: one sentence is a passing mention, and a
# near tie means the document is about several things, where the claims' own routes must decide.
_MIN_DOMINANT_SENTENCES = 2
_DOMINANCE = 2


class Thing(BaseModel):
    """A record or an individual a claim can hang on."""

    element_id: str
    name: str  # display name, carried onto HAS_OBSERVATION
    kind: str  # what precedence compares: "record:<label>" or "individual:<type>"


class Particular(BaseModel):
    """A mention of one document that refers to a record or an individual, with the names it is known by
    in that document."""

    mention: str
    doc_id: str
    names: list[str]  # the mention's own name, then the record's key and the canonical name
    thing: Thing


class ClaimSource(BaseModel):
    """One observation, reduced to what the routes read."""

    id: str
    doc_id: str
    chunk_id: str
    quote: str
    subject_name: str = ""  # the claim's own wording of its ends: which sentences of the quote are its own
    object_name: str = ""
    subject: str | None  # mention ids; None for an observation missing an end
    object: str | None
    part_of: bool  # its fact type states that the subject is one of the pieces the object is made of


class Attachment(BaseModel):
    """One HAS_OBSERVATION edge: the claim, its thing, the route and what showed it."""

    observation: str
    thing: Thing
    how: AttachRoute
    # key_in_sentence: the name as it stands in the quote; part_of: the part-of claim's quote; section: the
    # chunk id; document: the document id
    evidence: str


class AttachReport(BaseModel):
    """Counts of one attach run. Metric names in MLflow; keep them stable."""

    observations_total: int
    observations_attached: int  # the name of the link stage's metric before R76, same meaning
    attachments: int  # HAS_OBSERVATION edges
    by_route: dict[str, int]  # route -> edges; logged as `attached_<route>`
    documents_about_by_text: int  # documents this stage made ABOUT a record from their text
    documents_about_nothing: int  # documents left with no thing: their claims hang only on named things


def named_in_quote(claim: ClaimSource, particulars: list[Particular]) -> list[Attachment]:
    """Route 1: the things of the claim's own document whose name stands, as whole words, in a sentence of
    its quote that names one of its ends (`claim_sentences`)."""
    sentences = claim_sentences(claim.quote, [claim.subject_name, claim.object_name])
    found: dict[str, Attachment] = {}
    for p in particulars:
        name = next(
            (n for n in p.names if _long_enough(n) and any(contains_words(s, n) for s in sentences)), None
        )
        if name is not None and p.thing.element_id not in found:
            found[p.thing.element_id] = Attachment(
                observation=claim.id, thing=p.thing, how="key_in_sentence", evidence=name
            )
    return list(found.values())


def part_of_wholes(
    claim: ClaimSource, part_claims: list[ClaimSource], particular_of: dict[str, Particular]
) -> list[Attachment]:
    """Route 2: the things the claim's subject is a part of, by a part-of claim of the same document with
    the same subject mention (one mention per type, name and document, so "the same part")."""
    found: dict[str, Attachment] = {}
    for part in part_claims:
        if part.doc_id != claim.doc_id or claim.subject is None or part.subject != claim.subject:
            continue
        whole = particular_of.get(part.object or "")
        if whole is not None and whole.thing.element_id not in found:
            found[whole.thing.element_id] = Attachment(
                observation=claim.id, thing=whole.thing, how="part_of", evidence=part.quote
            )
    return list(found.values())


def choose(candidates: list[Attachment]) -> list[Attachment]:
    """Precedence: per kind of thing, only the most specific route that found a thing of that kind keeps
    the claim; a thing found by several routes keeps the most specific one. Order: by route, then as found.
    """
    rank = {how: i for i, how in enumerate(ROUTES)}
    best: dict[str, int] = {}
    for a in candidates:
        best[a.thing.kind] = min(best.get(a.thing.kind, len(ROUTES)), rank[a.how])
    kept: dict[str, Attachment] = {}
    for a in sorted(candidates, key=lambda a: rank[a.how]):  # stable: equal routes keep their order
        if rank[a.how] == best[a.thing.kind]:
            kept.setdefault(a.thing.element_id, a)
    return list(kept.values())


def attach(
    claims: list[ClaimSource],
    particulars: list[Particular],
    sections: dict[str, list[Thing]],
    documents: dict[str, list[Thing]],
) -> list[Attachment]:
    """Every claim's attachments by the four routes and the precedence. Pure.

    Inputs: the claims; the particulars (mentions referring to a record or an individual); the things each
    chunk (`sections`, by chunk id) and each document (`documents`, by document id) is ABOUT.
    """
    by_doc = _by_document(particulars)
    particular_of = {p.mention: p for p in particulars}
    part_claims = [c for c in claims if c.part_of]
    out: list[Attachment] = []
    for claim in claims:
        candidates = [
            *named_in_quote(claim, by_doc[claim.doc_id]),
            *part_of_wholes(claim, part_claims, particular_of),
            *(Attachment(observation=claim.id, thing=t, how="section", evidence=claim.chunk_id)
              for t in sections.get(claim.chunk_id, [])),
            *(Attachment(observation=claim.id, thing=t, how="document", evidence=claim.doc_id)
              for t in documents.get(claim.doc_id, [])),
        ]  # fmt: skip
        out += choose(candidates)
    return out


def dominant_record(sentences: list[str], particulars: list[Particular]) -> tuple[Thing, str] | None:
    """The record a document's text is about, with the evidence, or None.

    Counts, per record that a mention of the document refers to, the sentences naming it by any of its
    names; the top record must reach `_MIN_DOMINANT_SENTENCES` and `_DOMINANCE` times the runner-up.
    Individuals are not candidates: the task's rule reads the records the mentions link to.
    """
    names: dict[str, set[str]] = defaultdict(set)
    things: dict[str, Thing] = {}
    for p in particulars:
        if p.thing.kind.startswith("record:"):
            names[p.thing.element_id] |= {n for n in p.names if _long_enough(n)}
            things[p.thing.element_id] = p.thing
    counts = sorted(
        ((sum(any(contains_words(s, n) for n in names[e]) for s in sentences), e) for e in things),
        key=lambda c: (-c[0], things[c[1]].name),
    )
    if not counts:
        return None
    top, element_id = counts[0]
    runner_up = counts[1][0] if len(counts) > 1 else 0
    if top < _MIN_DOMINANT_SENTENCES or top < _DOMINANCE * runner_up:
        return None
    return things[element_id], f"named in {top} of {len(sentences)} sentences; the next record in {runner_up}"


def attach_claims(driver: Driver, plan: ConstructionPlan | None, schema: TextSchema | None) -> AttachReport:
    """Recompute the text ABOUT links and every HAS_OBSERVATION edge. Idempotent: both are derived data,
    deleted and written again, so a rerun after a new resolve keeps nothing the graph no longer supports.
    Without a plan there are no records, so only individuals can hold claims."""
    particulars = read_particulars(driver, plan)
    by_text = _link_documents_by_text(driver, _by_document(particulars))
    documents = _read_links(driver, "MATCH (s:Document)-[a:ABOUT]->(n) RETURN s.doc_id AS src, ")
    sections = _read_links(driver, "MATCH (s:Chunk)-[a:ABOUT]->(n) RETURN s.chunk_id AS src, ")
    claims = read_claims(driver, schema)
    attachments = attach(claims, particulars, sections, documents)
    _write_attachments(driver, attachments)
    total_documents = driver.execute_query("MATCH (d:Document) RETURN count(d) AS n")[0][0]["n"]
    return AttachReport(
        observations_total=len(claims),
        observations_attached=len({a.observation for a in attachments}),
        attachments=len(attachments),
        by_route={how: sum(a.how == how for a in attachments) for how in ROUTES},
        documents_about_by_text=by_text,
        documents_about_nothing=total_documents - len(documents),
    )


def _link_documents_by_text(driver: Driver, by_doc: dict[str, list[Particular]]) -> int:
    """Write `ABOUT {how: 'text', evidence}` for every document that `kg link` left about nothing and whose
    sentences name one record clearly most (`dominant_record`). Returns how many were written; the earlier
    run's text links are deleted first."""
    driver.execute_query("MATCH (:Document)-[l:ABOUT {how: $how}]->() DELETE l", how=TEXT_ABOUT)
    linked = _read_links(driver, "MATCH (s:Document)-[a:ABOUT]->(n) RETURN s.doc_id AS src, ")
    texts = _read_document_sentences(driver)
    rows = [
        {"src": doc, "dst": thing.element_id, "name": thing.name, "evidence": evidence}
        for doc in sorted(texts)
        if doc not in linked and (found := dominant_record(texts[doc], by_doc.get(doc, [])))
        for thing, evidence in [found]
    ]
    if rows:
        driver.execute_query(
            "UNWIND $rows AS r MATCH (d:Document {doc_id: r.src}) MATCH (n) WHERE elementId(n) = r.dst "
            "MERGE (d)-[l:ABOUT]->(n) SET l.name = r.name, l.how = $how, l.evidence = r.evidence",
            rows=rows,
            how=TEXT_ABOUT,
        )
    return len(rows)


def read_particulars(driver: Driver, plan: ConstructionPlan | None) -> list[Particular]:
    """Every mention that refers to a record or an individual, with its names. Ordered by mention id."""
    keys = {r.element_id: r.key for r in read_record_keys(driver, plan)} if plan else {}
    records, _, _ = driver.execute_query(
        "MATCH (m:Mention)-[r:REFERS_TO]->(t) WHERE r.kind IN ['record', 'individual'] "
        "RETURN m.id AS mention, m.name AS said, m.doc_id AS doc_id, elementId(t) AS element_id, "
        # a record's kind is its label; an individual's its type, since all individuals share one label
        "r.name AS name, "
        "r.kind + ':' + CASE r.kind WHEN 'record' THEN head(labels(t)) ELSE t.type END AS kind "
        "ORDER BY mention"
    )
    out = []
    for r in records:
        names = [r["said"], keys.get(r["element_id"], ""), r["name"]]
        out.append(
            Particular(
                mention=r["mention"],
                doc_id=r["doc_id"],
                names=list(dict.fromkeys(n for n in names if n)),  # deduplicated, order kept
                thing=Thing(element_id=r["element_id"], name=r["name"], kind=r["kind"]),
            )
        )
    return out


def read_claims(driver: Driver, schema: TextSchema | None) -> list[ClaimSource]:
    """Every observation with its chunk, document, quote and the mentions of its two ends."""
    records, _, _ = driver.execute_query(
        "MATCH (o:Observation)-[:FROM]->(c:Chunk) "
        "OPTIONAL MATCH (c)-[:PART_OF]->(d:Document) "
        "OPTIONAL MATCH (o)-[:SUBJECT]->(s:Mention) OPTIONAL MATCH (o)-[:OBJECT]->(t:Mention) "
        "RETURN o.id AS id, c.chunk_id AS chunk_id, d.doc_id AS doc_id, coalesce(o.evidence, '') AS quote, "
        "coalesce(o.subject_name, '') AS subject_name, coalesce(o.object_name, '') AS object_name, "
        "o.predicate AS predicate, s.id AS subject, s.type AS subject_type, t.id AS object, "
        "t.type AS object_type ORDER BY id"
    )
    return [
        ClaimSource(
            id=r["id"],
            # a hand-built chunk may have no document node; its id still names the document
            doc_id=r["doc_id"] or document_of(r["chunk_id"]),
            chunk_id=r["chunk_id"],
            quote=r["quote"],
            subject_name=r["subject_name"],
            object_name=r["object_name"],
            subject=r["subject"],
            object=r["object"],
            part_of=schema is not None
            and schema.is_part_of(r["subject_type"], r["predicate"], r["object_type"]),
        )
        for r in records
    ]


def _read_links(driver: Driver, match: str) -> dict[str, list[Thing]]:
    """The things each source (document or chunk) is ABOUT; `match` binds the source id as `src`, the
    link as `a` and the thing as `n`. ABOUT links point at records only."""
    records, _, _ = driver.execute_query(
        match + "elementId(n) AS element_id, coalesce(a.name, '') AS name, head(labels(n)) AS label "
        "ORDER BY src, element_id"
    )
    out: dict[str, list[Thing]] = defaultdict(list)
    for r in records:
        out[r["src"]].append(Thing(element_id=r["element_id"], name=r["name"], kind=f"record:{r['label']}"))
    return dict(out)


def _read_document_sentences(driver: Driver) -> dict[str, list[str]]:
    """The sentences of every document's chunks, in chunk order."""
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[:PART_OF]->(d:Document) "
        "RETURN d.doc_id AS doc_id, coalesce(c.text, '') AS text ORDER BY doc_id, c.index"
    )
    out: dict[str, list[str]] = defaultdict(list)
    for r in records:
        out[r["doc_id"]] += split_sentences(r["text"])
    return dict(out)


def _write_attachments(driver: Driver, attachments: list[Attachment]) -> None:
    """Replace every HAS_OBSERVATION edge with `attachments`."""
    driver.execute_query("MATCH ()-[h:HAS_OBSERVATION]->(:Observation) DELETE h")
    if attachments:
        driver.execute_query(
            "UNWIND $rows AS r MATCH (o:Observation {id: r.observation}) "
            "MATCH (n) WHERE elementId(n) = r.thing "
            "MERGE (n)-[h:HAS_OBSERVATION]->(o) SET h.name = r.name, h.how = r.how, h.evidence = r.evidence",
            rows=[
                {
                    "observation": a.observation,
                    "thing": a.thing.element_id,
                    "name": a.thing.name,
                    "how": a.how,
                    "evidence": a.evidence,
                }
                for a in attachments
            ],
        )


def _by_document(particulars: list[Particular]) -> dict[str, list[Particular]]:
    by_doc: dict[str, list[Particular]] = defaultdict(list)
    for p in particulars:
        by_doc[p.doc_id].append(p)
    return by_doc


def _long_enough(name: str) -> bool:
    return len(squash(name)) >= _MIN_NAME_CHARS
