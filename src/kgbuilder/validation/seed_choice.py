"""The pre-registered choice of plan R130-R136 (R130): which seeding setting the agent starts from, and how
many seeds (K) it is given.

Role in the pipeline: `kg seed-grid` (pipeline/seed_stages.py) applies it to the grid's reports
(seed_grid.py); R131 reports the choice, R132 confirms it live.
Design: pure, the rule exactly as the plan fixed it before any number:
1. on the choice dataset (furniture), the `choice` row with the most targets found at K = 20 (ties: more
   questions with every target found at 20, then the grid's order: the free RRF settings before the reranker,
   fewer candidates first);
2. its K: the smallest budget whose Seed Recall is within `TOLERANCE` targets of its own Seed Recall@50;
3. a reranker chosen in 1 is kept only if it beats the best free setting on the other datasets pooled, every
   target found at its K, by the exact McNemar test (p < ALPHA, more questions only it finds); otherwise the
   best free setting and its own K are chosen (free and deterministic);
4. every row is paired with the baseline (one card list alone) at the chosen K, per dataset and pooled.
Not here: the grid and its scores (seed_grid.py), files and MLflow (pipeline/).
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..core.errors import ConfigurationError
from .paired import ALPHA, PairedComparison
from .retrieval_scores import RetrievalReport, compare_retrieval
from .retrieval_table import POOLED, pool_reports
from .seed_grid import BUDGETS, GridEntry

CHOICE_K = 20  # the budget the setting is chosen at (the plan's rule)
TOLERANCE = 2  # targets: R128 found dense counts move by 2-3 between builds, so closer is not a difference


class Pairing(BaseModel):
    """Row `a` against row `b` at K on one dataset (or pooled): questions with every target found."""

    dataset: str
    a: str
    b: str
    k: int
    seed_found: PairedComparison


class SeedChoice(BaseModel):
    """The setting the rule chose, its K, and the evidence for each part of the rule."""

    choose_on: str
    confirm_on: list[str]
    best: str  # step 1's winner on the choice dataset
    best_free: str  # the best row that is not a reranker (= best when best is free)
    setting: str  # the chosen row
    k: int
    reason: str  # why the chosen row is the one, in one sentence
    rerank_confirmation: Pairing | None  # step 3: the reranker against the best free row, pooled
    against_baseline: list[Pairing]  # step 4

    def metrics(self) -> dict[str, float | int]:
        """Flat MLflow metrics: the chosen K and every pairing's discordant counts and p."""
        out: dict[str, float | int] = {"chosen_k": self.k}
        pairings = [*([self.rerank_confirmation] if self.rerank_confirmation else []), *self.against_baseline]
        for p in pairings:
            prefix = f"{p.dataset}_{p.a}_vs_{p.b}_at_{p.k}"
            out.update({f"{prefix}_only_a": p.seed_found.only_a, f"{prefix}_only_b": p.seed_found.only_b})
            out[f"{prefix}_p"] = p.seed_found.p_value
        return out

    def markdown(self) -> str:
        """The choice and its pairings as markdown."""
        lines = [
            "## The choice (pre-registered rule)",
            "",
            f"- Chosen on {self.choose_on}, confirmed on {', '.join(self.confirm_on) or 'nothing'}.",
            f"- Best at K = {CHOICE_K}: {self.best}; best free setting: {self.best_free}.",
            f"- **Chosen: {self.setting}, K = {self.k}.** {self.reason}",
            "",
            "Questions with every target found only by the first / only by the second row, exact McNemar p:",
            "",
            "| Dataset | First | Second | K | Only first | Only second | p |",
            "|---|---|---|---|---|---|---|",
        ]
        pairings = [*([self.rerank_confirmation] if self.rerank_confirmation else []), *self.against_baseline]
        for p in pairings:
            c = p.seed_found
            lines.append(
                f"| {p.dataset} | {p.a} | {p.b} | {p.k} | {c.only_a} | {c.only_b} | {c.p_value:.3f} |"
            )
        return "\n".join(lines) + "\n"


def knee(report: RetrievalReport) -> int:
    """The smallest budget whose Seed Recall is within `TOLERANCE` targets of the report's Seed Recall at the
    largest budget."""
    recall = report.overall.seed_recall
    floor = recall[max(BUDGETS)].k - TOLERANCE
    return next(k for k in BUDGETS if recall[k].k >= floor)


