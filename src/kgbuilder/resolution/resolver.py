"""Entity resolution: find and merge duplicate `:Entity` nodes of the same type, reversibly.

Role in the pipeline: `kg resolve`, after extraction and before linking.
Design: five small steps instead of one function, and only the first and the last two touch Neo4j:
  1. read_entities      load entities and their mention counts
  2. find_candidates    pure: score same-type pairs with the matchers (matchers.py); pairs close in
                        meaning are nominated by a blocking rule (blocking.py); both are Strategies.
                        Two guards (R66): numbers (`Value`) are never candidates, and neither are two
                        kinds that claims use with opposite polarity
  3. decide             pure given an adjudicator: auto-merge, ask the LLM (in parallel), or skip
  4. group_merges       pure: union-find over the merge decisions, pick a canonical entity per group
     Steps 2-4 repeat on the merged view (decide_in_passes, R45): a group of more than k wordings fills
     its members' k nearest slots, so it can meet a neighbouring group only once each is one entity.
  5. apply_merges       snapshot the members, then merge them with APOC (the SUBJECT / OBJECT edges of
                        their observations move to the merged node; three reviews stating one claim stay
                        three observations; only exact repeats go)
Every decision is logged, and the snapshot taken in step 5 is enough for `undo_merges` to restore the
graph, because a wrong merge silently corrupts every later query. `preview_candidates` runs steps 1-2
only (`kg resolve --preview`), to see what the thresholds and the blocking nominate.
Not here: similarity functions (matchers.py) and links to the domain graph (linking.py).
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Literal

from neo4j import Driver
from pydantic import BaseModel

from ..core.text import pick_sentence
from ..core.values import VALUE_TYPE
from ..llm.base import Embedder, LLMClient
from .blocking import Blocking
from .matchers import EmbeddingMatcher, EntityRecord, FuzzyNameMatcher, Matcher

# Conservative on purpose: a false merge destroys information, a missed merge only leaves a duplicate.
# Each name comes with the sentences that mention it and their document (R39): a name alone is often
# ambiguous ("rails", "switch"), and whether two names come from the same product's reviews matters. Until
# R39 the context was the first 300 characters of one arbitrary chunk, which for 20 of 78 names never
# contained the name at all.
# Item or kind (R41): an entity is one node per type and name across all documents, so for most types it
# stands for a kind ("drawer", "didn't align properly"), shared by every product that has it. Asked about
# "the same real-world thing" and shown the document names, the LLM kept same-kind wordings apart across
# products (all 12 errors on the R40 pairs). Whether a type names items or kinds is decided by the LLM
# from the type name, so the rule holds for any schema.
ADJUDICATE_PROMPT = """Do these two names, both of type {etype}, refer to the same thing?
The names may come from different documents. If {etype} names individual items (one specific product,
person, organisation or place), answer whether A and B are the same item. Otherwise {etype} names a kind
of thing (such as a part, a defect or a symptom): answer whether A and B are the same kind, even when
they are mentioned in different documents.
Different sizes, models, components or people are NOT the same. Answer conservatively.

A: {a}
B: {b}

Where A is mentioned ([document] sentence):
{ctx_a}

Where B is mentioned ([document] sentence):
{ctx_b}"""

_MENTIONS_PER_ENTITY = 3  # enough to see how a name is used, few enough to keep each call cheap
_SENTENCE_CHARS = 400  # bounds a runaway "sentence" (a table row, a list without full stops) on any data
_ADJUDICATION_WORKERS = 8
# a pass after the first asks only about groups the previous pass formed; the loop stops as soon as a pass
# merges nothing, and this cap bounds the LLM cost of a pathological chain of merges
_MAX_PASSES = 3

Action = Literal["auto", "llm_merge", "llm_keep", "skipped_borderline"]
Adjudicator = Callable[[EntityRecord, EntityRecord], bool]


class SamePair(BaseModel):
    """The LLM's response schema for one adjudication."""

    same: bool


class Candidate(BaseModel):
    a: str  # entity ids
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


class ObservationSnapshot(BaseModel):
    """An observation touching a merged entity, as it was before the merge."""

    props: dict  # every property, the id and chunk_id included
    subject: str  # entity ids
    object: str


class EntitySnapshot(BaseModel):
    id: str
    props: dict
    mentions: list[str]  # chunk ids


