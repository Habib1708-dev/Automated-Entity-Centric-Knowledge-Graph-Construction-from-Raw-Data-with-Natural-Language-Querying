"""The passage grid's stage (R197): `kg passage-grid`, plan R197-R198's experiment on whether a model's order
of `search_text`'s candidates shows more gold passages than RRF's, and plan R200-R201's on whether a local
cross-encoder orders them as well as the LLM.

Role in the pipeline: on a loaded graph, without any reader or agent. Every question of the given QA gold
files that cites evidence passages is the query, as `search_text` would get it: embedded once, its dense and
lexical chunk lists read `DEPTH` deep from the graph (the agent's own retrievers), each reranked method's
pool read whole from the store and ordered by its kind's reranker (the LLM's, hybrid/passage_rerank.py, for
`rerank`; the cross-encoder's, hybrid/cross_rerank.py, for `cross`), then every method of the grid scored
(validation/passage_grid.py).
Design: wiring and logging only, like seed_stages.py. One MLflow run (`passage_grid`): params name every gold
file with its hash, the graph digest, the grid, the fusion constant, the budget, the embedding model, the
LLM reranker's model, settings and version, and the cross-encoder's model, device and version; metrics are
every method's scores, the LLM reranker's slips, each reranker's seconds and the embedding volume; artifacts
are the report with every question's shown passages, the table, every reranked order and the prompt.
Questions are embedded one at a time: the Gemini embedder has no retry (retrieval_stages.py). A question
worded alike in two gold files is asked once.
Not here: fusing, reranking, scoring (hybrid/, validation/); loading the cross-encoder (the composition root).
"""

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..core.errors import ConfigurationError
from ..hybrid.cross_rerank import CrossEncoderReranker
from ..hybrid.passage_rerank import PROMPT as RERANK_PROMPT
from ..hybrid.passage_rerank import PassageReranker
from ..hybrid.retrievers import ChunkDense, ChunkLexical, Retriever
from ..hybrid.seed_rerank import Reranked, RerankOptions
from ..llm.base import Embedder
from ..llm.counting import CountingEmbedder
from ..query.graph_store import GraphStore, StoredChunk
from ..validation.passage_grid import (
    BASELINE,
    DEPTH,
    GRID,
    RERANKED,
    SHOWN,
    Kind,
    MethodScore,
    PassageLists,
    PassageOutcome,
    markdown,
    metrics,
    outcome,
    pool,
    score,
)
from ..validation.qa_gold import QAQuestion, load_qa_gold
from .inputs import digest, input_file
from .qa_graph import check_graph
from .qa_systems import source_parts
from .stages import BaseStage


class PoolReranker(Protocol):
    """What the grid needs of a reranker: an order of every pool (`PassageReranker`,
    `CrossEncoderReranker`)."""

    def rerank_all(self, pools: Mapping[str, tuple[str, Sequence[StoredChunk]]]) -> dict[str, Reranked]: ...


class PassageGridReport(BaseModel):
    """The grid's result: every method's score and every question's outcome."""

    scores: list[MethodScore]
    outcomes: list[PassageOutcome]


class PassageGridStage(BaseStage):
    """Read, rerank and score every gold question's passages (`kg passage-grid`)."""

    name = "passage_grid"
    REPORT_FILE = "passage_grid.json"
    MARKDOWN_FILE = "passage_grid.md"
    RERANK_FILE = "passage_rerank.jsonl"

    def __init__(self, golds: Sequence[Path], cross: CrossEncoderReranker, cross_details: Mapping[str, str]):
        """`cross` orders the `cross` methods' pools; `cross_details` (its model and device) are logged.
        Raises `ConfigurationError` for no gold file."""
        if not golds:
            raise ConfigurationError("kg passage-grid needs at least one QA gold file")
        self.golds = list(golds)
        self.cross = cross
        self.cross_details = dict(cross_details)
        self.report: PassageGridReport | None = None  # set by `run`, for the command to print

    def params(self, ctx, state):
        s = ctx.settings
        out: dict[str, object] = {
            "grid": [m.name for m in GRID],
            "baseline": BASELINE,
            "shown": SHOWN,
            "depth": DEPTH,
            "rrf_k": s.hybrid_rrf_k,
            "embed_model": s.embed_model,
            "rerank_version": PassageReranker.version,
            **{f"rerank_{k}": v for k, v in _options(ctx).model_dump(mode="json").items()},
            **{f"cross_{k}": v for k, v in self.cross_details.items()},
            "cross_version": self.cross.version,
        }
        for n, path in enumerate(self.golds, start=1):
            out.update({f"gold_{n}": str(path), f"gold_{n}_hash": digest(input_file(path, "QA gold file"))})
        return out

    def run(self, ctx, state, run):
        questions = []
        for path in self.golds:
            gold = load_qa_gold(path)
            # a graph without the gold's evidence is refused before any embedding call, so it costs nothing
            run.params(graph_digest=check_graph(ctx.driver, gold).value)
            questions += [(f"{path.stem}/{q.id}", q) for q in gold.questions if q.chunks]
        parts = source_parts(ctx, state)
        embedder = CountingEmbedder(parts.embedder)
        lists = read_lists(questions, ChunkDense(parts.store), ChunkLexical(parts.units), embedder)
        run.metrics(questions_read=len(lists), embedded_texts=embedder.texts, embedded_chars=embedder.chars)
        rerankers: dict[Kind, PoolReranker] = {
            "rerank": PassageReranker(ctx.require_llm(), _options(ctx)),
            "cross": self.cross,
        }
        self.report, orders, seconds = run_grid(lists, parts.store, rerankers, ctx.settings.hybrid_rrf_k)
        llm_orders = [r for key, r in orders.items() if _kind(key) == "rerank"]
        run.metrics(**metrics(self.report.scores), **_slips(llm_orders))
        run.metrics(**{f"{kind}_seconds": s for kind, s in seconds.items()})
        run.artifact(ctx.write(self.REPORT_FILE, self.report.model_dump_json(indent=1)))
        run.artifact(ctx.write(self.MARKDOWN_FILE, markdown(self.report.scores)))
        lines = [json.dumps({"key": k, **r.model_dump()}) for k, r in orders.items()]
        run.artifact(ctx.write(self.RERANK_FILE, "\n".join(lines) + "\n"))
        run.text(RERANK_PROMPT, "prompts/passage_rerank.txt")


