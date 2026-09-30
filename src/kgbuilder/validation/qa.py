"""Scores of the question-answer benchmark (R70): answer correctness, retrieval recall@k and citation
faithfulness, per question type, each with its n and Wilson interval.

Role in the pipeline: `kg qa` (Step 2 of the layered-model task) writes one answer per gold question
(qa_gold.py) and scores the answers here. The judge (Claude in the session, `evaluation` skill) decides
only the free-text answers, through a verdict file with a reason for each.
Design: the LLM proposes, code decides, also when answering: sets and numbers are compared by code, and
every cited quote is looked up by code in a chunk the reader was given. Scoring is pure; the caller
supplies the chunk texts.
Not here: the gold file and its checks (qa_gold.py), answering (Step 2), MLflow.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..core.errors import EvaluationError
from ..core.text import norm
from .interval import Proportion
from .judge import JudgeMeta
from .qa_gold import Expected, ExpectedEntity, QAGold, QAQuestion, QuestionType, Route

# Numbers must be equal; the tolerance only absorbs floating-point dust (0.1 + 0.2), never a rounding.
_NUMBER_TOLERANCE = 1e-9


class Citation(BaseModel):
    """A chunk the answer relies on and the words it quotes from it."""

    chunk_id: str
    quote: str


class QAAnswer(BaseModel):
    """One system's answer to one gold question: one line of the answers file.

    `retrieved` lists the chunk ids given to the reader, best first; it is empty when no chunk was read
    (an exact-route answer). An answer may fill any form; the gold's form decides which one is scored,
    and a form the answer left empty counts as wrong.
    """

    question_id: str
    retrieved: list[str] = []
    entities: list[str] | None = None
    number: float | None = None
    text: str | None = None
    citations: list[Citation] = []


class AnswerVerdict(BaseModel):
    """The judge's decision on one free-text answer, with the reason for it."""

    question_id: str
    correct: bool
    reason: str = Field(min_length=1)


class QAVerdicts(BaseModel):
    """The judge's verdict file for the free-text answers of one answers file."""

    judge: JudgeMeta
    gold: str  # the gold file the questions come from
    answers: str  # the answers file judged
    verdicts: list[AnswerVerdict]


class TypeScores(BaseModel):
    """The scores of one question type, or of all questions."""

    questions: int
    correct: Proportion  # questions answered right
    # gold evidence chunks among the top k retrieved, pooled over the retrieval-route questions: the gold
    # names the chunks those questions need, while an exact-route question may need none
    recall_at_k: Proportion
    faithful: Proportion  # citations whose quote code finds in the cited chunk, which the reader was given


class QAReport(BaseModel):
    """All scores of one answers file; `by_type` has every type, so metric names stay stable."""

    k: int
    overall: TypeScores
    by_type: dict[QuestionType, TypeScores]


@dataclass(frozen=True)
class _QuestionScore:
    type: QuestionType
    correct: bool
    hits: int  # gold evidence chunks in the top k
    needed: int  # gold evidence chunks counted for recall (0 for an exact-route question)
    faithful: int
    cited: int


def load_answers(path: Path) -> list[QAAnswer]:
    """The answers file (JSON lines; blank lines skipped), or `EvaluationError` naming each bad line."""
    answers: list[QAAnswer] = []
    issues: list[str] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            answers.append(QAAnswer.model_validate_json(line))
        except ValidationError as e:
            issues.append(f"answers line {number}: {e.errors()[0]['msg']}")
    if issues:
        raise EvaluationError(issues)
    return answers


def load_qa_verdicts(path: Path) -> QAVerdicts:
    """The verdict file, or `EvaluationError` naming every verdict that is not valid on its own."""
    try:
        return QAVerdicts.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise EvaluationError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def entities_match(expected: Sequence[ExpectedEntity], given: Sequence[str]) -> bool:
    """Set equality by name: the answer names every expected entity (by its name or an alias, compared
    with `norm`) and nothing else. A partly right set is wrong: an exact query must return exactly the
    set, and a retrieval answer that adds a wrong thing misleads its reader."""
    wanted = [{norm(name) for name in (e.name, *e.aliases)} for e in expected]
    named = {norm(name) for name in given}
    return all(names & named for names in wanted) and all(any(n in names for names in wanted) for n in named)


def is_correct(expected: Expected, answer: QAAnswer, verdict: AnswerVerdict | None) -> bool:
    """Whether the answer is right: a set or a number by code, a free text by the judge's verdict."""
    if expected.entities is not None:
        return answer.entities is not None and entities_match(expected.entities, answer.entities)
    if expected.number is not None:
        return answer.number is not None and math.isclose(
            answer.number, expected.number, rel_tol=0.0, abs_tol=_NUMBER_TOLERANCE
        )
    return verdict is not None and verdict.correct


