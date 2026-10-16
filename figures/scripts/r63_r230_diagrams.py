"""The construction diagrams of the thesis (designs of R63-R230, drawn in R232): why facts stored as shared
edges mislead and how the anchor graph avoids it, the anchor graph's layers with the walks that navigate it,
and how a mention gets its identity.

Role: `build.py` calls `draw_all`; the files land in `figures/diagrams/construction/r63_r230/`, whose
README.md is their caption list.
Design: boxes and arrows (boxes.py) with the system diagrams' fixed kinds: orange rounded = a language model
proposes, square = code decides, aqua cylinder = a store; in the graph diagrams a square is a record (from a
table), a rounded neutral box a node made from text, a blue box a chunk of text. diag1's example is the leak
the anchor-graph direction names (furniture, R63); its words are the corpus's, quoted in the direction
document. diag3's thresholds are the settings the code pins (config.py, presets.yaml, resolution/*).
Must not: show a measured number; draw a node, edge or rule the final code does not have.
"""

from pathlib import Path

from .boxes import TINT, arrow, box, canvas, code_box, frame, llm_box
from .style import INK_MUTED, INK_SECONDARY, NODE_ORANGE, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "construction" / "r63_r230"
TEXT = 11.0  # printed on a landscape page at about 0.73 scale: 8 pt
LABEL = 10.0
CHUNK = TINT[TEXT_BLUE]


def _title(fig, text: str, subtitle: str) -> None:
    """The figure's title, sized for a landscape page in the thesis (about 0.73 scale)."""
    title(fig, text, subtitle, size=15, subsize=11.5, gap=0.05)


def draw_all(out: Path = OUT) -> list[Path]:
    """The three diagrams into `out`; returns the files written."""
    return [*diag1_leak_and_witness(out), *diag2_anchor_layers(out), *diag3_identity(out)]


def _label(ax, x: float, y: float, text: str, ha: str = "center") -> None:
    ax.text(x, y, text, ha=ha, va="center", fontsize=LABEL, color=INK_SECONDARY, linespacing=1.25)


# --- diag1: a claim stored as a shared edge leaks; a shared node that only finds chunks does not ------------


def diag1_leak_and_witness(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 5.9)
    # A: the claim graph
    frame(
        ax, 0.15, 0.15, 6.3, 4.9, "A. Claims stored as edges between shared nodes (up to R62)", size=LABEL + 1
    )
    code_box(ax, 0.45, 3.45, 2.1, 0.7, "Helsingborg Dresser\n(product record)", size=TEXT)
    code_box(ax, 4.05, 3.45, 2.1, 0.7, "Linköping Bed\n(product record)", size=TEXT)
    box(ax, 2.25, 1.95, 2.0, 0.7, "drawer rails", align="center", size=TEXT)
    box(ax, 2.0, 0.45, 2.5, 0.7, "rough, uneven surfaces", align="center", size=TEXT)
    arrow(ax, (1.5, 3.45), (2.7, 2.65))
    arrow(ax, (5.1, 3.45), (3.8, 2.65))
    _label(ax, 1.55, 2.95, "has part", "right")
    _label(ax, 5.05, 2.95, "has part", "left")
    arrow(ax, (3.25, 1.95), (3.25, 1.15))
    _label(ax, 3.35, 1.55, "HAS_DEFECT\n(from the dresser's review)", "left")
    # the false reading, dashed orange
    ax.annotate(
        "", xy=(4.5, 0.85), xytext=(6.0, 3.45),
        arrowprops={"arrowstyle": "-|>", "color": NODE_ORANGE, "lw": 1.4, "linestyle": (0, (4, 3)),
                    "connectionstyle": "arc3,rad=-0.35"},
    )  # fmt: skip
    _label(
        ax,
        4.98,
        2.2,
        'path read as a fact:\n"the bed\'s rails are\ndefective" (wrong:\n'
        'its review praises\n"quality drawer slides")',
    )
    # B: the anchor graph
    frame(
        ax, 6.75, 0.15, 6.3, 4.9, "B. Anchor graph: a shared node only finds chunks (R88 on)", size=LABEL + 1
    )
    code_box(ax, 7.05, 3.55, 2.1, 0.7, "Helsingborg Dresser\n(product record)", size=TEXT)
    code_box(ax, 10.65, 3.55, 2.1, 0.7, "Linköping Bed\n(product record)", size=TEXT)
    box(ax, 7.0, 1.95, 2.2, 1.05, 'chunk of its review:\n"... rough, uneven\nsurfaces ..."', CHUNK, size=TEXT)
    box(
        ax,
        10.6,
        1.95,
        2.2,
        1.05,
        'chunk of its review:\n"quality drawer slides\nrated for 25kg loads"',
        CHUNK,
        size=TEXT,
    )
    box(ax, 8.85, 0.45, 2.1, 0.7, "drawer rails (concept)", align="center", size=TEXT)
    arrow(ax, (8.1, 3.0), (8.1, 3.55))
    arrow(ax, (11.7, 3.0), (11.7, 3.55))
    _label(ax, 8.2, 3.27, "ABOUT", "left")
    _label(ax, 11.8, 3.27, "ABOUT", "left")
    arrow(ax, (9.4, 1.15), (8.4, 1.95))
    arrow(ax, (10.4, 1.15), (11.3, 1.95))
    _label(ax, 9.9, 1.75, "mentioned in\n(MENTIONS, REFERS_TO)")
    _label(
        ax, 9.9, 4.6,
        "\"Which products have defective drawer rails?\": the concept leads to both chunks,\n"
        "each chunk to its product; the reader keeps the dresser's chunk and rejects the bed's.",
    )  # fmt: skip
    _title(
        fig,
        "A fact stored on a shared node leaks to everything linked to it; in the anchor graph a shared node "
        "only finds chunks, which are read",
        "Furniture data, the leak found by the path-truth audit (R63). Square: a record from a table; "
        "rounded: "
        "a node made from text; blue: a chunk of text.",
    )
    return save(fig, out, "r230_diag1_leak_and_witness_rule")


