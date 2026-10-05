"""Entity resolution's decisions: which concepts of one type are the same kind, as pure functions.

Role in the pipeline: the concept part of `kg resolve` (concepts.py reads the concepts and asks the LLM,
identity.py writes the outcome as identity edges). Since R75 nothing here touches Neo4j: a decision no longer
merges nodes, it becomes one `REFERS_TO` edge per mention, undone by deleting the edge.
Design: four small steps:
  1. find_candidates    score same-type pairs with the matchers (matchers.py); pairs close in meaning are
                        nominated by a blocking rule (blocking.py); numbers (`Value`) are never compared, and
                        a nominated pair that a guard blocks (guards.py: opposite polarity, both named in one
                        sentence, a part and its whole) is dropped and counted; all three are Strategies
  2. decide             given an adjudicator: auto-merge, ask the LLM (in parallel), or skip
  3. group_merges       union-find over the merge decisions, pick a canonical concept per group
  4. decide_in_passes   steps 1-3 repeated on the merged view (R45): a group of more than k wordings fills
                        its members' k nearest slots, so it can meet a neighbouring group only once each is
                        one concept
`mention_lines` builds the adjudication context; `preview` describes the candidates for `kg resolve
--preview`, to see what the thresholds and the blocking nominate.
Not here: similarity functions (matchers.py), reading and asking (concepts.py), writing (identity.py).
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from ..core.text import pick_sentence
from ..core.values import VALUE_TYPE
from ..llm.base import Embedder
from .blocking import Blocking
from .guards import DEFAULT_GUARDS, BlockLog, Guard
from .matchers import EmbeddingMatcher, EntityRecord, FuzzyNameMatcher, Matcher

_MENTIONS_PER_ENTITY = 3  # enough to see how a name is used, few enough to keep each call cheap
_SENTENCE_CHARS = 400  # bounds a runaway "sentence" (a table row, a list without full stops) on any data
_ADJUDICATION_WORKERS = 8
# a pass after the first asks only about groups the previous pass formed; the loop stops as soon as a pass
# merges nothing, and this cap bounds the LLM cost of a pathological chain of merges
_MAX_PASSES = 3

Action = Literal["auto", "llm_merge", "llm_keep", "skipped_borderline"]
Adjudicator = Callable[[EntityRecord, EntityRecord], bool]


class Candidate(BaseModel):
    a: str  # concept ids
    b: str
    score: float
    signal: str  # name of the matcher that nominated the pair


class Decision(BaseModel):
    a: str  # names, for a readable audit log
    b: str
    a_id: str
    b_id: str
    type: str
    score: float
    signal: str
    action: Action


class MergeGroup(BaseModel):
    canonical: str
    absorbed: list[str]


class MentionRow(BaseModel):
    """One chunk mentioning a concept, as the adjudication context is built from it."""

    entity: str  # concept id
    names: list[str]  # name and aliases: the sentence may use any of them
    document: str  # the chunk's context (its document heading), else its document id
    text: str


class PreviewPair(BaseModel):
    """One candidate pair as `kg resolve --preview` shows it."""

    a: str  # names
    b: str
    type: str
    score: float
    signal: str
    route: Literal["auto", "llm"]  # a real run merges it by spelling alone, or asks the LLM


class ResolvePreview(BaseModel):
    """What a resolve run would consider, without an LLM call or a write: thresholds are chosen from
    the real score distribution instead of being guessed."""

    entities: int
    pairs: list[PreviewPair]


def find_candidates(
    entities: list[EntityRecord],
    borderline: float,
    embedding: Matcher | None = None,
    blocking: Blocking | None = None,
    guards: Sequence[Guard] = DEFAULT_GUARDS,
    blocked: BlockLog | None = None,
) -> list[Candidate]:
    """All same-type pairs worth a decision: fuzzy score >= `borderline`, or nominated by `blocking` on
    the `embedding` scores (both needed; either None means spelling candidates only), unless a guard
    blocks the pair; `blocked` (when given) collects the dropped pairs under the first guard that blocked
    them.

    Entities of different types are never compared: a Product and a Problem with the same name are
    different things. Within a type every pair is scored; the fuzzy cutoff makes hopeless pairs cheap.
    Numbers are never compared ("25 kg" and "35 kg" are 91 alike by spelling and are different claims).
    """
    fuzzy = FuzzyNameMatcher()
    by_type: dict[str, list[EntityRecord]] = {}
    for entity in entities:
        if entity.type != VALUE_TYPE:
            by_type.setdefault(entity.type, []).append(entity)

    candidates = []
    for members in by_type.values():
        by_meaning = blocking.pairs(members, embedding.score) if embedding and blocking else {}
        for a, b in combinations(members, 2):
            score = fuzzy.score(a, b, cutoff=borderline)
            if score >= borderline:
                candidate = Candidate(a=a.id, b=b.id, score=round(score, 1), signal=fuzzy.name)
            elif (
                embedding is not None and (similarity := by_meaning.get(frozenset((a.id, b.id)))) is not None
            ):
                candidate = Candidate(a=a.id, b=b.id, score=round(similarity, 1), signal=embedding.name)
            else:
                continue
            guard = next((g for g in guards if g.blocks(a, b)), None)
            if guard is None:
                candidates.append(candidate)
            elif blocked is not None:
                blocked.setdefault(guard.name, set()).add(frozenset((a.id, b.id)))
    return candidates


def embedding_matcher(
    entities: list[EntityRecord], embedder: Embedder | None, blocking: Blocking | None
) -> EmbeddingMatcher | None:
    """The meaning-based matcher, switched on when both an embedder and a blocking are given (one batched
    embedding call for all names), else None."""
    return EmbeddingMatcher(embedder, entities) if embedder is not None and blocking is not None else None


def nominate(
    entities: list[EntityRecord],
    borderline: float,
    embedder: Embedder | None,
    blocking: Blocking | None,
    guards: Sequence[Guard] = DEFAULT_GUARDS,
) -> list[Candidate]:
    """Step 1 with the meaning-based matcher when it is switched on."""
    matcher = embedding_matcher(entities, embedder, blocking)
    return find_candidates(entities, borderline, matcher, blocking, guards)


def is_auto_merge(candidate: Candidate, auto_merge: float) -> bool:
    """Only a spelling score this high merges without the LLM. A meaning score never does: short names
    of different things ("drawer rails", "drawer handles") can have very close embeddings."""
    return candidate.signal == FuzzyNameMatcher.name and candidate.score >= auto_merge


def decide(
    candidates: list[Candidate],
    entities: dict[str, EntityRecord],
    auto_merge: float,
    adjudicate: Adjudicator | None,
) -> list[Decision]:
    """Turn candidates into decisions. Only a fuzzy score >= `auto_merge` merges without the LLM."""
    borderline = [c for c in candidates if not is_auto_merge(c, auto_merge)]
    verdicts: dict[tuple[str, str], bool] = {}
    if adjudicate is not None and borderline:
        # independent LLM calls: run them in parallel, keep the order for a deterministic log
        with ThreadPoolExecutor(max_workers=_ADJUDICATION_WORKERS) as pool:
            answers = pool.map(lambda c: adjudicate(entities[c.a], entities[c.b]), borderline)
            verdicts = {(c.a, c.b): same for c, same in zip(borderline, answers, strict=True)}

    decisions = []
    for c in candidates:
        if is_auto_merge(c, auto_merge):
            action: Action = "auto"
        elif adjudicate is None:
            action = "skipped_borderline"
        else:
            action = "llm_merge" if verdicts[(c.a, c.b)] else "llm_keep"
        a, b = entities[c.a], entities[c.b]
        decisions.append(
            Decision(
                a=a.name,
                b=b.name,
                a_id=a.id,
                b_id=b.id,
                type=a.type,
                score=c.score,
                signal=c.signal,
                action=action,
            )
        )
    return decisions


def group_merges(entities: dict[str, EntityRecord], decisions: list[Decision]) -> list[MergeGroup]:
    """Union-find over merge decisions: A=B and B=C puts A, B, C in one group, merged in one step."""
    parent = {entity_id: entity_id for entity_id in entities}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving keeps the trees flat
            x = parent[x]
        return x

    for d in decisions:
        if d.action in ("auto", "llm_merge"):
            parent[find(d.b_id)] = find(d.a_id)

    members_by_root: dict[str, list[str]] = {}
    for entity_id in entities:
        members_by_root.setdefault(find(entity_id), []).append(entity_id)

    groups = []
    for members in members_by_root.values():
        if len(members) > 1:
            # canonical: most mentioned, then the shortest (most general) name, then id for determinism.
            # A group is often one kind stated by several reviews; the longest name carried one review's
            # detail to all ("crack developing along the bottom" for a bed's cracked slats, R44). Every
            # other name stays an alias, and each fact keeps its own wording (subject_graph.py).
            members.sort(key=lambda i: (-entities[i].mentions, len(entities[i].name), i))
            groups.append(MergeGroup(canonical=members[0], absorbed=members[1:]))
    return groups


def merged_view(
    entities: dict[str, EntityRecord], groups: list[MergeGroup]
) -> tuple[list[EntityRecord], dict[str, list[str]]]:
    """Pure: the entities as they will be once `groups` are merged, and the member ids behind each.

    A merged entity keeps its canonical member's id and name (so the embedding computed for that name
    still scores it) and gathers every member's aliases. Entity order is kept, so a first pass with no
    groups builds exactly the candidates, and therefore the prompts, of a single-pass run.
    """
    canonical = {g.canonical: [g.canonical, *g.absorbed] for g in groups}
    absorbed = {i for g in groups for i in g.absorbed}
    members = {i: canonical.get(i, [i]) for i in entities if i not in absorbed}
    view = [
        entities[i]
        if len(ids) == 1
        else entities[i].model_copy(
            update={
                "aliases": sorted({a for m in ids for a in entities[m].aliases}),
                "mentions": sum(entities[m].mentions for m in ids),
                # a group carries every member's tones, so the guard holds for the merged kind too
                "polarities": sorted({p for m in ids for p in entities[m].polarities}),
            }
        )
        for i, ids in members.items()
    ]
    return view, members


AdjudicatorFor = Callable[[list[Candidate], dict[str, list[str]]], Adjudicator]


def decide_in_passes(
    records: list[EntityRecord],
    borderline: float,
    embedding: Matcher | None,
    blocking: Blocking | None,
    auto_merge: float,
    adjudicator_for: AdjudicatorFor | None,
    max_passes: int = _MAX_PASSES,
    guards: Sequence[Guard] = DEFAULT_GUARDS,
    blocked: BlockLog | None = None,
) -> tuple[list[Decision], list[MergeGroup], int]:
    """Steps 1-3, repeated on the merged view until a pass merges nothing: all decisions, the final
    groups (over the original concepts) and the passes run. A merged concept carries every member's names
    and tones, so the guards hold for it too; `blocked` collects what they dropped over all passes (a pair
    dropped in two passes once: a merged concept keeps its canonical member's id).

    A pair is asked again only when one side gained members since it was asked. `adjudicator_for` builds
    the adjudicator for one pass's candidates and member ids; None leaves borderline pairs undecided.
    """
    entities = {e.id: e for e in records}
    decisions: list[Decision] = []
    groups: list[MergeGroup] = []
    asked: set[frozenset[tuple[str, ...]]] = set()
    passes = 0
    while passes < max_passes:
        passes += 1
        view, members = merged_view(entities, groups)

        def key(c: Candidate, members: dict[str, list[str]] = members) -> frozenset[tuple[str, ...]]:
            return frozenset((tuple(members[c.a]), tuple(members[c.b])))

        nominated = find_candidates(view, borderline, embedding, blocking, guards, blocked)
        fresh = [c for c in nominated if key(c) not in asked]
        if not fresh:
            break
        asked |= {key(c) for c in fresh}
        adjudicate = adjudicator_for(fresh, members) if adjudicator_for is not None else None
        decisions += decide(fresh, {e.id: e for e in view}, auto_merge, adjudicate)
        regrouped = group_merges(entities, decisions)
        if regrouped == groups:
            break
        groups = regrouped
    return decisions, groups, passes


def mention_lines(
    rows: list[MentionRow], limit: int = _MENTIONS_PER_ENTITY, owner: dict[str, str] | None = None
) -> dict[str, list[str]]:
    """Up to `limit` distinct "[document] sentence" lines per entity, in the order of `rows`. With `owner`
    (member id -> merged entity id), the lines of all members of a merged entity are gathered under it.

    A chunk without a sentence naming the entity (it reached the chunk through the document context or an
    alias) contributes nothing: the LLM sees only what the text really says about the name.
    """
    lines: dict[str, list[str]] = {}
    for row in rows:
        found = lines.setdefault(owner.get(row.entity, row.entity) if owner else row.entity, [])
        if len(found) >= limit:
            continue
        sentence = pick_sentence(row.text, row.names)
        if sentence is None:
            continue
        line = f"[{row.document}] {sentence[:_SENTENCE_CHARS]}"
        if line not in found:  # the same sentence repeated in two reviews says nothing new
            found.append(line)
    return lines


def preview(entities: list[EntityRecord], candidates: list[Candidate], auto_merge: float) -> ResolvePreview:
    """Describe `candidates` by name and route, highest score first within each signal, so the point
    where real synonyms stop and unrelated pairs begin can be read off the list."""
    by_id = {e.id: e for e in entities}
    pairs = [
        PreviewPair(
            a=by_id[c.a].name,
            b=by_id[c.b].name,
            type=by_id[c.a].type,
            score=c.score,
            signal=c.signal,
            route="auto" if is_auto_merge(c, auto_merge) else "llm",
        )
        for c in candidates
    ]
    pairs.sort(key=lambda p: (p.signal, -p.score, p.a, p.b))
    return ResolvePreview(entities=len(entities), pairs=pairs)
