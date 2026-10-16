"""The five result charts of R229: the build accuracy of the five benchmarked graphs, drawn from `r229_data`.

Role: `build.py` calls `draw_all`; the files land in
`figures/results/construction/r229_build_accuracy/`, whose README.md is their caption list.
Design: the form follows the data's job (dataviz skill, choosing-a-form):
- fig1 (claim precision), fig3 (mentions) and fig5 (audit): dots with 95% Wilson intervals on a cut percent
  axis, as R157's accuracy charts. Two rates per row near the top; bars from zero would spend the width on
  what every dataset clears and hide the intervals. The two measures of a row are a blue square and an orange
  circle (two of the three validated hues, each with its own shape), and each row's k/n is written at the
  right. The two new datasets sit on a gray band, as in R157's fig. 1.
- fig2 (claim recall): a stacked bar per dataset, a share of a whole. The reader's claims of the random
  sentences are split into stored, missed though a fact type could hold them, and missed with no fact type.
  Blue, orange and the context gray: the gray part is the schema's, not the model's.
- fig4 (precision against recall): a scatter with Wilson bars on both axes, the trade-off form. All dots are
  ink, so no hue is spent on dataset identity: thesis datasets filled, new ones hollow, each named at its dot.
- fig5 draws each criterion's 0.95 bound as a solid labelled line and writes "passes" or "fails" at each row.
  C3 can fail at a 100% merge rate, because a wrong record link is C3's wrong record merge too (R93).
One axis per panel, never two; rates as percent with n in the labels.
Must not: compute a result; every number comes from `r229_data`.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import PercentFormatter

from . import r229_data as data
from .style import (
    CONTEXT,
    INK,
    INK_SECONDARY,
    NODE_ORANGE,
    SURFACE,
    SURFACE_TINT,
    TEXT_BLUE,
    Score,
    save,
    title,
)

OUT = Path(__file__).resolve().parents[1] / "results" / "construction" / "r229_build_accuracy"
OFFSET = 0.17  # how far each measure's dot sits above or below its row's centre
# y positions: the two new datasets, a gap, the three thesis datasets (R157's fig. 1 layout)
YS = {"ntsb": 0.0, "lighthouses": 1.0, "furniture": 2.4, "heldout": 3.4, "generality": 4.4}
# fig4: the dots whose label goes left, where an interval line of a neighbouring dot would cross it
LABEL_LEFT = frozenset({"ntsb", "lighthouses"})
BOUND = 0.95  # the audit's bound for C4 link precision and C6 purity (R93, config.py)

STRICT = Score("Strict: every stored field right", TEXT_BLUE, "s")
CONTENT = Score("Content: the fact itself right", NODE_ORANGE, "o")
EXACT_NAME = Score("Exact: found by name (code)", TEXT_BLUE, "s")
JUDGED = Score("Judged: near names mapped by the judge", NODE_ORANGE, "o")
AUDIT = Score("Judged rate", INK, "o")


def draw_all(builds: list[data.BuildAccuracy], out: Path = OUT) -> list[Path]:
    """The five R229 charts into `out`; returns the files written."""
    return [
        *fig1_claim_precision(builds, out),
        *fig2_claim_recall(builds, out),
        *fig3_mentions(builds, out),
        *fig4_precision_against_recall(builds, out),
        *fig5_audit(builds, out),
    ]


# --- shared: rows of dots with intervals -------------------------------------------------------------------


def _dot(ax, y: float, p, score: Score, size: float = 7.5, hollow: bool = False) -> None:
    """One rate with its Wilson interval; a rate of 1 can have an upper bound a hair below it in floating
    point, so a bar's length is never negative."""
    ax.errorbar(
        p.rate,
        y,
        xerr=[[max(0.0, p.rate - p.low)], [max(0.0, p.high - p.rate)]],
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
        markerfacecolor=SURFACE if hollow else score.color,
        markeredgecolor=score.color if hollow else SURFACE,
        markeredgewidth=1.4 if hollow else 1.2,
        linestyle="none",
        zorder=3,
    )