def citation_is_faithful(citation: Citation, answer: QAAnswer, chunk_texts: Mapping[str, str]) -> bool:
    """The cited chunk was given to the reader and holds the quote.

    Compared with `norm`, as extraction's `verify` compares evidence: a model changes case and drops
    markdown when it copies. The quote must be in the chunk the citation names, not merely somewhere in
    what was retrieved: a citation is the path back to the source, and a wrong path is not a path.
    """
    quote = norm(citation.quote)
    return (
        bool(quote)
        and citation.chunk_id in answer.retrieved
        and quote in norm(chunk_texts[citation.chunk_id])
    )


def score_qa(
    gold: QAGold,
    answers: Sequence[QAAnswer],
    chunk_texts: Mapping[str, str],
    k: int,
    verdicts: QAVerdicts | None = None,
) -> QAReport:
    """Score one answers file against its gold: correctness, recall@k and faithfulness, overall and per
    type. `chunk_texts` must hold every cited chunk the reader was given.

    Raises `ValueError` for k < 1, and `EvaluationError` when the answers do not cover the gold's
    questions exactly once, when free-text answers lack verdicts (or verdicts cover other questions), or
    when a cited chunk's text is missing.
    """
    if k < 1:
        raise ValueError(f"recall@k needs k >= 1, got {k}")
    _check(gold, answers, chunk_texts, verdicts)
    answer_of = {a.question_id: a for a in answers}
    verdict_of = {v.question_id: v for v in verdicts.verdicts} if verdicts else {}
    scores = [_score(q, answer_of[q.id], verdict_of.get(q.id), chunk_texts, k) for q in gold.questions]
    return QAReport(
        k=k,
        overall=_aggregate(scores),
        by_type={t: _aggregate([s for s in scores if s.type == t]) for t in QuestionType},
    )


def _score(
    question: QAQuestion,
    answer: QAAnswer,
    verdict: AnswerVerdict | None,
    chunk_texts: Mapping[str, str],
    k: int,
) -> _QuestionScore:
    needed = {c.chunk_id for c in question.chunks} if question.route == Route.RETRIEVAL else set()
    return _QuestionScore(
        type=question.type,
        correct=is_correct(question.expected, answer, verdict),
        hits=len(needed & set(answer.retrieved[:k])),
        needed=len(needed),
        faithful=sum(citation_is_faithful(c, answer, chunk_texts) for c in answer.citations),
        cited=len(answer.citations),
    )


def _aggregate(scores: list[_QuestionScore]) -> TypeScores:
    return TypeScores(
        questions=len(scores),
        correct=Proportion.of(sum(s.correct for s in scores), len(scores)),
        recall_at_k=Proportion.of(sum(s.hits for s in scores), sum(s.needed for s in scores)),
        faithful=Proportion.of(sum(s.faithful for s in scores), sum(s.cited for s in scores)),
    )


def _check(
    gold: QAGold, answers: Sequence[QAAnswer], chunk_texts: Mapping[str, str], verdicts: QAVerdicts | None
) -> None:
    """The answers cover every gold question once; the verdicts cover exactly the free-text ones."""
    issues = _coverage_issues("answer", [a.question_id for a in answers], {q.id for q in gold.questions})
    text_ids = {q.id for q in gold.questions if q.expected.kind == "text"}
    if verdicts is not None:
        issues += _coverage_issues("verdict", [v.question_id for v in verdicts.verdicts], text_ids)
    elif text_ids:
        issues.append(f"{len(text_ids)} free-text answers need the judge's verdict file")
    cited = {c.chunk_id for a in answers for c in a.citations if c.chunk_id in a.retrieved}
    if missing := sorted(cited - chunk_texts.keys()):
        issues.append(f"{len(missing)} cited chunks have no text to check against (first: {missing[:3]})")
    if issues:
        raise EvaluationError(issues)


def _coverage_issues(what: str, given: list[str], needed: set[str]) -> list[str]:
    issues = [f"a question has more than one {what}"] if len(given) != len(set(given)) else []
    if missing := needed - set(given):
        issues.append(f"{len(missing)} questions have no {what} (first: {sorted(missing)[:3]})")
    if unknown := set(given) - needed:
        issues.append(f"{len(unknown)} {what}s are for questions it does not cover ({sorted(unknown)[:3]})")
    return issues
