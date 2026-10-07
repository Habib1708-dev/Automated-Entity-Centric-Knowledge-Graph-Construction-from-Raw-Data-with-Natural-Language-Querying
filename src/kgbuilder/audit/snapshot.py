"""The graph of a finished build, rebuilt offline as data: the graph audit's snapshot (R87).

Role in the pipeline: after a build; read by `audit/fidelity.py` (is it the graph the build wrote?) and
`audit/checks.py` (is that graph right?). The Neo4j graph of the build is gone, and most of its edges were
never saved, so the snapshot recomputes them from what was saved, in the build's stage order:
  ingest (`audit/inputs.py`) -> subject graph (`text/subject_graph.collect_rows` over triples.jsonl) ->
  link: document and section ABOUT links (`resolution/linking`), derived claims
  (`resolution/derivation.derive_rows`) -> mention pass (R101, when the build has `mentions.jsonl`:
  `mention_pass.pass_rows` over its accepted findings, or over the findings the caller gives in their place,
  R105) -> identity: resolve.json's assignments are the
  REFERS_TO edges -> attach: the text ABOUT links (`attachment.dominant_record`) and every HAS_OBSERVATION
  (`attachment.attach`).
Design: the build's own pure functions do the work, so a difference from the build is a finding, not a
re-implementation drift; only the Cypher reads around them are restated here, each next to the query it
restates. Things are named by record ref or canonical id, never by display name (ten "Screws" records,
two "Utrecht" individuals).
Must not: write anything but the snapshot, call an LLM, or decide a link the build would not have made.
"""

import json
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from ..core.identity import document_of
from ..core.text import split_sentences
from ..resolution.attachment import TEXT_ABOUT, ClaimSource, Particular, Thing, attach, dominant_record
from ..resolution.derivation import Candidate, DerivationSource, derive_rows
from ..resolution.identity_graph import Assignment
from ..resolution.linking import DomainNode, RecordKey, match_chunk_records, match_document
from ..structured.plan import ConstructionPlan
from ..structured.profiler import DataProfile
from ..text.chunking import Chunk
from ..text.extraction import Triple
from ..text.mention_pass import PASS_FILE, PassFinding, pass_rows, read_findings
from ..text.schema import MentionClass, TextSchema
from ..text.subject_graph import MentionRow, ObservationRow, collect_rows
from .inputs import Record, Relation, read_corpus, read_records, read_relations


class SnapshotMention(MentionRow):
    """A mention with the chunks that MENTION it."""

    chunks: list[str]
    derived: bool  # written by derivation, named after a plan node, not by the extractor
    found_by_pass: bool = False  # written by the mention pass (R101), named by the text, not by a claim
    # the class the pass stated for it (R104); None for every other mention and for a pre-R104 pass file
    stated_class: MentionClass | None = None


class SnapshotClaim(ObservationRow):
    """An observation; `derived` claims come from code (derivation), the others from the extractor."""

    derived: bool

    @property
    def doc_id(self) -> str:
        return document_of(self.chunk_id)


class AboutLink(BaseModel):
    """A document or a chunk ABOUT a record."""

    source: str  # doc id (document links) or chunk id (section links)
    thing: str  # record ref
    name: str  # the name the link carries
    how: str  # "file" (name in the file name), "record" (a record's own document), "heading" (R67), "text"
    evidence: str = ""


class SnapshotAttachment(BaseModel):
    """One HAS_OBSERVATION edge."""

    observation: str
    thing: str  # record ref or individual id
    kind: str  # "record:<label>" or "individual:<type>"
    name: str
    how: str
    evidence: str


