"""Entity resolution: merge duplicate Entity nodes of the same type.

Exact/near-exact names merge automatically; borderline pairs go to the LLM when one is available;
everything else stays separate. Every decision is logged."""

from itertools import combinations

from neo4j import Driver
from pydantic import BaseModel
from rapidfuzz import fuzz

from .core.text import norm
from .llm.base import LLMClient

ADJUDICATE_PROMPT = """Do these two names, both of type {etype}, refer to the same real-world thing?
Different sizes, models, components or people are NOT the same. Answer conservatively.

A: {a}
B: {b}

Context for A: {ctx_a}
Context for B: {ctx_b}"""


class SamePair(BaseModel):
    same: bool


class Decision(BaseModel):
    a: str
    b: str
    type: str
    score: float
    action: str  # auto | llm_merge | llm_keep | skipped_borderline


class ResolveReport(BaseModel):
    entities_before: int
    entities_after: int
    merges: int
    self_loops_removed: int
    decisions: list[Decision]


def _find(parent: dict, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def resolve_entities(
    driver: Driver,
    llm: LLMClient | None,
    model: str,
    auto_merge: float = 92.0,
    borderline: float = 80.0,
) -> ResolveReport:
    """Merge duplicate entities. With `llm=None`, borderline pairs stay separate (logged as skipped)."""
    rows, _, _ = driver.execute_query(
        "MATCH (e:Entity) OPTIONAL MATCH (:Chunk)-[m:MENTIONS]->(e) "
        "RETURN e.id AS id, e.name AS name, e.type AS type, coalesce(e.aliases, [e.name]) AS aliases, count(m) AS n"
    )
    ents = {r["id"]: dict(r) for r in rows}
    parent = {i: i for i in ents}
    decisions: list[Decision] = []

    by_type: dict[str, list[str]] = {}
    for i, e in ents.items():
        by_type.setdefault(e["type"], []).append(i)

    for etype, ids in by_type.items():
        for x, y in combinations(ids, 2):
            a, b = ents[x], ents[y]
            score = fuzz.token_sort_ratio(norm(a["name"]), norm(b["name"]))
            if score < borderline:
                continue
            if score >= auto_merge:
                action = "auto"
            elif llm is not None:
                verdict = llm.generate(
                    ADJUDICATE_PROMPT.format(
                        etype=etype,
                        a=a["name"],
                        b=b["name"],
                        ctx_a=_context(driver, x),
                        ctx_b=_context(driver, y),
                    ),
                    SamePair,
                    model=model,
                )
                action = "llm_merge" if verdict.same else "llm_keep"
            else:
                action = "skipped_borderline"
            decisions.append(
                Decision(a=a["name"], b=b["name"], type=etype, score=round(score, 1), action=action)
            )
            if action in ("auto", "llm_merge"):
                parent[_find(parent, y)] = _find(parent, x)

    groups: dict[str, list[str]] = {}
    for i in ents:
        groups.setdefault(_find(parent, i), []).append(i)

    merges = 0
    for members in (g for g in groups.values() if len(g) > 1):
        members.sort(key=lambda i: (-ents[i]["n"], -len(ents[i]["name"]), i))
        canonical, others = members[0], members[1:]
        aliases = sorted({a for i in members for a in ents[i]["aliases"]})
        driver.execute_query(
            "MATCH (c:Entity {id: $c}) MATCH (o:Entity) WHERE o.id IN $others "
            "CALL apoc.refactor.mergeNodes([c] + collect(o), {properties: 'discard', mergeRels: true}) "
            "YIELD node SET node.aliases = $aliases, node.merged_from = $others "
            "RETURN count(node)",
            c=canonical,
            others=others,
            aliases=aliases,
        )
        merges += len(others)

    # a merge can turn a fact between two duplicates into a self-loop; those are noise
    loops, _, _ = driver.execute_query(
        "MATCH (e:Entity)-[r]->(e) WHERE type(r) <> 'MENTIONS' DELETE r RETURN count(r) AS n"
    )
    remaining, _, _ = driver.execute_query("MATCH (e:Entity) RETURN count(e) AS n")
    return ResolveReport(
        entities_before=len(ents),
        entities_after=remaining[0]["n"],
        merges=merges,
        self_loops_removed=loops[0]["n"] if loops else 0,
        decisions=decisions,
    )


def _context(driver: Driver, entity_id: str) -> str:
    rows, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[:MENTIONS]->(:Entity {id: $id}) RETURN c.text AS t LIMIT 1", id=entity_id
    )
    return rows[0]["t"][:300] if rows else "(none)"
