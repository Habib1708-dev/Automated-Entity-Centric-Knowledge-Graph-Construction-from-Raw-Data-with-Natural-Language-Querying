"""Scores of the question-answer benchmark (R70): answer correctness, retrieval recall@k and citation
faithfulness, per question type, each with its n and Wilson interval, and one outcome row per question
(R73) for the paired comparison of two systems (paired.py).

Role in the pipeline: `kg qa` (Step 2 of the layered-model task) writes one answer per gold question
(qa_gold.py) and scores the answers here. The judge (Claude in the session, `evaluation` skill) decides
only the free-text answers, through a verdict file with a reason for each.
Design: the LLM proposes, code decides, also when answering: sets and numbers are compared by code, and
every cited quote is looked up by code in a chunk the reader was given. Scoring is pure; the caller
supplies the chunk texts.
R205: outcomes. A gold question expecting no answer is right when the system's outcome equals the gold's,
by code; an answerable one is wrong when the system gave another outcome (a missing outcome, from a system
without outcomes or a file from before R204, reads as "answer"). The report adds the outcome scores:
outcome accuracy, recall and precision per outcome, and the false abstentions on answerable questions.
Not here: the gold file and its checks (qa_gold.py), answering (Step 2), comparing systems (paired.py),
MLflow.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..core.errors import EvaluationError
from ..core.text import norm
from .interval import Proportion
from .judge import JudgeMeta
from .qa_gold import Expected, ExpectedEntity, Outcome, QAGold, QAQuestion, QuestionType, Route

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
    # the route the system's router chose (R71), scored against the gold's route; None for a system that
    # has no router (the vector baseline), which is then left out of route accuracy
    route: Route | None = None
    # R204: how the request ended (the agent's settled outcome, or the screen's refusal); None for a system
    # without outcomes and for files from before R204, which scoring reads as "answer"
    outcome: Outcome | None = None

    @property
    def given_outcome(self) -> Outcome:
        """The outcome scored: the answer's own, or "answer" when it gives none."""
        return self.outcome or "answer"


class AnswerVerdict(BaseModel):
    """The judge's decision on one free-text answer, with the reason for it."""

    question_id: str
    correct: bool
    reason: str = Field(min_length=1)


class QAVerdicts(BaseModel):
    """The judge's verdict file for one answers file: the free-text answers only (`scope` "text", code scores
    sets and numbers: exact match), or every answer by meaning (`scope` "all", R144: the judged accuracy,
    reported next to exact match and never in its place; the `evaluation` skill's two scores)."""

    judge: JudgeMeta
    gold: str  # the gold file the questions come from
    answers: str  # the answers file judged
    scope: Literal["text", "all"] = "text"
    verdicts: list[AnswerVerdict]


class TypeScores(BaseModel):
    """The scores of one question type, or of all questions."""

    questions: int
    correct: Proportion  # questions answered right, over the judged ones
    # free-text answers left out of `correct` because no verdict file was given yet (`allow_unjudged`)
    unjudged: int = 0
    # gold evidence chunks among the top k retrieved, pooled over the retrieval-route questions: the gold
    # names the chunks those questions need, while an exact-route question may need none
    recall_at_k: Proportion
    # the same over every question with chunk evidence, whatever its route (R71): the retrieval-route
    # chunks are few (14-15 per dataset), and the vector baseline answers every question by retrieval
    recall_all_at_k: Proportion
    faithful: Proportion  # citations whose quote code finds in the cited chunk, which the reader was given
    route: Proportion  # router labels equal to the gold's route, over the answers that carry a label


class QAOutcome(BaseModel):
    """One question's result for one system: a line of `qa_outcomes_<system>.jsonl` (R73).

    The totals of `QAReport` cannot show which questions changed between two systems or two steps; these
    rows can, and paired.py compares two files of them question by question.
    """

    question_id: str
    type: QuestionType
    system: str
    correct: bool | None  # None: a free-text answer the judge has not decided yet
    route: Route  # the gold's route
    system_route: Route | None  # the router's label; None for a system without a router
    cited_chunks: list[str]  # the chunk ids the answer cites, in its order


