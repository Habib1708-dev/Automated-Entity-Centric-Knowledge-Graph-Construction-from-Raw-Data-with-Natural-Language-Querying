"""What a question-answering system returns: the answer, the chunks it was read from, and how they were found.

Role in the pipeline: every system in systems.py returns a `SystemAnswer`; `kg qa` writes one per question
to `answers_<system>.jsonl`, which the judge reads and `kg qa-score` scores (validation/qa.py).
Design: `SystemAnswer` extends the scoring model `QAAnswer` with what a reader of the file needs besides
the answer: the exact chunk texts the model was shown (the judge reads them, and citation faithfulness is
checked against them without a graph) and a trace of the retrieval (which node each name linked to, which
chunks the traversal reached), from which failures are grouped by cause; for a plan system (R74), the
plans tried, why they were refused, the steps they ran and the fallback that answered, if any; for the
agent (R135), its tool calls and why its loop stopped, and (R147) the answer form it declared and whether
an answer was sent back for leaving that form out.
Not here: producing answers (systems.py), scoring them (validation/qa.py).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..core.errors import EvaluationError
from ..validation.qa import QAAnswer
from .plan import QueryPlan


class ShownChunk(BaseModel):
    """A chunk as the reader saw it: its id, the document it belongs to, and its text."""

    chunk_id: str
    context: str  # the document's name (its first heading), which the chunk text may not repeat
    text: str


class LinkedNode(BaseModel):
    """A graph node a question was linked to, and how."""

    # a thing is a domain node (addressed by element id), a kind an individual or a concept of the identity
    # layer (by canonical id, R75)
    kind: Literal["thing", "kind"]
    node_id: str
    name: str
    by: Literal["spelling", "meaning"]


class ClaimHit(BaseModel):
    """A claim that claim retrieval found (R120): its observation, the chunk it was read from, its truth
    fields as stored (truth is never left to the claim sentence's text, which an embedding and a full-text
    analyzer both blur), and its siblings: the observations of the same canonical subject, predicate and
    object whose triple has the opposite truth, with their chunks, so a disagreement is read together."""

    id: str
    chunk_id: str
    truth: str  # of the statement (R77 part d)
    negation: str = ""
    modality: str = "actual"
    hedge: str = ""
    condition: str = ""
    triple_truth: str = "affirmed"  # whether the stored triple itself is denied: what siblings oppose
    siblings: list[str] = []  # the opposite-truth observations' ids, sorted
    sibling_chunks: list[str] = []  # their chunks, in the same order


class RetrievalTrace(BaseModel):
    """How the graph route chose its chunks, kept so a wrong answer can be traced to its cause."""

    linked: list[LinkedNode] = []
    # every chunk the traversal reached, best first: a gold chunk missing here was never reached, one
    # present beyond the top k was cut by the ranking
    candidates: list[str] = []
    reached_by: dict[str, list[str]] = {}  # traversal pattern -> the chunks it reached
    # the nodes retrieval started from, best first, by stable id (R117): record refs and canonical ids, the
    # ids the target gold is placed on, so `kg retrieve-eval` scores them; empty in files from before R117
    seeds: list[str] = []
    # hybrid retrieval (R120): each retriever's own ranked chunks before fusion, by retriever name, so a chunk
    # can be traced to the lists that brought it; and the claims the claim retrievers found, with their
    # truth fields. Empty for every other source
    lists: dict[str, list[str]] = {}
    claims: list[ClaimHit] = []


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


class StepTrace(BaseModel):
    """What one plan step did: how many items of each kind it produced, and a note (direction, checks)."""

    op: str
    items: dict[str, int]
    note: str = ""


class PlanAttempt(BaseModel):
    """One plan the planner wrote: the plan as written, why it was refused (empty when it ran to the end),
    the optional filters code dropped, and the steps it ran."""

    plan: QueryPlan
    issues: list[str] = []
    dropped: list[str] = []
    steps: list[StepTrace] = []


class PlanTrace(BaseModel):
    """How a plan system answered (R74): its plans, and the fallback that answered when no plan could."""

    attempts: list[PlanAttempt]
    fallback: Literal["text2cypher", "retrieval"] | None = None
    # the plan or text2cypher query was replayed from an earlier run (R80, `kg qa --plans`), not written
    # anew; its attempts are then the replayed ones, and a refused one is the graph change's doing
    frozen: bool = False
    checks: int = 0  # read_check model calls
    verified: int = 0  # candidates read_check verified


class AgentCall(BaseModel):
    """One tool call the agent proposed (R135): the tool and its arguments as written, why code refused it
    (empty when it ran), the result's note, the ids it returned, and how long it took."""

    turn: int
    tool: str
    arguments: str  # the JSON object the model wrote
    refused: str = ""
    note: str = ""
    returned: list[str] = []
    latency_ms: float = 0.0


AgentStop = Literal["answer", "turns", "calls", "prompt_chars", "timeout"]

# The form an agent's answer declares the question asks for (R147): one number, named things (or values that
# are not numbers), or a statement in words. Here, not in agent/, so the trace can keep it (query must not
# import agent).
AnswerForm = Literal["things", "number", "statement"]


class AgentTrace(BaseModel):
    """How the agent answered (R135): every call, how many turns it took, why the loop stopped (the model
    answered, or a cap was reached and it answered with the tools closed), the characters of every prompt
    sent, and the citations code kept and dropped (ids no tool returned)."""

    calls: list[AgentCall]
    turns: int
    stopped: AgentStop
    prompt_chars: int
    cited: list[str] = []
    dropped: list[str] = []
    retried: bool = False  # the answer was sent back once for citing ids no tool returned
    emptied: bool = False  # an empty answer was written back once, the tools open (R138)
    # R147: an answer that left out the form it declared was sent back once, the tools open; and the form
    # the final answer declared (None: a model off the schema, or a file from before R147)
    reformed: bool = False
    asks: AnswerForm | None = None
    # R142: time in the model's calls and in the whole loop; whether every named entity, and the number,
    # appear in what the cited results hold (None: the answer names none, or gives none), and the names
    # that do not
    llm_ms: float = 0.0
    wall_ms: float = 0.0
    entities_grounded: bool | None = None
    number_stated: bool | None = None
    ungrounded: list[str] = []


class SystemAnswer(QAAnswer):
    """One system's answer to one question, with the chunks shown and its traces: the retrieval route's,
    the exact route's (R71's graph system, and the text2cypher fallback of a plan system), the plans', and
    the agent's (R135)."""

    system: str
    shown: list[ShownChunk] = []
    trace: RetrievalTrace | None = None
    exact: ExactTrace | None = None
    plan: PlanTrace | None = None
    agent: AgentTrace | None = None
    latency_s: float | None = None  # wall-clock of answering it in `kg qa` (R142); None before R142


def shown_texts(answers: list[SystemAnswer]) -> dict[str, str]:
    """Chunk id -> text of every chunk shown to the reader, for checking citations without a graph. A chunk
    shown several times keeps its longest text: the agent's `find_passages` shows only a passage's sentences
    (R141), so one answer's excerpt must not replace the whole text another answer quoted from (R143b)."""
    texts: dict[str, str] = {}
    for c in (c for a in answers for c in a.shown):
        if len(c.text) > len(texts.get(c.chunk_id, "")):
            texts[c.chunk_id] = c.text
    return texts


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
