"""The three result charts of R157 (the benchmark: the frozen agent on 250 questions written before any
output, five datasets), drawn from `r157_data`.

Role: `build.py` calls `draw_all`; the files land in `figures/results/evaluation/r157_benchmark/`, whose
README.md is their caption list.
Design: the form follows the data's job (dataviz skill, choosing-a-form):
- fig1 and fig2, dots with 95 % Wilson intervals on one percent axis (two rates per group, both near the
  top): bars from zero would spend the width on the 75 % every group clears and hide the intervals, which
  are the point at n = 50. The two scores are the two `style.Score`s (exact match a blue square, by meaning
  an orange circle), drawn one above the other in each row; each row's counts are written as k/n at the
  right, so no value is read from the axis alone. In fig1 the two new datasets sit on a gray band and the
  pooled row is set apart, so new and thesis datasets read as groups;
- fig3, horizontal bars, a count per cause (causes overlap, so the bars are not parts of the 11 wrong
  answers), coloured by which score the answers fail: orange wrong by both scores, blue wrong by exact match
  only, the two groups set apart and labelled; the question ids are written at each bar, so a question with
  several causes can be followed from bar to bar.
One axis per panel, never two; rates as percent with n in the row labels.
Must not: compute a result; every number comes from `r157_data`.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import PercentFormatter

from . import r157_data as data
from .style import EXACT, INK, INK_SECONDARY, MEANING, SURFACE, SURFACE_TINT, Score, save, title

OUT = Path(__file__).resolve().parents[1] / "results" / "evaluation" / "r157_benchmark"
OFFSET = 0.17  # how far each score's dot sits above or below its row's centre


def draw_all(outcomes: list[data.Outcome], split: data.ExactSplit, out: Path = OUT) -> list[Path]:
    """The three R157 charts into `out`; returns the files written."""
    return [
        *fig1_accuracy_by_dataset(data.by_dataset(outcomes), split, out),
        *fig2_accuracy_by_type(data.by_type(outcomes), out),
        *fig3_failures_by_cause(data.by_cause(outcomes), len(data.wrong(outcomes)), len(outcomes), out),
    ]


# --- fig1 and fig2: both scores with their intervals ------------------------------------------------------


def _dot(ax, y: float, p, score: Score, size: float) -> None:
    """One score's rate with its Wilson interval, the marker ringed in the surface colour."""
    ax.errorbar(
        p.rate,
        y,
        xerr=[[p.rate - p.low], [p.high - p.rate]],
        fmt="none",
        ecolor=score.color,
        elinewidth=1.6,
        capsize=0,
        zorder=2,
    )
    ax.plot(
        p.rate,
        y,
        marker=score.marker,
        markersize=size,
        color=score.color,
        markeredgecolor=SURFACE,
        markeredgewidth=1.2,
        linestyle="none",
        zorder=3,
    )


def _accuracy_panel(ax, rows: list[tuple[float, str, data.Accuracy]], bold: set[str]) -> None:
    """Rows of (y, label, accuracy): both dots, the y labels, and each row's counts at the right."""
    for y, _, acc in rows:
        size = 9.0 if acc.group in bold else 7.5
        _dot(ax, y - OFFSET, acc.exact, EXACT, size)
        _dot(ax, y + OFFSET, acc.meaning, MEANING, size)
        weight = "bold" if acc.group in bold else "normal"
        counts = f"{acc.exact.k}/{acc.exact.n}  ·  {acc.meaning.k}/{acc.meaning.n}"
        right = ax.get_yaxis_transform()
        ax.text(1.02, y, counts, transform=right, va="center", color=INK, fontsize=8.5, fontweight=weight)
    ax.set_yticks([y for y, _, _ in rows], [label for _, label, _ in rows])
    for tick, (_, _, acc) in zip(ax.get_yticklabels(), rows, strict=True):
        tick.set_fontweight("bold" if acc.group in bold else "normal")
        tick.set_color(INK)
    ax.text(
        1.02, 1.01, "exact  ·  meaning", transform=ax.transAxes, va="bottom", fontsize=8, color=INK_SECONDARY
    )
    # the axis starts at 75 %: every interval's low end lies above it (the lowest, 32 of 35, is 78 %), and
    # dots carry no area that a cut axis would distort
    ax.set_xlim(0.75, 1.005)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    ax.invert_yaxis()


def _legend(fig, top: float = 0.915) -> None:
    """The two scores' key, upper left under the title; `top` moves it below a longer subtitle."""
    handles = [
        Line2D([], [], marker=s.marker, color=s.color, mec=SURFACE, markersize=8, lw=1.6, label=s.label)
        for s in (EXACT, MEANING)
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, top), ncol=2)


