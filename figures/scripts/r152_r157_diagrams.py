"""The method diagram of plan R152-R157: the benchmark protocol, from the five datasets to the committed
outcomes record.

Role: `build.py` calls `draw_all`; the file lands in `figures/diagrams/evaluation/r152_r157/`, whose
README.md is its caption.
Design: boxes and arrows (boxes.py) in the figures' one style, read left to right as the data flows (not in
the order the steps ran: the agent was frozen at R151, before the gold; its box says so): datasets, then the
gold questions and the graph builds (both from the sources, neither from the other), the frozen agent, the
two scores, the record. The agent gets only the gold's questions; the gold's answers go round the agent,
along the top, to the frame around the two scores, which both compare each answer with its gold answer.
Neutral boxes are steps; the two score boxes wear the scores' hues of the R157 charts (exact match blue, by
meaning orange), so the diagram and the charts read as one. Every count and cost is read through `r157_data`
from the committed records. Dollar signs are escaped.
Must not: show a number no committed record holds.
"""

from pathlib import Path

from . import r157_data as data
from .boxes import NEUTRAL, TINT, arrow, box, canvas
from .style import AXIS, EXACT, INK_MUTED, INK_SECONDARY, MEANING, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "evaluation" / "r152_r157"
WIDTH = 12.4  # inches: about the other diagrams' width, so a page scales them all alike
HEIGHT = 1.6  # a row box's height (inches): six lines of text
TEXT = 10.5  # the box text's size (points); on a 6.3-inch text width it prints at about 5.3 points
TOP, BOTTOM = 2.75, 0.75  # the two rows' lower edges: the new datasets, gold and exact match on top
LABEL = 9.0  # the size of an arrow's label and of the footnote


def draw_all(
    outcomes: list[data.Outcome], costs: data.Costs, split: data.ExactSplit, out: Path = OUT
) -> list[Path]:
    """The diagram into `out`; returns the files written."""
    return diag1_benchmark_protocol(outcomes, costs, split, out)


def diag1_benchmark_protocol(
    outcomes: list[data.Outcome], costs: data.Costs, split: data.ExactSplit, out: Path
) -> list[Path]:
    pooled = data.by_dataset(outcomes)[-1]
    n, exact, meaning = len(outcomes), pooled.exact, pooled.meaning
    fig, ax = canvas(WIDTH, 5.9)
    _boxes(ax, n, exact, meaning, costs, split)
    _arrows(ax)
    ax.text(
        0.15,
        0.5,
        "Gold and judges come from one model family (Claude Opus 5.5), which also wrote the agent's fixes; "
        "the agent answers with Gemini. One run, no repeat.\nEach judge compares the answer with its gold "
        "answer, without the other's verdicts.",
        fontsize=LABEL,
        color=INK_SECONDARY,
        va="top",
        linespacing=1.4,
    )
    title(
        fig,
        f"The benchmark protocol: {n} questions written before any output, answered by a frozen agent, "
        "scored twice",
        "Plan R152-R157, left to right as the data flows. The agent sees only the questions; both scores "
        "compare its answers with the gold answers. Blue and orange: the two scores, as in the R157 charts.",
    )
    return save(fig, out, "r157_diag1_benchmark_protocol")


