"""The technique table of plan R126-R128 (R127): each retrieval technique's own scores at every budget K, on
each dataset and pooled over them, with the questions whose list falls short of K, and per K the best
technique for evidence and for start nodes, paired with its runner-up.

Role in the pipeline: `kg retrieve-table` (pipeline/retrieval_stages.py) reads the reports `kg retrieve-eval`
wrote for each dataset and builds the table here; its markdown is what the result document quotes.
Design: pure, over `RetrievalReport`s. A dataset's rates are its reports' own; the pooled table re-scores the
concatenated outcome rows with `score_retrieval` (question ids prefixed by the dataset), so a pooled rate is
computed exactly as a dataset's is. The pairing is `compare_retrieval`'s exact McNemar test. The best
technique is chosen by the rule the plan pre-registered: for evidence the most complete questions at K
(ties: more gold chunks found, then the technique order given), for start nodes the most targets found (ties:
more questions with every target found, then the order); only techniques that start from nodes compete there.
Not here: ranking or scoring one system (retrieval_scores.py), files and MLflow (pipeline/).
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from ..core.errors import EvaluationError
from .interval import Proportion
from .paired import PairedComparison
from .retrieval_scores import RetrievalFingerprint, RetrievalReport, compare_retrieval, score_retrieval

POOLED = "pooled"
Measure = Literal["evidence", "seeds"]


class TechniqueScores(BaseModel):
    """One technique at one budget K: its rates, and the questions whose list holds fewer than K items (a
    technique that cannot fill K is scored on what it gives; this says how often that happened)."""

    system: str
    k: int
    evidence_recall: Proportion
    complete: Proportion
    short_chunks: int  # of the questions with evidence, those ranked fewer than K chunks
    seed_recall: Proportion | None  # None for a technique that starts from no node
    seed_found: Proportion | None
    short_seeds: int | None  # of the questions with targets, those given fewer than K seeds


class Best(BaseModel):
    """The best technique for one measure at one K, and the paired test against the runner-up (best = a)."""

    k: int
    measure: Measure
    best: str
    runner_up: str | None  # None when one technique competes
    paired: PairedComparison | None


class DatasetTable(BaseModel):
    """Every technique at every K on one dataset (or pooled), and the best per measure and K."""

    name: str
    graph_digest: str  # the pooled table joins its datasets' digests with "+"
    scores: list[TechniqueScores]  # in technique order, then K
    best: list[Best]

    def score(self, system: str, k: int) -> TechniqueScores:
        return next(s for s in self.scores if s.system == system and s.k == k)


class RetrievalTable(BaseModel):
    """The tables of every dataset in the order given, then the pooled one when there are two or more."""

    systems: list[str]
    budgets: list[int]
    tables: list[DatasetTable]

    def metrics(self) -> dict[str, float | int]:
        """Flat MLflow metrics: each pairing's discordant counts and p, `<dataset>_<measure>_at_<k>_...`."""
        out: dict[str, float | int] = {}
        for table in self.tables:
            for b in table.best:
                if b.paired is not None:
                    prefix = f"{table.name}_{b.measure}_at_{b.k}"
                    out[f"{prefix}_only_best"] = b.paired.only_a
                    out[f"{prefix}_only_runner_up"] = b.paired.only_b
                    out[f"{prefix}_p"] = b.paired.p_value
        return out

    def markdown(self) -> str:
        """Every table as markdown, the form the result document quotes: counts as k/n, never bare rates."""
        head = "| Technique | " + " | ".join(f"K = {k}" for k in self.budgets) + " |"
        rule = "|---" * (len(self.budgets) + 1) + "|"
        lines = []
        for t in self.tables:
            lines += [f"## {t.name} (graph {t.graph_digest})", "", _EVIDENCE_NOTE, "", head, rule]
            lines += [_row(s, [_evidence_cell(t.score(s, k)) for k in self.budgets]) for s in self.systems]
            seeded = [s for s in self.systems if t.score(s, self.budgets[0]).seed_recall is not None]
            if seeded:
                lines += ["", _SEEDS_NOTE, "", head, rule]
                lines += [_row(s, [_seed_cell(t.score(s, k)) for k in self.budgets]) for s in seeded]
            lines += ["", _BEST_NOTE, "", "| K | Evidence | Start nodes |", "|---|---|---|"]
            for k in self.budgets:
                by = {b.measure: b for b in t.best if b.k == k}
                lines.append(f"| {k} | {_best_cell(by.get('evidence'))} | {_best_cell(by.get('seeds'))} |")
            lines.append("")
        return "\n".join(lines)


_EVIDENCE_NOTE = (
    "**Evidence:** questions with every gold chunk in the top K (Complete@K), then gold chunks in the top K "
    "(Evidence Recall@K) in brackets; *short*: questions whose list holds fewer than K chunks."
)
_SEEDS_NOTE = (
    "**Start nodes:** targets with a node among the top K seeds (Seed Recall@K), then questions with every "
    "target found (Seeds found@K) in brackets; *short*: questions given fewer than K seeds."
)
_BEST_NOTE = (
    "**Best per K** (the pre-registered rule), against the runner-up: questions only the best / only the "
    "runner-up complete (evidence) or find every target of (start nodes), exact McNemar p."
)


def _row(system: str, cells: list[str]) -> str:
    return f"| {system} | " + " | ".join(cells) + " |"


def _fraction(share: Proportion | None) -> str:
    return "-" if share is None else f"{share.k}/{share.n}"


def _evidence_cell(s: TechniqueScores) -> str:
    cell = f"{_fraction(s.complete)} ({_fraction(s.evidence_recall)})"
    return cell + (f", {s.short_chunks} short" if s.short_chunks else "")