class ResolveReport(BaseModel):
    """Result and audit log of one run. `snapshots` and `observations` are the pre-merge state for
    `undo_merges`."""

    entities_before: int
    entities_after: int
    merges: int
    self_loops_removed: int
    # observations identical in predicate, ends, chunk and quote after a merge (the same statement
    # extracted under two spellings); default 0 so that resolve.json files written before R28 still load
    duplicate_facts_removed: int = 0
    decisions: list[Decision]
    groups: list[MergeGroup] = []
    snapshots: list[EntitySnapshot] = []
    # before R64 the snapshot held fact edges (`facts`); such a file no longer matches the graph's shape
    observations: list[ObservationSnapshot] = []
    passes: int = 1  # rounds of nominate-and-decide (R45); 1 for resolve.json files written before


class MentionRow(BaseModel):
    """One chunk mentioning an entity, as the adjudication context is built from it."""

    entity: str  # entity id
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


def read_entities(driver: Driver) -> list[EntityRecord]:
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) OPTIONAL MATCH (:Chunk)-[m:MENTIONS]->(e) "
        "WITH e, count(m) AS mentions "
        # the tones of the claims ending in this kind; sorted, so the same graph gives the same record
        "WITH e, mentions, apoc.coll.sort(apoc.coll.toSet([(e)<-[:OBJECT]-(o:Observation) "
        "WHERE o.polarity IN ['positive', 'negative'] | o.polarity])) AS polarities "
        "RETURN e.id AS id, e.name AS name, e.type AS type, coalesce(e.aliases, [e.name]) AS aliases, "
        "mentions, polarities ORDER BY id"
    )
    return [EntityRecord(**r.data()) for r in records]


def opposed(a: EntityRecord, b: EntityRecord) -> bool:
    """True when claims use one kind positively and the other negatively: "resistant to scratches" and
    "scratches easily" spell and embed alike, and a merge would turn praise into a complaint (R66)."""
    return ("positive" in a.polarities and "negative" in b.polarities) or (
        "negative" in a.polarities and "positive" in b.polarities
    )


def find_candidates(
    entities: list[EntityRecord],
    borderline: float,
    embedding: Matcher | None = None,
    blocking: Blocking | None = None,
) -> list[Candidate]:
    """All same-type pairs worth a decision: fuzzy score >= `borderline`, or nominated by `blocking` on
    the `embedding` scores (both needed; either None means spelling candidates only).

    Entities of different types are never compared: a Product and a Problem with the same name are
    different things. Within a type every pair is scored; the fuzzy cutoff makes hopeless pairs cheap.
    Numbers are never compared ("25 kg" and "35 kg" are 91 alike by spelling and are different claims),
    and neither are kinds used with opposite polarity (`opposed`).
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
            if opposed(a, b):
                continue
            score = fuzzy.score(a, b, cutoff=borderline)
            if score >= borderline:
                candidates.append(Candidate(a=a.id, b=b.id, score=round(score, 1), signal=fuzzy.name))
            elif (
                embedding is not None and (similarity := by_meaning.get(frozenset((a.id, b.id)))) is not None
            ):
                candidates.append(
                    Candidate(a=a.id, b=b.id, score=round(similarity, 1), signal=embedding.name)
                )
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
) -> list[Candidate]:
    """Step 2 with the meaning-based matcher when it is switched on."""
    return find_candidates(entities, borderline, embedding_matcher(entities, embedder, blocking), blocking)


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
) -> tuple[list[Decision], list[MergeGroup], int]:
    """Steps 2-4, repeated on the merged view until a pass merges nothing: all decisions, the final
    groups (over the original entities, so one snapshot and one merge apply them) and the passes run.

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

        fresh = [c for c in find_candidates(view, borderline, embedding, blocking) if key(c) not in asked]
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


def read_mentions(driver: Driver, ids: list[str]) -> list[MentionRow]:
    """Every chunk mentioning one of `ids`, ordered by entity, document and position in the document, so
    the same graph always builds the same prompts (and hits the LLM cache)."""
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE e.id IN $ids "
        "RETURN e.id AS entity, [e.name] + coalesce(e.aliases, []) AS names, "
        # chunks written before R26 have no context, or an empty one: name them by their document id
        "CASE WHEN coalesce(c.context, '') = '' THEN c.doc_id ELSE c.context END AS document, "
        "c.text AS text "
        "ORDER BY entity, c.doc_id, c.index",
        ids=ids,
    )
    return [MentionRow.model_validate(dict(r)) for r in records]


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