class GraphSnapshot(BaseModel):
    """The rebuilt graph of one build."""

    source: str  # the build's out/ folder
    records: list[Record]
    relations: list[Relation]
    chunks: list[Chunk]
    mentions: list[SnapshotMention]
    claims: list[SnapshotClaim]
    references: list[Assignment]  # REFERS_TO, one per mention
    documents_about: list[AboutLink]
    sections_about: list[AboutLink]
    attachments: list[SnapshotAttachment]
    # MENTIONS edges the subject-graph writer wrote, before derivation added its own
    extracted_mentions_edges: int
    derived_object_collisions: int  # derived claims whose id an extracted claim already had (MERGE: one node)
    # the mention pass's MENTIONS edges, and those of them that reached a mention a claim already had (R101)
    pass_mentions_edges: int = 0
    pass_reused: int = 0


def build_snapshot(
    out_dir: Path, data_dir: Path, chunking: tuple[int, int, int], findings: list[PassFinding] | None = None
) -> GraphSnapshot:
    """Rebuild the graph of the build in `out_dir` from its saved files and the dataset in `data_dir`.

    `findings` replaces the build's own mention pass file (R105): an empty list gives the graph as the pass
    found it, another pass's findings the graph that pass would have written. Identity is still the build's
    resolve.json, so a mention it never saw refers to nothing.

    Failure modes: a missing file of the build raises (FileNotFoundError); a file of another shape raises
    pydantic's ValidationError. Both mean the folder is not a finished build.
    """
    plan = ConstructionPlan.model_validate_json(_read(out_dir / "plan.json"))
    schema = TextSchema.model_validate_json(_read(out_dir / "text_schema.json"))
    profile = DataProfile.model_validate_json(_read(out_dir / "profile.json"))
    staged = out_dir / "staging"
    records = read_records(staged, plan)
    corpus = read_corpus(data_dir, staged, plan, profile, chunking)
    triples = [
        Triple.model_validate_json(line) for line in _read(out_dir / "triples.jsonl").splitlines() if line
    ]
    mentions, mentioned_in, rows = collect_rows(triples)
    extracted_edges = len(mentioned_in)
    claims = {r.id: SnapshotClaim(**r.model_dump(), derived=False) for r in rows}
    derived_mentions: set[str] = set()

    documents_about, sections_about = _link(corpus.documents, corpus.chunks, records)
    by_doc = defaultdict(list)
    for link in documents_about:
        by_doc[link.source].append(link.thing)
    chunks = {c.chunk_id: c for c in corpus.chunks}
    collisions = 0
    names_by_node = {r.id: r.name for r in records if r.name is not None}
    for fact_type in schema.derived():
        sources = _derivation_sources(fact_type.subject_type, mentions, mentioned_in, chunks, by_doc)
        candidates = _candidates(fact_type.object_type, mentions, mentioned_in)
        derived = derive_rows(fact_type, sources, candidates, names_by_node)
        for m in derived.mentions:  # MERGE: a mention the extractor wrote stays as it was
            if m.id not in mentions:
                mentions[m.id] = m
                derived_mentions.add(m.id)
        mentioned_in |= derived.mentioned_in
        for o in derived.observations:
            collisions += o.id in claims
            claims.setdefault(o.id, SnapshotClaim(**o.model_dump(), derived=True))

    # the mention pass ran after link: its rows come after derivation's, and derivation never saw them
    passed: dict[str, MentionClass | None] = {}  # pass mention id -> the class it was stated with
    pass_edges = reused = 0
    if findings is None and (out_dir / PASS_FILE).exists():
        findings = read_findings(out_dir / PASS_FILE)
    if findings:
        found = pass_rows(findings, list(mentions.values()), mentioned_in)
        for m in found.mentions:
            mentions[m.id] = m
            passed[m.id] = found.classes.get(m.id)
        mentioned_in |= set(found.mentioned_in)
        pass_edges, reused = len(found.mentioned_in), found.reused

    resolved = json.loads(_read(out_dir / "resolve.json"))
    references = [Assignment.model_validate(a) for a in resolved["assignments"]]
    snapshot_mentions = _mentions(mentions, mentioned_in, derived_mentions, passed)
    particulars = _particulars(references, snapshot_mentions, records)
    documents_about += _text_links(corpus.documents, corpus.chunks, documents_about, particulars)
    attachments = _attach(
        list(claims.values()),
        snapshot_mentions,
        particulars,
        documents_about,
        sections_about,
        records,
        schema,
    )
    return GraphSnapshot(
        source=out_dir.as_posix(),
        records=records,
        relations=read_relations(staged, plan, records),
        chunks=corpus.chunks,
        mentions=snapshot_mentions,
        claims=sorted(claims.values(), key=lambda c: c.id),
        references=references,
        documents_about=documents_about,
        sections_about=sections_about,
        attachments=attachments,
        extracted_mentions_edges=extracted_edges,
        derived_object_collisions=collisions,
        pass_mentions_edges=pass_edges,
        pass_reused=reused,
    )


