"""The seed grid's stage (R130): `kg seed-grid`, plan R130-R136's offline experiment on how the agent finds
its start nodes.

Role in the pipeline: after R128's `kg retrieve-eval` runs, whose card reports it reads; no graph. Every
setting of the pre-registered grid (validation/seed_grid.py) fuses each question's dense and lexical card
lists; with `--rerank` the reranker (hybrid/seed_rerank.py) also orders each `rerank` setting's pool, reading
the question from the QA gold and the template cards from a units file (`index/units.jsonl` of a `kg index
--cards template` run, or its MLflow artifact). Then the plan's rule chooses (validation/seed_choice.py).
Without `--rerank` no model is called at all.
Design: wiring and logging only, like retrieval_stages.py. One MLflow run (`seed_grid`): params name every
input with its hash, the grid, the budgets, the rule's datasets and baseline, and with `--rerank` the model,
its settings and the reranker's version; metrics are the choice's pairings, the chosen setting's scores, and
how far the reranker's replies were off; artifacts are the tables, the choice, every reranked order and the
prompt. Only questions with targets are reranked: the others score nothing, so asking would only cost.
Not here: fusing, reranking, scoring, choosing (hybrid/, validation/).
"""

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from ..core.errors import ConfigurationError, EvaluationError
from ..hybrid.seed_rerank import PROMPT as RERANK_PROMPT
from ..hybrid.seed_rerank import Reranked, RerankOptions, SeedReranker
from ..validation.qa_gold import load_qa_gold
from ..validation.retrieval_scores import RetrievalReport, load_retrieval_report
from ..validation.seed_choice import choose
from ..validation.seed_grid import (
    BASELINES,
    BUDGETS,
    GRID,
    card_report,
    fused_seeds,
    needed_reports,
    seed_tables,
    seeded_report,
    tables_markdown,
)
from .inputs import digest, input_file
from .stages import BaseStage


class SeedDataset(BaseModel):
    """One dataset's inputs: the folder of its R128 reports, and for `--rerank` its template cards and its
    QA gold (the questions' wording)."""

    reports: Path
    units: Path | None = None
    gold: Path | None = None