def choose(
    reports: Mapping[str, Mapping[str, RetrievalReport]],
    entries: Sequence[GridEntry],
    choose_on: str,
    baseline: str,
) -> SeedChoice:
    """Apply the rule to the grid's reports (dataset -> entry name -> report). Raises `ConfigurationError`
    when the choice dataset is not among them or no row competes."""
    if choose_on not in reports:
        raise ConfigurationError(f"the choice dataset '{choose_on}' is not among {sorted(reports)}")
    competing = [e for e in entries if e.role == "choice"]
    free = [e for e in competing if e.setting is None or e.setting.method != "rerank"]
    if not free:
        raise ConfigurationError("no free setting competes: the rule needs one to fall back on")
    on = reports[choose_on]
    best, best_free = _best(on, competing), _best(on, free)
    confirm_on = [d for d in reports if d != choose_on]
    chosen, reason, confirmation = _confirm(reports, best, best_free, confirm_on, knee(on[best.name]))
    k = knee(on[chosen.name])
    against = _against_baseline(reports, entries, baseline, k)
    return SeedChoice(
        choose_on=choose_on,
        confirm_on=confirm_on,
        best=best.name,
        best_free=best_free.name,
        setting=chosen.name,
        k=k,
        reason=reason,
        rerank_confirmation=confirmation,
        against_baseline=against,
    )


def _confirm(
    reports: Mapping[str, Mapping[str, RetrievalReport]],
    best: GridEntry,
    best_free: GridEntry,
    confirm_on: Sequence[str],
    k: int,
) -> tuple[GridEntry, str, Pairing | None]:
    """Step 3: the chosen row, why, and the pooled pairing of a reranker against the best free row at its K
    (None when the best row is free, or no other dataset can confirm)."""
    if best.name == best_free.name:
        return best_free, "The best setting at K = 20 on the choice dataset is free.", None
    if not confirm_on:
        return best_free, "The reranker could not be confirmed: no other dataset.", None
    confirmation = _pairing(POOLED, reports, confirm_on, best.name, best_free.name, k)
    c = confirmation.seed_found
    if c.p_value < ALPHA and c.only_a > c.only_b:
        return best, "The reranker beats the best free setting on the other datasets pooled.", confirmation
    return (
        best_free,
        "The reranker did not beat the best free setting on the other datasets pooled.",
        confirmation,
    )


def _against_baseline(
    reports: Mapping[str, Mapping[str, RetrievalReport]], entries: Sequence[GridEntry], baseline: str, k: int
) -> list[Pairing]:
    """Step 4: every row against the baseline at K, on each dataset, then pooled over two or more."""
    rows = [e.name for e in entries if e.name != baseline]
    datasets = list(reports)
    out = [_pairing(d, reports, [d], row, baseline, k) for row in rows for d in datasets]
    if len(datasets) > 1:
        out += [_pairing(POOLED, reports, datasets, row, baseline, k) for row in rows]
    return out


def _best(reports: Mapping[str, RetrievalReport], rows: Sequence[GridEntry]) -> GridEntry:
    """The row with the most targets found at `CHOICE_K`, then the most questions fully found, then the
    earliest in `rows`."""

    def key(item: tuple[int, GridEntry]) -> tuple[int, int, int]:
        position, e = item
        o = reports[e.name].overall
        return -o.seed_recall[CHOICE_K].k, -o.seed_found[CHOICE_K].k, position

    return min(enumerate(rows), key=key)[1]


def _pairing(
    label: str,
    reports: Mapping[str, Mapping[str, RetrievalReport]],
    datasets: Sequence[str],
    a: str,
    b: str,
    k: int,
) -> Pairing:
    """Row `a` against row `b` at K over `datasets` (one, or several pooled)."""
    if len(datasets) == 1:
        ra, rb = reports[datasets[0]][a], reports[datasets[0]][b]
    else:
        ra = pool_reports(a, {d: reports[d][a] for d in datasets}, BUDGETS)
        rb = pool_reports(b, {d: reports[d][b] for d in datasets}, BUDGETS)
    found = compare_retrieval(ra, rb, k, a, b).seed_found
    if found is None:  # both rows start from nodes by construction; a report without seeds is a caller's bug
        raise ConfigurationError(f"{a} or {b} gives no seeds")
    return Pairing(dataset=label, a=a, b=b, k=k, seed_found=found.overall)
