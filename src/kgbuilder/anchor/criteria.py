"""The code-computed criteria of the anchor-graph evaluation (R90 part b): C2, C5, C7, C8 and C9.

Role in the pipeline: after a build, in `kg anchor-eval` (pipeline/anchor_stages.py). The walks come from
`anchor/navigation.py`, the targets from `anchor/targets.py`, the questions and their evidence from the QA
gold. C0 and C1 are R87's fidelity gate and provenance checks, carried by the stage as they are.
  C2 findability   does W1 on a target's name and aliases return one of its nodes in the top 1 / top k?
  C5 evidence reach  do the walks from a question's starts rank its gold chunks within a budget of k chunks?
                   Two start modes: the gold nodes (the graph alone) and W1's best hit per target (end to end)
  C7 selectivity   how many chunks does a node W1 returns lead to (W2), as a share of the corpus? hubs listed
  C8 connectivity  do the walks from a multi-hop question's starts reach its gold records and chunks, and is
                   every thing-to-thing hop of the arm witnessed?
  C9 size and cost nodes and edges per chunk, and the build's logged cost
Design: pure functions of an `AnchorGraph` and data; every rate is a `Proportion` (k, n, Wilson interval) and
every per-question outcome is kept, so two arms can be compared question by question. The ranking rule of
C5 is the direction's, fixed before measuring: more distinct targets reaching a chunk first, then the
shorter walk, then the chunk id.
Must not: call an LLM or judge meaning (C3, C4, C6 are the judge's, a later step).
"""

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..audit.snapshot import GraphSnapshot
from ..validation.interval import Proportion
from ..validation.qa_gold import QAGold, QuestionType
from .navigation import AnchorGraph, Arm
from .targets import PlacedTarget

Placed = Mapping[str, Sequence[PlacedTarget]]  # question id -> its targets
# the W1 hits per target C7 looks at: what a reader would see of a lookup (the same 5 as C2's hit@5)
_SHOWN = 5


class TargetFind(BaseModel):
    """C2 for one target: its rank among W1's hits (1-based), or None when no hit is one of its nodes."""

    question: str
    name: str
    nodes: list[str]
    rank: int | None
    top: list[str]  # W1's first hits, as a reader would see them


class Findability(BaseModel):
    """C2: hit@1 and hit@k over every gold target; a target without a node in the build counts as a miss."""

    targets: list[TargetFind]
    unplaced: int  # targets with no node in this build (the graph lacks the thing)
    hit_at: dict[int, Proportion]


def findability(graph: AnchorGraph, placed: Placed, ks: Sequence[int] = (1, _SHOWN)) -> Findability:
    rows = []
    for qid, targets in placed.items():
        for t in targets:
            found = [f.node for f in graph.find([t.name, *t.aliases])]
            rank = next((i + 1 for i, node in enumerate(found) if node in t.nodes), None)
            rows.append(TargetFind(question=qid, name=t.name, nodes=t.nodes, rank=rank, top=found[:_SHOWN]))
    return Findability(
        targets=rows,
        unplaced=sum(not r.nodes for r in rows),
        hit_at={
            k: Proportion.of(sum(r.rank is not None and r.rank <= k for r in rows), len(rows)) for k in ks
        },
    )


class QuestionReach(BaseModel):
    """C5 for one question: its gold chunks, the starts used, the ranked chunks up to the largest budget,
    and how many gold chunks each budget holds."""

    question: str
    type: QuestionType
    gold: list[str]
    starts: list[str]
    ranked: list[str]
    hits: dict[int, int]  # budget -> gold chunks within it
    reached: int  # gold chunks reached at any walk length (no budget)

    def complete(self, k: int) -> bool:
        return self.hits[k] == len(self.gold)