def _boxes(ax, n: int, exact, meaning, costs: data.Costs, split: data.ExactSplit) -> None:
    """The boxes, column by column; the agent and the record span both rows, so every arrow is level."""
    per_dataset = n // len(data.DATASETS)
    rows = (
        # column 1: the datasets
        (
            0.25,
            TOP,
            1.9,
            "New datasets: NTSB\naviation, lighthouses,\nfetched by a fixed rule\n"
            "before any text was\nread (R153, R154)",
            NEUTRAL,
        ),
        (0.25, BOTTOM, 1.9, "Thesis datasets:\nfurniture, held-out\n(NHTSA), generality\n(Part 1)", NEUTRAL),
        # column 2: gold questions and graph builds, each from the sources, neither from the other
        (
            2.75,
            TOP,
            2.3,
            f"Gold: {per_dataset} questions per\ndataset, {n} in all, six\ntypes, written from "
            "the\nsources before any\noutput (R152-R154)",
            NEUTRAL,
        ),
        (
            2.75,
            BOTTOM,
            2.3,
            "Graph builds: two new\ngraphs, composite keys\n"
            f"(R155: \\${costs.builds:.3f}, incl. the\nrefused first plan);\n"
            "thesis graphs reloaded\nunchanged",
            NEUTRAL,
        ),
        # column 4: the two scores, in the scores' hues
        (
            8.6,
            TOP,
            1.95,
            f"Exact match:\nby code {split.code.k}/{split.code.n};\nfree text by the\n"
            f"judges' verdict {split.free_text.k}/{split.free_text.n}\nin all {exact.k}/{exact.n}",
            TINT[EXACT.color],
        ),
        (
            8.6,
            BOTTOM,
            1.95,
            "By meaning:\ntwo blind judges,\na lead judge for\ndisagreements\n"
            f"(none): {meaning.k}/{meaning.n}",
            TINT[MEANING.color],
        ),
    )
    # two frames, drawn first so the boxes sit on them: the datasets (each feeds both the gold and the
    # builds) and the two scores (both read the gold answers)
    box(ax, 0.1, 0.6, 2.2, 3.9, "", ("none", AXIS))
    box(ax, 8.45, 0.6, 2.25, 3.9, "", ("none", AXIS))
    ax.text(1.2, 4.58, "Five datasets", ha="center", va="bottom", fontsize=LABEL, color=INK_SECONDARY)
    for x, y, w, text, colours in rows:
        box(ax, x, y, w, HEIGHT, text, colours, size=TEXT)
    # column 3: the frozen agent, both rows high
    box(
        ax,
        6.0,
        1.25,
        2.05,
        2.6,
        f"Frozen agent\n(Gemini), tag\nquery-engine-r151:\nfrozen at R151,\nbefore the gold;\n"
        f"answers all {n}\n(R156, \\${costs.agent:.3f})",
        size=TEXT,
    )
    # column 5: the record the figures read
    box(
        ax,
        11.05,
        1.25,
        1.25,
        2.6,
        "Outcomes\nrecord\n(R158):\nper question,\nwith causes",
        size=TEXT,
        align="center",
    )


def _arrows(ax) -> None:
    """The flow: the datasets join and feed both the gold and the builds; questions and graphs into the
    agent; its answers into both scores; the gold answers round the agent into the score frame."""
    upper, lower = TOP + 0.55, BOTTOM + HEIGHT - 0.55  # the level lanes between the row boxes and the agent
    line = {"color": INK_MUTED, "lw": 1.2, "solid_capstyle": "butt"}
    # the two dataset groups sit in one frame; one line leaves it between them and forks to both targets, so
    # neither group reads as feeding only the box level with it, and no arrows cross
    top, bottom, fork = TOP + HEIGHT / 2, BOTTOM + HEIGHT / 2, 2.5
    ax.plot([2.3, fork], [(top + bottom) / 2] * 2, **line)
    ax.plot([fork, fork], [bottom, top], **line)
    for y in (top, bottom):
        arrow(ax, (fork, y), (2.75, y))
    for x_from, x_to in ((5.05, 6.0), (8.05, 8.6), (10.55, 11.05)):
        arrow(ax, (x_from, upper), (x_to, upper))
        arrow(ax, (x_from, lower), (x_to, lower))
    _label(ax, 5.525, upper, "questions\nonly")
    _label(ax, 5.525, lower, "graphs")
    _label(ax, 8.325, upper, "answers")
    # the gold answers: up from the gold box, along the top over the agent, down into the score frame
    lane, gold_x, frame_x = 4.85, 3.9, 9.575
    ax.plot([gold_x, gold_x, frame_x], [TOP + HEIGHT, lane, lane], **line)
    arrow(ax, (frame_x, lane), (frame_x, 4.5))
    ax.text(
        (gold_x + frame_x) / 2,
        lane + 0.06,
        "gold answers, to both scores",
        ha="center",
        va="bottom",
        fontsize=LABEL,
        color=INK_SECONDARY,
    )


def _label(ax, x: float, y: float, text: str) -> None:
    """An arrow's label, centred above it."""
    ax.text(x, y + 0.07, text, ha="center", va="bottom", fontsize=LABEL, color=INK_SECONDARY, linespacing=1.2)