def fig1_accuracy_by_dataset(accuracy: list[data.Accuracy], split: data.ExactSplit, out: Path) -> list[Path]:
    by_group = {a.group: a for a in accuracy}
    # y positions: the two new datasets, a gap, the three thesis datasets, a gap, pooled
    ys = {"ntsb": 0.0, "lighthouses": 1.0, "furniture": 2.4, "heldout": 3.4, "generality": 4.4, "pooled": 5.8}
    fig, ax = plt.subplots(figsize=(9.6, 6.0))
    ax.add_patch(
        Rectangle((0, -0.5), 1, 2.0, transform=ax.get_yaxis_transform(), color=SURFACE_TINT, zorder=0, lw=0)
    )
    rows = [(ys[g], data.LABELS[g], by_group[g]) for g in (*data.DATASETS, "pooled")]
    _accuracy_panel(ax, rows, bold={"pooled"})
    ax.axhline(5.1, color=INK_SECONDARY, lw=0.6)
    ax.set_ylim(6.35, -0.95)
    for y, text in ((-0.75, "New datasets (fetched for the benchmark)"), (1.85, "Thesis datasets")):
        ax.text(0.752, y, text, ha="left", va="center", fontsize=8, color=INK_SECONDARY, style="italic")
    pooled, per = by_group["pooled"].meaning, by_group[data.DATASETS[0]].meaning.n
    ax.set_xlabel(
        f"Questions answered right (95 % Wilson interval); n = {per} per dataset, {pooled.n} pooled"
    )
    _legend(fig, top=0.86)
    title(
        fig,
        f"The frozen agent answers {pooled.k} of {pooled.n} unseen questions right by meaning, "
        "as well on the new datasets as on the thesis ones",
        f"R156-R157: {per} gold questions per dataset, written before any output. Each row: the exact-match "
        "score (square) above the score by meaning (circle);\nlines are 95 % Wilson intervals; the counts at "
        "right are k/n. The axis starts at 75 %. Exact match: code scored "
        f"{split.code.k}/{split.code.n}; the {split.free_text.n} free-text answers have no exact form,\nso "
        f"their exact-match outcome is the judges' verdict ({split.free_text.k}/{split.free_text.n} right).",
    )
    fig.subplots_adjust(top=0.755, left=0.17, right=0.84, bottom=0.11)
    return save(fig, out, "r157_fig1_accuracy_by_dataset")


def fig2_accuracy_by_type(accuracy: list[data.Accuracy], out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(9.6, 5.0))
    rows = [(float(i), f"{data.TYPE_LABELS[a.group]} (n = {a.meaning.n})", a) for i, a in enumerate(accuracy)]
    _accuracy_panel(ax, rows, bold=set())
    ax.set_ylim(len(rows) - 0.45, -0.55)
    ax.set_xlabel("Questions answered right (95 % Wilson interval), pooled over the five datasets")
    # the two hardest types by meaning: their intervals overlap (and by exact match they nearly tie), so the
    # title names both rather than one weakest type
    low, next_low = sorted(accuracy, key=lambda a: (a.meaning.rate, a.exact.rate))[:2]
    _legend(fig)
    title(
        fig,
        f"{data.TYPE_LABELS[low.group]} and {data.TYPE_LABELS[next_low.group].lower()} are the hardest "
        f"types ({low.meaning.k}/{low.meaning.n}, {next_low.meaning.k}/{next_low.meaning.n} by meaning); "
        "intervals overlap",
        "R156-R157: the 250 benchmark questions by the type the gold gives them (n per type in the labels). "
        "Square: exact match; circle: by meaning;\nlines are 95 % Wilson intervals; the counts at right are "
        "k/n. The axis starts at 75 %.",
    )
    fig.subplots_adjust(top=0.8, left=0.21, right=0.84, bottom=0.12)
    return save(fig, out, "r157_fig2_accuracy_by_type")


# --- fig3: the wrong answers by cause ----------------------------------------------------------------------

# a cause class as a reader sees it: the name, and what it was in the 11 traces (REFACTOR_PLAN R157)
_CAUSE_LABELS = {
    "agent_reasoning": "Agent reasoning\ncounted by eye, searched once,\n"
    "misread a column, spent the call budget",
    "tool_limit": "Tool limit\nno anti-join, literal search, no paging,\n"
    "a schema showing only the lowest values",
    "retrieval_miss": "Retrieval miss\nother passages ranked first",
    "graph_build": "Graph build\na handle stored without its @",
    "answer_form": "Answer form\nright, in a form code cannot read",
}


