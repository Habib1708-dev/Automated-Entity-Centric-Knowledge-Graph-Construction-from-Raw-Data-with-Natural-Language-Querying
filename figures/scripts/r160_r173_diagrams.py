"""The two method diagrams of plans R160-R173: the fix-and-confirm protocol (how the engine was measured,
fixed and measured again, each time on questions written blind), and the agent loop one answer runs through
(the ReAct-style turn: the model proposes one typed call, code checks and runs it, the result is observed,
until the answer).

Role: `build.py` calls `draw_all`; the files land in `figures/diagrams/evaluation/r160_r173/`, whose
README.md is their caption list.
Design: boxes and arrows (boxes.py) in the figures' one style. diag1 reads left to right in time, three
columns (one per measurement) in three rows: the question sets on top (each written before the engine it
measures answered anything), the engine in the middle, the run and its scores below; the failures of one
measurement feed the fixes of the next (the slanted arrows), the fixes never touch the question sets. The
score boxes wear the score hues of the charts. diag2 reads left to right as a turn flows, with the loop drawn
back from the observation to the model; the code boxes are neutral, the model's box is tinted orange (the
one box that proposes), so the division "the LLM proposes, code decides" is visible. Every count and cost is
read through `r173_data` from the committed records; the caps are the settings a test pins. Dollar signs
are escaped.
Must not: show a number no committed record or pinned setting holds.
"""

from pathlib import Path

from . import r173_data as data
from .boxes import NEUTRAL, TINT, arrow, box, canvas
from .style import EXACT, INK_MUTED, INK_SECONDARY, NODE_ORANGE, TEXT_BLUE, save, title

OUT = Path(__file__).resolve().parents[1] / "diagrams" / "evaluation" / "r160_r173"
WIDTH = 12.4
TEXT = 9.5
LABEL = 8.5
# diag1: the three rows' lower edges and the row box's height
ROWS = {"questions": 3.75, "engine": 2.2, "scores": 0.5}
HEIGHT = 1.25
COLS = (0.3, 4.45, 8.6)  # the three measurements' left edges
COL_W = 3.6
# diag2: the agent's caps, as the settings pin them (tests/test_agent_loop.py)
CALLS_CAP = 10


def draw_all(measurements: list[data.Measurement], costs: list[data.RunCost], out: Path = OUT) -> list[Path]:
    """The two diagrams into `out`; returns the files written."""
    return [*diag1_fix_and_confirm_protocol(measurements, costs, out), *diag2_agent_loop(out)]


# --- diag1: measure, fix, measure again --------------------------------------------------------------------


def diag1_fix_and_confirm_protocol(
    measurements: list[data.Measurement], costs: list[data.RunCost], out: Path
) -> list[Path]:
    by_key = {m.key: m for m in measurements}
    cost = {c.key: c for c in costs}
    fig, ax = canvas(WIDTH, 5.8)  # the top row ends 0.4 in below the subtitle
    for label, row in (
        ("Questions,\nwritten blind", "questions"),
        ("Engine", "engine"),
        ("Run and\nscores", "scores"),
    ):
        ax.text(
            0.05, ROWS[row] + HEIGHT / 2, label, ha="left", va="center", fontsize=LABEL, color=INK_SECONDARY
        )
    questions = (
        "R152-R154: 50 per dataset,\n250 in all, five datasets,\nfrom the sources only",
        "R160: 20 per dataset, 100,\nwritten before the fixes,\nno repeat of an earlier fact",
        "R171: 20 per dataset, 100,\nwritten after the fixes but\nblind to them; one review each",
    )
    engines = (
        "Frozen at R151\n(tag query-engine-r151),\nbefore any of these questions",
        "R161-R164: count with the\ntool, word forms, the form\nrule, varied schema examples",
        "R167-R170: empty cell, distinct\nand year/decade, hyphens,\nheadings kept in excerpts",
    )
    for x, key, q, e in zip(COLS, data.KEYS, questions, engines, strict=True):
        m, c = by_key[key], cost[key]
        wrong = len([o for o in m.outcomes if o.wrong])
        box(ax, x + 0.9, ROWS["questions"], COL_W - 0.9, HEIGHT, q, size=TEXT)
        box(ax, x + 0.9, ROWS["engine"], COL_W - 0.9, HEIGHT, e, TINT[NODE_ORANGE], size=TEXT)
        run = f"{c.label.split(' ')[0]}: \\${c.cost:.3f}" + (
            f" (+ \\${c.wasted:.3f} wasted)" if c.wasted else ""
        )
        pooled = m.pooled
        box(
            ax, x + 0.9, ROWS["scores"], COL_W - 0.9, HEIGHT,
            f"{run}\n{m.key.upper()}: exact {pooled.exact.k}/{pooled.exact.n}, by meaning "
            f"{pooled.meaning.k}/{pooled.meaning.n};\n{wrong} wrong, each traced to its cause",
            TINT[EXACT.color], size=TEXT,
        )  # fmt: skip
        # the column's own flow: questions and the engine into the run
        arrow(ax, (x + 2.65, ROWS["questions"]), (x + 2.65, ROWS["engine"] + HEIGHT))
        arrow(ax, (x + 2.65, ROWS["engine"]), (x + 2.65, ROWS["scores"] + HEIGHT))
    # the slanted arrows: a measurement's traced failures feed the next engine's fixes, never its questions
    for left, right in zip(COLS[:-1], COLS[1:], strict=True):  # each column and the next
        start = (left + COL_W, ROWS["scores"] + HEIGHT / 2)
        end = (right + 0.9, ROWS["engine"] + HEIGHT / 2)
        arrow(ax, start, end)
        ax.text(
            (start[0] + end[0]) / 2 + 0.05, (start[1] + end[1]) / 2 - 0.02, "failures traced\n-> fixes",
            ha="left", va="center", fontsize=LABEL, color=INK_SECONDARY, linespacing=1.2,
        )  # fmt: skip
    ax.text(
        0.3, 0.12,
        "Blue: the scores, as in the charts; orange: the engine. Each fix was proven by an offline replay of "
        "the recorded failing calls ($0) before the next paid run.\nThe tool text changes with every fix, so "
        "each score describes the engine of its column on its own questions; the question sets differ, so "
        "no difference is attributed to the fixes.",
        fontsize=LABEL, color=INK_SECONDARY, va="bottom", linespacing=1.4,
    )  # fmt: skip
    title(
        fig,
        "Measure, fix, measure again: three question sets written blind, the engine fixed between them "
        "from its traced failures",
        "Plans R152-R157, R160-R166 and R167-R173, left to right in time. Within a column the questions "
        "and the engine feed the run; a column's failures feed only the next column's fixes.",
    )
    return save(fig, out, "r173_diag1_fix_and_confirm_protocol")