def _llm_adjudicator(
    driver: Driver, llm: LLMClient, model: str, candidates: list[Candidate], members: dict[str, list[str]]
) -> Adjudicator:
    """An adjudicator that shows the LLM both names with the sentences that mention them (for an entity
    merged in an earlier pass, the sentences of all its members)."""
    shown = sorted({i for c in candidates for i in (c.a, c.b)})
    owner = {m: i for i in shown for m in members.get(i, [i])}
    context = mention_lines(read_mentions(driver, sorted(owner)), owner=owner)

    def render(entity: str) -> str:
        return "\n".join(f"- {line}" for line in context.get(entity, [])) or "- (no sentence names it)"

    def adjudicate(a: EntityRecord, b: EntityRecord) -> bool:
        prompt = ADJUDICATE_PROMPT.format(
            etype=a.type, a=a.name, b=b.name, ctx_a=render(a.id), ctx_b=render(b.id)
        )
        return llm.generate(prompt, SamePair, model=model).same

    return adjudicate


def snapshot(driver: Driver, ids: list[str]) -> tuple[list[EntitySnapshot], list[ObservationSnapshot]]:
    """The state of the given entities before a merge: properties, mentions, and every observation with
    one of them as subject or object."""
    nodes, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.id IN $ids OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e) "
        "RETURN e.id AS id, properties(e) AS props, collect(c.chunk_id) AS mentions ORDER BY id",
        ids=ids,
    )
    observations, _, _ = driver.execute_query(
        "MATCH (s:Entity)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Entity) "
        "WHERE s.id IN $ids OR t.id IN $ids "
        "RETURN properties(o) AS props, s.id AS subject, t.id AS object ORDER BY o.id",
        ids=ids,
    )
    return (
        [EntitySnapshot(**n.data()) for n in nodes],
        [ObservationSnapshot(**o.data()) for o in observations],
    )


def apply_merges(driver: Driver, entities: dict[str, EntityRecord], groups: list[MergeGroup]) -> int:
    """Physically merge each group into its canonical entity; all names survive as aliases.

    Returns the number of exact repeats removed afterwards: observations that became identical in
    predicate, ends, chunk and quote (one statement extracted under two spellings). Doubled mentions of
    one chunk are removed as well.
    """
    for group in groups:
        members = [group.canonical, *group.absorbed]
        aliases = sorted({alias for i in members for alias in entities[i].aliases})
        driver.execute_query(
            "MATCH (c:Entity {id: $canonical}) MATCH (o:Entity) WHERE o.id IN $absorbed "
            # aggregate first: a procedure call cannot take collect() as an argument
            "WITH c, collect(o) AS others "
            # 'discard' keeps the canonical's properties. mergeRels stays false: APOC would fold every
            # relationship of one type between the same two nodes into one. Since R64 a claim is a node, so
            # only doubled MENTIONS could fold, and those are removed below; while claims were edges, three
            # reviews stating one fact became one relationship with one quote (found in R25)
            "CALL apoc.refactor.mergeNodes([c] + others, {properties: 'discard', mergeRels: false}) "
            "YIELD node SET node.aliases = $aliases, node.merged_from = $absorbed RETURN count(node)",
            canonical=group.canonical,
            absorbed=group.absorbed,
            aliases=aliases,
        )
    if not groups:
        return 0
    # The repeat rule: same subject, predicate, object, chunk, quote and time = one observation (the thing is
    # the same too, since it follows from the chunk). Three reviews of one claim differ in chunk and stay
    # three; a claim with a time and the same claim without one are two (R66).
    # The repeats can differ in the wording they keep (R44); ordered, the same one survives every rebuild,
    # so the id the judge's verdicts refer to is stable
    repeats, _, _ = driver.execute_query(
        "MATCH (s:Entity)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Entity) "
        "WITH s, t, o ORDER BY o.subject_name, o.object_name, o.id "
        "WITH s, t, o.predicate AS p, o.chunk_id AS chunk, o.evidence AS evidence, "
        "coalesce(o.time, '') AS time, collect(o) AS repeats "
        "WHERE size(repeats) > 1 FOREACH (x IN tail(repeats) | DETACH DELETE x) "
        "RETURN sum(size(repeats) - 1) AS n"
    )
    driver.execute_query(
        "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity) WITH c, e, collect(m) AS repeats "
        "WHERE size(repeats) > 1 FOREACH (x IN tail(repeats) | DELETE x)"
    )
    return repeats[0]["n"] or 0


