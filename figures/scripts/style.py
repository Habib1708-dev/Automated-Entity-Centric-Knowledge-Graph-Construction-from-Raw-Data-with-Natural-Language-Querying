"""The one visual style of every figure (R129): the palette, the encoding of a retrieval technique, the
matplotlib defaults and the saving of SVG and PNG.

Role: every chart and diagram module draws with these constants, so one technique looks the same in every
figure and a palette change happens in one place.
Design: the palette is the dataviz skill's validated reference instance (light mode, print). Categorical hue
encodes the technique's family, at most three hues (the validated all-pairs cap: text retrieval blue, node
cards orange, the name linker aqua; validator run 2026-10-09: worst all-pairs CVD dE 9.2, normal-vision
24.0; aqua is below 3:1 on white, so its marks always carry a direct label or a legend entry). Marker shape
encodes what is searched (circle chunk, triangle claim, square template card, diamond summary, pentagon name
linker) and fill encodes the mode (filled dense, hollow lexical): hue x shape x fill, never a fourth hue.
Must not: load data or decide what a figure shows.
"""

import io
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # files only: figures are built headless, in tests and on any machine

import matplotlib.pyplot as plt  # noqa: E402  (after the backend is chosen)
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# chart chrome and ink (light mode; the figures are for print, so the surface is white)
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CONTEXT = "#d3d2cb"  # de-emphasis gray of the emphasis form: the techniques a panel is not about

# the three family hues (categorical slots 1-3, validated all-pairs)
TEXT_BLUE = "#2a78d6"
NODE_ORANGE = "#eb6834"
LINKER_AQUA = "#1baf7a"

# one-hue sequential ramp (blue 100 -> 700) for magnitudes on a grid
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "seq_blue",
    ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"],
)


@dataclass(frozen=True)
class Technique:
    """How one retrieval technique is named and drawn."""

    system: str  # the system name in the reports (`kg retrieve-eval --system`)
    label: str  # the name a reader sees
    family: str  # "text", "node" or "linker": the hue
    marker: str  # what is searched: the shape
    dense: bool  # filled (dense, by vector) or hollow (lexical, by words)

    @property
    def color(self) -> str:
        return {"text": TEXT_BLUE, "node": NODE_ORANGE, "linker": LINKER_AQUA}[self.family]

    def marker_style(self, size: float = 8.0) -> dict[str, object]:
        """Keyword arguments for `plot`: the shape, the fill (dense filled, lexical hollow) and the edge."""
        return {
            "marker": self.marker,
            "markersize": size,
            "markerfacecolor": self.color if self.dense else SURFACE,
            "markeredgecolor": self.color,
            "markeredgewidth": 1.6,
        }


# the nine techniques of plan R126-R128, in the table's order (also the legend order)
TECHNIQUES = (
    Technique("chunk_dense", "Chunks, dense", "text", "o", True),
    Technique("chunk_lexical", "Chunks, lexical", "text", "o", False),
    Technique("claim_dense", "Claims, dense", "text", "^", True),
    Technique("claim_lexical", "Claims, lexical", "text", "^", False),
    Technique("card_dense_template", "Template cards (A), dense", "node", "s", True),
    Technique("card_lexical_template", "Template cards (A), lexical", "node", "s", False),
    Technique("card_dense_summary", "LLM summaries (B), dense", "node", "D", True),
    Technique("card_lexical_summary", "LLM summaries (B), lexical", "node", "D", False),
    Technique("graph_retrieval", "Name linker (reference)", "linker", "p", True),
)
BY_SYSTEM = {t.system: t for t in TECHNIQUES}


def apply_style() -> None:
    """matplotlib defaults for every figure: a sans face, a recessive solid hairline grid, 2px lines."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Segoe UI", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "semibold",
            "axes.labelsize": 9,
            "axes.labelcolor": INK_SECONDARY,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.8,
            "axes.facecolor": SURFACE,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",  # solid: a dashed grid reads as a threshold
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelcolor": INK_SECONDARY,
            "ytick.labelcolor": INK_SECONDARY,
            "lines.linewidth": 2.0,
            "lines.solid_capstyle": "round",
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "svg.fonttype": "path",  # text as outlines: the SVG looks the same on a machine without the font
            "svg.hashsalt": "kgbuilder-figures",  # fixed ids: a rebuild of unchanged data gives the same SVG
        }
    )


def title(fig: plt.Figure, text: str, subtitle: str) -> None:
    """The figure's title (what it shows) and a subtitle (its data and n), left-aligned above the panels."""
    fig.text(0.01, 0.995, text, ha="left", va="top", fontsize=12, fontweight="semibold", color=INK)
    fig.text(0.01, 0.955, subtitle, ha="left", va="top", fontsize=8.5, color=INK_SECONDARY)


def save(fig: plt.Figure, folder: Path, name: str) -> list[Path]:
    """Write `name`.svg (vector, for the thesis) and `name`.png (200 dpi preview) into `folder`; close it."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / f"{name}.svg", folder / f"{name}.png"]
    # no date or software stamp, and the SVG text written with "\n" (matplotlib would write the platform's
    # line ends): a rebuild of unchanged data gives the same files
    svg = io.StringIO()
    fig.savefig(svg, format="svg", bbox_inches="tight", metadata={"Date": None})
    paths[0].write_text(svg.getvalue(), encoding="utf-8", newline="\n")
    fig.savefig(paths[1], dpi=200, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return paths