def read_lists(
    questions: Sequence[tuple[str, QAQuestion]], dense: Retriever, lexical: Retriever, embedder: Embedder
) -> list[PassageLists]:
    """Each (key, question) with its dense and lexical chunk lists read `DEPTH` deep; a question worded like
    an earlier one is asked once, under the earlier key. One embedding call per question."""
    seen: set[str] = set()
    lists = []
    for key, q in questions:
        if q.question in seen:
            continue
        seen.add(q.question)
        vector = embedder.embed([q.question])[0]
        lists.append(
            PassageLists(
                key=key,
                question=q.question,
                gold=sorted({c.chunk_id for c in q.chunks}),
                dense=dense.retrieve(q.question, vector, DEPTH).chunks,
                lexical=lexical.retrieve(q.question, vector, DEPTH).chunks,
            )
        )
    return lists


def run_grid(
    lists: Sequence[PassageLists], store: GraphStore, rerankers: Mapping[Kind, PoolReranker], rrf_k: int
) -> tuple[PassageGridReport, dict[str, Reranked], dict[Kind, float]]:
    """Every method of the grid on every question: each reranked method's pool read whole from `store` and
    ordered by its kind's reranker in `rerankers` (one order per pool, keyed `<method>|<question key>`), then
    scored. Also returns each reranker's wall-clock seconds. Raises `KeyError` for a kind without a
    reranker."""
    reranked = [m for m in GRID if m.kind in RERANKED]
    orders: dict[str, Reranked] = {}
    seconds: dict[Kind, float] = {}
    for kind in RERANKED:
        pools = {
            f"{m.name}|{q.key}": (q.question, store.chunks(pool(m, q, rrf_k)))
            for m in reranked
            if m.kind == kind
            for q in lists
        }
        started = time.perf_counter()
        orders |= rerankers[kind].rerank_all(pools)
        seconds[kind] = time.perf_counter() - started
    outcomes = [
        outcome(q, rrf_k, {m.name: orders[f"{m.name}|{q.key}"].order for m in reranked}, GRID) for q in lists
    ]
    return PassageGridReport(scores=score(outcomes, GRID), outcomes=outcomes), orders, seconds


def _options(ctx) -> RerankOptions:
    """The reranker's model and settings: R130's reranker's (the summary model), so the two rerankers of the
    thesis read with one model."""
    s = ctx.settings
    return RerankOptions(
        model=s.index_summary_model, temperature=s.llm_temperature, thinking=s.index_summary_thinking
    )


def _kind(key: str) -> Kind:
    """The kind of the method an order's key (`<method>|<question key>`) names."""
    name = key.split("|", 1)[0]
    return next(m.kind for m in GRID if m.name == name)


def _slips(orders) -> dict[str, int]:
    """How many pools the LLM reranked, and how far its replies were off in total (a cross-encoder's order
    cannot be off)."""
    every: list[Reranked] = list(orders)
    return {
        "rerank_pools": len(every),
        "rerank_dropped": sum(r.dropped for r in every),
        "rerank_repeated": sum(r.repeated for r in every),
        "rerank_missing": sum(r.missing for r in every),
    }
