"""The graph a dataset tab draws (R222): the things of a finished build and what connects them, read offline.

Role in the pipeline: the web app's graph routes read it; it is made from the build's `GraphSnapshot`
(audit/snapshot.py), the build's own pure functions rerun over its saved files, so every tab can be drawn
whichever dataset Neo4j holds, at no cost.
Design: a read model of one build. A *thing* is what a reader would call a node: a table record, or the
individual or concept a text mention refers to (`REFERS_TO`); a mention no assignment covers stands for
itself. A *fact* is one edge with its provenance: a table relationship (the source file and both rows'
keys) or a text claim (its predicate, evidence sentence and chunk). The drawn views aggregate facts into
links (one per ends and type, with a count), so a pair named in twenty sentences is one line; a thing's
detail lists every fact. Every view is capped by its caller's `limit`.
Must not: open Neo4j, call a model, or decide anything the build did not: it only regroups the snapshot.
"""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..audit.snapshot import GraphSnapshot, build_snapshot
from ..structured.plan import ConstructionPlan

ThingKind = Literal["record", "individual", "concept", "mention"]
Origin = Literal["table", "text"]


class Thing(BaseModel):
    """A node as the interface draws it: its id (a record ref, a canonical id, or a mention id), kind,
    type (a record's label, a text thing's entity type), display name and number of facts."""

    id: str
    kind: ThingKind
    type: str
    name: str
    degree: int = 0


class Fact(BaseModel):
    """One edge with its provenance: from a table (`source` names the file and rows) or from a text claim
    (`evidence` is the sentence, `chunk_id` the passage holding it)."""

    source: str  # thing ids
    target: str
    type: str
    origin: Origin
    evidence: str = ""
    chunk_id: str = ""
    row: str = ""  # a table relationship's source: "<file> row <from column>=<key> -> <to column>=<key>"


class Link(BaseModel):
    """The facts between two things of one type, drawn as one line."""

    source: str
    target: str
    type: str
    origin: Origin
    count: int


class Subgraph(BaseModel):
    """A drawable part of the graph: the things shown, the links among them, and how many things there are
    in all (so the page can say what was left out)."""

    things: list[Thing]
    links: list[Link]
    total: int


class TypeCount(BaseModel):
    kind: ThingKind
    type: str
    count: int


class LinkTypeCount(BaseModel):
    source_type: str
    type: str
    target_type: str
    origin: Origin
    count: int


class SchemaView(BaseModel):
    """What the graph holds, by type: things per kind and type, facts per (source type, type, target type)
    and origin, and the build's text size."""

    things: list[TypeCount]
    links: list[LinkTypeCount]
    chunks: int
    documents: int


class PropertyRow(BaseModel):
    """A record's column value and the row it came from (the agent's `Property.source` format)."""

    name: str
    value: str
    source: str


class ThingDetail(BaseModel):
    """One thing with its properties, its facts (capped, `total_facts` in all), and the things at their
    other ends."""

    thing: Thing
    properties: list[PropertyRow]
    facts: list[Fact]
    total_facts: int
    neighbours: list[Thing]


class ChunkView(BaseModel):
    """A passage as the provenance drawer shows it: its document, the heading context, and its text."""

    chunk_id: str
    doc_id: str
    context: str
    text: str


