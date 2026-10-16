"""The three result charts of R231: the query engine's question-answering accuracy on the four selected
datasets (NTSB, furniture, held-out, generality), drawn from `r231_data`.

Role: `build.py` calls `draw_all`; the files land in `figures/results/evaluation/r231_qa_four_datasets/`,
whose README.md is their caption list.
Design: the forms of R157 and R173 (dataviz skill, choosing-a-form), with their panel routines:
- fig1 and fig2: dots with 95 % Wilson intervals, the two scores as `style.Score`s (exact match a blue
  square, by meaning an orange circle), one panel per measurement, k/n at the right of each row;
- fig3: the benchmark's wrong answers by cause as bars, coloured by which score they fail.
One axis per panel, never two; rates as percent with n in the titles.
Must not: compute a result; every number comes from `r231_data` and `r157_data`.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from . import r157_data as base
from . import r173_data
from . import r231_data as data
from .r157_charts import _accuracy_panel, _cause_bars, _legend
from .r173_charts import _panels
from .r229_data import SELECTED
from .style import EXACT, INK_SECONDARY, MEANING, PRINT_WIDTH, SURFACE_TINT, save, title

OUT = Path(__file__).resolve().parents[1] / "results" / "evaluation" / "r231_qa_four_datasets"
# y positions: the new dataset, a gap, the three thesis datasets, a gap, pooled
YS = {"ntsb": 0.0, "furniture": 1.4, "heldout": 2.4, "generality": 3.4, "pooled": 4.8}
SCOPE = "Four datasets: NTSB, furniture, held-out, generality."


def draw_all(measurements: list[r173_data.Measurement], out: Path = OUT) -> list[Path]:
    """The three R231 charts into `out`; returns the files written."""
    return [
        *fig1_accuracy_by_dataset(measurements, out),
        *fig2_accuracy_by_type(measurements, out),
        *fig3_failures_by_cause(measurements[0], out),
    ]


def fig1_accuracy_by_dataset(measurements: list[r173_data.Measurement], out: Path) -> list[Path]:
    fig, axes = _panels(len(measurements), 5.0, width=PRINT_WIDTH - 1.1)
    pooled = []
    for ax, m in zip(axes, measurements, strict=True):
        by_group = {a.group: a for a in data.by_dataset(list(m.outcomes))}
        ax.add_patch(
            Rectangle(
                (0, -0.5), 1, 1.0, transform=ax.get_yaxis_transform(), color=SURFACE_TINT, zorder=0, lw=0
            )
        )
        rows = [(YS[g], data.LABELS[g], by_group[g]) for g in (*SELECTED, "pooled")]
        _accuracy_panel(ax, rows, bold={"pooled"})
        ax.axhline(4.1, color=INK_SECONDARY, lw=0.6)
        ax.set_ylim(5.35, -0.95)
        per = by_group[SELECTED[0]].meaning.n
        ax.set_title(
            f"{m.label.split(',')[0]}, {by_group['pooled'].meaning.n} questions\n(n = {per} per dataset)",
            loc="left",
            fontsize=9,
        )
        ax.set_xlabel("Right (95 % Wilson interval)")
        if ax is not axes[0]:
            ax.tick_params(labelleft=False)
        pooled.append(by_group["pooled"].meaning)
    for y, text in ((-0.75, "New dataset"), (0.85, "Thesis datasets")):
        axes[0].text(0.752, y, text, ha="left", va="center", fontsize=8, color=INK_SECONDARY, style="italic")
    _legend(fig, top=0.885)
    title(
        fig,
        "On unseen questions the agent answers "
        + ", ".join(f"{p.k}/{p.n}" for p in pooled)
        + " right by meaning on the four datasets",
        f"{SCOPE} Three measurements in time order, each on its own questions written before any output: the "
        "frozen engine (R157), and before and after the R167-R170 fixes (R166, R173).\nEach row: exact match "
        "(square) above the score by meaning (circle); lines are 95 % Wilson intervals; the counts at "
        "right are k/n. The axis starts at 75 %. The question sets differ, so a difference is not "
        "attributed to the fixes.",
    )
    fig.subplots_adjust(top=0.74, left=0.12, right=0.93, bottom=0.12, wspace=0.95)
    return save(fig, out, "r231_fig1_accuracy_by_dataset")


def fig2_accuracy_by_type(measurements: list[r173_data.Measurement], out: Path) -> list[Path]:
    fig, axes = _panels(len(measurements), 4.6, width=PRINT_WIDTH - 0.7)
    for ax, m in zip(axes, measurements, strict=True):
        accuracy = base.by_type(list(m.outcomes))
        rows = [(float(i), base.TYPE_LABELS[a.group], a) for i, a in enumerate(accuracy)]
        _accuracy_panel(ax, rows, bold=set())
        # the confirmation sets have 12-17 questions per type, so intervals reach down to about 55 %
        ax.set_xlim(0.5, 1.005)
        ax.set_ylim(len(rows) - 0.45, -0.55)
        ax.set_title(f"{m.label.split(',')[0]},\n{len(m.outcomes)} questions", loc="left", fontsize=9)
        ax.set_xlabel("Right (95 % Wilson interval)")
        if ax is not axes[0]:
            ax.tick_params(labelleft=False)
    _legend(fig, top=0.885)
    # the weakest type by meaning in each measurement, named from the data (it is not the same one each time)
    weakest = [min(base.by_type(list(m.outcomes)), key=lambda a: a.meaning.rate) for m in measurements]
    floor = min(a.meaning.rate for a in weakest)
    title(
        fig,
        f"Every question type stays at {floor:.0%} or more by meaning; the weakest is "
        + ", ".join(f"{base.TYPE_LABELS[a.group].lower()} ({a.meaning.k}/{a.meaning.n})" for a in weakest),
        f"{SCOPE} Each measurement's questions by the type the gold gives them. Square: exact match; circle: "
        "by meaning;\nlines are 95 % Wilson intervals; the counts at right are k/n. The axis starts at 50 %; "
        "an interval of 12-17 questions is wide.",
    )
    fig.subplots_adjust(top=0.76, left=0.12, right=0.93, bottom=0.13, wspace=0.95)
    return save(fig, out, "r231_fig2_accuracy_by_type")


def fig3_failures_by_cause(benchmark: r173_data.Measurement, out: Path) -> list[Path]:
    outcomes = list(benchmark.outcomes)
    causes, failed = base.by_cause(outcomes), base.wrong(outcomes)
    fig, ax = plt.subplots(figsize=(PRINT_WIDTH, 5.6))
    _cause_bars(ax, causes, len(failed))
    meaning_wrong = sum(not o.meaning for o in failed)
    fig.legend(
        handles=[
            Patch(color=MEANING.color, label=f"Wrong by both scores ({meaning_wrong})"),
            Patch(
                color=EXACT.color,
                label=f"Wrong by exact match only, right by meaning ({len(failed) - meaning_wrong})",
            ),
        ],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.9),
        ncol=2,
    )
    title(
        fig,
        f"{len(failed)} of {len(outcomes)} benchmark answers are wrong by either score; every one wrong by "
        "meaning involves the agent's reasoning",
        f"{SCOPE} R157: the wrong answers, each traced to its causes; a question can have several, so the "
        f"bars add up to more than {len(failed)}.\nQuestion ids at each bar: FB furniture, HB held-out, "
        "GB generality; no NTSB answer was wrong.",
    )
    fig.subplots_adjust(top=0.78, left=0.3, right=0.97, bottom=0.11)
    return save(fig, out, "r231_fig3_failures_by_cause")
