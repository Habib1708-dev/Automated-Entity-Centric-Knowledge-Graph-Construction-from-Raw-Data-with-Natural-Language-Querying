"""The query engine's diagrams for the thesis (designs of R130-R210, drawn in R234): the engine from the
question to the answer, the two ranked retrieval paths inside the agent's tools, and the typed structured
query.

Role: `build.py` calls `draw_all`; the files land in `figures/diagrams/query/r130_r210/`, whose README.md is
their caption list.
Design: boxes and arrows (boxes.py) with the system diagrams' fixed kinds: orange rounded = a language model
proposes, square = code decides (blue: a tool), aqua cylinder = a store, dashed frame = measured but not
adopted.
Drawn for a landscape page of the thesis (13.2 in wide, text 11.2-11.5 pt: 8 pt printed). The settings shown
(caps, K, RRF constants) are the code's (config.py, agent/, hybrid/).
Must not: show a measured number; draw a component the final code does not have.
"""

from pathlib import Path

from .boxes import TINT, arrow, canvas, code_box, frame, llm_box, store
from .style import INK_SECONDARY, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "query" / "r130_r210"
TEXT = 11.5  # printed on a landscape page at about 0.72 scale: 8.3 pt
LABEL = 11.2  # the smallest text: 8.1 pt printed
TOOL = TINT[TEXT_BLUE]


def _title(fig, text: str, subtitle: str) -> None:
    """The figure's title, sized for a landscape page in the thesis (about 0.72 scale)."""
    title(fig, text, subtitle, size=15, subsize=11.5, gap=0.05)


def _label(ax, x: float, y: float, text: str, ha: str = "center") -> None:
    ax.text(x, y, text, ha=ha, va="center", fontsize=LABEL, color=INK_SECONDARY, linespacing=1.25)


def draw_all(out: Path = OUT) -> list[Path]:
    """The three diagrams into `out`; returns the files written."""
    return [*diag1_engine(out), *diag2_retrieval(out), *diag3_structured_query(out)]


# --- diag1: from the question to the answer ---------------------------------------------------------------


