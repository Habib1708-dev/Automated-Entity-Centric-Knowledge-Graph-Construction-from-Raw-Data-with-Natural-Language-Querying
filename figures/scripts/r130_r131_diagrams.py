"""The method diagram of plan R130-R131: how the agent will find its start nodes, and why no separate
reranker sits in that path (the seeding decision, R131b).

Role: `build.py` calls `draw_all`; the file lands in `figures/diagrams/retrieval/r130_r131/`, whose
README.md is its caption.
Design: boxes and arrows (boxes.py) in the figures' one style: neutral boxes for steps, orange for node
cards (the family hue), and the reranker the decision leaves out drawn as a dashed, muted branch, so the
reader sees the road not taken and why. Every count is R131's record (`tests/gold/r131/`), read through
r131_data; the cost of the reranker is its MLflow `cost_usd` over its 282 calls. Dollar signs are escaped.
Must not: show a number no committed record holds.
"""

from pathlib import Path

from matplotlib.patches import FancyBboxPatch

from . import r131_data as data
from .boxes import NEUTRAL, TINT, arrow, box, canvas
from .style import INK, INK_MUTED, INK_SECONDARY, NODE_ORANGE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "retrieval" / "r130_r131"
CARDS = TINT[NODE_ORANGE]


def draw_all(tables, choice, out: Path = OUT) -> list[Path]:
    """The diagram into `out`; returns the files written."""
    return diag1_seeding_decision(tables, choice, out)


def diag1_seeding_decision(tables, choice, out: Path) -> list[Path]:
    pooled = tables["pooled"]
    chosen = pooled.score(data.CHOSEN, choice.k).seed_recall
    reranked = pooled.score(data.RERANKER, 5).seed_recall
    pool_at_5 = pooled.score(data.CHOSEN, 5).seed_recall
    fig, ax = canvas(13.2, 5.4)
    box(ax, 0.2, 2.75, 2.2, 0.95, "The question", NEUTRAL, align="center")
    box(ax, 3.0, 3.35, 2.6, 0.95, "Template cards, dense\ntop 25 by vector", CARDS)
    box(ax, 3.0, 2.15, 2.6, 0.95, "Template cards, lexical\ntop 25 by words (BM25)", CARDS)
    box(ax, 6.2, 2.75, 2.3, 0.95, "RRF, k = 60\nby rank alone, free", CARDS)
    box(ax, 9.1, 2.75, 1.7, 0.95, f"Top {choice.k}\nstart nodes", CARDS, align="center")
    box(ax, 11.3, 2.55, 1.7, 1.35, "The agent\nreads the\ncards and\nchooses", NEUTRAL, align="center")
    arrow(ax, (2.4, 3.22), (3.0, 3.82))
    arrow(ax, (2.4, 3.22), (3.0, 2.62))
    arrow(ax, (5.6, 3.82), (6.2, 3.35))
    arrow(ax, (5.6, 2.62), (6.2, 3.15))
    arrow(ax, (8.5, 3.22), (9.1, 3.22))
    arrow(ax, (10.8, 3.22), (11.3, 3.22))
    _not_taken(ax, 6.2, 0.35, 4.6, 1.3)
    ax.annotate(
        "",
        xy=(8.5, 1.65),
        xytext=(7.35, 2.75),
        arrowprops={"arrowstyle": "-|>", "color": INK_MUTED, "lw": 1.0, "linestyle": "--"},
    )
    ax.text(
        0.2,
        1.55,
        f"Chosen: {chosen.k} of {chosen.n} targets at K = {choice.k} (pooled), free.\n"
        f"Reranker: {reranked.k} against {pool_at_5.k} at K = 5, level from K = 15.\n"
        "The agent is a model reading the cards anyway:\nit reorders them itself.",
        fontsize=8.5,
        color=INK_SECONDARY,
        va="top",
        linespacing=1.5,
    )
    title(
        fig,
        f"The seeding decision: RRF over 25 + 25 template cards, {choice.k} seeds, no separate reranker",
        "R131 (pre-registered rule), the decision R131b. Dashed: the reranker, measured and kept on record, "
        "not in the agent's path.",
    )
    return save(fig, out, "r131_diag1_seeding_decision")


def _not_taken(ax, x: float, y: float, w: float, h: float) -> None:
    """The reranker's box, dashed and muted: measured, not used."""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0,rounding_size=0.08",
            fc="#ffffff",
            ec=INK_MUTED,
            lw=1,
            linestyle="--",
        )
    )
    ax.text(
        x + 0.15,
        y + h / 2,
        "Not chosen: an LLM reranks the top 50\n(one call per question, about \\$0.002)\n"
        "helps only when the agent reads 5 seeds",
        ha="left",
        va="center",
        fontsize=8.5,
        color=INK,
        linespacing=1.4,
    )
