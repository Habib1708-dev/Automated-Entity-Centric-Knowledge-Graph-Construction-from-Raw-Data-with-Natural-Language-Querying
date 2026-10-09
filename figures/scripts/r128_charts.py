"""The five result charts of R128 (each retrieval technique alone, K = 5 to 50), drawn from `r128_data`.

Role: `build.py` calls `draw_all`; the files land in `figures/results/retrieval/r128_techniques/`, whose
README.md is their caption list.
Design: the form follows the data's job (dataviz skill, choosing-a-form):
- fig1, a scatter: where each technique sits on the two jobs at once (evidence against start nodes), with
  95 % Wilson intervals on both axes; three hues (the scatter cap), technique by shape and fill;
- fig2, small multiples of lines (the emphasis form): recall against K, one family coloured per panel, the
  rest gray, so nine series never share one colour scheme;
- fig3, heatmaps (a magnitude on a grid, one sequential hue): every technique x K per dataset, the count in
  each cell, so the per-dataset detail is readable without a table;
- fig4, diverging bars (polarity): the questions only the card technique or only the text technique gets
  fully right, per K, with the exact McNemar p; card orange to the right, text blue to the left;
- fig5, lines (emphasis): the share of lists shorter than K, which explains why claims and the name linker
  level off.
K is drawn on a log axis with ticks at the measured budgets (5 to 50 spans a factor of ten). One y-scale per
panel, never two.
Must not: compute a result; every number comes from `r128_data`.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, NullLocator, PercentFormatter

from . import r128_data as data
from .style import (
    AXIS,
    BY_SYSTEM,
    CONTEXT,
    INK,
    INK_SECONDARY,
    NODE_ORANGE,
    SEQUENTIAL,
    SURFACE,
    TECHNIQUES,
    TEXT_BLUE,
    Technique,
    save,
    title,
)

OUT = Path(__file__).resolve().parents[1] / "results" / "retrieval" / "r128_techniques"
FAMILIES = (
    ("Chunks", ("chunk_dense", "chunk_lexical")),
    ("Claims", ("claim_dense", "claim_lexical")),
    ("Template cards (A)", ("card_dense_template", "card_lexical_template")),
    ("LLM summaries (B)", ("card_dense_summary", "card_lexical_summary")),
    ("Name linker (reference)", ("graph_retrieval",)),
)


def draw_all(table, pairings, out: Path = OUT) -> list[Path]:
    """Every R128 chart into `out`; returns the files written."""
    return [
        *fig1_evidence_vs_start_nodes(table, out),
        *fig2_recall_by_k(table, out),
        *fig3_per_dataset_heatmaps(table, out),
        *fig4_paired_card_vs_text(pairings, out),
        *fig5_short_lists(table, out),
    ]


def _k_axis(ax, budgets: list[int]) -> None:
    """A log K axis with ticks at the measured budgets only."""
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator(budgets))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([str(k) for k in budgets])
    ax.set_xlim(budgets[0] * 0.86, budgets[-1] * 1.16)


def _point(ax, t: Technique, x: float, y: float, size: float = 8.5) -> None:
    """One technique's marker with a white surface ring, so overlapping marks stay legible."""
    ax.plot([x], [y], linestyle="none", marker=t.marker, markersize=size + 3, color=SURFACE, zorder=3)
    ax.plot([x], [y], linestyle="none", zorder=4, **t.marker_style(size))


def _legend_handles(techniques) -> list[Line2D]:
    return [Line2D([], [], linestyle="none", label=t.label, **t.marker_style(7.5)) for t in techniques]


# --- fig1: evidence against start nodes ---------------------------------------------------------------

# where each technique's name goes in the K = 5 panel, placed after rendering so that no label collides:
# (x, y) of the text in data units and its alignment; the four in the crowded middle get a leader line
_LABEL_AT_5 = {
    "chunk_dense": (0.808, 0.505, "right", False),
    "chunk_lexical": (0.878, 0.470, "left", False),
    "claim_dense": (0.836, 0.700, "left", False),
    "claim_lexical": (0.605, 0.640, "left", True),
    "card_dense_template": (0.605, 0.800, "left", True),
    "card_lexical_template": (0.806, 0.790, "left", False),
    "card_dense_summary": (0.775, 0.640, "left", True),
    "card_lexical_summary": (0.605, 0.735, "left", True),
    "graph_retrieval": (0.856, 0.758, "left", False),
}
# claim_lexical and card_dense_summary have the same counts at K = 5 (74 / 99, 147 / 213): drawn side by side
_DODGE = {"claim_lexical": -0.007, "card_dense_summary": 0.007}


