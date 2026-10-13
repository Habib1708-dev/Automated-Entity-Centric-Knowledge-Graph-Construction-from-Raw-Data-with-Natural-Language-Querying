"""The four result charts of R174: the agent's three measurements on unseen questions (R157 the frozen
engine, R166 before the R167-R170 fixes, R173 after them) side by side, drawn from `r173_data`.

Role: `build.py` calls `draw_all`; the files land in `figures/results/evaluation/r173_confirmation/`, whose
README.md is their caption list.
Design: the form follows the data's job (dataviz skill, choosing-a-form), and the three measurements are
small multiples, one panel each in time order, never three hues (the style's cap is three, and two are the
scores'):
- fig1 and fig2, the R157 dot-and-interval panel (`r157_charts._accuracy_panel`) once per measurement, the
  rows shared, so a dataset or a type is read across the panels on one line; both scores in each row;
- fig3, the wrong answers by cause, one bar panel per measurement on one x scale, so the bars shrink from
  panel to panel as the record does; the ids at each bar;
- fig4, the cost of each run as a bar on one scale, the wasted run a gray segment beside its scored run,
  and the cents a question written at each bar (one headline each, so no second axis).
One axis per panel, never two; rates as percent with n in the labels; dollar signs escaped.
Must not: compute a result; every number comes from `r173_data` (and `r157_data` for the rates).
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from . import r157_data as base
from . import r173_data as data
from .r157_charts import _accuracy_panel, _legend
from .style import CONTEXT, EXACT, INK, INK_SECONDARY, MEANING, SURFACE, SURFACE_TINT, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "results" / "evaluation" / "r173_confirmation"


def draw_all(measurements: list[data.Measurement], costs: list[data.RunCost], out: Path = OUT) -> list[Path]:
    """The four R174 charts into `out`; returns the files written."""
    return [
        *fig1_accuracy_by_dataset(measurements, out),
        *fig2_accuracy_by_type(measurements, out),
        *fig3_failures_by_cause(measurements, out),
        *fig4_cost_per_run(costs, out),
    ]


# --- fig1 and fig2: both scores, three panels ------------------------------------------------------------


def _panels(count: int, height: float) -> tuple[plt.Figure, list]:
    """A row of `count` panels with the same rows: each sets its own ticks (the R157 panel routine labels
    them) and the later panels hide their labels; the room at each panel's right holds its k/n."""
    fig, axes = plt.subplots(1, count, figsize=(14.0, height))
    fig.subplots_adjust(wspace=0.55)
    return fig, list(axes)


def _pooled_counts(measurements: list[data.Measurement]) -> str:
    return ", ".join(f"{m.pooled.meaning.k}/{m.pooled.meaning.n}" for m in measurements)


def fig1_accuracy_by_dataset(measurements: list[data.Measurement], out: Path) -> list[Path]:
    ys = {"ntsb": 0.0, "lighthouses": 1.0, "furniture": 2.4, "heldout": 3.4, "generality": 4.4, "pooled": 5.8}
    fig, axes = _panels(len(measurements), 5.6)
    for ax, m in zip(axes, measurements, strict=True):
        by_group = {a.group: a for a in base.by_dataset(list(m.outcomes))}
        ax.add_patch(
            Rectangle(
                (0, -0.5), 1, 2.0, transform=ax.get_yaxis_transform(), color=SURFACE_TINT, zorder=0, lw=0
            )
        )
        rows = [(ys[g], base.LABELS[g], by_group[g]) for g in (*base.DATASETS, "pooled")]
        _accuracy_panel(ax, rows, bold={"pooled"})
        ax.axhline(5.1, color=INK_SECONDARY, lw=0.6)
        ax.set_ylim(6.35, -0.95)
        per = by_group[base.DATASETS[0]].meaning.n
        ax.set_title(f"{m.label}\n(n = {per} per dataset)", loc="left", fontsize=9)
        ax.set_xlabel("Right (95 % Wilson interval)")
        if ax is not axes[0]:
            ax.tick_params(labelleft=False)
    axes[0].text(
        0.752, -0.75, "New datasets", ha="left", va="center", fontsize=8, color=INK_SECONDARY, style="italic"
    )
    axes[0].text(
        0.752,
        1.85,
        "Thesis datasets",
        ha="left",
        va="center",
        fontsize=8,
        color=INK_SECONDARY,
        style="italic",
    )
    pooled = [m.pooled.meaning for m in measurements]
    _legend(fig, top=0.885)
    title(
        fig,
        "On unseen questions the agent answers "
        + ", ".join(f"{p.rate:.1%}" for p in pooled)
        + f" right by meaning ({_pooled_counts(measurements)}); every interval overlaps the others",
        "Three measurements in time order, each on its own questions written before any output: the frozen "
        "engine (R157), the engine before the R167-R170 fixes (R166) and after them (R173).\nEach row: exact "
        "match (square) above the score by meaning (circle); lines are 95 % Wilson intervals; the counts at "
        "right are k/n. The axis starts at 75 %. The sets differ, so a difference is not attributed to the "
        "fixes.",
    )
    fig.subplots_adjust(top=0.74, left=0.1, right=0.95, bottom=0.12)
    return save(fig, out, "r173_fig1_accuracy_by_dataset_three_measurements")