# --- diag2: the agent loop ---------------------------------------------------------------------------------


def diag2_agent_loop(out: Path) -> list[Path]:
    fig, ax = canvas(WIDTH, 5.3)
    y, h = 1.75, 1.75
    steps = (
        (0.25, 1.75, "Question\n+ the tools' list\nand schemas\n(one dataset)", NEUTRAL),
        (
            2.3,
            2.5,
            "Model: one reply\nper turn, held to a\nschema: a call (tool,\ntyped arguments) or\nthe answer; "
            "its reasoning\nis its own thinking,\nnever written out",
            TINT[NODE_ORANGE],
        ),
        (
            5.1,
            2.35,
            "Code checks the call:\nunknown tool, arguments\nthat do not parse, a repeat\n-> refused, the "
            "reason\nwritten back; a query is\nchecked against the schema",
            NEUTRAL,
        ),
        (
            7.75,
            2.35,
            "The tool runs, read-only:\ngraph walk (find_nodes,\nget_node, neighbours),\ntext (search_text, "
            "find_passages,\nread_document), tables and\nrecords (schema, query_tables,\nquery_records)",
            TINT[TEXT_BLUE],
        ),
        (10.4, 1.75, "Result written\nback as this\nturn's observation", NEUTRAL),
    )
    for x, w, text, colours in steps:
        box(ax, x, y, w, h, text, colours, size=TEXT)
    for (x, w, _, _), (nx, _, _, _) in zip(steps[:-1], steps[1:], strict=True):  # each step and the next
        arrow(ax, (x + w, y + h / 2), (nx, y + h / 2))
    # the loop: from the observation back to the model, along the top
    lane = y + h + 0.45
    line = {"color": INK_MUTED, "lw": 1.2, "solid_capstyle": "butt"}
    ax.plot([11.3, 11.3, 3.55], [y + h, lane, lane], **line)
    arrow(ax, (3.55, lane), (3.55, y + h))
    ax.text(
        7.4, lane + 0.06,
        f"next turn: the model reads every earlier call with its result; at a cap ({CALLS_CAP} tool calls, "
        "turns, prompt size, time) the tools close and it answers from what it has",
        ha="center", va="bottom", fontsize=LABEL, color=INK_SECONDARY,
    )  # fmt: skip
    # the answer's checks, below the model: sent back once, then scored
    box(
        ax, 2.3, 0.15, 4.6, 1.15,
        "The answer, checked by code:\nan id no tool returned -> sent back once, then dropped;\n"
        "empty, or missing the form it declares (a number, named\nthings) -> sent back once, then it stands",
        NEUTRAL, size=TEXT,
    )  # fmt: skip
    arrow(ax, (3.55, y), (3.55, 1.3))
    ax.text(3.65, 1.5, "answer", ha="left", va="center", fontsize=LABEL, color=INK_SECONDARY)
    # the scores' box is neutral: code decides them (the judges' verdicts are data code scores)
    box(
        ax,
        7.35,
        0.15,
        2.0,
        1.15,
        "Scored twice:\nexact match (code),\nby meaning (judges)",
        NEUTRAL,
        size=TEXT,
    )
    arrow(ax, (6.9, 0.725), (7.35, 0.725))
    ax.text(
        9.6, 0.72,
        "ReAct in shape (reason, act, observe,\nrepeat); not in letter: no written thought,\na typed call "
        "code validates, refuses or caps,\nand the send-backs.",
        ha="left", va="center", fontsize=LABEL, color=INK_SECONDARY, linespacing=1.3,
    )  # fmt: skip
    title(
        fig,
        "The agent loop: the model proposes one typed tool call per turn, code checks and runs it, the "
        "result is observed, until the answer",
        "R135-R170, one question. Orange: the one box that proposes; blue: the tools over the graph, the "
        "text and the staged tables; neutral: code that decides and contains.",
    )
    return save(fig, out, "r173_diag2_agent_loop")