def fig1_evidence_vs_start_nodes(table, out: Path) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.6), sharex=True, sharey=True)
    for ax, k in zip(axes, (5, 20), strict=True):
        for t in TECHNIQUES:
            x, y = _tradeoff_point(ax, table, t, k)
            if k == 5:
                _tradeoff_label(ax, t, x, y)
        ax.set_title("K = 5 (what the reader reads)" if k == 5 else "K = 20", loc="left")
        ax.set_xlabel("Evidence: questions with all gold chunks in the top K (n = 99)")
        ax.set_xlim(0.6, 1.0)
        ax.set_ylim(0.4, 1.0)
        ax.set_xticks([0.6, 0.7, 0.8, 0.9, 1.0])
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    axes[0].set_ylabel("Start nodes: targets found in the top K seeds (n = 213)")
    fig.legend(handles=_legend_handles(TECHNIQUES), loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.06))
    title(
        fig,
        "Text retrieval finds evidence; node cards find start nodes",
        "Each technique alone, pooled over furniture, held-out and generality (R128). Bars: 95 % Wilson "
        "intervals. Up and right is better on both jobs.",
    )
    fig.subplots_adjust(top=0.86, bottom=0.17, wspace=0.12)
    return save(fig, out, "r128_fig1_evidence_vs_start_nodes")


def _tradeoff_point(ax, table, t: Technique, k: int) -> tuple[float, float]:
    """One technique at budget k: Complete@k against Seed Recall@k, with both Wilson intervals."""
    i = table.budgets.index(k)
    ev = data.series(table, "pooled", t.system, "complete")[i]
    sd = data.series(table, "pooled", t.system, "seed_recall")[i]
    x = ev.rate + (_DODGE.get(t.system, 0.0) if k == 5 else 0.0)
    ax.errorbar(
        x,
        sd.rate,
        xerr=[[ev.rate - ev.low], [ev.high - ev.rate]],
        yerr=[[sd.rate - sd.low], [sd.high - sd.rate]],
        color=t.color,
        alpha=0.3,
        lw=0.9,
        zorder=2,
    )
    _point(ax, t, x, sd.rate)
    return x, sd.rate


def _tradeoff_label(ax, t: Technique, x: float, y: float) -> None:
    tx, ty, ha, leader = _LABEL_AT_5[t.system]
    arrow = {
        "arrowstyle": "-",
        "color": INK_SECONDARY,
        "lw": 0.6,
        "shrinkA": 1,
        "shrinkB": 6,
        "relpos": (1, 0.5) if tx < x else (0, 0.5),
    }
    ax.annotate(
        t.label,
        (x, y),
        xytext=(tx, ty),
        textcoords="data",
        ha=ha,
        va="center",
        fontsize=7.5,
        color=INK_SECONDARY,
        arrowprops=arrow if leader else None,
    )


# --- fig2: recall against K, one family per panel --------------------------------------------------------


def fig2_recall_by_k(table, out: Path) -> list[Path]:
    fig, axes = plt.subplots(2, 5, figsize=(14, 6.2), sharex=True, sharey="row")
    rows = (
        ("complete", (0.70, 1.0), "Evidence: Complete@K\n(of 99 questions)"),
        ("seed_recall", (0.45, 1.0), "Start nodes: Seed Recall@K\n(of 213 targets)"),
    )
    for r, (measure, ylim, ylabel) in enumerate(rows):
        for c, (family, systems) in enumerate(FAMILIES):
            ax = axes[r][c]
            _recall_panel(ax, table, measure, systems)
            ax.set_ylim(*ylim)
            ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
            _k_axis(ax, table.budgets)
            if r == 0:
                ax.set_title(family, loc="left")
            if c == 0:
                ax.set_ylabel(ylabel)
            if r == 1:
                ax.set_xlabel("budget K (log scale)")
    title(
        fig,
        "How each technique's recall grows with the budget K",
        "Pooled over three datasets (R128). Coloured: the panel's technique, filled marker dense, hollow "
        "lexical; gray: the other eight, for context.",
    )
    fig.subplots_adjust(top=0.85, hspace=0.18, wspace=0.08)
    return save(fig, out, "r128_fig2_recall_by_k")