def fig2_accuracy_by_type(measurements: list[data.Measurement], out: Path) -> list[Path]:
    fig, axes = _panels(len(measurements), 4.9)
    aggregation = []
    for ax, m in zip(axes, measurements, strict=True):
        accuracy = base.by_type(list(m.outcomes))
        rows = [(float(i), base.TYPE_LABELS[a.group], a) for i, a in enumerate(accuracy)]
        _accuracy_panel(ax, rows, bold=set())
        # R166's aggregation (14/19) lies below the 75 % the shared panel starts at, and its interval's low
        # end is 51 %: this chart's axis starts at 50 % so every marker and interval is in view
        ax.set_xlim(0.5, 1.005)
        ax.set_ylim(len(rows) - 0.45, -0.55)
        ax.set_title(m.label, loc="left", fontsize=9)
        ax.set_xlabel("Right (95 % Wilson interval)")
        if ax is not axes[0]:
            ax.tick_params(labelleft=False)
        aggregation.append(next(a for a in accuracy if a.group == "aggregation").meaning)
    _legend(fig, top=0.885)
    title(
        fig,
        "Aggregation, the weakest type, goes "
        + " to ".join(f"{p.k}/{p.n}" for p in aggregation)
        + " by meaning across the three measurements; the other types stay near the top",
        "The questions of each measurement by the type the gold gives them (n per type: 35-46 in R157, "
        "15-21 in R166 and R173). Square: exact match; circle: by meaning;\nlines are 95 % Wilson intervals; "
        "the counts at right are k/n. The axis starts at 50 %; an interval of 15-20 questions is wide.",
    )
    fig.subplots_adjust(top=0.76, left=0.11, right=0.95, bottom=0.13)
    return save(fig, out, "r173_fig2_accuracy_by_type_three_measurements")


# --- fig3: the wrong answers by cause, three panels -------------------------------------------------------

_CAUSE_LABELS = {
    "agent_reasoning": "Agent reasoning",
    "tool_limit": "Tool limit",
    "retrieval_miss": "Retrieval miss",
    "graph_build": "Graph build",
    "answer_form": "Answer form (exact only)",
}
X_MAX = 9.0  # one x scale for the three panels: R157's largest count is 7, and the ids need room


def fig3_failures_by_cause(measurements: list[data.Measurement], out: Path) -> list[Path]:
    fig, axes = plt.subplots(1, len(measurements), figsize=(14.0, 4.2), sharey=True)
    fig.subplots_adjust(wspace=0.12)
    wrong_counts = []
    for ax, m in zip(axes, measurements, strict=True):
        outcomes = list(m.outcomes)
        causes = base.by_cause(outcomes)
        n_wrong = len(base.wrong(outcomes))
        wrong_counts.append(n_wrong)
        _cause_bars(ax, causes)
        ax.set_title(f"{m.label}\n{n_wrong} of {m.n} wrong by either score", loc="left", fontsize=9)
        ax.set_xlabel("Wrong answers traced to the cause")
    fig.legend(
        handles=[
            Patch(color=MEANING.color, label="Wrong by both scores"),
            Patch(color=EXACT.color, label="Wrong by exact match only, right by meaning"),
        ],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.86),
        ncol=2,
    )
    title(
        fig,
        "The wrong answers fall from "
        + " to ".join(str(c) for c in wrong_counts)
        + "; what remains is a tool limit the agent's reasoning does not get round",
        "Each measurement's wrong answers by the cause its step traced; a question can have several causes, "
        "so a panel's bars add up to more than its wrong answers.\nThe question ids are written at each bar; "
        "one x scale for the three panels.",
    )
    fig.subplots_adjust(top=0.72, left=0.13, right=0.98, bottom=0.16)
    return save(fig, out, "r173_fig3_failures_by_cause_three_measurements")