class SeedGridStage(BaseStage):
    """Fuse, optionally rerank, score and choose (`kg seed-grid`)."""

    name = "seed_grid"
    GRID_FILE = "seed_grid.json"
    CHOICE_FILE = "seed_choice.json"
    MARKDOWN_FILE = "seed_grid.md"
    RERANK_FILE = "seed_rerank.jsonl"
    BASELINE = "card_lexical_template"  # R128's best start-node finder at K = 5 (164 of 213 targets)

    def __init__(self, datasets: Mapping[str, SeedDataset], rerank: bool, choose_on: str):
        if not datasets:
            raise ConfigurationError("kg seed-grid needs at least one dataset")
        if rerank and (missing := [d for d, s in datasets.items() if s.units is None or s.gold is None]):
            raise ConfigurationError(f"--rerank needs the units file and the QA gold of {', '.join(missing)}")
        self.datasets = dict(datasets)
        self.rerank = rerank
        self.choose_on = choose_on
        self.entries = [
            *BASELINES,
            *(e for e in GRID if rerank or e.setting is None or e.setting.method != "rerank"),
        ]

    def params(self, ctx, state):
        out: dict[str, object] = {
            "grid": [e.name for e in self.entries],
            "budgets": list(BUDGETS),
            "choose_on": self.choose_on,
            "baseline": self.BASELINE,
            "rerank": self.rerank,
        }
        for name, s in self.datasets.items():
            out[f"{name}_reports"] = str(s.reports)
            out[f"{name}_reports_hash"] = "+".join(digest(p) for p in self._report_files(s).values())
            for kind, path in (("units", s.units), ("gold", s.gold)):
                if self.rerank and path is not None:
                    out.update(
                        {f"{name}_{kind}": str(path), f"{name}_{kind}_hash": digest(input_file(path, kind))}
                    )
        if self.rerank:
            options = {f"rerank_{k}": v for k, v in self._options(ctx).model_dump(mode="json").items()}
            out.update(options, rerank_version=SeedReranker.version)
        return out

    def run(self, ctx, state, run):
        reports, orders = {}, {}
        for name, s in self.datasets.items():
            reports[name], orders[name] = self._dataset(ctx, s)
        tables = seed_tables(reports, self.entries)
        choice = choose(reports, self.entries, self.choose_on, self.BASELINE)
        state.seed_choice = choice
        run.metrics(**choice.metrics(), **_rerank_metrics(orders))
        for t in tables:
            row = {
                f"{t.name}_chosen_seed_recall_at_{k}": t.score(choice.setting, k).seed_recall.k
                for k in BUDGETS
            }
            run.metrics(**row)
        grid = json.dumps(
            {
                "entries": [e.model_dump(mode="json") for e in self.entries],
                "tables": [t.model_dump(mode="json") for t in tables],
            },
            indent=1,
        )
        run.artifact(ctx.write(self.GRID_FILE, grid))
        run.artifact(ctx.write(self.CHOICE_FILE, choice.model_dump_json(indent=1)))
        run.artifact(
            ctx.write(self.MARKDOWN_FILE, tables_markdown(tables, self.entries) + "\n" + choice.markdown())
        )
        if self.rerank:
            lines = [
                json.dumps({"dataset": d, "setting": s, "question_id": q, **r.model_dump()})
                for d, by in orders.items()
                for s, qs in by.items()
                for q, r in qs.items()
            ]
            run.artifact(ctx.write(self.RERANK_FILE, "\n".join(lines) + "\n"))
            run.text(RERANK_PROMPT, "prompts/seed_rerank.txt")

    def _dataset(
        self, ctx, s: SeedDataset
    ) -> tuple[dict[str, RetrievalReport], dict[str, dict[str, Reranked]]]:
        """Every row's report on one dataset, and the reranked orders by setting and question."""
        saved = {system: load_retrieval_report(path) for system, path in self._report_files(s).items()}
        reranker = self._reranker(ctx, s) if self.rerank else None
        questions = {q.id: q.question for q in load_qa_gold(s.gold).questions} if s.gold else {}
        reports, orders = {}, {}
        for e in self.entries:
            if e.setting is None:  # a baseline: its own list, rescored at the grid's budgets
                base = saved[e.name]
                reports[e.name] = seeded_report(
                    base, e.name, {o.question_id: o.seeds or [] for o in base.outcomes}
                )
                continue
            dense = saved[card_report("dense", e.setting.representation)]
            seeds = fused_seeds(dense, saved[card_report("lexical", e.setting.representation)], e.setting)
            if e.setting.method == "rerank" and reranker is not None:
                orders[e.name] = _rerank(reranker, dense, seeds, questions)
                seeds = {**seeds, **{q: r.order for q, r in orders[e.name].items()}}
            reports[e.name] = seeded_report(dense, e.name, seeds)
        return reports, orders

    def _report_files(self, s: SeedDataset) -> dict[str, Path]:
        """System -> its R128 report file; `MissingInputError` names the first one missing."""
        names = needed_reports(self.entries)
        return {n: input_file(s.reports / f"retrieval_{n}.json", "retrieval report") for n in names}

    def _options(self, ctx) -> RerankOptions:
        """The reranker's model and settings: the summary model's, as the plan fixed."""
        st = ctx.settings
        return RerankOptions(
            model=st.index_summary_model, temperature=st.llm_temperature, thinking=st.index_summary_thinking
        )

    def _reranker(self, ctx, s: SeedDataset) -> SeedReranker:
        return SeedReranker(
            ctx.require_llm(), self._options(ctx), read_template_cards(input_file(s.units, "units"))
        )


def _rerank(
    reranker: SeedReranker,
    dense: RetrievalReport,
    pools: Mapping[str, list[str]],
    questions: Mapping[str, str],
) -> dict[str, Reranked]:
    """The pools of the questions with targets, ordered. Raises `EvaluationError` for a question the gold
    does not word."""
    targeted = [o.question_id for o in dense.outcomes if o.gold_targets]
    if unworded := [q for q in targeted if q not in questions]:
        raise EvaluationError([f"the QA gold holds no question {q}" for q in unworded[:3]])
    return reranker.rerank_all({q: (questions[q], pools[q]) for q in targeted})


def _rerank_metrics(orders: Mapping[str, Mapping[str, Mapping[str, Reranked]]]) -> dict[str, int]:
    """How many pools were reranked, and how far the replies were off in total."""
    every = [r for by in orders.values() for qs in by.values() for r in qs.values()]
    if not every:
        return {}
    return {
        "rerank_pools": len(every),
        "rerank_dropped": sum(r.dropped for r in every),
        "rerank_repeated": sum(r.repeated for r in every),
        "rerank_missing": sum(r.missing for r in every),
    }


def read_template_cards(path: Path) -> dict[str, str]:
    """Node ref -> its template card text, from a units file. Raises `EvaluationError` when the file holds no
    template cards (a summary card carries `fallback`; a template card does not)."""
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cards = [line for line in lines if line.get("unit") == "card"]
    if not cards or any(c.get("fallback") is not None for c in cards):
        raise EvaluationError([f"{path} holds no template cards (kg index --cards template writes them)"])
    return {c["ref"]: c["text"] for c in cards}