def _recall_panel(ax, table, measure: str, systems: tuple[str, ...]) -> None:
    """All techniques in gray, then the panel's own in their colour with markers and a small legend."""
    budgets = table.budgets
    for t in TECHNIQUES:
        if t.system not in systems:
            ax.plot(
                budgets,
                [p.rate for p in data.series(table, "pooled", t.system, measure)],
                color=CONTEXT,
                lw=1,
                zorder=1,
            )
    handles = []
    for system in systems:
        t = BY_SYSTEM[system]
        rates = [p.rate for p in data.series(table, "pooled", system, measure)]
        ax.plot(budgets, rates, color=t.color, zorder=3)
        for k, y in zip(budgets, rates, strict=True):
            _point(ax, t, k, y, size=6.5)
        handles.append(
            Line2D([], [], color=t.color, label="dense" if t.dense else "lexical", **t.marker_style(6.5))
        )
    if len(handles) > 1:
        ax.legend(handles=handles, loc="lower right")


# --- fig3: per dataset, every technique x K ----------------------------------------------------------------


def fig3_per_dataset_heatmaps(table, out: Path) -> list[Path]:
    # not shared: each panel inverts its own y-axis (a shared axis would flip back and forth)
    fig, axes = plt.subplots(2, 3, figsize=(13, 8.4))
    # one colour range per row: evidence rates lie in 60-100 %, start-node rates in 30-100 %; one range for
    # both would leave the evidence row almost uniformly dark
    rows = (
        ("complete", "Evidence: Complete@K", (0.6, 1.0)),
        ("seed_recall", "Start nodes: Seed Recall@K", (0.3, 1.0)),
    )
    fig.subplots_adjust(top=0.89, right=0.86, hspace=0.22, wspace=0.05)
    for r, (measure, name, scale) in enumerate(rows):
        mesh = None
        for c, dataset in enumerate(data.DATASETS):
            mesh = _heatmap(axes[r][c], table, dataset, measure, name, scale, first_column=c == 0)
        box = axes[r][2].get_position()
        cax = fig.add_axes((0.88, box.y0 + box.height * 0.1, 0.012, box.height * 0.8))
        bar = fig.colorbar(mesh, cax=cax)
        bar.set_label(f"{name.split(':')[0]}: share of the panel's n", fontsize=8)
        bar.outline.set_visible(False)
        bar.ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    title(
        fig,
        "Each technique on each dataset, at every budget",
        "Cell colour: the rate (one blue scale per row); cell number: the count of the n in the panel title. "
        "Read along a row for K, down a column for the techniques (R128).",
    )
    return save(fig, out, "r128_fig3_per_dataset_heatmaps")


def _heatmap(
    ax, table, dataset: str, measure: str, name: str, scale: tuple[float, float], first_column: bool
):
    """One technique x K grid: the rate as colour, the count as text (white on the scale's darker half)."""
    rows = [data.series(table, dataset, t.system, measure) for t in TECHNIQUES]
    rates = [[p.rate for p in row] for row in rows]
    low, high = scale
    mesh = ax.pcolormesh(rates, cmap=SEQUENTIAL, vmin=low, vmax=high, edgecolors=SURFACE, linewidth=2)
    for i, row in enumerate(rows):
        for j, p in enumerate(row):
            dark = (p.rate - low) / (high - low) > 0.5
            ax.text(
                j + 0.5,
                i + 0.5,
                str(p.k),
                ha="center",
                va="center",
                fontsize=7.5,
                color=SURFACE if dark else INK,
            )
    ax.set_title(f"{data.LABELS[dataset]}\n{name}, of {rows[0][0].n}", loc="left", fontsize=9)
    ax.set_xticks([j + 0.5 for j in range(len(table.budgets))], [f"K={k}" for k in table.budgets])
    labels = [t.label for t in TECHNIQUES] if first_column else []
    ax.set_yticks([i + 0.5 for i in range(len(TECHNIQUES))], labels)
    ax.invert_yaxis()
    ax.grid(False)
    ax.tick_params(length=0)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    return mesh


# --- fig4: paired card against text ----------------------------------------------------------------------


def fig4_paired_card_vs_text(pairings, out: Path) -> list[Path]:
    panels = list(dict.fromkeys((p.measure, p.card, p.text) for p in pairings))
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 6.6), sharex=True)
    order = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]
    for (r, c), (measure, card, text) in zip(order, panels, strict=True):
        rows = [p for p in pairings if (p.measure, p.card, p.text) == (measure, card, text)]
        _paired_panel(axes[r][c], rows, measure)
    axes[1][2].axis("off")
    axes[1][2].text(0, 0.95, _PAIRED_KEY, va="top", ha="left", fontsize=8, color=INK_SECONDARY)
    title(
        fig,
        "Where card and text techniques disagree, question by question",
        "Pooled over three datasets (R128); exploratory pairings, chosen after the table was seen. Bars: "
        "questions only one of the two gets fully right.",
    )
    fig.subplots_adjust(top=0.86, hspace=0.42, wspace=0.58)
    return save(fig, out, "r128_fig4_paired_card_vs_text")


