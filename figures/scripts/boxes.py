"""Boxes and arrows for the method diagrams (moved out of r126_r128_diagrams.py in R131b, unchanged): a canvas
in inches, a rounded box with its text, an arrow, and the box colours. The thesis's system diagrams (R232) add
three kinds of element with a fixed meaning: a square box for code that decides, an orange rounded box for a
language model that proposes, an aqua cylinder for a store; and a frame around a subsystem.

Role: every `<steps>_diagrams.py` draws with these, so all diagrams share one look.
Design: matplotlib in data coordinates (inches), the figures' one style (style.py): neutral boxes for steps,
family-tinted boxes for techniques (text blue, node cards orange, name linker aqua).
Must not: draw a whole diagram or hold any number.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyBboxPatch, Rectangle

from .style import AXIS, INK, INK_MUTED, LINKER_AQUA, NODE_ORANGE, SURFACE_TINT, TEXT_BLUE

NEUTRAL = (SURFACE_TINT, AXIS)  # face, edge
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


def arrow(
    ax, start: tuple[float, float], end: tuple[float, float], both: bool = False, dashed: bool = False
) -> None:
    """An arrow from `start` to `end`; `both` draws heads at both ends (a call and its result), `dashed` a
    secondary flow (logging, a reference)."""
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={
            "arrowstyle": "<|-|>" if both else "-|>",
            "color": INK_MUTED,
            "lw": 1.2,
            "shrinkA": 0,
            "shrinkB": 0,
            "linestyle": (0, (4, 3)) if dashed else "-",
        },
    )


# --- the system diagrams' three kinds of element (thesis, R232): what proposes, what decides, what stores ---


def code_box(
    ax, x: float, y: float, w: float, h: float, text: str, colours=NEUTRAL, size: float = 9.0
) -> None:
    """A square-cornered box with centred text: a step done by deterministic code."""
    face, edge = colours
    ax.add_patch(Rectangle((x, y), w, h, fc=face, ec=edge, lw=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=INK, linespacing=1.35)


def llm_box(ax, x: float, y: float, w: float, h: float, text: str, size: float = 9.0) -> None:
    """A rounded, orange-tinted box with centred text: a step where a language model proposes."""
    box(ax, x, y, w, h, text, TINT[NODE_ORANGE], size=size, align="center")


def store(ax, x: float, y: float, w: float, h: float, text: str, size: float = 9.0) -> None:
    """A cylinder (a database or a log) with (x, y) its lower left corner and centred text."""
    cap = min(0.22, h / 5)  # the height of the top and bottom ellipses
    face, edge = TINT[LINKER_AQUA][0], LINKER_AQUA
    ax.add_patch(Rectangle((x, y + cap / 2), w, h - cap, fc=face, ec="none"))
    ax.add_patch(Ellipse((x + w / 2, y + cap / 2), w, cap, fc=face, ec=edge, lw=1))
    ax.plot([x, x], [y + cap / 2, y + h - cap / 2], color=edge, lw=1)
    ax.plot([x + w, x + w], [y + cap / 2, y + h - cap / 2], color=edge, lw=1)
    ax.add_patch(Ellipse((x + w / 2, y + h - cap / 2), w, cap, fc=face, ec=edge, lw=1))
    ax.text(
        x + w / 2,
        y + (h - cap) / 2,
        text,
        ha="center",
        va="center",
        fontsize=size,
        color=INK,
        linespacing=1.35,
    )


def frame(
    ax, x: float, y: float, w: float, h: float, label: str, dashed: bool = False, size: float = 9.5
) -> None:
    """A light container around a group of boxes (a subsystem), its label at the top left; `dashed` marks a
    part that was measured but not adopted."""
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12", fc="none", ec=AXIS, lw=1.2,
            linestyle=(0, (5, 3)) if dashed else "-",
        )
    )  # fmt: skip
    ax.text(
        x + 0.12,
        y + h - 0.1,
        label,
        ha="left",
        va="top",
        fontsize=size,
        fontweight="semibold",
        color=INK_MUTED,
    )
