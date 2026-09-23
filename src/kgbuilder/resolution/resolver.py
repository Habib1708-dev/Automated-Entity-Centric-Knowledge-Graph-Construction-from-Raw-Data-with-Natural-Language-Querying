"""Entity resolution: find and merge duplicate `:Entity` nodes of the same type, reversibly.

Role in the pipeline: `kg resolve`, after extraction and before linking.
Design: five small steps instead of one function, and only the first and the last two touch Neo4j:
  1. read_entities      load entities and their mention counts
  2. find_candidates    pure: score all same-type pairs with the matchers (Strategy, see matchers.py)
  3. decide             pure given an adjudicator: auto-merge, ask the LLM (in parallel), or skip
  4. group_merges       pure: union-find over the merge decisions, pick a canonical entity per group
  5. apply_merges       snapshot the members, then merge them with APOC (relationships are moved, never
                        folded: three reviews stating one fact stay three facts; only exact repeats go)
Every decision is logged, and the snapshot taken in step 5 is enough for `undo_merges` to restore the
graph, because a wrong merge silently corrupts every later query. `preview_candidates` runs steps 1-2
only (`kg resolve --preview`), to choose the thresholds from real scores.
Not here: similarity functions (matchers.py) and links to the domain graph (linking.py).
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Literal

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..llm.base import Embedder, LLMClient
from .matchers import EmbeddingMatcher, EntityRecord, FuzzyNameMatcher, Matcher

# Conservative on purpose: a false merge destroys information, a missed merge only leaves a duplicate.
ADJUDICATE_PROMPT = """Do these two names, both of type {etype}, refer to the same real-world thing?
Different sizes, models, components or people are NOT the same. Answer conservatively.

A: {a}
B: {b}