OUTCOMES: tuple[Outcome, ...] = ("answer", "clarify", "out_of_scope", "insufficient_evidence", "refuse")


class OutcomeScores(BaseModel):
    """How well the outcomes were told apart (R205), all by code: outcome accuracy over every question;
    per outcome, recall (of the questions expecting it, the share given it) and precision (of the answers
    giving it, the share expecting it); and the false abstentions, answerable questions given another
    outcome. Every outcome has an entry, so metric names stay stable."""

    accuracy: Proportion
    recall: dict[Outcome, Proportion]
    precision: dict[Outcome, Proportion]
    false_abstention: Proportion

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics: the accuracy with its interval and n, the false abstentions with their n,
        and each outcome's recall and precision with their n."""
        out: dict[str, float | int | None] = {
            "outcome_accuracy": self.accuracy.rate, "outcome_accuracy_low": self.accuracy.low,
            "outcome_accuracy_high": self.accuracy.high, "outcome_accuracy_n": self.accuracy.n,
            "false_abstention": self.false_abstention.rate, "false_abstention_n": self.false_abstention.n,
        }  # fmt: skip
        for outcome in OUTCOMES:
            for name, share in (("recall", self.recall[outcome]), ("precision", self.precision[outcome])):
                out.update({f"outcome_{name}_{outcome}": share.rate, f"outcome_{name}_{outcome}_n": share.n})
        return out


def score_outcomes(pairs: Sequence[tuple[Outcome, Outcome]]) -> OutcomeScores:
    """The outcome scores of (expected, given) pairs, one per question."""
    answerable = [given for expected, given in pairs if expected == "answer"]
    return OutcomeScores(
        accuracy=Proportion.of(sum(e == g for e, g in pairs), len(pairs)),
        recall={
            o: Proportion.of(sum(g == o for e, g in pairs if e == o), sum(e == o for e, _ in pairs))
            for o in OUTCOMES
        },
        precision={
            o: Proportion.of(sum(e == o for e, g in pairs if g == o), sum(g == o for _, g in pairs))
            for o in OUTCOMES
        },
        false_abstention=Proportion.of(sum(g != "answer" for g in answerable), len(answerable)),
    )


class QAReport(BaseModel):
    """All scores of one answers file; `by_type` has every type, so metric names stay stable. `outcomes`
    holds one row per gold question, in the gold's order. `outcome_scores` (R205) is set when any answer
    gives an outcome or any question expects one other than an answer; None otherwise, so a baseline's
    report on answerable questions carries no outcome scores it cannot have."""

    k: int
    overall: TypeScores
    by_type: dict[QuestionType, TypeScores]
    outcomes: list[QAOutcome]
    outcome_scores: OutcomeScores | None = None

    def metrics(self) -> dict[str, float | int | None]:
        """Flat MLflow metrics: every rate overall with its interval and n, and every rate per type with its
        n (the per-type intervals are in the report file); an empty rate is None, logged as nothing."""
        out: dict[str, float | int | None] = {"questions": self.overall.questions}
        for name, share in _shares(self.overall).items():
            out.update({name: share.rate, f"{name}_low": share.low, f"{name}_high": share.high})
            out[f"{name}_n"] = share.n
        out["answers_unjudged"] = self.overall.unjudged
        for qtype, scores in self.by_type.items():
            out[f"questions_{qtype.value}"] = scores.questions
            for name, share in _shares(scores).items():
                out.update({f"{name}_{qtype.value}": share.rate, f"{name}_n_{qtype.value}": share.n})
        if self.outcome_scores is not None:
            out.update(self.outcome_scores.metrics())
        return out


def _shares(scores: TypeScores) -> dict[str, Proportion]:
    """The rates of `scores` under their metric names."""
    return {
        "answer_accuracy": scores.correct,
        "recall_at_k": scores.recall_at_k,
        "recall_all_at_k": scores.recall_all_at_k,
        "citation_faithfulness": scores.faithful,
        "route_accuracy": scores.route,
    }


