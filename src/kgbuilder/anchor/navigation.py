"""The navigation contract of the anchor graph (R90): the five walks W1-W5 over one offline snapshot.

Role in the pipeline: after a build, in the anchor-graph evaluation (docs/direction/2026-10-06_anchor-graph,
section 4). The snapshot of R87 (`audit/snapshot.py`) is the build's graph as data; this module answers the
walks the graph guarantees, and the criteria (`anchor/criteria.py`) measure them against the target gold.
  W1 find      names -> nodes (records, individuals, concepts): exact normalised names, then shared words
  W2 chunks    node -> the chunks that concern it (MENTIONS + REFERS_TO; ABOUT for records)
  W3 about     chunk -> the records it is about (its own ABOUT, else its document's)
     named     chunk -> the records and individuals its mentions refer to, never a concept (R97)
  W4 related   record -> the records the plan's relations reach, any number of hops
  W5 context   chunk -> its neighbours (NEXT_CHUNK) and its document (PART_OF)
Design: two arms are two settings of one class (Strategy by flag, since they differ only in which edges
exist). Arm A walks only the anchor edges above. Arm B (`LAYERED`) also walks the claim layer the build kept:
a thing reaches the chunk of every claim attached to it or ending on it, and a claim joins the things at
its ends and the thing it is attached to. Every thing-to-thing hop says what witnesses it: a record relation,
or a chunk that concerns both ends (the witness rule of section 3.1); `thing_edges` lists them all. A hop
through a chunk (W2 then W3) needs no entry there: the chunk itself is its witness, since it is about the
thing or names it.
Must not: call an LLM, read Neo4j, or rank chunks for a question (that rule belongs to the criteria).
"""

import re
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..audit.snapshot import GraphSnapshot
from ..core.text import norm


class Arm(StrEnum):
    """Which edges a walk may use."""

    ANCHOR = "anchor"  # arm A: records, mentions, identity and ABOUT links only
    LAYERED = "layered"  # arm B: also through the claims (HAS_OBSERVATION, SUBJECT, OBJECT, FROM)


@dataclass(frozen=True)
class Found:
    """One node W1 returns: `exact` when a name equals one of the node's names after `norm`, else the
    share of words the best name and query have in common (Jaccard)."""

    node: str
    exact: bool
    score: float


@dataclass(frozen=True)
class Context:
    """W5: a chunk's document and its neighbours in reading order (None at either end)."""

    document: str
    previous: str | None
    next: str | None


@dataclass(frozen=True)
class ThingEdge:
    """One thing-to-thing hop and what witnesses it: `relation` (a record relation of the plan) or `claim`
    (arm B), with `chunk` the claim's chunk and `witnessed` whether that chunk concerns both ends."""

    a: str
    b: str
    how: str
    chunk: str | None
    witnessed: bool


_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(norm(text)))


