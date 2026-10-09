"""The seed grid of plan R130-R136 (R130): every pre-registered way to fuse a question's dense and lexical
card lists, scored on start nodes at every budget K, per dataset and pooled.

Role in the pipeline: `kg seed-grid` (pipeline/seed_stages.py) reads R128's saved card reports (each holds
every question's top 50 seeds and its targets as placed on the build), fuses them here with every setting of
`GRID`, has the `rerank` settings' pools ordered by the reranker, and lets seed_choice.py apply the plan's
rule. No graph and no embedding: the lists are R128's.
Design: pure, over `RetrievalReport`s. A setting's report is the dense report's outcome rows with the fused
seeds in place of the dense ones, rescored by `score_retrieval` at `BUDGETS`, so a fused rate is computed
exactly as R128's are. The rows keep no chunks (`ranked` and `gold_chunks` empty): this grid measures start
nodes only. The baselines (each card list alone) are rescored the same way, so every row has every budget.
`GRID` is the plan's pre-registration, in the tie order of its rule: the free RRF settings before the
reranker, fewer candidates first.
Not here: fusing and reranking one list (hybrid/seed_fusion.py, seed_rerank.py), the choice (seed_choice.py),
files and MLflow (pipeline/).
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from ..core.errors import EvaluationError
from ..hybrid.seed_fusion import Representation, SeedSetting, fuse
from .interval import Proportion
from .retrieval_scores import RetrievalReport, score_retrieval
from .retrieval_table import POOLED, pool_reports

# the budgets K of the plan: R128's five plus 25 and 30, between which the stopping rule may fall
BUDGETS = (5, 10, 15, 20, 25, 30, 50)
CANDIDATES = (10, 25, 50)  # how deep each card list is read before fusing
RRF_KS = (60, 10)  # 60 the literature's default, 10 the value R121 sealed for the hybrid's chunk lists
RERANK_POOL = 50  # the fused nodes the reranker orders: R128's lists are 50 deep
RERANK_CANDIDATES = (25, 50)  # the plan's two pools: 25 + 25 and 50 + 50 candidates, fused by RRF k = 60
RERANK_RRF_K = 60

# what a row is for: `choice` competes under the rule, `reference` (interleaving) and `record` (summary cards)
# are scored beside it, `baseline` is one card list alone
Role = Literal["choice", "reference", "record", "baseline"]


class GridEntry(BaseModel):
    """One row of the grid: a setting, or a baseline (`setting` None), and what the row is for."""

    name: str
    role: Role
    setting: SeedSetting | None = None


def card_report(mode: Literal["dense", "lexical"], representation: Representation) -> str:
    """The system name of an R128 card report (`card_dense_template`)."""
    return f"card_{mode}_{representation}"


BASELINES = [GridEntry(name=card_report(m, "template"), role="baseline") for m in ("lexical", "dense")]


def _rrf_rows(representation: Representation, role: Role) -> list[GridEntry]:
    rows = []
    for c in CANDIDATES:
        for k in RRF_KS:
            name = f"rrf{k}_c{c}_{representation}"
            setting = SeedSetting(
                name=name, representation=representation, method="rrf", candidates=c, rrf_k=k
            )
            rows.append(GridEntry(name=name, role=role, setting=setting))
        name = f"interleave_c{c}_{representation}"
        setting = SeedSetting(name=name, representation=representation, method="interleave", candidates=c)
        rows.append(GridEntry(name=name, role="reference" if role == "choice" else role, setting=setting))
    return rows


def _rerank_rows() -> list[GridEntry]:
    return [
        GridEntry(
            name=f"rerank_c{c}_template",
            role="choice",
            setting=SeedSetting(
                name=f"rerank_c{c}_template",
                representation="template",
                method="rerank",
                candidates=c,
                rrf_k=RERANK_RRF_K,
                pool=RERANK_POOL,
            ),
        )
        for c in RERANK_CANDIDATES
    ]


# the pre-registered grid, in the rule's tie order
GRID = _rrf_rows("template", "choice") + _rerank_rows() + _rrf_rows("summary", "record")


def needed_reports(entries: Sequence[GridEntry]) -> list[str]:
    """The R128 card reports the entries read, in a fixed order."""
    reps = {e.setting.representation for e in entries if e.setting} | {"template"}  # baselines are template
    return [card_report(m, r) for r in ("template", "summary") if r in reps for m in ("dense", "lexical")]


def fused_seeds(
    dense: RetrievalReport, lexical: RetrievalReport, setting: SeedSetting
) -> dict[str, list[str]]:
    """Question id -> the setting's seeds (a `rerank` setting: its pool, in RRF order). Raises
    `EvaluationError` when the two reports were not made on the same questions, targets and build."""
    if dense.fingerprint != lexical.fingerprint:
        raise EvaluationError([f"{dense.system} and {lexical.system} ran on different questions or builds"])
    lexical_seeds = {o.question_id: o.seeds or [] for o in lexical.outcomes}
    if lexical_seeds.keys() != {o.question_id for o in dense.outcomes}:
        raise EvaluationError([f"{dense.system} and {lexical.system} hold other questions"])
    return {o.question_id: fuse(o.seeds or [], lexical_seeds[o.question_id], setting) for o in dense.outcomes}


def seeded_report(base: RetrievalReport, name: str, seeds: Mapping[str, Sequence[str]]) -> RetrievalReport:
    """`base`'s questions and targets with `seeds` as every question's start nodes, scored at `BUDGETS`;
    no chunks (this grid measures start nodes only) and no latency (nothing was run)."""
    outcomes = [
        o.model_copy(
            update={
                "system": name,
                "seeds": list(seeds[o.question_id]),
                "ranked": [],
                "gold_chunks": [],
                "latency_ms": 0.0,
                "lists": {},
            }
        )
        for o in base.outcomes
    ]
    return score_retrieval(outcomes, BUDGETS, name, base.fingerprint)


class SeedScores(BaseModel):
    """One row at one budget K: the targets found, the questions with every target found, and the questions
    given fewer than K seeds."""

    name: str
    role: Role
    k: int
    seed_recall: Proportion
    seed_found: Proportion
    short: int


class SeedTable(BaseModel):
    """Every row at every K on one dataset, or pooled over every dataset."""

    name: str
    graph_digest: str
    scores: list[SeedScores]

    def score(self, name: str, k: int) -> SeedScores:
        return next(s for s in self.scores if s.name == name and s.k == k)


def seed_tables(
    reports: Mapping[str, Mapping[str, RetrievalReport]], entries: Sequence[GridEntry]
) -> list[SeedTable]:
    """The table of every dataset in the order given, then the pooled one when there are two or more;
    `reports` maps a dataset to its rows' reports by entry name."""
    tables = [_table(name, by_name, entries) for name, by_name in reports.items()]
    if len(reports) > 1:
        pooled = {
            e.name: pool_reports(e.name, {d: r[e.name] for d, r in reports.items()}, BUDGETS) for e in entries
        }
        tables.append(_table(POOLED, pooled, entries))
    return tables