def _rows(ax, builds: list[data.BuildAccuracy], labels: bool = True) -> None:
    """The dataset rows: the gray band of the new datasets, the y labels, the group names."""
    ax.add_patch(
        Rectangle((0, -0.5), 1, 2.0, transform=ax.get_yaxis_transform(), color=SURFACE_TINT, zorder=0, lw=0)
    )
    ax.set_yticks([YS[b.dataset] for b in builds], [data.LABELS[b.dataset] for b in builds] if labels else [])
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
    ax.set_ylim(4.95, -0.95)
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)


def _group_names(ax, x: float) -> None:
    for y, text in ((-0.75, "New datasets (R155b builds)"), (1.85, "Thesis datasets (r108 builds)")):
        ax.text(x, y, text, ha="left", va="center", fontsize=8, color=INK_SECONDARY, style="italic")


def _counts(ax, y: float, text: str, x: float = 1.02, weight: str = "normal") -> None:
    ax.text(
        x,
        y,
        text,
        transform=ax.get_yaxis_transform(),
        va="center",
        color=INK,
        fontsize=8.5,
        fontweight=weight,
    )


def _key(fig, scores: tuple[Score, ...], top: float) -> None:
    handles = [
        Line2D([], [], marker=s.marker, color=s.color, mec=SURFACE, markersize=8, lw=1.6, label=s.label)
        for s in scores
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, top), ncol=len(scores))


def _kn(p) -> str:
    return f"{p.k}/{p.n}"


# --- fig1: claim precision ---------------------------------------------------------------------------------


def fig1_claim_precision(builds: list[data.BuildAccuracy], out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(9.6, 5.2))
    _rows(ax, builds)
    for b in builds:
        _dot(ax, YS[b.dataset] - OFFSET, b.strict, STRICT)
        _dot(ax, YS[b.dataset] + OFFSET, b.content, CONTENT)
        _counts(ax, YS[b.dataset], f"{_kn(b.strict)}  ·  {_kn(b.content)}")
    ax.text(
        1.02, 1.01, "strict  ·  content", transform=ax.transAxes, va="bottom", fontsize=8, color=INK_SECONDARY
    )
    # the axis starts at 80 %: every interval's low end lies above it (the lowest, lighthouses strict, 85 %)
    ax.set_xlim(0.80, 1.005)
    ax.set_xticks([0.80, 0.85, 0.90, 0.95, 1.00])
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Extracted claims judged right (95 % Wilson interval); n = every stored claim of the build")
    _group_names(ax, 0.802)
    lowest = min(builds, key=lambda b: b.content.rate)
    _key(fig, (STRICT, CONTENT), top=0.83)
    title(
        fig,
        f"What the builds store is right: content precision {lowest.content.rate:.0%} or more on all five "
        "datasets",
        "R110 (thesis datasets) and R226 (new datasets): every extracted claim judged blind against its "
        "chunk by Claude Opus 5.5. Square: strict (every field: truth,\nmodality, condition, time, type "
        "right); circle: content (the subject-relation-object triple right). Lines: 95 % Wilson intervals; "
        "k/n at right. The axis starts at 80 %.",
    )
    fig.subplots_adjust(top=0.73, left=0.17, right=0.84, bottom=0.12)
    return save(fig, out, "r229_fig1_claim_precision_by_dataset")


# --- fig2: claim recall -------------------------------------------------------------------------------------