class AnchorGraph:
    """W1-W5 over one snapshot, in one arm. Nodes are named as the snapshot names them: a record by its
    `record_ref` (`Product:P-1007`), an individual or concept by its canonical id."""

    def __init__(self, s: GraphSnapshot, arm: Arm) -> None:
        self.arm = arm
        self.kind: dict[str, str] = {r.id: "record" for r in s.records}
        self.chunk_ids: list[str] = [c.chunk_id for c in sorted(s.chunks, key=lambda c: (c.doc_id, c.index))]
        self._names: dict[str, set[str]] = defaultdict(set)
        self._mentions: dict[str, int] = defaultdict(int)
        for r in s.records:
            self._names[r.id] |= {norm(n) for n in (r.name, r.key) if n}
        canonical = {a.mention: a.canonical for a in s.references}
        for a in s.references:
            self.kind.setdefault(a.canonical, a.kind)
            self._names[a.canonical] |= {norm(a.name), norm(a.said)}
            self._mentions[a.canonical] += 1
        self._words = {node: [_words(n) for n in names] for node, names in self._names.items()}
        self._chunks = self._anchor_chunks(s, canonical)
        self._about = self._chunk_about(s)
        self._named = self._chunk_named(s)
        self._edges = [
            ThingEdge(a=r.source, b=r.target, how="relation", chunk=None, witnessed=True) for r in s.relations
        ]
        if arm is Arm.LAYERED:
            self._add_claims(s, canonical)
        self._neighbours: dict[str, set[str]] = defaultdict(set)
        for e in self._edges:
            self._neighbours[e.a].add(e.b)
            self._neighbours[e.b].add(e.a)
        self._context = self._contexts(s)

    # --- building -------------------------------------------------------------------------------------

    @staticmethod
    def _anchor_chunks(s: GraphSnapshot, canonical: dict[str, str]) -> dict[str, set[str]]:
        """W2 in arm A: a mention's chunks reach what it refers to; a document's chunks and a chunk reach
        the record they are ABOUT."""
        out: dict[str, set[str]] = defaultdict(set)
        for m in s.mentions:
            if m.id in canonical:
                out[canonical[m.id]] |= set(m.chunks)
        of_doc: dict[str, set[str]] = defaultdict(set)
        for c in s.chunks:
            of_doc[c.doc_id].add(c.chunk_id)
        for link in s.documents_about:
            out[link.thing] |= of_doc[link.source]
        for link in s.sections_about:
            out[link.thing].add(link.source)
        return out

    @staticmethod
    def _chunk_about(s: GraphSnapshot) -> dict[str, set[str]]:
        """W3: a chunk's own ABOUT links, else its document's (`Chunk -ABOUT->`, else `Document -ABOUT->`)."""
        own: dict[str, set[str]] = defaultdict(set)
        for link in s.sections_about:
            own[link.source].add(link.thing)
        of_doc: dict[str, set[str]] = defaultdict(set)
        for link in s.documents_about:
            of_doc[link.source].add(link.thing)
        return {c.chunk_id: own.get(c.chunk_id) or of_doc.get(c.doc_id, set()) for c in s.chunks}

    @staticmethod
    def _chunk_named(s: GraphSnapshot) -> dict[str, set[str]]:
        """W3's second half (R97): the particular things a chunk names, read the other way along W2's edges
        (`Chunk -MENTIONS-> Mention -REFERS_TO-> record | Individual`). ABOUT assumes one subject per
        document or section, so a report naming a pump and a person was about neither; what it names is
        every thing it concerns. Concepts are left out: a kind ("leaks") is shared by every chunk that names
        it, so following it from a chunk would join unrelated chunks through the kind (section 3.1)."""
        particular = {a.mention: a.canonical for a in s.references if a.kind in ("record", "individual")}
        out: dict[str, set[str]] = defaultdict(set)
        for m in s.mentions:
            if m.id in particular:
                for chunk in m.chunks:
                    out[chunk].add(particular[m.id])
        return out

    @staticmethod
    def _contexts(s: GraphSnapshot) -> dict[str, Context]:
        """W5: chunks in index order within their document; NEXT_CHUNK joins neighbours of one document."""
        of_doc: dict[str, list[str]] = defaultdict(list)
        for c in sorted(s.chunks, key=lambda c: (c.doc_id, c.index)):
            of_doc[c.doc_id].append(c.chunk_id)
        out = {}
        for doc, chunks in of_doc.items():
            for i, chunk in enumerate(chunks):
                previous = chunks[i - 1] if i > 0 else None
                following = chunks[i + 1] if i + 1 < len(chunks) else None
                out[chunk] = Context(document=doc, previous=previous, next=following)
        return out

    def _add_claims(self, s: GraphSnapshot, canonical: dict[str, str]) -> None:
        """Arm B: a claim's chunk reaches the things attached to the claim and the things at its ends, and
        the claim joins those things pairwise. A join is witnessed when its chunk concerns both things in
        arm A (W2 without claims), which is what a reader could check."""
        anchor = {node: set(chunks) for node, chunks in self._chunks.items()}
        attached: dict[str, set[str]] = defaultdict(set)
        for att in s.attachments:
            attached[att.observation].add(att.thing)
        for claim in s.claims:
            ends = {canonical[m] for m in (claim.subject, claim.object) if m in canonical}
            things = sorted(ends | attached[claim.id])
            for thing in things:
                self._chunks[thing].add(claim.chunk_id)
            for i, a in enumerate(things):
                for b in things[i + 1 :]:
                    witnessed = claim.chunk_id in anchor.get(a, set()) and claim.chunk_id in anchor.get(
                        b, set()
                    )
                    self._edges.append(
                        ThingEdge(a=a, b=b, how="claim", chunk=claim.chunk_id, witnessed=witnessed)
                    )

    # --- the walks ------------------------------------------------------------------------------------

    def find(self, names: Sequence[str]) -> list[Found]:
        """W1: every node one of `names` names, best first.

        Exact hits (a name equal to one of the node's names after `norm`: its record name or key, its
        canonical name, the name of a mention that refers to it) come first, then nodes sharing words
        with a name, by the best Jaccard share. Ties go to the node more mentions refer to (the thing the
        text talks about more), then to the node id, so the order is reproducible.
        """
        wanted = {norm(n) for n in names if norm(n)}
        query = [_words(n) for n in wanted]
        found = []
        for node, own in self._names.items():
            if own & wanted:
                found.append(Found(node=node, exact=True, score=1.0))
                continue
            best = max(
                (len(q & w) / len(q | w) for q in query for w in self._words[node] if q | w), default=0.0
            )
            if best > 0:
                found.append(Found(node=node, exact=False, score=best))
        return sorted(found, key=lambda f: (not f.exact, -f.score, -self._mentions[f.node], f.node))

    def chunks_of(self, node: str) -> set[str]:
        """W2: the chunks that concern `node` in this arm (empty for an unknown node)."""
        return set(self._chunks.get(node, set()))

    def about(self, chunk: str) -> set[str]:
        """W3: the records `chunk` is about."""
        return set(self._about.get(chunk, set()))

    def named(self, chunk: str) -> set[str]:
        """W3: the records and individuals `chunk` names through its mentions (never a concept; empty for an
        unknown chunk). The composed walk goes from a chunk to `about | named`."""
        return set(self._named.get(chunk, set()))

    def related(self, node: str) -> dict[str, int]:
        """W4: every thing the thing-to-thing hops reach from `node`, with its hop count (`node` excluded).
        In arm A the hops are the plan's record relations; arm B adds the claims' joins."""
        hops = {node: 0}
        queue = deque([node])
        while queue:
            current = queue.popleft()
            for nxt in self._neighbours.get(current, ()):
                if nxt not in hops:
                    hops[nxt] = hops[current] + 1
                    queue.append(nxt)
        del hops[node]
        return hops

    def context(self, chunk: str) -> Context:
        """W5: the chunk's document and its neighbours. Raises KeyError for an unknown chunk."""
        return self._context[chunk]

    def walk(self, starts: Iterable[str]) -> dict[str, int]:
        """Every chunk the composed walks reach from `starts`, with its walk length: the fewest W2, W3 and
        W4 steps on the way (a start's own chunks have length 1). Composition is what a multi-hop need
        does: a concept's chunks (W2), the record a chunk is about or names (W3), that record's relations
        (W4)."""
        return {item: n for (kind, item), n in self._search(starts).items() if kind == "chunk"}

    def reached_nodes(self, starts: Iterable[str]) -> set[str]:
        """The nodes the same composed walk reaches from `starts`, the starts included."""
        return {item for kind, item in self._search(starts) if kind == "node"}

    def _search(self, starts: Iterable[str]) -> dict[tuple[str, str], int]:
        """Breadth-first over nodes and chunks: node -> chunk (W2), node -> node (W4 and, in arm B, the
        claims' joins), chunk -> the records it is about and the records and individuals it names (W3);
        each item with its fewest steps from a start."""
        seen = {("node", node): 0 for node in starts}
        queue = deque(seen)
        while queue:
            kind, item = queue.popleft()
            if kind == "node":
                nexts = [("chunk", c) for c in sorted(self._chunks.get(item, ()))]
                nexts += [("node", n) for n in sorted(self._neighbours.get(item, ()))]
            else:
                things = self._about.get(item, set()) | self._named.get(item, set())
                nexts = [("node", n) for n in sorted(things)]
            for nxt in nexts:
                if nxt not in seen:
                    seen[nxt] = seen[(kind, item)] + 1
                    queue.append(nxt)
        return seen

    def thing_edges(self) -> list[ThingEdge]:
        """Every thing-to-thing hop of this arm with its witness, for the witness rule (C8)."""
        return list(self._edges)