def _table(name: str, reports: Mapping[str, RetrievalReport], entries: Sequence[GridEntry]) -> SeedTable:
    scores = []
    for e in entries:
        report = reports[e.name]
        targeted = [o for o in report.outcomes if o.gold_targets]
        for k in BUDGETS:
            scores.append(
                SeedScores(
                    name=e.name,
                    role=e.role,
                    k=k,
                    seed_recall=report.overall.seed_recall[k],
                    seed_found=report.overall.seed_found[k],
                    short=sum(len(o.seeds or []) < k for o in targeted),
                )
            )
    return SeedTable(name=name, graph_digest=reports[entries[0].name].fingerprint.graph_digest, scores=scores)


_NOTE = (
    "Targets with a node among the top K seeds (Seed Recall@K), then questions with every target found "
    "(Seeds found@K) in brackets; *short*: questions given fewer than K seeds. Role: *choice* competes under "
    "the pre-registered rule, *reference* (interleaving) and *record* (summary cards) are scored beside it, "
    "*baseline* is one card list alone."
)


def tables_markdown(tables: Sequence[SeedTable], entries: Sequence[GridEntry]) -> str:
    """Every table as markdown: counts as k/n, never bare rates."""
    head = "| Setting | Role | " + " | ".join(f"K = {k}" for k in BUDGETS) + " |"
    rule = "|---" * (len(BUDGETS) + 2) + "|"
    lines = []
    for t in tables:
        lines += [f"## {t.name} (graph {t.graph_digest})", "", _NOTE, "", head, rule]
        for e in entries:
            cells = [_cell(t.score(e.name, k)) for k in BUDGETS]
            lines.append(f"| {e.name} | {e.role} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _cell(s: SeedScores) -> str:
    cell = f"{s.seed_recall.k}/{s.seed_recall.n} ({s.seed_found.k}/{s.seed_found.n})"
    return cell + (f", {s.short} short" if s.short else "")
