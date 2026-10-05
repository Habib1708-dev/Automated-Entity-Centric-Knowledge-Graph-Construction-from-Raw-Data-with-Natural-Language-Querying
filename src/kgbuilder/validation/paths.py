"""Path truth: does every claim a query can reach from a thing hold for that thing?

Role in the pipeline: one section of `kg eval` and `kg rescore` (R63, step 1 of the observation-graph task).
Why: the judge scores each fact against its own sentence, so every fact can be true while the graph still
states something false. Entities are kinds shared across documents (R41): one `drawer rails` node for the
whole corpus. In the edge graph (before R64), a derived fact attached a part to every product whose
document mentions it, so the query product <-PART_OF- part -> claim reached the claims of every product
with that part. In R62 the Linköping Bed reached the Helsingborg Dresser's defective drawer rails this way.
Two graph shapes, one question:
- Edge graph (a graph or judge sheet from before R64: no fact is attached to a thing). A *thing* is the
  object of a derived fact, and the documents about it are the documents its derived facts come from. For
  every thing, every part a derived fact attaches to it and every extracted fact with that part at one
  end, the path is true when a fact with the same subject, relation and object (the same entities after
  resolution, whatever each review's wording) comes from a document about the thing.
- Observation graph (R64): a path is thing -HAS_OBSERVATION-> observation, and it is true when the
  observation's own document is ABOUT that thing. The link stage attaches observations exactly that way,
  so anything below 1.0 means the attachments and the ABOUT links have come apart (a stale link, an undo
  without `kg link`).
- Attached graph (R76, layered-model Step 6): every HAS_OBSERVATION edge names the route that attached it
  and its evidence, and a path is true when that evidence holds in the graph as it is: `key_in_sentence`,
  the name stands in a sentence of the claim's quote that names one of its ends (`claim_sentences`);
  `part_of`, a part-of claim of the same document has the claim's subject and the thing as its whole;
  `section`, the claim's chunk is ABOUT the thing; `document`, its document is. The rule of the
  observation graph is counted next to it (`path_truth_about`), so the series stays comparable; it is
  below 1.0 by design wherever a claim hangs on what its quote or section names rather than on the
  document's thing.
In all three, derived facts and kind structure are not paths: a fact between two part types ("drawer pulls
PART_OF drawer") says how a kind of thing is built, not what one document claims about one thing, and
leaving it out keeps the two shapes' numbers comparable.
Design: a pure function over `StoredFact`s, so it is tested without Neo4j and gives the same number on the
live graph (`kg eval`) and on a logged judge sheet (`kg rescore`). The text schema is needed only to know
which fact types are derived. Not here: whether a fact matches its own sentence (judge.py).
"""

from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel

from ..core.text import claim_sentences, contains_words, norm
from ..text.schema import TextSchema
from .checks.base import Attached, StoredFact
from .gold import doc_of

# an entity as resolution left it: (type, normalised display name). Facts keep their own wording (R44), but
# a path runs through nodes, so two wordings of one merged node are one entity here
EntityKey = tuple[str, str]
Claim = tuple[EntityKey, str, EntityKey]
Signature = tuple[str, str, str]  # (subject type, predicate, object type)


class FalsePath(BaseModel):
    """A claim the graph reaches from a thing although no document about that thing states it."""

    thing: str
    via: str  # what the path runs through: the shared part (edge graph) or the observation's chunk
    claim: str  # "subject -[PREDICATE]-> object", display names
    stated_in: list[str]  # the documents that do state the claim
    how: str = ""  # the attachment's route (R76); empty before


class PathReport(BaseModel):
    paths_total: int
    paths_true: int
    false_paths: list[FalsePath]
    # the attached graph's paths by the observation graph's rule (R76); None for the other two shapes
    paths_true_about: int | None = None

    @property
    def truth(self) -> float:
        # no path means nothing was asserted through a shared part, so nothing was wrong
        return self.paths_true / self.paths_total if self.paths_total else 1.0

    @property
    def truth_about(self) -> float | None:
        if self.paths_true_about is None:
            return None
        return self.paths_true_about / self.paths_total if self.paths_total else 1.0


def _key(entity_type: str, names: list[str]) -> EntityKey:
    return entity_type, norm(names[0])


def score_paths(facts: list[StoredFact], schema: TextSchema) -> PathReport:
    """Count the paths from a thing to a claim and how many hold for their thing.

    Inputs: all facts of the graph (or of a logged sheet) and the text schema they were built with. The
    graph's shape is read from the facts: observation paths when any fact is attached to a thing, else
    paths through shared parts. Output: the counts and every false path, sorted so that the report is the
    same for the same graph.
    """
    derived = {(f.subject_type, f.predicate, f.object_type) for f in schema.fact_types if f.derived}
    part_types = {subject_type for subject_type, _, _ in derived}

    def is_claim(f: StoredFact) -> bool:
        signature = (f.subject_type, f.predicate, f.object_type)
        # kind structure: see the module docstring
        return signature not in derived and not (f.subject_type in part_types and f.object_type in part_types)

    if any(f.attachments for f in facts):
        return _attachment_paths([f for f in facts if is_claim(f)], _part_wholes(facts, schema))
    if any(f.things for f in facts):
        return _observation_paths([f for f in facts if is_claim(f)])
    return _part_paths(facts, derived, is_claim)