@dataclass(frozen=True)
class _QuestionScore:
    type: QuestionType
    judged: bool  # False: a free-text answer with no verdict yet
    correct: bool
    hits: int  # gold evidence chunks in the top k
    needed: int  # gold evidence chunks counted for recall (0 for an exact-route question)
    hits_all: int  # the same over the question's chunk evidence whatever its route
    needed_all: int
    faithful: int
    cited: int
    route_right: bool | None  # None: the answer carries no route label


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


def load_outcomes(path: Path) -> list[QAOutcome]:
    """An outcome file written by `kg qa-score` (JSON lines), or `EvaluationError` naming each bad line."""
    outcomes: list[QAOutcome] = []
    issues: list[str] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            outcomes.append(QAOutcome.model_validate_json(line))
        except ValidationError as e:
            issues.append(f"outcomes line {number}: {e.errors()[0]['msg']}")
    if issues:
        raise EvaluationError(issues)
    return outcomes


def load_qa_verdicts(path: Path) -> QAVerdicts:
    """The verdict file, or `EvaluationError` naming every verdict that is not valid on its own."""
    try:
        return QAVerdicts.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise EvaluationError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def name_key(name: str) -> str:
    """The form two names are compared in: `norm`, with its words sorted.

    Word order is ignored (a scoring decision of R73, taken before any new output): a graph that stores a
    vehicle as make, model and year names "FORD ESCAPE 2015", a text "2015 Ford Escape"; both are one
    name. The words themselves must all be there, so "Ford Escape" is still another name.
    """
    return " ".join(sorted(norm(name).split()))


def entities_match(expected: Sequence[ExpectedEntity], given: Sequence[str]) -> bool:
    """Set equality by name: the answer names every expected entity (by its name or an alias, compared
    by `name_key`) and nothing else. A partly right set is wrong: an exact query must return exactly the
    set, and a retrieval answer that adds a wrong thing misleads its reader. An answer in another form
    than the gold's (a sentence for a "which" question) is wrong too (R73): the form is part of the
    answer, and code cannot tell a sentence that names only the answer from one that names more."""
    wanted = [{name_key(name) for name in (e.name, *e.aliases)} for e in expected]
    named = {name_key(name) for name in given}
    return all(names & named for names in wanted) and all(any(n in names for names in wanted) for n in named)


def is_correct(expected: Expected, answer: QAAnswer, verdict: AnswerVerdict | None) -> bool:
    """Whether the answer is right: a set or a number by code, a free text by the judge's verdict, an
    expected outcome other than an answer by code (R205: the outcome given equals it). An answerable
    question given another outcome is wrong whatever its fields hold: the system said it does not answer."""
    if expected.outcome != "answer" or answer.given_outcome != "answer":
        return answer.given_outcome == expected.outcome
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
    *,
    allow_unjudged: bool = False,
    system: str = "",
) -> QAReport:
    """Score one answers file against its gold: correctness, recall@k and faithfulness, overall and per
    type. `chunk_texts` must hold every cited chunk the reader was given.

    With `allow_unjudged` and no verdict file, free-text answers are left out of correctness and counted
    as unjudged: what `kg qa` logs before the judge has read them. Off by default, so a final score is
    never computed over a silently smaller set of questions. `system` names the answers' system in the
    outcome rows.

    Raises `ValueError` for k < 1, and `EvaluationError` when the answers do not cover the gold's
    questions exactly once, when free-text answers lack verdicts (or verdicts cover other questions), or
    when a cited chunk's text is missing.
    """
    if k < 1:
        raise ValueError(f"recall@k needs k >= 1, got {k}")
    _check(gold, answers, chunk_texts, verdicts, allow_unjudged)
    answer_of = {a.question_id: a for a in answers}
    verdict_of = {v.question_id: v for v in verdicts.verdicts} if verdicts else {}
    judged = verdicts is not None or not allow_unjudged
    by_meaning = verdicts is not None and verdicts.scope == "all"
    scores = [
        _score(q, answer_of[q.id], verdict_of.get(q.id), chunk_texts, k, judged, by_meaning)
        for q in gold.questions
    ]
    pairs = [(q.expected.outcome, answer_of[q.id].given_outcome) for q in gold.questions]
    with_outcomes = any(a.outcome is not None for a in answers) or any(e != "answer" for e, _ in pairs)
    return QAReport(
        outcome_scores=score_outcomes(pairs) if with_outcomes else None,
        k=k,
        overall=_aggregate(scores),
        by_type={t: _aggregate([s for s in scores if s.type == t]) for t in QuestionType},
        outcomes=[
            _outcome(q, answer_of[q.id], s, system) for q, s in zip(gold.questions, scores, strict=True)
        ],
    )