def fig2_claim_recall(builds: list[data.BuildAccuracy], out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(9.6, 5.2))
    _rows(ax, builds)
    parts = (
        ("stored", TEXT_BLUE, lambda b: b.recall.k),
        ("missed, a fact type could hold it", NODE_ORANGE, lambda b: b.other_misses),
        ("missed, no fact type for it", CONTEXT, lambda b: b.schema_misses),
    )
    for b in builds:
        left = 0.0
        for _, color, count in parts:
            share = count(b) / b.recall.n
            ax.barh(YS[b.dataset], share, left=left, height=0.62, color=color, edgecolor=SURFACE, lw=0.8)
            if count(b):
                ink = SURFACE if color != CONTEXT else INK
                ax.text(
                    left + share / 2,
                    YS[b.dataset],
                    str(count(b)),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color=ink,
                )
            left += share
        _counts(ax, YS[b.dataset], f"{_kn(b.recall)} = {b.recall.rate:.0%}")
    ax.text(1.02, 1.01, "stored", transform=ax.transAxes, va="bottom", fontsize=8, color=INK_SECONDARY)
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Share of the reader's claims in the random sentences (n per dataset at right)")
    ax.grid(axis="x", visible=False)
    _group_names(ax, 0.005)
    fig.legend(
        handles=[Patch(color=c, label=label) for label, c, _ in parts],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.83),
        ncol=3,
    )
    best = max(builds, key=lambda b: b.recall.rate)
    title(
        fig,
        f"The builds store few of a reader's claims: from {min(b.recall.rate for b in builds):.0%} to "
        f"{best.recall.rate:.0%} ({data.LABELS[best.dataset].lower()})",
        "R111 and R226: every claim a careful reader lists for 40 random sentences per dataset "
        "(written blind, R77 / R225), matched by a blind judge to the stored claims.\n"
        "Orange: a fact type existed (the model's miss); gray: no fact type could hold it (the schema's). "
        "The numbers in the bars are claims.",
    )
    fig.subplots_adjust(top=0.73, left=0.17, right=0.86, bottom=0.12)
    return save(fig, out, "r229_fig2_claim_recall_by_dataset")


# --- fig3: mentions -----------------------------------------------------------------------------------------


def fig3_mentions(builds: list[data.BuildAccuracy], out: Path) -> list[Path]:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.4, 5.2), gridspec_kw={"width_ratios": [1.25, 1]})
    _rows(left, builds)
    _rows(right, builds, labels=False)
    for b in builds:
        y = YS[b.dataset]
        _dot(left, y - OFFSET, b.mention_exact, EXACT_NAME)
        _dot(left, y + OFFSET, b.mention_judged, JUDGED)
        _counts(left, y, f"{_kn(b.mention_exact)} · {_kn(b.mention_judged)}")
        _dot(right, y, b.mention_precision, JUDGED)
        _counts(right, y, _kn(b.mention_precision))
    for ax, name in (
        (left, "Recall: gold mentions the build holds"),
        (right, "Precision: sampled pass mentions"),
    ):
        # both axes start at 55 %: the lowest interval's low end (furniture exact recall) is 60 %
        ax.set_xlim(0.55, 1.005)
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_title(name, loc="left", color=INK)
    left.set_xlabel("95 % Wilson interval; n = gold mentions (75 to 184)")
    right.set_xlabel("Judged; n = 57 to 60 sampled mentions")
    _group_names(left, 0.552)
    _key(fig, (EXACT_NAME, JUDGED), top=0.83)
    found = [b.mention_judged.rate for b in builds]
    title(
        fig,
        f"The mention index finds {min(found):.0%}-{max(found):.0%} of what a reader names, and most of what "
        "it adds is a real mention",
        "R103 (thesis datasets, the pass r108 reused) and R226: 40 random sentences per dataset with every "
        "mention a reader names (R101 / R225 gold, written blind).\nLeft: found by exact name (square, code) "
        "and with near names the judge mapped (circle). Right: a seeded sample of the pass's own mentions "
        "judged inside the definition.",
    )
    fig.subplots_adjust(top=0.72, left=0.14, right=0.93, bottom=0.12, wspace=0.42)
    return save(fig, out, "r229_fig3_mention_recall_and_precision")


# --- fig4: precision against recall -------------------------------------------------------------------------