Context for A: {ctx_a}
Context for B: {ctx_b}"""

_CONTEXT_CHARS = 300  # enough of one mentioning chunk to disambiguate, small enough to keep calls cheap
_ADJUDICATION_WORKERS = 8

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


class FactSnapshot(BaseModel):
    type: str
    source: str
    target: str
    props: dict


class EntitySnapshot(BaseModel):
    id: str
    props: dict
    mentions: list[str]  # chunk ids


class ResolveReport(BaseModel):
    """Result and audit log of one run. `snapshots` and `facts` are the pre-merge state for `undo_merges`."""

    entities_before: int
    entities_after: int
    merges: int
    self_loops_removed: int
    # facts identical in type, ends, chunk and quote after a merge (the same statement extracted under
    # two spellings); default 0 so that resolve.json files written before R28 still load for --undo
    duplicate_facts_removed: int = 0
    decisions: list[Decision]
    groups: list[MergeGroup] = []
    snapshots: list[EntitySnapshot] = []
    facts: list[FactSnapshot] = []


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
        "RETURN e.id AS id, e.name AS name, e.type AS type, coalesce(e.aliases, [e.name]) AS aliases, "
        "count(m) AS mentions ORDER BY id"
    )
    return [EntityRecord(**r.data()) for r in records]


def find_candidates(
    entities: list[EntityRecord],
    borderline: float,
    embedding: Matcher | None = None,
    embedding_threshold: float = 0.0,
) -> list[Candidate]:
    """All same-type pairs worth a decision: fuzzy score >= `borderline`, or nominated by embeddings.

    Entities of different types are never compared: a Product and a Problem with the same name are
    different things. Within a type every pair is scored; the fuzzy cutoff makes hopeless pairs cheap.
    """
    fuzzy = FuzzyNameMatcher()
    by_type: dict[str, list[EntityRecord]] = {}
    for entity in entities:
        by_type.setdefault(entity.type, []).append(entity)

    candidates = []
    for members in by_type.values():
        for a, b in combinations(members, 2):
            score = fuzzy.score(a, b, cutoff=borderline)
            if score >= borderline:
                candidates.append(Candidate(a=a.id, b=b.id, score=round(score, 1), signal=fuzzy.name))
            elif embedding is not None and (similarity := embedding.score(a, b)) >= embedding_threshold:
                candidates.append(
                    Candidate(a=a.id, b=b.id, score=round(similarity, 1), signal=embedding.name)
                )
    return candidates


def nominate(
    entities: list[EntityRecord],
    borderline: float,
    embedder: Embedder | None,
    embedding_threshold: float,
) -> list[Candidate]:
    """Step 2 with the meaning-based matcher switched on when an embedder is given and
    `embedding_threshold` > 0 (one batched embedding call for all names)."""
    use_embeddings = embedder is not None and embedding_threshold > 0
    embedding = EmbeddingMatcher(embedder, entities) if use_embeddings else None
    return find_candidates(entities, borderline, embedding, embedding_threshold)


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
            # canonical: most mentioned, then the longest (most specific) name, then id for determinism
            members.sort(key=lambda i: (-entities[i].mentions, -len(entities[i].name), i))
            groups.append(MergeGroup(canonical=members[0], absorbed=members[1:]))
    return groups


def _llm_adjudicator(driver: Driver, llm: LLMClient, model: str, candidates: list[Candidate]) -> Adjudicator:
    """An adjudicator that shows the LLM both names plus one mentioning chunk each."""
    ids = sorted({i for c in candidates for i in (c.a, c.b)})
    records, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE e.id IN $ids "
        "RETURN e.id AS id, head(collect(c.text)) AS text",
        ids=ids,
    )
    context = {r["id"]: r["text"][:_CONTEXT_CHARS] for r in records}

    def adjudicate(a: EntityRecord, b: EntityRecord) -> bool:
        prompt = ADJUDICATE_PROMPT.format(
            etype=a.type,
            a=a.name,
            b=b.name,
            ctx_a=context.get(a.id, "(none)"),
            ctx_b=context.get(b.id, "(none)"),
        )
        return llm.generate(prompt, SamePair, model=model).same

    return adjudicate


def snapshot(driver: Driver, ids: list[str]) -> tuple[list[EntitySnapshot], list[FactSnapshot]]:
    """The state of the given entities before a merge: properties, mentions, and every fact touching them."""
    nodes, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.id IN $ids OPTIONAL MATCH (c:Chunk)-[:MENTIONS]->(e) "
        "RETURN e.id AS id, properties(e) AS props, collect(c.chunk_id) AS mentions ORDER BY id",
        ids=ids,
    )
    facts, _, _ = driver.execute_query(
        "MATCH (s:Entity)-[r]->(o:Entity) WHERE s.id IN $ids OR o.id IN $ids "
        "RETURN type(r) AS type, s.id AS source, o.id AS target, properties(r) AS props",
        ids=ids,
    )
    return [EntitySnapshot(**n.data()) for n in nodes], [FactSnapshot(**f.data()) for f in facts]


def apply_merges(driver: Driver, entities: dict[str, EntityRecord], groups: list[MergeGroup]) -> int:
    """Physically merge each group into its canonical entity; all names survive as aliases.

    Returns the number of exact repeats removed afterwards: facts that became identical in type, ends,
    chunk and quote (one statement extracted under two spellings), and doubled mentions of one chunk.
    """
    for group in groups:
        members = [group.canonical, *group.absorbed]
        aliases = sorted({alias for i in members for alias in entities[i].aliases})
        driver.execute_query(
            "MATCH (c:Entity {id: $canonical}) MATCH (o:Entity) WHERE o.id IN $absorbed "
            # aggregate first: a procedure call cannot take collect() as an argument
            "WITH c, collect(o) AS others "
            # 'discard' keeps the canonical's properties. mergeRels stays false: with true, APOC folds every
            # relationship of one type between the same two nodes into one, and three reviews stating the
            # same fact (three chunk ids, three quotes) became one relationship with one quote (found in R25)
            "CALL apoc.refactor.mergeNodes([c] + others, {properties: 'discard', mergeRels: false}) "
            "YIELD node SET node.aliases = $aliases, node.merged_from = $absorbed RETURN count(node)",
            canonical=group.canonical,
            absorbed=group.absorbed,
            aliases=aliases,
        )
    if not groups:
        return 0
    # the subject-graph writer's MERGE key, applied after the fact: same statement, same evidence = one fact
    facts, _, _ = driver.execute_query(
        "MATCH (s:Entity)-[r]->(o:Entity) "
        "WITH s, o, type(r) AS t, r.chunk_id AS chunk, r.evidence AS evidence, collect(r) AS repeats "
        "WHERE size(repeats) > 1 FOREACH (x IN tail(repeats) | DELETE x) RETURN sum(size(repeats) - 1) AS n"
    )
    driver.execute_query(
        "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity) WITH c, e, collect(m) AS repeats "
        "WHERE size(repeats) > 1 FOREACH (x IN tail(repeats) | DELETE x)"
    )
    return facts[0]["n"] or 0


def resolve_entities(
    driver: Driver,
    llm: LLMClient | None,
    model: str,
    auto_merge: float = 92.0,
    borderline: float = 80.0,
    embedder: Embedder | None = None,
    embedding_threshold: float = 0.0,
) -> ResolveReport:
    """Run the five steps. With `llm=None`, borderline pairs stay separate (logged as skipped).

    Embedding candidates are used only when an embedder is given and `embedding_threshold` > 0.
    """
    records = read_entities(driver)
    entities = {e.id: e for e in records}
    candidates = nominate(records, borderline, embedder, embedding_threshold)
    adjudicate = _llm_adjudicator(driver, llm, model, candidates) if llm is not None else None
    decisions = decide(candidates, entities, auto_merge, adjudicate)
    groups = group_merges(entities, decisions)

    member_ids = [i for g in groups for i in (g.canonical, *g.absorbed)]
    snapshots, facts = snapshot(driver, member_ids) if groups else ([], [])
    duplicates = apply_merges(driver, entities, groups)

    # a merge turns a fact between two duplicates into a self-loop; those are noise
    loops, _, _ = driver.execute_query(
        "MATCH (e:Entity)-[r]->(e) WHERE type(r) <> 'MENTIONS' DELETE r RETURN count(r) AS n"
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
        facts=facts,
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
    embedding_threshold: float,
) -> ResolvePreview:
    """Steps 1 and 2 of `resolve_entities` on the current graph. Reads only; no LLM call."""
    records = read_entities(driver)
    return preview(records, nominate(records, borderline, embedder, embedding_threshold), auto_merge)


def undo_merges(driver: Driver, report: ResolveReport) -> int:
    """Restore the entities merged by the run that produced `report`. Returns how many came back.

    Meant to follow a `kg resolve` directly: facts extracted after the merge are dropped from the
    canonical entities. REFERS_TO links are derived data; re-run `kg link` afterwards. Idempotent.
    """
    if not report.groups:
        return 0
    canonical_ids = [g.canonical for g in report.groups]
    # 1. strip the canonical entities: everything they legitimately had is in the snapshot
    driver.execute_query(
        "MATCH (e:Entity) WHERE e.id IN $ids OPTIONAL MATCH (e)-[r]-(:Entity) DELETE r", ids=canonical_ids
    )
    driver.execute_query(
        "MATCH (:Chunk)-[m:MENTIONS]->(e:Entity) WHERE e.id IN $ids DELETE m", ids=canonical_ids
    )
    # 2. recreate absorbed entities and reset canonical ones (`SET e = props` also drops merged_from)
    driver.execute_query(
        "UNWIND $rows AS r MERGE (e:Entity {id: r.id}) SET e = r.props",
        rows=[s.model_dump() for s in report.snapshots],
    )
    # 3. mentions and facts exactly as they were
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.c}), (e:Entity {id: r.e}) MERGE (c)-[:MENTIONS]->(e)",
        rows=[{"c": c, "e": s.id} for s in report.snapshots for c in s.mentions],
    )
    facts_by_type: dict[str, list[dict]] = {}
    for fact in report.facts:
        facts_by_type.setdefault(fact.type, []).append(fact.model_dump())
    for fact_type, rows in facts_by_type.items():
        driver.execute_query(
            "UNWIND $rows AS r MATCH (s:Entity {id: r.source}), (o:Entity {id: r.target}) "
            # same MERGE key as the subject-graph writer, so a second undo cannot duplicate facts
            f"MERGE (s)-[f:{cypher_ident(fact_type)} "
            "{chunk_id: r.props.chunk_id, evidence: r.props.evidence}]->(o) SET f = r.props",
            rows=rows,
        )
    return sum(len(g.absorbed) for g in report.groups)