PartKey = tuple[str, str, str]  # (document, subject type, normalised subject wording): one mention


def _part_wholes(facts: list[StoredFact], schema: TextSchema) -> dict[PartKey, set[str]]:
    """For each part (one mention: its document, type and wording), the names of the wholes the part-of
    claims of its document give it."""
    wholes: dict[PartKey, set[str]] = defaultdict(set)
    for f in facts:
        if schema.is_part_of(f.subject_type, f.predicate, f.object_type):
            wholes[(doc_of(f) or "", f.subject_type, norm(f.own_subject))] |= set(f.object_names)
    return wholes


def _holds(a: Attached, f: StoredFact, wholes: dict[PartKey, set[str]]) -> bool:
    """Whether an attachment's evidence holds for its claim (the module docstring, attached graph)."""
    if a.how == "key_in_sentence":
        sentences = claim_sentences(f.evidence or "", [f.own_subject, f.own_object])
        return any(contains_words(s, a.evidence) for s in sentences)
    if a.how == "part_of":
        return a.thing in wholes.get((doc_of(f) or "", f.subject_type, norm(f.own_subject)), set())
    if a.how == "section":
        return a.thing in f.sections
    if a.how == "document":
        return a.thing in f.about
    return False  # a route this reader does not know cannot be checked, so it is not counted as true


def _attachment_paths(claims: list[StoredFact], wholes: dict[PartKey, set[str]]) -> PathReport:
    """One path per (claim, attachment); true when the attachment's evidence holds."""
    total = true = true_about = 0
    false_paths: list[FalsePath] = []
    for f in claims:
        for a in sorted(f.attachments, key=lambda a: (a.thing, a.how)):
            total += 1
            true_about += a.thing in f.about
            if _holds(a, f, wholes):
                true += 1
                continue
            doc = doc_of(f)
            false_paths.append(
                FalsePath(
                    thing=a.thing,
                    via=f.chunk_id or "",
                    claim=f"{f.own_subject} -[{f.predicate}]-> {f.own_object}",
                    stated_in=[doc] if doc else [],
                    how=a.how,
                )
            )
    false_paths.sort(key=lambda p: (p.thing, p.via, p.claim))
    return PathReport(
        paths_total=total, paths_true=true, false_paths=false_paths, paths_true_about=true_about
    )


def _observation_paths(claims: list[StoredFact]) -> PathReport:
    """One path per (thing, observation); true when the observation's document is about the thing."""
    total = true = 0
    false_paths: list[FalsePath] = []
    for f in claims:
        for thing in sorted(set(f.things)):
            total += 1
            if thing in f.about:
                true += 1
                continue
            doc = doc_of(f)
            false_paths.append(
                FalsePath(
                    thing=thing,
                    via=f.chunk_id or "",
                    claim=f"{f.own_subject} -[{f.predicate}]-> {f.own_object}",
                    stated_in=[doc] if doc else [],
                )
            )
    false_paths.sort(key=lambda p: (p.thing, p.via, p.claim))
    return PathReport(paths_total=total, paths_true=true, false_paths=false_paths)


def _part_paths(
    facts: list[StoredFact], derived: set[Signature], is_claim: Callable[[StoredFact], bool]
) -> PathReport:
    """The edge graph's paths: thing <- derived fact - part -> claim."""
    display: dict[EntityKey, str] = {}
    about: dict[EntityKey, set[str]] = defaultdict(set)  # thing -> documents about it
    parts: dict[EntityKey, set[EntityKey]] = defaultdict(set)  # thing -> parts attached to it
    claims_at: dict[EntityKey, set[Claim]] = defaultdict(set)  # entity -> extracted claims touching it
    stated: dict[Claim, set[str]] = defaultdict(set)  # claim -> documents stating it
    for f in facts:
        s, o = _key(f.subject_type, f.subject_names), _key(f.object_type, f.object_names)
        display[s], display[o] = f.subject_names[0], f.object_names[0]
        doc = doc_of(f)
        if (f.subject_type, f.predicate, f.object_type) in derived:
            parts[o].add(s)
            if doc:
                about[o].add(doc)
            continue
        if not is_claim(f):
            continue
        claim = (s, f.predicate, o)
        claims_at[s].add(claim)
        claims_at[o].add(claim)
        if doc:
            stated[claim].add(doc)

    total = true = 0
    false_paths: list[FalsePath] = []
    for thing in sorted(parts):
        for part in sorted(parts[thing]):
            for claim in sorted(claims_at[part]):
                total += 1
                if stated[claim] & about[thing]:
                    true += 1
                    continue
                s, predicate, o = claim
                false_paths.append(
                    FalsePath(
                        thing=display[thing],
                        via=display[part],
                        claim=f"{display[s]} -[{predicate}]-> {display[o]}",
                        stated_in=sorted(stated[claim]),
                    )
                )
    return PathReport(paths_total=total, paths_true=true, false_paths=false_paths)
