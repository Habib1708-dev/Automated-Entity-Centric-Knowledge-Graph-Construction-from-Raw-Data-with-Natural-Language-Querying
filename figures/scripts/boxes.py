"""Boxes and arrows for the method diagrams (moved out of r126_r128_diagrams.py in R131b, unchanged): a canvas
in inches, a rounded box with its text, an arrow, and the box colours.

Role: every `<steps>_diagrams.py` draws with these, so all diagrams share one look.
Design: matplotlib in data coordinates (inches), the figures' one style (style.py): neutral boxes for steps,
family-tinted boxes for techniques (text blue, node cards orange, name linker aqua).
Must not: draw a whole diagram or hold any number.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from .style import INK, INK_MUTED, LINKER_AQUA, NODE_ORANGE, TEXT_BLUE

NEUTRAL = ("#f4f4f1", "#c3c2b7")  # face, edge
TINT = {
    TEXT_BLUE: ("#e8f1fc", TEXT_BLUE),
    NODE_ORANGE: ("#fdeee7", NODE_ORANGE),
    LINKER_AQUA: ("#e3f6ef", LINKER_AQUA),
}


def canvas(width: float, height: float) -> tuple[plt.Figure, plt.Axes]:
    """A figure whose lower 88 % is one axis in inches (x 0..width, y 0..0.88 height), the rest the title."""
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes((0, 0, 1, 0.88))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height * 0.88)
    ax.axis("off")
    return fig, ax


def box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    colours=NEUTRAL,
    size: float = 9.0,
    align: str = "left",
) -> None:
    """A rounded box with (x, y) its lower left corner; its text left-aligned (or centred) and centred
    vertically."""
    face, edge = colours
    ax.add_patch(
        FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08", fc=face, ec=edge, lw=1)
    )
    tx = x + 0.15 if align == "left" else x + w / 2
    ax.text(tx, y + h / 2, text, ha=align, va="center", fontsize=size, color=INK, linespacing=1.4)


def arrow(ax, start: tuple[float, float], end: tuple[float, float]) -> None:
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "-|>", "color": INK_MUTED, "lw": 1.2, "shrinkA": 0, "shrinkB": 0},
    )