def diag1_engine(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 7.0)
    h = 1.05
    code_box(ax, 0.15, 3.9, 1.7, h, "question", size=TEXT)
    code_box(ax, 2.15, 3.9, 2.0, h, "screen:\nrefuse requests\nto change data", size=TEXT)
    arrow(ax, (1.85, 3.9 + h / 2), (2.15, 3.9 + h / 2))
    code_box(ax, 2.15, 2.3, 1.95, 0.8, "refuse", size=TEXT)
    arrow(ax, (3.15, 3.9), (3.15, 3.1))
    # the loop
    frame(ax, 4.45, 1.0, 5.4, 4.95, "Agent loop, one typed step per turn", size=LABEL + 1)
    llm_box(ax, 4.7, 3.9, 2.3, h, "model: a tool call\nor the answer", size=TEXT)
    code_box(
        ax, 7.3, 3.9, 2.3, h, "code checks the call:\nknown tool, valid\narguments, no repeat", size=TEXT
    )
    code_box(ax, 7.3, 2.3, 2.3, h, "the tool runs,\nread-only", TOOL, size=TEXT)
    code_box(ax, 4.7, 2.3, 2.3, h, "result written back,\nevery cut announced", size=TEXT)
    arrow(ax, (4.15, 3.9 + h / 2), (4.7, 3.9 + h / 2))
    arrow(ax, (7.0, 3.9 + h / 2), (7.3, 3.9 + h / 2))
    arrow(ax, (8.45, 3.9), (8.45, 2.3 + h))
    arrow(ax, (7.3, 2.3 + h / 2), (7.0, 2.3 + h / 2))
    arrow(ax, (5.85, 2.3 + h), (5.85, 3.9))
    _label(
        ax,
        7.15,
        1.55,
        "caps: 12 turns, 10 tool calls, 400k prompt characters, 180 s;\n"
        "at a cap the tools close and the model answers",
    )
    # tools and stores
    code_box(
        ax,
        10.15,
        4.55,
        2.9,
        1.2,
        "graph tools: find nodes,\nnode, neighbours,\nevidence, rank",
        TOOL,
        size=TEXT,
    )
    code_box(
        ax,
        10.15,
        3.1,
        2.9,
        1.2,
        "text tools: ranked search,\nclaims, whole document,\nexhaustive word search",
        TOOL,
        size=TEXT,
    )
    code_box(
        ax,
        10.15,
        1.65,
        2.9,
        1.2,
        "structured tools:\nschema, typed queries\nover records and tables",
        TOOL,
        size=TEXT,
    )
    for y in (5.15, 3.7, 2.25):
        arrow(ax, (9.6, 2.3 + h / 2), (10.15, y))
    store(ax, 10.15, 0.1, 1.4, 1.2, "Neo4j\ngraph,\nindexes", size=LABEL)
    store(ax, 11.65, 0.1, 1.4, 1.2, "DuckDB\ntables", size=LABEL)
    # the answer and its checks
    code_box(
        ax,
        0.15,
        0.1,
        4.0,
        1.75,
        "checks on the answer, each sent back once:\nnot empty; an outcome label code can verify;\n"
        "the declared form (a number, names);\na wider search than one word search;\n"
        "citations only to ids a tool returned",
        size=TEXT,
    )
    # the answer leaves the loop on the left, down a lane between the screen's column and the loop's frame
    ax.plot([4.7, 4.3, 4.3], [4.05, 4.05, 1.3], color="#898781", lw=1.2)
    arrow(ax, (4.3, 1.3), (4.15, 1.3))
    _label(ax, 4.22, 3.5, "answer", "right")
    # the tools read the stores
    arrow(ax, (10.85, 1.65), (10.85, 1.3), both=True)
    arrow(ax, (12.35, 1.65), (12.35, 1.3), both=True)
    code_box(
        ax,
        4.45,
        0.1,
        5.4,
        0.7,
        "answer with citations | clarify | out of scope | insufficient evidence",
        size=TEXT,
    )
    arrow(ax, (4.15, 0.45), (4.45, 0.45))
    _title(
        fig,
        "The query engine: a model chooses one read-only tool call per turn; code checks every call, every "
        "citation and the outcome",
        "Final engine (R130-R210). Orange rounded: the model proposes. Square: code decides; blue: the "
        "tools. "
        "Cylinders: stores.",
    )
    return save(fig, out, "r210_diag1_query_engine")


# --- diag2: the two ranked retrieval paths ----------------------------------------------------------------


def diag2_retrieval(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 6.2)
    h = 1.05
    # A: start nodes
    frame(ax, 0.15, 3.0, 12.9, 2.45, "A. find nodes: where a question starts", size=LABEL + 1)
    code_box(ax, 0.4, 3.35, 2.1, h, "the question's\nwords", size=TEXT)
    code_box(ax, 3.0, 4.05, 2.6, 0.95, "25 cards, dense\n(embedding)", size=TEXT)
    code_box(ax, 3.0, 3.25, 2.6, 0.7, "25 cards, lexical (BM25)", size=TEXT)
    code_box(ax, 6.2, 3.35, 2.4, h, "RRF fusion,\nk = 60", size=TEXT)
    code_box(
        ax,
        9.2,
        3.35,
        3.6,
        h,
        "15 nodes shown;\nthe agent reads their cards\nand chooses where to go",
        TOOL,
        size=TEXT,
    )
    for y in (4.5, 3.6):
        arrow(ax, (2.5, 3.35 + h / 2), (3.0, y))
        arrow(ax, (5.6, y), (6.2, 3.35 + h / 2))
    arrow(ax, (8.6, 3.35 + h / 2), (9.2, 3.35 + h / 2))
    # B: passages
    frame(ax, 0.15, 0.1, 12.9, 2.6, "B. search text: which passages to read", size=LABEL + 1)
    code_box(ax, 0.4, 0.5, 2.1, h, "the agent's\nsearch words", size=TEXT)
    code_box(ax, 3.0, 1.25, 2.6, 0.95, "10 chunks, dense\n(embedding)", size=TEXT)
    code_box(ax, 3.0, 0.45, 2.6, 0.7, "10 chunks, lexical (BM25)", size=TEXT)
    code_box(ax, 6.2, 0.5, 2.0, h, "RRF pool,\nk = 10", size=TEXT)
    llm_box(ax, 8.55, 0.5, 2.15, h, "small model\nreranks the pool", size=TEXT)
    code_box(ax, 11.0, 0.5, 1.8, h, "10 whole\npassages", TOOL, size=TEXT)
    for y in (1.7, 0.8):
        arrow(ax, (2.5, 0.5 + h / 2), (3.0, y))
        arrow(ax, (5.6, y), (6.2, 0.5 + h / 2))
    arrow(ax, (8.2, 0.5 + h / 2), (8.55, 0.5 + h / 2))
    arrow(ax, (10.7, 0.5 + h / 2), (11.0, 0.5 + h / 2))
    _label(ax, 9.6, 2.3, "if the reranker fails: the RRF order")
    _title(
        fig,
        "Two ranked paths inside the tools: fused card lists find start nodes; fused and reranked chunk "
        "lists "
        "find passages",
        "Settings chosen by the retrieval experiments (R130-R132, R197-R199). Cards are deterministic text "
        "built from each node's fields. Lexical search is Neo4j's full-text (Lucene) index.",
    )
    return save(fig, out, "r210_diag2_retrieval_paths")