class EvidenceReach(BaseModel):
    """C5 in one start mode: recall of gold chunks within each budget (micro over chunks), the questions
    whose every gold chunk fits the budget (the per-question outcome two arms are paired on), and the
    unbudgeted reach, which only shows that nothing is missing."""

    mode: str  # "gold_start" (the targets' nodes) or "end_to_end" (W1's best hit per target)
    questions: list[QuestionReach]
    no_start: list[str]  # questions with gold chunks but no target at all: left out
    recall_at: dict[int, Proportion]
    complete_at: dict[int, Proportion]
    reached: Proportion


def rank_chunks(walks: Sequence[Mapping[str, int]]) -> list[str]:
    """The direction's ranking rule over one walk per target: chunks reached by more distinct targets
    first, then by the shortest walk any target needs, then by chunk id."""
    count: dict[str, int] = defaultdict(int)
    length: dict[str, int] = {}
    for walk in walks:
        for chunk, n in walk.items():
            count[chunk] += 1
            length[chunk] = min(n, length.get(chunk, n))
    return sorted(count, key=lambda c: (-count[c], length[c], c))


def evidence_reach(
    graph: AnchorGraph, qa: QAGold, placed: Placed, budgets: Sequence[int], mode: str
) -> EvidenceReach:
    """C5 over the questions with chunk evidence. In `gold_start` mode a target starts from its gold nodes
    (none when the build lacks them); in `end_to_end` mode from W1's best hit on its name and aliases."""
    rows, no_start = [], []
    for q in qa.questions:
        gold = sorted({c.chunk_id for c in q.chunks})
        if not gold:
            continue
        targets = placed.get(q.id, [])
        if not targets:
            no_start.append(q.id)
            continue
        starts = [_starts(graph, t, mode) for t in targets]
        walks = [graph.walk(s) for s in starts]
        ranked = rank_chunks(walks)
        reached = set().union(*walks)
        rows.append(
            QuestionReach(
                question=q.id,
                type=q.type,
                gold=gold,
                starts=sorted({n for s in starts for n in s}),
                ranked=ranked[: max(budgets)],
                hits={k: len(set(ranked[:k]) & set(gold)) for k in budgets},
                reached=len(reached & set(gold)),
            )
        )
    chunks = sum(len(r.gold) for r in rows)
    return EvidenceReach(
        mode=mode,
        questions=rows,
        no_start=no_start,
        recall_at={k: Proportion.of(sum(r.hits[k] for r in rows), chunks) for k in budgets},
        complete_at={k: Proportion.of(sum(r.complete(k) for r in rows), len(rows)) for k in budgets},
        reached=Proportion.of(sum(r.reached for r in rows), chunks),
    )


def _starts(graph: AnchorGraph, target: PlacedTarget, mode: str) -> list[str]:
    if mode == "gold_start":
        return list(target.nodes)
    found = graph.find([target.name, *target.aliases])
    return [found[0].node] if found else []


class Hub(BaseModel):
    node: str
    chunks: int
    share: float


class Selectivity(BaseModel):
    """C7: chunks per node W1 shows for a gold name, as a share of the corpus; nodes above the hub line."""

    nodes: int
    corpus: int
    median_share: float | None
    p90_share: float | None
    hubs: list[Hub]


def selectivity(graph: AnchorGraph, placed: Placed, hub_share: float) -> Selectivity:
    shown = {
        f.node
        for targets in placed.values()
        for t in targets
        for f in graph.find([t.name, *t.aliases])[:_SHOWN]
    }
    corpus = len(graph.chunk_ids)
    sizes = {node: len(graph.chunks_of(node)) for node in sorted(shown)}
    shares = sorted(n / corpus for n in sizes.values())
    return Selectivity(
        nodes=len(shares),
        corpus=corpus,
        median_share=statistics.median(shares) if shares else None,
        # nearest rank: the smallest share at or above which 90 % of the nodes lie
        p90_share=shares[math.ceil(0.9 * len(shares)) - 1] if shares else None,
        hubs=sorted(
            (Hub(node=n, chunks=c, share=c / corpus) for n, c in sizes.items() if c / corpus > hub_share),
            key=lambda h: (-h.share, h.node),
        ),
    )


