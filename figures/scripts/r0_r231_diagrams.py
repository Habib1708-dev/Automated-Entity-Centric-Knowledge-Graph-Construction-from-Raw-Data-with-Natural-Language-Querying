"""The system diagram of the whole project (R0-R231, drawn for the thesis in R232): the sources, the builder's
stages, the three stores, the query engine and the web interface, as the final code runs them.

Role: `build.py` calls `draw_all`; the file lands in `figures/diagrams/system/r0_r231/`, whose README.md is
its caption.
Design: boxes and arrows (boxes.py), read left to right as data flows: sources -> builder -> stores -> query
engine -> answer, with the user's question entering the engine from the top and every stage logging to MLflow
along the bottom. The element kinds carry fixed meanings: orange rounded = a language model proposes, square =
code decides, blue square = the read-only tools, aqua cylinder = a store. Stage names are the `kg` commands,
so a reader can find each in the code.
Must not: show a number; draw a component the final code does not run.
"""

from pathlib import Path

from .boxes import TINT, arrow, canvas, code_box, frame, llm_box, store
from .style import INK_SECONDARY, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "system" / "r0_r231"
TEXT = 11.5  # printed on a landscape page at about 0.72 scale: 8.3 pt
LABEL = 11.2  # the smallest text: 8.1 pt printed


def _title(fig, text: str, subtitle: str) -> None:
    """The figure's title, sized for a landscape page in the thesis (about 0.73 scale)."""
    title(fig, text, subtitle, size=15, subsize=11.5, gap=0.05)


def draw_all(out: Path = OUT) -> list[Path]:
    """The diagram into `out`; returns the files written."""
    return diag1_system_architecture(out)