# --- diag2: the anchor graph's layers and the walks over them --------------------------------------------


def diag2_anchor_layers(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 6.7)
    # lexical layer
    box(ax, 0.3, 4.55, 2.3, 0.8, "(:Document)\ndoc_id, title", align="center", size=TEXT)
    box(
        ax,
        0.3,
        2.75,
        2.3,
        1.0,
        "(:Chunk)\ntext, embedding,\nheading context",
        CHUNK,
        size=TEXT,
        align="center",
    )
    box(ax, 0.3, 0.8, 2.3, 1.0, "(:Chunk)\nthe next chunk", CHUNK, size=TEXT, align="center")
    arrow(ax, (1.45, 3.75), (1.45, 4.55))
    _label(ax, 1.55, 4.15, "PART_OF", "left")
    arrow(ax, (1.45, 2.75), (1.45, 1.8))
    _label(ax, 1.55, 2.27, "NEXT_CHUNK\n(W5 context)", "left")
    # mention layer
    box(ax, 3.7, 2.75, 2.4, 1.0, "(:Mention)\nname, type, document,\nstated class", align="center", size=TEXT)
    arrow(ax, (2.6, 3.25), (3.7, 3.25))
    _label(ax, 3.15, 3.5, "MENTIONS")
    # identity targets
    code_box(
        ax, 7.6, 4.45, 2.4, 1.0, "record\n(:Product, :Vehicle, ...)\nkey and fields from the plan", size=TEXT
    )
    box(
        ax,
        7.6,
        2.75,
        2.4,
        1.0,
        "(:Individual)\na named thing only\nthe text knows",
        align="center",
        size=TEXT,
    )
    box(ax, 7.6, 1.05, 2.4, 1.0, "(:Concept)\na kind, part, state\nor property", align="center", size=TEXT)
    for y in (4.95, 3.25, 1.55):
        arrow(ax, (6.1, 3.25), (7.6, y))
    _label(ax, 6.0, 4.35, "REFERS_TO\n{reason, score,\nevidence, by}", "left")
    # record layer
    code_box(ax, 10.9, 4.45, 2.0, 1.0, "another record", size=TEXT)
    arrow(ax, (10.0, 4.95), (10.9, 4.95))
    _label(ax, 10.45, 5.75, "typed relation\nfrom the plan (W4)")
    # attachment: document and chunk ABOUT a record or individual
    ax.plot([2.6, 2.95, 2.95, 8.8, 8.8], [5.15, 5.15, 5.85, 5.85, 5.45], color=INK_MUTED, lw=1.2)
    arrow(ax, (8.8, 5.6), (8.8, 5.45))
    _label(ax, 5.8, 6.03, "ABOUT {how, evidence}: a document or a section is about one record (W3)")
    # the frozen claim layer
    frame(ax, 10.55, 0.3, 2.5, 3.6, "kept, not navigated", dashed=True, size=LABEL + 1)
    box(
        ax,
        10.7,
        1.55,
        2.2,
        1.35,
        "(:Observation)\na claim: predicate,\ntruth, modality,\nverbatim quote",
        align="center",
        size=TEXT,
    )
    _label(ax, 11.8, 0.85, "SUBJECT / OBJECT -> Mention\nFROM -> Chunk")
    # the walks
    _label(
        ax, 0.3, 0.3,
        "W1 find: a name or alias -> its record, individual or concept.   W2 chunks of a node: back along "
        "REFERS_TO and MENTIONS, and ABOUT.   W3 what a chunk\nis about or names (records and individuals, "
        "never a concept).   W4 related records: the plan's relations.   W5 context: neighbouring chunks, "
        "the document.",
        "left",
    )  # fmt: skip
    _title(
        fig,
        "The anchor graph: text is linked to the records and things it mentions; every link carries its "
        "reason, and the claims are kept aside",
        "The final graph model (R88-R230). Square: a record from a table; rounded: a node made from text; "
        "blue: "
        "a chunk. Dashed frame: stored but not used to navigate.",
    )
    return save(fig, out, "r230_diag2_anchor_graph_layers")