def fig3_failures_by_cause(causes: list[data.CauseCount], n_wrong: int, n: int, out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(10.4, 5.8))
    _cause_bars(ax, causes, n_wrong)
    meaning_wrong = len({q for c in causes for q in c.both})
    tool = next(c.total for c in causes if c.cause == "tool_limit")
    exact_only = len({q for c in causes for q in c.exact_only})
    fig.legend(
        handles=[
            Patch(color=MEANING.color, label=f"Wrong by both scores ({meaning_wrong})"),
            Patch(color=EXACT.color, label=f"Wrong by exact match only, right by meaning ({exact_only})"),
        ],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.9),
        ncol=2,
    )
    title(
        fig,
        f"All {meaning_wrong} answers wrong by meaning involve the agent's reasoning, {tool} with a tool "
        f"limit; {exact_only} more miss exact match only by their form",
        f"R157: the {n_wrong} of {n} benchmark answers wrong by either score, each traced to its causes; a "
        "question can have several, so the bars add up to more than "
        f"{n_wrong}.\nQuestion ids at each bar: {_prefixes(causes)}.",
    )
    fig.subplots_adjust(top=0.82, left=0.29, right=0.98, bottom=0.13)
    return save(fig, out, "r157_fig3_failures_by_cause")


def _cause_bars(ax, causes: list[data.CauseCount], n_wrong: int) -> None:
    """One bar per cause, its count and ids; the causes of answers wrong by both scores first (longest first,
    as `data.CAUSES` lists them), then, after a rule, the causes of answers wrong by exact match only."""
    first = [c for c in causes if c.both]
    second = [c for c in causes if not c.both]
    gap = 0.6  # the extra space between the two groups, where the rule and the second group's label sit
    ys = [float(i) for i in range(len(first))] + [len(first) + gap + i for i in range(len(second))]
    ordered = first + second
    both = [len(c.both) for c in ordered]
    # a bar's exact-only part is stacked after its both part; no recorded cause has both, but a record that
    # did would still be drawn whole. The 2px surface gap between segments is the dataviz marks spec
    ax.barh(ys, both, height=0.62, color=MEANING.color, edgecolor=SURFACE, lw=1.5)
    only_exact = [len(c.exact_only) for c in ordered]
    ax.barh(ys, only_exact, left=both, height=0.62, color=EXACT.color, edgecolor=SURFACE, lw=1.5)
    for y, c in zip(ys, ordered, strict=True):
        ids = ", ".join((*c.both, *c.exact_only))
        ax.text(c.total + 0.15, y, f"{c.total}   {ids}", ha="left", va="center", fontsize=8.5, color=INK)
    rule = len(first) - 0.5 + gap / 2 + 0.2
    ax.axhline(rule, color=INK_SECONDARY, lw=0.6)
    for y, text in (
        (-0.62, "Answers wrong by both scores"),
        (rule + 0.12, "Answers wrong by exact match only"),
    ):
        ax.text(0.06, y, text, ha="left", va="top" if y > 0 else "center", fontsize=8, color=INK_SECONDARY)
    ax.set_yticks(ys, [_CAUSE_LABELS[c.cause] for c in ordered])
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
        tick.set_linespacing(1.5)
    ax.set_ylim(ys[-1] + 0.55, -0.95)  # inverted: the first cause at the top
    # the room at the right holds the id lists; the axis line stops at the last tick
    ax.set_xlim(0, 13.5)
    ax.set_xticks(range(0, n_wrong + 1))
    ax.spines["bottom"].set_bounds(0, n_wrong)
    ax.set_xlabel(f"Wrong answers traced to the cause (of the {n_wrong} wrong by either score)")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)


# the id prefix of each dataset's benchmark questions (tests/gold/qa/<dataset>_qa_bench.json)
_PREFIX = {"ntsb": "NB", "lighthouses": "LB", "furniture": "FB", "heldout": "HB", "generality": "GB"}


def _prefixes(causes: list[data.CauseCount]) -> str:
    """The id prefixes that occur at the bars, and the datasets with no wrong answer."""
    ids = {q for c in causes for q in (*c.both, *c.exact_only)}
    seen = [d for d in data.DATASETS if any(q.startswith(_PREFIX[d]) for q in ids)]
    text = ", ".join(f"{_PREFIX[d]} {data.LABELS[d]}" for d in seen)
    clean = [data.LABELS[d] for d in data.DATASETS if d not in seen]
    return text + (f"; no {' or '.join(clean)} answer was wrong" if clean else "")