def diag1_system_architecture(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 7.9)
    h = 0.78  # a stage box's height
    # --- sources (left) ---
    code_box(ax, 0.15, 4.55, 1.75, 0.8, "Goal\none sentence", size=TEXT)
    code_box(ax, 0.15, 3.15, 1.75, 1.0, "Tables\nCSV, JSON, NDJSON,\nPostgreSQL", size=TEXT)
    code_box(ax, 0.15, 1.75, 1.75, 1.0, "Documents\nMarkdown, text,\nPDF", size=TEXT)
    # --- builder ---
    frame(ax, 2.2, 1.2, 5.05, 4.95, "Builder (Chapter 4)", size=LABEL + 1)
    xs = (2.4, 4.0, 5.6)
    w = 1.45
    # structured path
    code_box(ax, xs[0], 4.65, w, h, "profile\ntypes, keys", size=TEXT)
    llm_box(ax, xs[1], 4.65, w, h, "plan\nrecord plan", size=TEXT)
    code_box(ax, xs[2], 4.65, w, h, "build\nvalidate, import", size=TEXT)
    # text path: chunks, the fact schema, claims
    code_box(ax, xs[0], 3.35, w, h, "ingest-text\nchunk, embed", size=TEXT)
    llm_box(ax, xs[1], 3.35, w, h, "text-schema\nfact types", size=TEXT)
    llm_box(ax, xs[2], 3.35, w, h, "extract\nclaims + quotes", size=TEXT)
    # identity: mentions, links, attachment (right to left)
    llm_box(ax, xs[2], 2.05, w, h, "mention-pass\nmentions", size=TEXT)
    code_box(ax, xs[1], 2.05, w, h, "link, resolve\nrules; LLM only\nfor near misses", size=TEXT)
    code_box(ax, xs[0], 2.05, w, h, "attach\nABOUT edges", size=TEXT)
    for row in (4.65, 3.35):
        arrow(ax, (xs[0] + w, row + h / 2), (xs[1], row + h / 2))
        arrow(ax, (xs[1] + w, row + h / 2), (xs[2], row + h / 2))
    arrow(ax, (xs[2] + w / 2, 3.35), (xs[2] + w / 2, 2.05 + h))
    arrow(ax, (xs[2], 2.05 + h / 2), (xs[1] + w, 2.05 + h / 2))
    arrow(ax, (xs[1], 2.05 + h / 2), (xs[0] + w, 2.05 + h / 2))
    ax.text(
        2.4, 1.45, "Every model output is parsed and checked by code;\n"
        "every item from text keeps a verbatim quote.",
        fontsize=LABEL, color=INK_SECONDARY, va="center", linespacing=1.3,
    )  # fmt: skip
    # sources into the builder
    # the goal steers both proposals: along a lane above the builder's rows, down into each proposal
    ax.plot([1.9, 2.03, 2.03, 3.9, 3.9], [4.95, 4.95, 5.6, 5.6, 4.3], color="#898781", lw=1.2)
    arrow(ax, (3.9, 4.3), (xs[1], 3.35 + h - 0.12))
    arrow(ax, (3.9, 5.6), (xs[1] + 0.3, 4.65 + h))
    ax.text(2.95, 5.63, "goal", fontsize=LABEL, color=INK_SECONDARY, ha="center", va="bottom")
    arrow(ax, (1.9, 3.65), (xs[0], 4.65 + h / 2 - 0.1))
    arrow(ax, (1.9, 2.25), (xs[0], 3.35 + h / 2 - 0.1))
    # --- stores (middle) ---
    store(ax, 7.6, 3.2, 1.65, 2.1, "Neo4j\nanchor graph\n+ vector index\n+ full-text index", size=TEXT)
    store(ax, 7.6, 1.35, 1.65, 1.3, "DuckDB\nstaged tables", size=TEXT)
    arrow(ax, (xs[2] + w, 4.65 + h / 2), (7.6, 4.65 + h / 2))  # records
    arrow(ax, (7.25, 3.6), (7.6, 3.6))  # from the frame: chunks, claims, mentions, identity, ABOUT edges
    # the source tables, staged as they are for the agent's table queries: a dashed lane below the builder
    ax.plot(
        [1.9, 2.07, 2.07, 7.42, 7.42], [3.3, 3.3, 1.04, 1.04, 1.85], color="#898781", lw=1.2, ls=(0, (4, 3))
    )
    arrow(ax, (7.42, 1.85), (7.6, 1.85), dashed=True)
    ax.text(
        5.6, 1.07, "tables staged as they are", fontsize=LABEL, color=INK_SECONDARY, ha="center", va="bottom"
    )
    # --- query engine ---
    frame(ax, 9.6, 1.2, 3.45, 4.95, "Query engine (Chapter 6)", size=LABEL + 1)
    code_box(ax, 9.8, 6.4, 3.05, 0.5, "User: web interface or command line", size=TEXT)
    code_box(ax, 9.8, 4.8, 3.05, 0.7, "screen: refuses requests\nto change the data", size=TEXT)
    code_box(
        ax, 9.8, 3.35, 1.35, 1.05, "12 read-only\ntools: graph,\ntext, tables", TINT[TEXT_BLUE], size=TEXT
    )
    llm_box(ax, 11.5, 3.35, 1.35, 1.05, "agent\none typed call\nper turn", size=TEXT)
    code_box(ax, 9.8, 2.35, 3.05, 0.65, "checks: citations, form, outcome", size=TEXT)
    code_box(ax, 9.8, 1.4, 3.05, 0.7, "answer with citations, or\nclarify / out of scope / refuse", size=TEXT)
    arrow(ax, (11.33, 6.4), (11.33, 5.5))
    arrow(ax, (12.17, 4.8), (12.17, 4.4))
    arrow(ax, (11.15, 3.88), (11.5, 3.88), both=True)
    arrow(ax, (12.17, 3.35), (12.17, 3.0))
    arrow(ax, (11.33, 2.35), (11.33, 2.1))
    arrow(ax, (9.8, 3.95), (9.25, 3.95), both=True)  # the tools read the graph
    arrow(ax, (9.8, 3.55), (9.25, 2.1), both=True)  # and the tables
    # --- MLflow along the bottom ---
    store(
        ax,
        2.2,
        0.08,
        10.85,
        0.8,
        "MLflow: one run per stage and per question set; every model call with its tokens, cost and latency; "
        "LLM response cache",
        size=TEXT,
    )
    for x in (3.3, 11.3):
        arrow(ax, (x, 1.2), (x, 0.88), dashed=True)
    _title(
        fig,
        "The system: a pipeline builds an anchor graph from tables and text; an agent answers over it with "
        "read-only tools",
        "Final code (R231). Orange rounded: a language model proposes. Square: code decides (blue: the "
        "agent's "
        "tools). Cylinders: stores. Stage names are the kg commands.",
    )
    return save(fig, out, "r231_diag1_system_architecture")