def _outcome(question: QAQuestion, answer: QAAnswer, score: _QuestionScore, system: str) -> QAOutcome:
    return QAOutcome(
        question_id=question.id,
        type=question.type,
        system=system,
        correct=score.correct if score.judged else None,
        route=question.route,
        system_route=answer.route,
        cited_chunks=[c.chunk_id for c in answer.citations],
    )


def _score(
    question: QAQuestion,
    answer: QAAnswer,
    verdict: AnswerVerdict | None,
    chunk_texts: Mapping[str, str],
    k: int,
    judged: bool,
    by_meaning: bool = False,
) -> _QuestionScore:
    """One question's score; `by_meaning`: its correctness is the judge's verdict whatever the gold's form,
    unless an outcome other than an answer is expected or given, which code scores (R205). A question
    expecting no answer needs no chunk for recall: its chunks only show that the dataset holds the subject."""
    answerable = question.expected.outcome == "answer"
    evidence = {c.chunk_id for c in question.chunks} if answerable else set()
    meaning = by_meaning and verdict is not None and answerable and answer.given_outcome == "answer"
    needed = evidence if question.route == Route.RETRIEVAL else set()
    top_k = set(answer.retrieved[:k])
    return _QuestionScore(
        type=question.type,
        judged=judged or question.expected.kind != "text",
        correct=verdict.correct if meaning and verdict else is_correct(question.expected, answer, verdict),
        hits=len(needed & top_k),
        needed=len(needed),
        hits_all=len(evidence & top_k),
        needed_all=len(evidence),
        faithful=sum(citation_is_faithful(c, answer, chunk_texts) for c in answer.citations),
        cited=len(answer.citations),
        route_right=None if answer.route is None else answer.route == question.route,
    )


def _aggregate(scores: list[_QuestionScore]) -> TypeScores:
    judged = [s for s in scores if s.judged]
    routed = [s for s in scores if s.route_right is not None]
    return TypeScores(
        questions=len(scores),
        correct=Proportion.of(sum(s.correct for s in judged), len(judged)),
        unjudged=len(scores) - len(judged),
        recall_at_k=Proportion.of(sum(s.hits for s in scores), sum(s.needed for s in scores)),
        recall_all_at_k=Proportion.of(sum(s.hits_all for s in scores), sum(s.needed_all for s in scores)),
        faithful=Proportion.of(sum(s.faithful for s in scores), sum(s.cited for s in scores)),
        route=Proportion.of(sum(bool(s.route_right) for s in routed), len(routed)),
    )


def _check(
    gold: QAGold,
    answers: Sequence[QAAnswer],
    chunk_texts: Mapping[str, str],
    verdicts: QAVerdicts | None,
    allow_unjudged: bool,
) -> None:
    """The answers cover every gold question once; the verdicts cover exactly the free-text ones, or every
    question when they judge every answer (scope "all")."""
    issues = _coverage_issues("answer", [a.question_id for a in answers], {q.id for q in gold.questions})
    text_ids = {q.id for q in gold.questions if q.expected.kind == "text"}
    if verdicts is not None:
        # by meaning, every answerable question is judged; an expected outcome is code's (R205)
        answerable = {q.id for q in gold.questions if q.expected.outcome == "answer"}
        judged_ids = answerable if verdicts.scope == "all" else text_ids
        issues += _coverage_issues("verdict", [v.question_id for v in verdicts.verdicts], judged_ids)
    elif text_ids and not allow_unjudged:
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