def _link(documents, chunks: list[Chunk], records: list[Record]) -> tuple[list[AboutLink], list[AboutLink]]:
    """`linking.link_graphs`: a file document ABOUT the node its file name names, a record's document ABOUT
    its record, a chunk ABOUT each record its headings name by key."""
    by_ref = {r.id: r for r in records}
    domain = [DomainNode(element_id=r.id, label=r.label, name=r.name) for r in records if r.name is not None]
    docs: list[AboutLink] = []
    for d in documents:
        if d.record is None:
            node = match_document(d.title, domain)
            if node is not None:
                docs.append(AboutLink(source=d.doc_id, thing=node.element_id, name=node.name, how="file"))
        elif (ref := f"{d.record.label}:{d.record.key}") in by_ref:
            docs.append(AboutLink(source=d.doc_id, thing=ref, name=d.title, how="record"))
    # coalesce(name, key) as `read_record_keys` reads it
    keys = [RecordKey(element_id=r.id, key=r.key, name=r.name or r.key) for r in records]
    sections = [
        AboutLink(source=c.chunk_id, thing=k.element_id, name=k.name, how="heading", evidence=k.key)
        for c in chunks
        for k in match_chunk_records(c.text, keys)
    ]
    return docs, sections


def _derivation_sources(
    subject_type: str,
    mentions: dict[str, MentionRow],
    mentioned_in: set[tuple[str, str]],
    chunks: dict[str, Chunk],
    about: dict[str, list[str]],
) -> list[DerivationSource]:
    """derivation.py's read: MATCH (m:Mention {type})<-[:MENTIONS]-(c)-[:PART_OF]->(d)-[:ABOUT]->(n)
    ORDER BY id, chunk_id. Only the link stage's ABOUT links exist when derivation runs."""
    out = [
        DerivationSource(
            id=m, name=mentions[m].name, chunk_id=c, text=chunks[c].text, doc_id=document_of(c), node=node
        )
        for c, m in mentioned_in
        if mentions[m].type == subject_type and c in chunks
        for node in about.get(document_of(c), [])
    ]
    return sorted(out, key=lambda s: (s.id, s.chunk_id, s.node))


def _candidates(
    object_type: str, mentions: dict[str, MentionRow], mentioned_in: set[tuple[str, str]]
) -> dict[str, list[Candidate]]:
    """derivation.py's `_candidates`: the object type's mentions per document, with their chunk counts."""
    counts: dict[str, int] = defaultdict(int)
    for _, m in mentioned_in:
        counts[m] += 1
    out: dict[str, list[Candidate]] = defaultdict(list)
    for m in sorted(mentions.values(), key=lambda m: (m.doc_id, m.id)):
        if m.type == object_type:
            out[m.doc_id].append(Candidate(id=m.id, name=m.name, chunks=counts[m.id]))
    return dict(out)


def _mentions(
    mentions: dict[str, MentionRow],
    mentioned_in: set[tuple[str, str]],
    derived: set[str],
    passed: dict[str, MentionClass | None],
) -> list[SnapshotMention]:
    chunks_of: dict[str, list[str]] = defaultdict(list)
    for c, m in sorted(mentioned_in):
        chunks_of[m].append(c)
    return [
        SnapshotMention(
            **m.model_dump(),
            chunks=chunks_of[m.id],
            derived=m.id in derived,
            found_by_pass=m.id in passed,
            stated_class=passed.get(m.id),
        )
        for m in sorted(mentions.values(), key=lambda m: m.id)
    ]


