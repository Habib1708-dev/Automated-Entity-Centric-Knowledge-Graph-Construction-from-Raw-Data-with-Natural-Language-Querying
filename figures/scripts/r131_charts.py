"""The two result charts of R131 (the seed grid: how the agent finds its start nodes), drawn from `r131_data`.

Role: `build.py` calls `draw_all`; the files land in `figures/results/retrieval/r131_seed_grid/`, whose
README.md is their caption list.
Design: the form follows the data's job (dataviz skill, choosing-a-form):
- fig1, small multiples of lines on a log-K axis (a rate against a budget, past four series: the emphasis
  form): every setting the rule saw in gray, the two card lists alone, the chosen fusion and the reranker
  drawn on top, with the chosen K marked. Every row is node cards, so the family hue (orange) cannot tell
  them apart; fill does, as everywhere: hollow lexical, filled dense, and half-filled for a fusion of both.
  The reranker is the one row in dark ink: a fourth hue is never generated (figures/CLAUDE.md);
- fig2, diverging bars (a paired comparison, polarity): left, the chosen fusion against the lexical cards
  alone at the chosen K (pre-registered); right, the reranker against its own pool, which is the chosen
  fusion, at K = 5 and 10 (exploratory). The chosen fusion is orange in both panels, so it reads as one thing.
One y-scale per panel, never two; rates as percent with n in the panel title.
Must not: compute a result; every number comes from `r131_data`.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, NullLocator, PercentFormatter

from kgbuilder.validation.seed_grid import BUDGETS

from . import r131_data as data
from .style import AXIS, CONTEXT, INK, INK_MUTED, INK_SECONDARY, NODE_ORANGE, SURFACE, save, title

OUT = Path(__file__).resolve().parents[1] / "results" / "retrieval" / "r131_seed_grid"
RERANK_INK = INK_SECONDARY  # the reranker: dark ink, not a fourth hue


def draw_all(tables, choice, against_lexical, against_pool, out: Path = OUT) -> list[Path]:
    """Both R131 charts into `out`; returns the files written."""
    return [
        *fig1_recall_by_k(tables, choice, out),
        *fig2_paired_decision(against_lexical, against_pool, out),
    ]


# --- fig1: Seed Recall against K ----------------------------------------------------------------------------

# the rows drawn on top of the gray context: (row, label, colour, fill, line width)
_FOCUS = (
    (data.DENSE, "Template cards, dense alone", NODE_ORANGE, "full", 1.3),
    (data.LEXICAL, "Template cards, lexical alone (R128's best)", NODE_ORANGE, "none", 1.3),
    (data.RERANKER, "RRF then LLM rerank, 25 + 25 (not chosen)", RERANK_INK, "left", 1.8),
    (data.CHOSEN, "RRF k = 60, 25 + 25 (chosen)", NODE_ORANGE, "left", 2.8),
)


def fig1_recall_by_k(tables, choice, out: Path) -> list[Path]:
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), sharex=True)
    for ax, name in zip(axes.flat, ("pooled", *data.DATASETS), strict=True):
        _recall_panel(ax, tables[name], choice.k)
        n = tables[name].score(data.CHOSEN, BUDGETS[0]).seed_recall.n
        ax.set_title(f"{data.LABELS[name]}: {n} targets", loc="left")
    for ax in axes[1]:
        ax.set_xlabel("K, the start nodes the agent is given (log scale)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Seed Recall@K (targets among the top K)")
    handles = [_handle(label, colour, fill, width) for _, label, colour, fill, width in _FOCUS]
    handles.append(Line2D([], [], color=CONTEXT, lw=1.2, label="every other setting the rule saw"))
    fig.legend(handles=handles, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.005))
    title(
        fig,
        "Fusing dense and lexical cards finds more start nodes; 15 seeds reach 203 of 213 targets",
        "Seed Recall@K, exact match against the R89 targets, 141 questions (R131). The dotted line is the "
        f"chosen K = {choice.k}: the smallest K within 2 targets of the chosen setting's recall at K = 50.",
    )
    fig.subplots_adjust(top=0.88, bottom=0.13, hspace=0.3, wspace=0.18)
    return save(fig, out, "r131_fig1_recall_by_k")


def _recall_panel(ax, table, chosen_k: int) -> None:
    focus = {row for row, *_ in _FOCUS}
    for row in data.template_rows(table):
        if row not in focus:
            ax.plot(BUDGETS, [p.rate for p in data.recall(table, row)], color=CONTEXT, lw=1.2, zorder=1)
    for z, (row, _, colour, fill, width) in enumerate(_FOCUS, start=2):
        rates = [p.rate for p in data.recall(table, row)]
        ax.plot(BUDGETS, rates, color=colour, lw=width, zorder=z, **_marker(colour, fill))
    ax.axvline(chosen_k, color=INK_MUTED, lw=1.2, linestyle=":", zorder=0)
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator(list(BUDGETS)))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([str(k) for k in BUDGETS])
    ax.set_xlim(BUDGETS[0] * 0.9, BUDGETS[-1] * 1.1)
    ax.set_ylim(0.6, 1.005)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))


def _marker(colour: str, fill: str) -> dict[str, object]:
    """A square (template cards), filled, hollow or half-filled (a fusion of dense and lexical)."""
    return {
        "marker": "s",
        "markersize": 6.5,
        "fillstyle": fill,
        "markerfacecolor": colour,
        "markerfacecoloralt": SURFACE,
        "markeredgecolor": colour,
        "markeredgewidth": 1.4,
    }


def _handle(label: str, colour: str, fill: str, width: float) -> Line2D:
    return Line2D([], [], color=colour, lw=width, label=label, **_marker(colour, fill))


# --- fig2: the paired comparisons behind the decision ------------------------------------------------------


def fig2_paired_decision(against_lexical, against_pool, out: Path) -> list[Path]:
    fig, (left, right) = plt.subplots(1, 2, figsize=(12.5, 5.4))
    rows = [(data.LABELS[p.dataset].split(" (")[0], p) for p in against_lexical]
    _paired_panel(left, rows, "only the lexical cards alone", "only the chosen fusion", CONTEXT, NODE_ORANGE)
    left.set_title(
        f"Chosen fusion against the lexical cards alone, K = {against_lexical[0].k}\n(pre-registered)",
        loc="left",
        fontsize=9,
    )
    rows = [(f"K = {p.k}, {data.LABELS[p.dataset].split(' (')[0]}", p) for p in against_pool]
    _paired_panel(right, rows, "only the chosen fusion", "only the reranker", NODE_ORANGE, RERANK_INK)
    right.set_title(
        "LLM reranker against its own pool (the chosen fusion)\n(exploratory, chosen after the "
        "table was seen)",
        loc="left",
        fontsize=9,
    )
    title(
        fig,
        "Fusion beats one card list at K = 15; the reranker helps only at K = 5",
        "Bars: questions with every target found by only one of the two settings (141 questions; per dataset "
        "50, 51, 40). p: exact McNemar test. Questions both or neither find are left out.",
    )
    fig.subplots_adjust(top=0.8, wspace=0.62, bottom=0.14)
    return save(fig, out, "r131_fig2_paired_decision")


def _paired_panel(ax, rows, left_label: str, right_label: str, left_colour: str, right_colour: str) -> None:
    """Diverging bars: only `b` to the left, only `a` to the right, the counts at the bar ends, p at right."""
    ys = list(range(len(rows)))
    ax.barh(ys, [-p.only_b for _, p in rows], height=0.6, color=left_colour)
    ax.barh(ys, [p.only_a for _, p in rows], height=0.6, color=right_colour)
    for y, (_, p) in zip(ys, rows, strict=True):
        ax.text(-p.only_b - 0.6, y, str(p.only_b), ha="right", va="center", fontsize=8, color=INK_SECONDARY)
        ax.text(p.only_a + 0.6, y, str(p.only_a), ha="left", va="center", fontsize=8, color=INK_SECONDARY)
        label = "p < 0.01" if p.p < 0.01 else f"p = {p.p:.3f}"
        ax.text(
            1.03,
            y,
            label,
            transform=ax.get_yaxis_transform(),
            ha="left",
            va="center",
            fontsize=8,
            color=INK,
            fontweight="bold" if p.p < 0.05 else "normal",
        )
    ax.axvline(0, color=AXIS, lw=1)
    ax.set_yticks(ys, [name for name, _ in rows])
    ax.invert_yaxis()
    ax.set_xlim(-26, 26)
    ax.set_xticks([-20, -10, 0, 10, 20], ["20", "10", "0", "10", "20"])
    ax.grid(axis="y", visible=False)
    ax.text(0.0, -0.12, f"← {left_label}", transform=ax.transAxes, ha="left", fontsize=8, color=INK_SECONDARY)
    ax.text(
        1.0, -0.12, f"{right_label} →", transform=ax.transAxes, ha="right", fontsize=8, color=INK_SECONDARY
    )
