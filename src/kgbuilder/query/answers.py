"""What a question-answering system returns: the answer, the chunks it was read from, and how they were found.

Role in the pipeline: every system in systems.py returns a `SystemAnswer`; `kg qa` writes one per question
to `answers_<system>.jsonl`, which the judge reads and `kg qa-score` scores (validation/qa.py).
Design: `SystemAnswer` extends the scoring model `QAAnswer` with what a reader of the file needs besides
the answer: the exact chunk texts the model was shown (the judge reads them, and citation faithfulness is
checked against them without a graph) and a trace of the retrieval (which node each name linked to, which
chunks the traversal reached), from which failures are grouped by cause.
Not here: producing answers (systems.py), scoring them (validation/qa.py).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..core.errors import EvaluationError
from ..validation.qa import QAAnswer


class ShownChunk(BaseModel):
    """A chunk as the reader saw it: its id, the document it belongs to, and its text."""

    chunk_id: str
    context: str  # the document's name (its first heading), which the chunk text may not repeat
    text: str


class LinkedNode(BaseModel):
    """A graph node a question was linked to, and how."""

    # a thing is a domain node (addressed by element id), a kind an `:Entity` of the subject graph (by id)
    kind: Literal["thing", "kind"]
    node_id: str
    name: str
    by: Literal["spelling", "meaning"]


class RetrievalTrace(BaseModel):
    """How the graph route chose its chunks, kept so a wrong answer can be traced to its cause."""

    linked: list[LinkedNode] = []
    # every chunk the traversal reached, best first: a gold chunk missing here was never reached, one
    # present beyond the top k was cut by the ranking
    candidates: list[str] = []
    reached_by: dict[str, list[str]] = {}  # traversal pattern -> the chunks it reached


class ExactAttempt(BaseModel):
    """One Cypher proposal of the exact route and what became of it."""

    cypher: str  # as checked and run: with the LIMIT code added
    parameters: dict[str, bool | int | float | str | list[str]]
    issues: list[str]  # why the checks or the run refused it; empty when it ran


class ExactTrace(BaseModel):
    """How the exact route answered, or why it gave up (then the question falls back to retrieval)."""

    attempts: list[ExactAttempt]
    answered: bool
    rows: int = 0


class SystemAnswer(QAAnswer):
    """One system's answer to one question, with the chunks shown and, for the graph, its traces: the
    retrieval route's, and the exact route's when the router chose it (also when it then fell back)."""

    system: str
    shown: list[ShownChunk] = []
    trace: RetrievalTrace | None = None
    exact: ExactTrace | None = None


def shown_texts(answers: list[SystemAnswer]) -> dict[str, str]:
    """Chunk id -> text of every chunk shown to the reader, for checking citations without a graph."""
    return {c.chunk_id: c.text for a in answers for c in a.shown}


def load_system_answers(path: Path) -> list[SystemAnswer]:
    """An answers file written by `kg qa` (JSON lines), or `EvaluationError` naming each bad line."""
    answers: list[SystemAnswer] = []
    issues: list[str] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            answers.append(SystemAnswer.model_validate_json(line))
        except ValidationError as e:
            issues.append(f"answers line {number}: {e.errors()[0]['msg']}")
    if issues:
        raise EvaluationError(issues)
    return answers