def _particulars(
    references: list[Assignment], mentions: list[SnapshotMention], records: list[Record]
) -> list[Particular]:
    """`attachment.read_particulars`: every mention referring to a record or an individual, with its names
    (the mention's, the record's key, the canonical name), ordered by mention id. An individual's kind is
    the type of the first assignment that created it (`write_identity`: ON CREATE SET n.type)."""
    by_id = {m.id: m for m in mentions}
    keys = {r.id: r.key for r in records}
    founder_type: dict[str, str] = {}
    for a in references:
        founder_type.setdefault(a.canonical, a.type)
    out = []
    for a in sorted(references, key=lambda a: a.mention):
        if a.kind not in ("record", "individual") or a.mention not in by_id:
            continue
        label = a.canonical.split(":", 1)[0] if a.kind == "record" else founder_type[a.canonical]
        names = [by_id[a.mention].name, keys.get(a.canonical, "") if a.kind == "record" else "", a.name]
        out.append(
            Particular(
                mention=a.mention,
                doc_id=by_id[a.mention].doc_id,
                names=list(dict.fromkeys(n for n in names if n)),
                thing=Thing(element_id=a.canonical, name=a.name, kind=f"{a.kind}:{label}"),
            )
        )
    return out


def _text_links(documents, chunks: list[Chunk], linked: list[AboutLink], particulars: list[Particular]):
    """`attachment._link_documents_by_text`: a document the link stage left about nothing is ABOUT the record
    its sentences name clearly most."""
    about = {link.source for link in linked}
    sentences: dict[str, list[str]] = defaultdict(list)
    for c in sorted(chunks, key=lambda c: (c.doc_id, c.index)):
        sentences[c.doc_id] += split_sentences(c.text)
    by_doc: dict[str, list[Particular]] = defaultdict(list)
    for p in particulars:
        by_doc[p.doc_id].append(p)
    out = []
    for doc in sorted(sentences):
        if doc not in about and (found := dominant_record(sentences[doc], by_doc.get(doc, []))):
            thing, evidence = found
            out.append(
                AboutLink(
                    source=doc, thing=thing.element_id, name=thing.name, how=TEXT_ABOUT, evidence=evidence
                )
            )
    return out


def _attach(
    claims: list[SnapshotClaim],
    mentions: list[SnapshotMention],
    particulars: list[Particular],
    documents_about: list[AboutLink],
    sections_about: list[AboutLink],
    records: list[Record],
    schema: TextSchema,
) -> list[SnapshotAttachment]:
    """`attachment.attach_claims` without the graph: the claims as `read_claims` reads them, the ABOUT links
    as `_read_links` reads them (records only), then the pure `attach`."""
    type_of = {m.id: m.type for m in mentions}
    label_of = {r.id: r.label for r in records}

    def things(links: list[AboutLink]) -> dict[str, list[Thing]]:
        out: dict[str, list[Thing]] = defaultdict(list)
        for link in sorted(links, key=lambda link: (link.source, link.thing)):
            out[link.source].append(
                Thing(element_id=link.thing, name=link.name, kind=f"record:{label_of[link.thing]}")
            )
        return dict(out)

    sources = [
        ClaimSource(
            id=c.id,
            doc_id=c.doc_id,
            chunk_id=c.chunk_id,
            quote=c.evidence,
            subject_name=c.subject_name,
            object_name=c.object_name,
            subject=c.subject,
            object=c.object,
            part_of=schema.is_part_of(type_of.get(c.subject, ""), c.predicate, type_of.get(c.object, "")),
        )
        for c in claims
    ]
    return [
        SnapshotAttachment(
            observation=a.observation,
            thing=a.thing.element_id,
            kind=a.thing.kind,
            name=a.thing.name,
            how=a.how,
            evidence=a.evidence,
        )
        for a in attach(sources, particulars, things(sections_about), things(documents_about))
    ]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")