class KnowledgeGraph:
    """The things and facts of one build, with the views the web routes ask for."""

    def __init__(self, snapshot: GraphSnapshot, plan: ConstructionPlan):
        self._things: dict[str, Thing] = {}
        self._properties: dict[str, list[PropertyRow]] = {}
        self._facts: list[Fact] = []
        self._chunks = {c.chunk_id: c for c in snapshot.chunks}
        self._documents = len({c.doc_id for c in snapshot.chunks})
        self._add_records(snapshot, plan)
        self._add_relations(snapshot, plan)
        self._add_claims(snapshot)
        self._by_thing: dict[str, list[Fact]] = defaultdict(list)
        for fact in self._facts:
            self._by_thing[fact.source].append(fact)
            if fact.target != fact.source:
                self._by_thing[fact.target].append(fact)
        for thing_id, facts in self._by_thing.items():
            self._things[thing_id].degree = len(facts)

    def _add_records(self, snapshot: GraphSnapshot, plan: ConstructionPlan) -> None:
        rules = {rule.label: rule for rule in plan.nodes}
        for record in snapshot.records:
            self._things[record.id] = Thing(
                id=record.id, kind="record", type=record.label, name=record.name or record.key
            )
            rule = rules.get(record.label)
            source = f"{rule.source_file} row {rule.unique_column}={record.key}" if rule else ""
            self._properties[record.id] = [
                PropertyRow(name=k, value=v, source=source) for k, v in sorted(record.properties.items())
            ]

    def _add_relations(self, snapshot: GraphSnapshot, plan: ConstructionPlan) -> None:
        rules = {rule.relationship_type: rule for rule in plan.relationships}
        for rel in snapshot.relations:
            rule = rules.get(rel.type)
            row = ""
            if rule is not None:  # the record refs are "<label>:<key>": the key is what the row holds
                from_key, to_key = rel.source.split(":", 1)[-1], rel.target.split(":", 1)[-1]
                row = f"{rule.source_file} row {rule.from_column}={from_key} -> {rule.to_column}={to_key}"
            fact = Fact(source=rel.source, target=rel.target, type=rel.type, origin="table", row=row)
            self._facts.append(fact)

    def _add_claims(self, snapshot: GraphSnapshot) -> None:
        """Each claim between the things its two mentions refer to; a mention no assignment covers (the
        identity layer left it out) stands for itself, so no claim is dropped from the view."""
        refers = {a.mention: a for a in snapshot.references}
        mentions = {m.id: m for m in snapshot.mentions}

        def thing_of(mention_id: str) -> str:
            assignment = refers.get(mention_id)
            if assignment is not None:
                if assignment.canonical not in self._things:  # a record is already there under its ref
                    a = assignment
                    self._things[a.canonical] = Thing(id=a.canonical, kind=a.kind, type=a.type, name=a.name)
                return assignment.canonical
            if mention_id not in self._things:
                mention = mentions.get(mention_id)
                name, kind_type = (mention.name, mention.type) if mention else (mention_id, "Mention")
                self._things[mention_id] = Thing(id=mention_id, kind="mention", type=kind_type, name=name)
            return mention_id

        for claim in snapshot.claims:
            self._facts.append(
                Fact(
                    source=thing_of(claim.subject),
                    target=thing_of(claim.object),
                    type=claim.predicate,
                    origin="text",
                    evidence=claim.evidence,
                    chunk_id=claim.chunk_id,
                )  # fmt: skip
            )

    def schema(self) -> SchemaView:
        """The graph by type, largest first."""
        things = Counter((t.kind, t.type) for t in self._things.values())
        links = Counter(
            (self._things[f.source].type, f.type, self._things[f.target].type, f.origin) for f in self._facts
        )
        return SchemaView(
            things=[TypeCount(kind=k, type=t, count=n) for (k, t), n in things.most_common()],
            links=[
                LinkTypeCount(source_type=s, type=t, target_type=o, origin=g, count=n)
                for (s, t, o, g), n in links.most_common()
            ],  # fmt: skip
            chunks=len(self._chunks),
            documents=self._documents,
        )

    def overview(self, limit: int) -> Subgraph:
        """The `limit` things with the most facts, and the links among them: the graph's busiest part."""
        ranked = sorted(self._things.values(), key=lambda t: (-t.degree, t.id))[:limit]
        return self._subgraph({t.id for t in ranked})

    def neighbourhood(self, thing_id: str, limit: int) -> Subgraph | None:
        """`thing_id` and up to `limit - 1` of its neighbours, those sharing the most facts with it first, and
        the links among them; None for an unknown id."""
        if thing_id not in self._things:
            return None
        shared = Counter(f.target if f.source == thing_id else f.source for f in self._by_thing[thing_id])
        shared.pop(thing_id, None)
        ranked = sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))
        neighbours = [n for n, _ in ranked[: max(limit - 1, 0)]]
        return self._subgraph({thing_id, *neighbours})

    def search(self, text: str, limit: int) -> list[Thing]:
        """Things whose name or id has `text` (case ignored): names starting with it first, then by facts."""
        needle = text.strip().casefold()
        if not needle:
            return []
        hits = [t for t in self._things.values() if needle in t.name.casefold() or needle in t.id.casefold()]
        hits.sort(key=lambda t: (not t.name.casefold().startswith(needle), -t.degree, t.name))
        return hits[:limit]

    def detail(self, thing_id: str, limit: int) -> ThingDetail | None:
        """One thing with its properties and first `limit` facts (table facts first); None for unknown ids."""
        thing = self._things.get(thing_id)
        if thing is None:
            return None
        facts = sorted(self._by_thing[thing_id], key=lambda f: (f.origin != "table", f.type))
        shown = facts[:limit]
        ends = {f.target if f.source == thing_id else f.source for f in shown} - {thing_id}
        return ThingDetail(
            thing=thing,
            properties=self._properties.get(thing_id, []),
            facts=shown,
            total_facts=len(facts),
            neighbours=[self._things[i] for i in sorted(ends)],
        )

    def chunk(self, chunk_id: str) -> ChunkView | None:
        """The passage `chunk_id`, or None when the build has none of that id."""
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            return None
        return ChunkView(chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, context=chunk.context, text=chunk.text)

    def _subgraph(self, ids: set[str]) -> Subgraph:
        counts = Counter(
            (f.source, f.target, f.type, f.origin) for f in self._facts if f.source in ids and f.target in ids
        )
        return Subgraph(
            things=sorted((self._things[i] for i in ids), key=lambda t: (-t.degree, t.id)),
            links=[Link(source=s, target=t, type=y, origin=o, count=n) for (s, t, y, o), n in counts.items()],
            total=len(self._things),
        )


def from_build(build: Path, data_dir: Path, chunking: tuple[int, int, int]) -> KnowledgeGraph:
    """The graph of the finished build in `build`, rebuilt offline over the dataset in `data_dir` with the
    build's chunking (max, min, overlap characters: the preset's, or every chunk id differs). Raises what
    `build_snapshot` raises for a folder that is not a finished build."""
    plan = ConstructionPlan.model_validate_json((build / "plan.json").read_text(encoding="utf-8"))
    return KnowledgeGraph(build_snapshot(build, data_dir, chunking), plan)
