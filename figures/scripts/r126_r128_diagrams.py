"""The two method diagrams of plan R126-R128: how chunk and claim retrieval name start nodes (R126), and the
design of the technique experiment (R128).

Role: `build.py` calls `draw_all`; the files land in `figures/diagrams/retrieval/r126_r128/`, whose README.md
is their caption list.
Design: boxes and arrows drawn with matplotlib in data coordinates, in the figures' one style (style.py):
neutral boxes for steps, family-tinted boxes for techniques (text blue, node cards orange, name linker aqua).
The worked example of the first diagram is the hand-made graph of `tests/graphs.py` (invented words), whose
start nodes `test_r126_the_store_reads_what_a_chunk_concerns_and_what_a_claim_joins` checks; the second
diagram's counts are R128's record (`tests/gold/r128/runs.json`). matplotlib reads a pair of "$" as maths, so
every dollar sign in a text here is escaped.
Must not: show a number no committed record or test holds.
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from .style import INK, INK_MUTED, LINKER_AQUA, NODE_ORANGE, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "retrieval" / "r126_r128"
NEUTRAL = ("#f4f4f1", "#c3c2b7")  # face, edge
TINT = {
    TEXT_BLUE: ("#e8f1fc", TEXT_BLUE),
    NODE_ORANGE: ("#fdeee7", NODE_ORANGE),
    LINKER_AQUA: ("#e3f6ef", LINKER_AQUA),
}


def draw_all(out: Path = OUT) -> list[Path]:
    """Both diagrams into `out`; returns the files written."""
    return [*diag1_seed_derivation(out), *diag2_experiment_design(out)]


def _canvas(width: float, height: float) -> tuple[plt.Figure, plt.Axes]:
    """A figure whose lower 88 % is one axis in inches (x 0..width, y 0..0.88 height), the rest the title."""
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes((0, 0, 1, 0.88))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height * 0.88)
    ax.axis("off")
    return fig, ax


def _box(
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


def _arrow(ax, start: tuple[float, float], end: tuple[float, float]) -> None:
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "-|>", "color": INK_MUTED, "lw": 1.2, "shrinkA": 0, "shrinkB": 0},
    )


# --- diag1: how chunk and claim retrieval name start nodes -----------------------------------------------

_LANES = (
    (
        "Chunk retrieval (chunk_dense, chunk_lexical)",
        'Ranked chunks, best first (50 deep)\n\ne.g. chunk notes.md#0:\n"The spindle wobbles."',
        "For each chunk, in rank order:\n1. the records it is ABOUT\n"
        "    (its own ABOUT link, else its document's)\n"
        "2. the nodes its mentions refer to (REFERS_TO),\n"
        "    in the order their names first occur in its text",
        "Start nodes: every node once,\nbest chunk first\n\ne.g. 1. Press:P1   (notes.md is ABOUT it)\n"
        '      2. Part:S1   ("spindle", at 4)\n      3. k-wobble  ("wobbles", at 12)',
    ),
    (
        "Claim retrieval (claim_dense, claim_lexical)",
        "Ranked claims, found by their\nsentences, best first (50 deep)\n\ne.g. claim o1: spindle\n"
        "-HAS_CONDITION-> wobbles",
        "For each claim, in rank order:\n1. its subject's entity\n2. its object's entity\n"
        "3. the things it is attached to\n    (HAS_OBSERVATION)",
        "Start nodes: every node once,\nbest claim first\n\ne.g. 1. Part:S1    (subject)\n"
        "      2. k-wobble  (object)\n      3. Press:P1   (attached)",
    ),
)


def diag1_seed_derivation(out: Path) -> list[Path]:
    fig, ax = _canvas(12.4, 6.0)
    xs, ws, h = (0.2, 4.0, 8.95), (3.3, 4.45, 3.25), 1.75
    for lane, (name, source, rule, seeds) in enumerate(_LANES):
        y = 2.95 - lane * 2.6
        ax.text(0.2, y + h + 0.2, name, fontsize=10, fontweight="semibold", color=INK)
        for i, (x, w, text) in enumerate(zip(xs, ws, (source, rule, seeds), strict=True)):
            _box(ax, x, y, w, h, text, TINT[TEXT_BLUE] if i == 0 else NEUTRAL)
            if i:
                _arrow(ax, (xs[i - 1] + ws[i - 1], y + h / 2), (x, y + h / 2))
    title(
        fig,
        "How chunk and claim retrieval name start nodes (R126)",
        "The graph's own navigation contract read the other way; a mention without a REFERS_TO edge gives "
        "no node. Examples: the hand-made test graph (tests/graphs.py), checked by a test.",
    )
    return save(fig, out, "r126_diag1_seed_derivation")


# --- diag2: the experiment's design ------------------------------------------------------------------------

_COLUMN_X = (0.2, 3.0, 5.8, 8.6, 11.4)
_COLUMN_W = 2.45
_TOP, _BOTTOM = 4.0, 0.5  # the box area of every column


def diag2_experiment_design(out: Path) -> list[Path]:
    fig, ax = _canvas(14.1, 5.6)
    heads = (
        "1. Three frozen builds,\nreloaded from the cache",
        "2. Retrieval units\nin Neo4j",
        "3. Nine techniques, each one\nranked list, 50 deep",
        "4. Scored by code at\nK = 5, 10, 15, 20, 50",
        "5. kg retrieve-table",
    )
    for x, head in zip(_COLUMN_X, heads, strict=True):
        ax.text(x, _TOP + 0.75, head, fontsize=9.5, fontweight="semibold", color=INK, va="top")
    _column(
        ax,
        0,
        [
            "Furniture (tuning set)\n70 chunks, 737 nodes",
            "Held-out (NHTSA)\n81 chunks, 629 nodes",
            "Generality\n32 chunks, 332 nodes",
        ],
    )
    _column(
        ax,
        1,
        [
            "Chunks\nvector and full-text index",
            "Claim sentences\n685, 568, 216",
            "Template cards (A)\nLLM summaries (B)",
        ],
    )
    techniques = (
        ("Chunks: dense, lexical", TEXT_BLUE),
        ("Claims: dense, lexical", TEXT_BLUE),
        ("Template cards: dense, lexical", NODE_ORANGE),
        ("LLM summaries: dense, lexical", NODE_ORANGE),
        ("Name linker (reference)", LINKER_AQUA),
    )
    _column(ax, 2, [t for t, _ in techniques], [TINT[hue] for _, hue in techniques])
    _column(
        ax,
        3,
        [
            "Evidence: gold chunks\nComplete@K, Evidence Recall@K\n99 questions",
            "Start nodes: R89 targets\nSeed Recall@K, Seeds found@K\n213 targets",
        ],
    )
    _column(
        ax,
        4,
        [
            "Per dataset and pooled",
            "Best against runner-up\npre-registered, McNemar",
            "Card against text\nexploratory pairings",
        ],
    )
    middle = (_TOP + _BOTTOM) / 2
    for left, right in zip(_COLUMN_X, _COLUMN_X[1:], strict=False):
        _arrow(ax, (left + _COLUMN_W + 0.04, middle), (right - 0.06, middle))
    title(
        fig,
        "The R128 experiment: each retrieval technique alone, at five budgets",
        "No reader and no judge: every score is an exact count against the gold, computed by code. "
        "\\$0 logged (every LLM answer from the cache); embeddings about \\$0.02.",
    )
    return save(fig, out, "r128_diag2_experiment_design")


def _column(ax, index: int, texts: list[str], colours: list | None = None) -> None:
    """Boxes filling one column's box area top to bottom, equally tall, 0.15 apart."""
    gap = 0.15
    h = (_TOP - _BOTTOM - gap * (len(texts) - 1)) / len(texts)
    for i, text in enumerate(texts):
        y = _TOP - h - i * (h + gap)
        _box(ax, _COLUMN_X[index], y, _COLUMN_W, h, text, colours[i] if colours else NEUTRAL, align="center")


__all__ = ["OUT", "draw_all"]