# --- diag3: the typed structured query --------------------------------------------------------------------


def diag3_structured_query(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 5.6)
    h = 1.3
    llm_box(
        ax,
        0.15,
        2.3,
        2.9,
        1.75,
        "the model fills a typed\nquery: a label or table,\nfilters, a measure, a\ngrouping, a path",
        size=TEXT,
    )
    code_box(ax, 3.45, 2.5, 2.6, h, "code validates it\nagainst the schema:\nfields, operators", size=TEXT)
    code_box(ax, 6.45, 3.35, 2.9, 1.05, "compiled to\nparameterised Cypher", size=TEXT)
    code_box(ax, 6.45, 1.95, 2.9, 1.05, "compiled to a\nDuckDB SELECT", size=TEXT)
    store(ax, 9.75, 3.3, 1.5, 1.15, "Neo4j\n(READ)", size=TEXT)
    store(ax, 9.75, 1.9, 1.5, 1.15, "DuckDB\n(locked)", size=TEXT)
    code_box(ax, 11.6, 2.35, 1.45, 1.55, "rows with\ntheir record\nkeys, cited", TOOL, size=TEXT)
    arrow(ax, (3.05, 3.15), (3.45, 3.15))
    arrow(ax, (6.05, 3.15), (6.45, 3.85))
    arrow(ax, (6.05, 3.15), (6.45, 2.45))
    arrow(ax, (9.35, 3.85), (9.75, 3.85))
    arrow(ax, (9.35, 2.45), (9.75, 2.45))
    arrow(ax, (11.25, 3.85), (11.6, 3.4))
    arrow(ax, (11.25, 2.45), (11.6, 2.85))
    _label(
        ax,
        0.15,
        4.75,
        "Measures: rows, count, sum, average, min, max, distinct. Filters: equals, comparisons, in, "
        "contains, whole word, empty cell;\none field against another. Group by a field or by year or "
        "decade; paths of up to "
        "three relationships; paging.",
        "left",
    )
    # the free-SQL variant, measured, not adopted
    frame(ax, 0.15, 0.3, 12.9, 1.1, "", dashed=True)
    _label(
        ax,
        6.6,
        0.82,
        "Measured, not adopted (agent_sql): the model writes one SELECT; DuckDB's own parser checks it "
        "is a single\nread-only statement over the known tables (no table functions, no other schemas); "
        "it runs on locked, "
        "offline tables with a time-out.",
    )
    _title(
        fig,
        "Structured questions: the model fills a typed query; code validates and compiles it, and only reads",
        "Tools query_records and query_tables (R133-R175). Orange rounded: the model proposes; square: code "
        "decides; cylinders: stores. Dashed: an experimental variant.",
    )
    return save(fig, out, "r210_diag3_structured_query")