# --- diag3: identity, per class of thing ----------------------------------------------------------------


def diag3_identity(out: Path) -> list[Path]:
    fig, ax = canvas(13.2, 6.6)
    w, h = 1.78, 1.05
    box(ax, 0.15, 2.45, 1.5, 1.3, "a mention\nfrom the\nmention pass", align="center", size=TEXT)
    lanes = {"Records": 4.25, "Individuals": 2.45, "Concepts": 0.65}
    for name, y in lanes.items():
        _label(ax, 2.05, y + h + 0.15, name, "left")
    arrow(ax, (1.65, 3.4), (2.05, lanes["Records"] + h / 2))
    arrow(ax, (1.65, 3.1), (2.05, lanes["Individuals"] + h / 2))
    arrow(ax, (1.65, 2.8), (2.05, lanes["Concepts"] + h / 2))
    xs = [1.95 + i * 1.98 for i in range(5)]
    # records: rules first, the model only for near misses
    y = lanes["Records"]
    code_box(ax, xs[0], y, w, h, "the key in the name\n(a key decides alone\nif it has a digit)", size=TEXT)
    code_box(ax, xs[1], y, w, h, "the name inside the\ndocument's scope\n(same words, >= 90)", size=TEXT)
    code_box(ax, xs[2], y, w, h, "a key or key attribute\nnext to the name\nin a sentence", size=TEXT)
    llm_box(ax, xs[3], y, w, h, "near misses only:\nthe model picks one\nand quotes a sentence", size=TEXT)
    code_box(
        ax, xs[4], y, w, h, "code links if the id\nwas listed, the quote\nis in the chunk, no twin", size=TEXT
    )
    # individuals
    y = lanes["Individuals"]
    code_box(ax, xs[0], y, w, h, "one unit per record\nlinked; else one\nper mention", size=TEXT)
    code_box(ax, xs[1], y, w, h, "pairs nominated:\nname variants, spelling\n>= 80, embedding NN", size=TEXT)
    llm_box(ax, xs[2], y, w, h, "same / different /\nunsure, with a quote\nfrom each side", size=TEXT)
    code_box(
        ax, xs[3], y, w, h, "joined only if both\nquotes are found; two\nrecords never joined", size=TEXT
    )
    # concepts
    y = lanes["Concepts"]
    code_box(ax, xs[0], y, w, h, "one per type and\nnormalised name", size=TEXT)
    code_box(ax, xs[1], y, w, h, "candidates: spelling\n>= 80, or mutual\nneighbours (k = 2)", size=TEXT)
    code_box(
        ax,
        xs[2],
        y,
        w,
        h,
        "four guards block:\nopposite polarity, one\nsentence, part/whole,\none extra word",
        size=TEXT,
    )
    llm_box(ax, xs[3], y, w, h, "spelling >= 92: merged;\nelse the model says\nsame or not", size=TEXT)
    for name, n in (("Records", 5), ("Individuals", 4), ("Concepts", 4)):
        y = lanes[name] + h / 2
        for i in range(n - 1):
            arrow(ax, (xs[i] + w, y), (xs[i + 1], y))
    # the outcome: an edge, never a merged node
    code_box(
        ax, 11.8, 0.65, 1.35, 4.65,
        "code writes\nREFERS_TO\n{reason, score,\nevidence, by}\n\n"
        "reversible:\nresolve --undo\ndeletes only\nthe identity\nlayer",
        size=TEXT - 0.5,
    )  # fmt: skip
    for name, last in (("Records", 4), ("Individuals", 3), ("Concepts", 3)):
        y = lanes[name] + h / 2
        arrow(ax, (xs[last] + w, y), (11.8, y))
    _label(
        ax,
        xs[0],
        0.3,
        "Unlinked mentions the text states as kinds become concepts (R107). A record is never merged "
        "with another record: text only links to it.",
        "left",
    )
    _title(
        fig,
        "Identity: rules decide what they can; a model is asked only about near misses, and its answer "
        "counts "
        "only if its quotes are found",
        "Resolve stage (R75-R108). Orange rounded: the model proposes; square: code decides. Thresholds are "
        "the code's settings (spelling scores are rapidfuzz token-sort ratios, 0-100).",
    )
    return save(fig, out, "r230_diag3_identity_resolution")