def resolve_entities(
    driver: Driver,
    llm: LLMClient | None,
    model: str,
    auto_merge: float = 92.0,
    borderline: float = 80.0,
    embedder: Embedder | None = None,
    blocking: Blocking | None = None,
) -> ResolveReport:
    """Run the five steps. With `llm=None`, borderline pairs stay separate (logged as skipped).

    Meaning-based candidates are used only when both an embedder and a `blocking` are given.
    """
    records = read_entities(driver)
    entities = {e.id: e for e in records}
    adjudicator_for: AdjudicatorFor | None = None
    if llm is not None:

        def adjudicator_for(candidates: list[Candidate], members: dict[str, list[str]]) -> Adjudicator:
            return _llm_adjudicator(driver, llm, model, candidates, members)

    embedding = embedding_matcher(records, embedder, blocking)
    decisions, groups, passes = decide_in_passes(
        records, borderline, embedding, blocking, auto_merge, adjudicator_for
    )

    member_ids = [i for g in groups for i in (g.canonical, *g.absorbed)]
    snapshots, observations = snapshot(driver, member_ids) if groups else ([], [])
    duplicates = apply_merges(driver, entities, groups)

    # a merge turns a claim between two duplicates into "X relates to X"; those are noise
    loops, _, _ = driver.execute_query(
        "MATCH (e:Entity)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(e) DETACH DELETE o RETURN count(o) AS n"
    )
    remaining, _, _ = driver.execute_query("MATCH (e:Entity) RETURN count(e) AS n")
    return ResolveReport(
        entities_before=len(records),
        entities_after=remaining[0]["n"],
        merges=sum(len(g.absorbed) for g in groups),
        self_loops_removed=loops[0]["n"],
        duplicate_facts_removed=duplicates,
        decisions=decisions,
        groups=groups,
        snapshots=snapshots,
        observations=observations,
        passes=passes,
    )


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


def preview_candidates(
    driver: Driver,
    auto_merge: float,
    borderline: float,
    embedder: Embedder | None,
    blocking: Blocking | None,
) -> ResolvePreview:
    """Steps 1 and 2 of `resolve_entities` on the current graph. Reads only; no LLM call."""
    records = read_entities(driver)
    return preview(records, nominate(records, borderline, embedder, blocking), auto_merge)


def undo_merges(driver: Driver, report: ResolveReport) -> int:
    """Restore the entities merged by the run that produced `report`. Returns how many came back.

    Meant to follow a `kg resolve` directly: observations written after the merge that touch a canonical
    entity are dropped. REFERS_TO and HAS_OBSERVATION links are derived data; re-run `kg link` afterwards.
    Idempotent.
    """
    if not report.groups:
        return 0
    canonical_ids = [g.canonical for g in report.groups]
    # 1. strip the canonical entities: everything they legitimately had is in the snapshot, including the
    # observations the merge removed as repeats or self-references
    driver.execute_query(
        "MATCH (o:Observation)-[:SUBJECT|OBJECT]->(e:Entity) WHERE e.id IN $ids DETACH DELETE o",
        ids=canonical_ids,
    )
    driver.execute_query(
        "MATCH (:Chunk)-[m:MENTIONS]->(e:Entity) WHERE e.id IN $ids DELETE m", ids=canonical_ids
    )
    # 2. recreate absorbed entities and reset canonical ones (`SET e = props` also drops merged_from)
    driver.execute_query(
        "UNWIND $rows AS r MERGE (e:Entity {id: r.id}) SET e = r.props",
        rows=[s.model_dump() for s in report.snapshots],
    )
    # 3. mentions and observations exactly as they were
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.c}), (e:Entity {id: r.e}) MERGE (c)-[:MENTIONS]->(e)",
        rows=[{"c": c, "e": s.id} for s in report.snapshots for c in s.mentions],
    )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (s:Entity {id: r.subject}), (t:Entity {id: r.object}), "
        "(c:Chunk {chunk_id: r.props.chunk_id}) "
        # same MERGE key as the subject-graph writer, so a second undo cannot duplicate observations
        "MERGE (o:Observation {id: r.props.id}) SET o = r.props "
        "MERGE (o)-[:SUBJECT]->(s) MERGE (o)-[:OBJECT]->(t) MERGE (o)-[:FROM]->(c)",
        rows=[o.model_dump() for o in report.observations],
    )
    return sum(len(g.absorbed) for g in report.groups)