def fig4_precision_against_recall(builds: list[data.BuildAccuracy], out: Path) -> list[Path]:
    fig, ax = plt.subplots(figsize=(8.4, 5.6))
    for b in builds:
        r, p = b.recall, b.content
        ax.errorbar(
            r.rate,
            p.rate,
            xerr=[[r.rate - r.low], [r.high - r.rate]],
            yerr=[[p.rate - p.low], [max(0.0, p.high - p.rate)]],
            fmt="none",
            ecolor=INK_SECONDARY,
            elinewidth=1.0,
            capsize=0,
            zorder=2,
        )
        new = b.dataset in data.NEW
        ax.plot(
            r.rate,
            p.rate,
            marker="o",
            markersize=8,
            markerfacecolor=SURFACE if new else INK,
            markeredgecolor=INK,
            markeredgewidth=1.5,
            linestyle="none",
            zorder=3,
        )
        ax.annotate(
            f"{data.LABELS[b.dataset]}\n{_kn(r)} · {_kn(p)}",
            (r.rate, p.rate),
            xytext=(-10, 4) if b.dataset in LABEL_LEFT else (8, -4),
            textcoords="offset points",
            fontsize=8,
            color=INK,
            ha="right" if b.dataset in LABEL_LEFT else "left",
            va="bottom" if b.dataset in LABEL_LEFT else "top",
        )
    ax.set_xlim(0, 0.8)
    ax.set_ylim(0.85, 1.005)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Claim recall: the reader's claims of the random sentences the build stores (judged)")
    ax.set_ylabel("Claim precision, content (judged)")
    fig.legend(
        handles=[
            Line2D([], [], marker="o", color=INK, mfc=INK, lw=0, label="Thesis datasets (r108 builds)"),
            Line2D([], [], marker="o", color=INK, mfc=SURFACE, lw=0, label="New datasets (R155b builds)"),
        ],
        loc="upper left",
        bbox_to_anchor=(0.005, 0.86),
        ncol=2,
    )
    title(
        fig,
        "Every build is precise; recall is what separates them",
        "R110/R111 and R226: each dataset's content precision (all stored claims) against its claim recall "
        "(the reader's claims\nof 40 random sentences). Lines: 95 % Wilson intervals on both axes; labels: "
        "recall k/n · precision k/n. The y axis starts at 85 %.",
    )
    fig.subplots_adjust(top=0.76, left=0.12, right=0.95, bottom=0.11)
    return save(fig, out, "r229_fig4_precision_against_recall")


# --- fig5: the audit ----------------------------------------------------------------------------------------


def fig5_audit(builds: list[data.BuildAccuracy], out: Path) -> list[Path]:
    panels = (
        ("C3 identity: merges judged one thing", lambda b: (b.c3, b.c3_passed)),
        ("C4 record links: links right", lambda b: (b.c4, b.c4_passed)),
        ("C6 purity: chunks about their start", lambda b: (b.c6, b.c6_passed)),
    )
    # no shared y axis: the panels without labels would blank the first panel's dataset names
    fig, axes = plt.subplots(1, 3, figsize=(11.6, 5.0))
    for i, (ax, (name, pick)) in enumerate(zip(axes, panels, strict=True)):
        _rows(ax, builds, labels=i == 0)
        for b in builds:
            p, passed = pick(b)
            _dot(ax, YS[b.dataset], p, AUDIT)
            verdict = "passes" if passed else "fails"
            _counts(ax, YS[b.dataset], f"{_kn(p)} {verdict}", weight="normal" if passed else "bold")
        ax.axvline(BOUND, color=INK_SECONDARY, lw=0.9, zorder=1)
        ax.text(BOUND, 4.85, " bound 95 %", fontsize=7.5, color=INK_SECONDARY, va="bottom")
        # every panel starts at 75 %: the lowest interval's low end (lighthouses C4) is 82 %
        ax.set_xlim(0.75, 1.005)
        ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_title(name, loc="left", color=INK, fontsize=9.5)
    axes[1].set_xlabel("Judged rate (95 % Wilson interval); C6: record and individual starts, anchor arm")
    _group_names(axes[0], 0.752)
    failed = [b for b in builds if not (b.c3_passed and b.c4_passed and b.c6_passed)]
    # the failing builds and, for each, its wrong record links (C3 fails on them too, R93)
    fails = "; ".join(
        f"{data.LABELS[b.dataset]} fails on {b.c4.n - b.c4.k} wrong record links" for b in failed
    )
    title(
        fig,
        f"{len(builds) - len(failed)} of {len(builds)} builds pass every audit criterion"
        + (f"; {fails}" if fails else ""),
        "R108 (thesis datasets) and R228 (new datasets), judged blind by Claude Opus 5.5 under R93's rules. "
        "Each row: the judged rate with its interval and k/n; bold = the criterion's\nhard rule fails. "
        "C3 also fails on a wrong record link (C4's), so a 100 % merge rate can fail; the bound line applies "
        "to C4 and C6.",
    )
    fig.subplots_adjust(top=0.76, left=0.12, right=0.95, bottom=0.12, wspace=0.55)
    return save(fig, out, "r229_fig5_audit_by_dataset")