class QuestionConnections(BaseModel):
    """C8 for one multi-hop question: its gold records and chunks, and those the walks did not reach."""

    question: str
    gold: list[str]
    unreached: list[str]


class Connectivity(BaseModel):
    """C8: recall of gold connections from the multi-hop questions' starts (no budget), and the witness
    rule over every thing-to-thing hop of the arm (hard: none unwitnessed)."""

    questions: list[QuestionConnections]
    connections: Proportion
    thing_hops: int
    unwitnessed_hops: int
    unwitnessed_examples: list[str]


def connectivity(
    graph: AnchorGraph, qa: QAGold, placed: Placed, record_nodes: Mapping[str, Sequence[str]]
) -> Connectivity:
    """`record_nodes` gives the record nodes of each question's record evidence (by question id): rows of a
    relationship table have none and give no connection. A gold record that is itself a start is no
    connection either."""
    rows = []
    for q in qa.questions:
        targets = placed.get(q.id, [])
        if q.type != QuestionType.MULTI_HOP or not targets:
            continue
        starts = {n for t in targets for n in t.nodes}
        gold = sorted(({c.chunk_id for c in q.chunks} | set(record_nodes.get(q.id, ()))) - starts)
        if not gold:
            continue
        reached = set(graph.walk(starts)) | graph.reached_nodes(starts)
        rows.append(
            QuestionConnections(question=q.id, gold=gold, unreached=[g for g in gold if g not in reached])
        )
    edges = graph.thing_edges()
    unwitnessed = [e for e in edges if not e.witnessed]
    return Connectivity(
        questions=rows,
        connections=Proportion.of(
            sum(len(r.gold) - len(r.unreached) for r in rows), sum(len(r.gold) for r in rows)
        ),
        thing_hops=len(edges),
        unwitnessed_hops=len(unwitnessed),
        unwitnessed_examples=[f"{e.a} -{e.how}- {e.b} ({e.chunk})" for e in unwitnessed[:10]],
    )


class Size(BaseModel):
    """C9: the arm's graph per chunk, and the build's logged cost (every stage of the logged runs)."""

    chunks: int
    nodes: int
    edges: int
    nodes_per_chunk: float
    edges_per_chunk: float
    cost_usd: float | None  # None when the logged counts carry no usage
    tokens: float | None


def size(s: GraphSnapshot, arm: Arm, usage: Mapping[str, float]) -> Size:
    """Nodes: records, individuals and concepts, mentions, chunks, documents (arm B: and claims). Edges:
    record relations, MENTIONS, REFERS_TO, ABOUT, PART_OF, NEXT_CHUNK (arm B: and HAS_OBSERVATION, FROM,
    SUBJECT, OBJECT): the labels each arm walks, as the build wrote them."""
    documents = {c.doc_id for c in s.chunks}
    canonicals = {a.canonical for a in s.references if a.kind != "record"}
    nodes = len(s.records) + len(canonicals) + len(s.mentions) + len(s.chunks) + len(documents)
    edges = (
        len(s.relations)
        + sum(len(m.chunks) for m in s.mentions)
        + len(s.references)
        + len(s.documents_about)
        + len(s.sections_about)
        + len(s.chunks)  # PART_OF
        + len(s.chunks)
        - len(documents)  # NEXT_CHUNK joins the chunks of a document in a row
    )
    if arm is Arm.LAYERED:
        nodes += len(s.claims)
        edges += len(s.attachments) + 3 * len(s.claims)  # FROM, SUBJECT, OBJECT per claim
    cost = [v for k, v in usage.items() if k.endswith(".cost_usd")]
    tokens = [v for k, v in usage.items() if k.endswith("_tokens")]
    return Size(
        chunks=len(s.chunks),
        nodes=nodes,
        edges=edges,
        nodes_per_chunk=nodes / len(s.chunks),
        edges_per_chunk=edges / len(s.chunks),
        cost_usd=sum(cost) if cost else None,
        tokens=sum(tokens) if tokens else None,
    )