def _seed_cell(s: TechniqueScores) -> str:
    cell = f"{_fraction(s.seed_recall)} ({_fraction(s.seed_found)})"
    return cell + (f", {s.short_seeds} short" if s.short_seeds else "")


def _best_cell(b: Best | None) -> str:
    if b is None:
        return "-"
    if b.paired is None:
        return b.best
    return f"{b.best} over {b.runner_up}: {b.paired.only_a} / {b.paired.only_b}, p {b.paired.p_value:.3f}"


def build_table(
    datasets: Mapping[str, Mapping[str, RetrievalReport]], systems: Sequence[str]
) -> RetrievalTable:
    """The table of `systems` (in this order, the tie order) on every dataset, `datasets` mapping a dataset's
    name to its reports by system.

    Raises `EvaluationError` when a dataset lacks a report of a named system, when a dataset's reports were
    computed on other questions, targets, graphs or embedding models, when a dataset is named like the
    pooled table, or when the reports share no budget.
    """
    _check(datasets, systems)
    budgets = sorted(set.intersection(*(set(d[s].budgets) for d in datasets.values() for s in systems)))
    if not budgets:
        raise EvaluationError(["the reports share no budget"])
    tables = [_table(name, reports, systems, budgets) for name, reports in datasets.items()]
    if len(datasets) > 1:
        pooled = {
            s: _pooled(s, {name: reports[s] for name, reports in datasets.items()}, budgets) for s in systems
        }
        tables.append(_table(POOLED, pooled, systems, budgets))
    return RetrievalTable(systems=list(systems), budgets=budgets, tables=tables)


def _check(datasets: Mapping[str, Mapping[str, RetrievalReport]], systems: Sequence[str]) -> None:
    issues = []
    if POOLED in datasets:
        issues.append(f"'{POOLED}' names the table over every dataset: name the dataset otherwise")
    for name, reports in datasets.items():
        if missing := [s for s in systems if s not in reports]:
            issues.append(f"{name}: no report of {', '.join(missing)}")
            continue
        first = reports[systems[0]]
        questions = {o.question_id: o.type for o in first.outcomes}
        for s in systems[1:]:
            if reports[s].fingerprint != first.fingerprint:
                issues.append(
                    f"{name}: {s} ran on {reports[s].fingerprint}, {systems[0]} on {first.fingerprint}"
                )
            elif {o.question_id: o.type for o in reports[s].outcomes} != questions:
                issues.append(f"{name}: {s} holds other questions or question types than {systems[0]}")
    if issues:
        raise EvaluationError(issues)


def _pooled(system: str, by_dataset: Mapping[str, RetrievalReport], budgets: list[int]) -> RetrievalReport:
    """One report of `system` over every dataset's questions, each id prefixed by its dataset."""
    outcomes = [
        o.model_copy(update={"question_id": f"{name}/{o.question_id}"})
        for name, report in by_dataset.items()
        for o in report.outcomes
    ]
    prints = [r.fingerprint for r in by_dataset.values()]
    fingerprint = RetrievalFingerprint(
        gold_hash="+".join(p.gold_hash for p in prints),
        targets_hash="+".join(p.targets_hash for p in prints),
        graph_digest="+".join(p.graph_digest for p in prints),
        embed_model="+".join(dict.fromkeys(p.embed_model for p in prints)),
    )
    return score_retrieval(outcomes, budgets, system, fingerprint)


def _table(
    name: str, reports: Mapping[str, RetrievalReport], systems: Sequence[str], budgets: list[int]
) -> DatasetTable:
    best = [_best(reports, systems, k, m) for k in budgets for m in ("evidence", "seeds")]
    return DatasetTable(
        name=name,
        graph_digest=reports[systems[0]].fingerprint.graph_digest,
        scores=[_scores(reports[s], k) for s in systems for k in budgets],
        best=[b for b in best if b is not None],
    )


def _scores(report: RetrievalReport, k: int) -> TechniqueScores:
    overall = report.overall
    evidence = [o for o in report.outcomes if o.gold_chunks]
    targeted = [o for o in report.outcomes if o.gold_targets]
    return TechniqueScores(
        system=report.system,
        k=k,
        evidence_recall=overall.evidence_recall[k],
        complete=overall.complete[k],
        short_chunks=sum(len(o.ranked) < k for o in evidence),
        seed_recall=overall.seed_recall[k] if report.seeded else None,
        seed_found=overall.seed_found[k] if report.seeded else None,
        short_seeds=sum(len(o.seeds or []) < k for o in targeted) if report.seeded else None,
    )


def _best(
    reports: Mapping[str, RetrievalReport], systems: Sequence[str], k: int, measure: Measure
) -> Best | None:
    """The pre-registered choice at K for `measure`; None when no technique competes (no seeds at all)."""

    def counts(s: str) -> tuple[int, int]:
        o = reports[s].overall
        if measure == "evidence":
            return o.complete[k].k, o.evidence_recall[k].k
        return o.seed_recall[k].k, o.seed_found[k].k

    competing = [s for s in systems if measure == "evidence" or reports[s].seeded]
    if not competing:
        return None
    ranked = sorted(competing, key=lambda s: (-counts(s)[0], -counts(s)[1], systems.index(s)))
    if len(ranked) == 1:
        return Best(k=k, measure=measure, best=ranked[0], runner_up=None, paired=None)
    best, runner_up = ranked[0], ranked[1]
    pairing = compare_retrieval(reports[best], reports[runner_up], k, best, runner_up)
    paired = pairing.complete if measure == "evidence" else pairing.seed_found
    return Best(
        k=k, measure=measure, best=best, runner_up=runner_up, paired=paired.overall if paired else None
    )