def _cause_bars(ax, causes: list[base.CauseCount]) -> None:
    """One bar per cause in `base.CAUSES` order (the same rows in every panel), its both-scores part in
    orange and its exact-only part in blue, the count and the ids at its end; a cause with no answer keeps
    its row, so the panels line up."""
    ys = [float(i) for i in range(len(causes))]
    both = [len(c.both) for c in causes]
    exact_only = [len(c.exact_only) for c in causes]
    ax.barh(ys, both, height=0.62, color=MEANING.color, edgecolor=SURFACE, lw=1.5)
    ax.barh(ys, exact_only, left=both, height=0.62, color=EXACT.color, edgecolor=SURFACE, lw=1.5)
    for y, c in zip(ys, causes, strict=True):
        if c.total:
            # at most four ids a line: R157's seven would run into the next panel
            all_ids = (*c.both, *c.exact_only)
            ids = "\n".join(", ".join(all_ids[i : i + 4]) for i in range(0, len(all_ids), 4))
            ax.text(c.total + 0.12, y, f"{c.total}  {ids}", ha="left", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(ys, [_CAUSE_LABELS[c.cause] for c in causes])
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
    ax.set_ylim(len(causes) - 0.45, -0.55)
    ax.set_xlim(0, X_MAX)
    ax.set_xticks(range(0, 8))
    ax.spines["bottom"].set_bounds(0, 7)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)


# --- fig4: what each run cost -----------------------------------------------------------------------------


def fig4_cost_per_run(costs: list[data.RunCost], out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(9.6, 4.4))  # tall enough for the title, subtitle and key above the bars
    ys = [float(i) for i in range(len(costs))]
    ax.barh(ys, [c.cost for c in costs], height=0.58, color=TEXT_BLUE, edgecolor=SURFACE, lw=1.5)
    ax.barh(
        ys, [c.wasted for c in costs], left=[c.cost for c in costs], height=0.58, color=CONTEXT,
        edgecolor=SURFACE, lw=1.5,
    )  # fmt: skip
    for y, c in zip(ys, costs, strict=True):
        text = f"\\${c.cost:.3f} for {c.questions} questions: {100 * c.per_question:.1f} ¢ a question"
        if c.wasted:
            text += f"  (+ \\${c.wasted:.3f} wasted, not scored)"
        ax.text(c.cost + c.wasted + 0.06, y, text, ha="left", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(ys, [c.label for c in costs])
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
    ax.set_ylim(len(costs) - 0.45, -0.55)
    ax.set_xlim(0, 6.6)
    ax.set_xticks([0, 1, 2, 3, 4])
    ax.spines["bottom"].set_bounds(0, 4)
    ax.xaxis.set_major_formatter(lambda x, _: f"\\${x:.0f}")
    ax.set_xlabel("Cost of the agent's run (USD, from MLflow and prices.yaml)")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    fig.legend(
        handles=[Patch(color=TEXT_BLUE, label="Scored run"), Patch(color=CONTEXT, label="Wasted command")],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.86),
        ncol=2,
    )
    cents = [100 * c.per_question for c in costs]
    title(
        fig,
        f"An answer costs {min(cents):.1f}-{max(cents):.1f} cents; the one wasted command cost "
        f"\\${sum(c.wasted for c in costs):.3f}",
        "The three agent runs behind the measurements (Gemini, priced by prices.yaml); the graphs were "
        "reloaded from cache at no cost. The wasted command ran the agent over an empty schema (R172).",
    )
    fig.subplots_adjust(top=0.72, left=0.24, right=0.98, bottom=0.2)
    return save(fig, out, "r173_fig4_cost_per_run")