_PAIRED_KEY = (
    "How to read\n\n"
    "Right (orange): questions only the card\ntechnique gets fully right.\n"
    "Left (blue): questions only the text\ntechnique gets fully right.\n"
    "Questions both or neither get right are\nleft out: they say nothing about which\nis better.\n\n"
    "Start nodes: every target in the top K seeds.\n"
    "Evidence: every gold chunk in the top K.\n\n"
    "p: exact McNemar test. Bold: p < 0.00125,\nwhich survives a Bonferroni correction\nover the 40 "
    "exploratory tests."
)


def _paired_panel(ax, rows, measure: str) -> None:
    """Diverging bars per K: only-text to the left, only-card to the right, the count at each bar end."""
    card, text = BY_SYSTEM[rows[0].card], BY_SYSTEM[rows[0].text]
    ys = list(range(len(rows)))
    ax.barh(ys, [-p.only_text for p in rows], height=0.62, color=TEXT_BLUE)
    ax.barh(ys, [p.only_card for p in rows], height=0.62, color=NODE_ORANGE)
    for y, p in zip(ys, rows, strict=True):
        ax.text(
            -p.only_text - 1.5,
            y,
            str(p.only_text),
            ha="right",
            va="center",
            fontsize=7.5,
            color=INK_SECONDARY,
        )
        ax.text(
            p.only_card + 1.5, y, str(p.only_card), ha="left", va="center", fontsize=7.5, color=INK_SECONDARY
        )
        label = "p < 0.001" if p.p < 0.001 else f"p = {p.p:.3f}"
        ax.text(
            1.02,
            y,
            label,
            transform=ax.get_yaxis_transform(),
            ha="left",
            va="center",
            fontsize=7.5,
            color=INK,
            fontweight="bold" if p.p < 0.00125 else "normal",
        )
    ax.axvline(0, color=AXIS, lw=1)
    ax.set_yticks(ys, [f"K = {p.k}" for p in rows])
    ax.invert_yaxis()
    ax.set_xlim(-70, 70)
    ax.set_xticks([-60, -40, -20, 0, 20, 40, 60], ["60", "40", "20", "0", "20", "40", "60"])
    ax.grid(axis="y", visible=False)
    job = "Start nodes" if measure == "seeds" else "Evidence"
    ax.set_title(f"{job}: {card.label}\nagainst {text.label}", loc="left", fontsize=9)
    # which side is which, under the axis: the title names both techniques
    ax.text(
        0.0,
        -0.2,
        "← only the text technique",
        transform=ax.transAxes,
        ha="left",
        fontsize=7.5,
        color=INK_SECONDARY,
    )
    ax.text(
        1.0,
        -0.2,
        "only the card technique →",
        transform=ax.transAxes,
        ha="right",
        fontsize=7.5,
        color=INK_SECONDARY,
    )


# --- fig5: lists shorter than K ----------------------------------------------------------------------------

_SHORT_FOCUS = ("claim_dense", "claim_lexical", "graph_retrieval")


def fig5_short_lists(table, out: Path) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), sharey=True)
    panels = (
        ("chunks", "Chunk lists shorter than K\n(share of 99 questions with evidence)"),
        ("seeds", "Seed lists shorter than K\n(share of 141 questions with targets)"),
    )
    for ax, (lists, name) in zip(axes, panels, strict=True):
        for t in TECHNIQUES:
            shares = data.shortfall(table, "pooled", t.system, lists)
            focus = t.system in _SHORT_FOCUS
            ax.plot(
                table.budgets,
                shares,
                color=t.color if focus else CONTEXT,
                lw=2 if focus else 1,
                zorder=3 if focus else 1,
            )
            if focus:
                for k, y in zip(table.budgets, shares, strict=True):
                    _point(ax, t, k, y, size=6.5)
        _k_axis(ax, table.budgets)
        ax.set_ylim(-0.02, 1.02)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_title(name, loc="left", fontsize=9)
        ax.set_xlabel("budget K (log scale)")
    handles = _legend_handles([BY_SYSTEM[s] for s in _SHORT_FOCUS])
    handles.append(Line2D([], [], color=CONTEXT, lw=1, label="the other techniques"))
    fig.legend(handles=handles, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.07))
    title(
        fig,
        "Why claims and the name linker level off: their lists run out",
        "Pooled (R128). A list shorter than K is scored as it is. At K = 50 most chunk lists are short too: "
        "generality has 32 chunks, furniture 70, held-out 81.",
    )
    fig.subplots_adjust(top=0.80, bottom=0.2, wspace=0.08)
    return save(fig, out, "r128_fig5_short_lists")


__all__ = ["OUT", "draw_all"]
