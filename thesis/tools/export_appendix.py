"""Export, verbatim from the code and the committed gold, the material the thesis's appendices print.

Role: writes `thesis/appendices/generated/{prompts,tools,question_types}.tex` so that Appendix A (prompt
templates), Appendix D (question types, with one benchmark example each) and Appendix F (tool reference) are
copies of the final code at the commit the thesis is built from, never retyped. Run from the repository root:
`uv run python thesis/tools/export_appendix.py`; the outputs are committed with the thesis.
Must not: call a model or a database, or change anything outside `thesis/appendices/generated/`.
"""

import json
from pathlib import Path

from kgbuilder.agent import prompt as agent_prompt
from kgbuilder.agent import system
from kgbuilder.hybrid import passage_rerank
from kgbuilder.resolution import concepts, individuals, record_choice
from kgbuilder.structured import proposer
from kgbuilder.text import extraction, mention_pass
from kgbuilder.text import schema as text_schema

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "thesis" / "appendices" / "generated"
GOLD = ROOT / "tests" / "gold" / "qa"
DATASETS = ("ntsb", "furniture", "heldout", "generality")  # the four thesis datasets, in the thesis's order
TYPES = ("lookup", "structured_filter", "multi_hop", "aggregation", "disambiguation", "negation_sensitive")
TYPE_NAMES = {
    "lookup": "Lookup",
    "structured_filter": "Structured filter",
    "multi_hop": "Multi-hop",
    "aggregation": "Aggregation",
    "disambiguation": "Disambiguation",
    "negation_sensitive": "Negation-sensitive",
}
PROMPTS = (
    ("Builder: construction plan (\\texttt{structured/proposer.py})", proposer.PROPOSER_PROMPT),
    ("Builder: plan critic (\\texttt{structured/proposer.py})", proposer.CRITIC_PROMPT),
    ("Builder: text schema (\\texttt{text/schema.py})", text_schema.PROMPT),
    ("Builder: claim extraction (\\texttt{text/extraction.py})", extraction.PROMPT),
    ("Builder: mention pass (\\texttt{text/mention\\_pass.py})", mention_pass.MENTION_PROMPT),
    ("Builder: record chooser (\\texttt{resolution/record\\_choice.py})", record_choice.CHOICE_PROMPT),
    ("Builder: individual identity (\\texttt{resolution/individuals.py})", individuals.IDENTITY_PROMPT),
    ("Builder: concept identity (\\texttt{resolution/concepts.py})", concepts.ADJUDICATE_PROMPT),
    ("Query engine: agent (\\texttt{agent/prompt.py})", agent_prompt.PROMPT),
    ("Query engine: closing prompt at a cap (\\texttt{agent/prompt.py})", agent_prompt.CLOSING),
    ("Query engine: passage reranker (\\texttt{hybrid/passage\\_rerank.py})", passage_rerank.PROMPT),
)


def tex(text: str) -> str:
    """Escape the characters LaTeX treats specially in running text."""
    for a, b in (
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ):
        text = text.replace(a, b) if a != "\\" else text.replace(a, "\x00")
    return text.replace("\x00", "\\textbackslash{}")


def prompts() -> str:
    parts = []
    for title, body in PROMPTS:
        heading, source = title.split(" (", 1)  # the module path goes on its own line: it cannot break
        parts.append(
            f"\\section*{{{heading}}}\n\\noindent Source: {source[:-1]}\n"
            f"\\begin{{lstlisting}}\n{body.strip()}\n\\end{{lstlisting}}\n"
        )
    return "\n".join(parts)


def tools() -> str:
    rows = []
    for tool in system.AGENT_TOOL_TYPES:
        fields = tool.arguments.model_fields
        args = "; ".join(
            f"\\texttt{{{tex(n)}}}: {tex(f.description.rstrip('.'))}"
            if f.description
            else f"\\texttt{{{tex(n)}}}"
            for n, f in fields.items()
        )
        rows.append(
            f"\\paragraph{{\\texttt{{{tex(tool.name)}}}}} {tex(tool.description)}\n\n"
            f"\\noindent\\emph{{Arguments:}} {args or 'none'}.\n"
        )
    return "\n".join(rows)


def question_types() -> str:
    questions = []
    for ds in DATASETS:
        data = json.loads((GOLD / f"{ds}_qa_bench.json").read_text(encoding="utf-8"))
        questions += [(ds, q) for q in data["questions"]]
    rows = []
    for i, t in enumerate(TYPES):
        # a fixed rule, not a picked example: type i takes the first benchmark question of its type from
        # dataset i (mod 4), so the examples spread over the four datasets
        want = DATASETS[i % len(DATASETS)]
        ds, q = next((ds, q) for ds, q in questions if q["type"] == t and ds == want)
        # a long JSON answer may break after its commas
        expected = tex(json.dumps(q["expected"], ensure_ascii=False)).replace(", ", ",\\allowbreak{} ")
        rows.append(
            f"\\paragraph{{{TYPE_NAMES[t]} ({tex(q['id'])}).}} {tex(q['question'])}\n\n"
            # ragged right: a long JSON answer in a monospace font would otherwise overflow the line
            f"{{\\raggedright\\noindent\\emph{{Expected:}} \\texttt{{{expected}}}\\par}}\n"
        )
    return "\n".join(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "prompts.tex").write_text(prompts(), encoding="utf-8", newline="\n")
    (OUT / "tools.tex").write_text(tools(), encoding="utf-8", newline="\n")
    (OUT / "question_types.tex").write_text(question_types(), encoding="utf-8", newline="\n")
    print("written to", OUT)


if __name__ == "__main__":
    main()
