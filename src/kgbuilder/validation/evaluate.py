"""Score the graph against gold data: triples, entities, entity resolution, questions, and (with a
verdict file) the judge's validated precision and recall.

Role in the pipeline: `kg eval gold.json [--verdicts out/judge_verdicts.json]`, and the accuracy check
inside `kg validate --gold`. This is the regression suite for comparing prompt, model and threshold
variants in MLflow. Two accuracy sources are always logged side by side: exact match (deterministic,
`triple_*`) and the judge (by meaning, `*_validated`; see judge.py and the `evaluation` skill).
Design: pure scoring functions over `StoredFact` lists, so they are unit-tested without Neo4j; only
`run_questions` and `evaluate` touch the database. Gold models and matching live in gold.py.
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.text import norm
from .checks.base import CheckContext, StoredFact
from .gold import GoldPair, GoldQuestion, GoldSet, GoldTriple, in_scope, matches
from .judge import JudgeReport, JudgeSheet, Verdicts, build_sheet, score_verdicts


class Score(BaseModel):
    """Precision, recall and F1 with the counts they came from."""

    precision: float
    recall: float
    f1: float
    predicted: int
    gold: int

    @classmethod
    def of(cls, correct_predicted: int, predicted: int, found_gold: int, gold: int) -> "Score":
        # an empty denominator scores 1.0: nothing was claimed, so nothing was wrong
        precision = correct_predicted / predicted if predicted else 1.0
        recall = found_gold / gold if gold else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return cls(precision=precision, recall=recall, f1=f1, predicted=predicted, gold=gold)


class QuestionResult(BaseModel):
    question: str
    correct: bool
    expected: list[str]
    answered: list[str]


class EvalReport(BaseModel):
    triples: Score | None = None
    entities: Score | None = None
    er_accuracy: float | None = None
    questions: list[QuestionResult] = []
    judge: JudgeReport | None = None
    # what the judge still has to decide; written as its own artifact, so it is left out of the report file
    judge_sheet: JudgeSheet | None = None

    def metrics(self) -> dict[str, float]:
        """Flat metric names for MLflow; stable across runs so that variants can be compared."""
        out: dict[str, float] = {}
        for level, score in (("triple", self.triples), ("entity", self.entities)):
            if score is not None:
                out.update({f"{level}_{k}": getattr(score, k) for k in ("precision", "recall", "f1")})
        if self.triples is not None:
            out["gold_recall"] = self.triples.recall  # name kept from the first version of the pipeline
        if self.er_accuracy is not None:
            out["er_accuracy"] = self.er_accuracy
        if self.questions:
            out["question_accuracy"] = sum(q.correct for q in self.questions) / len(self.questions)
        if self.judge is not None:
            out.update(self.judge.metrics())
        return out


def score_triples(facts: list[StoredFact], gold: list[GoldTriple]) -> Score:
    scoped = in_scope(facts, gold)
    correct = sum(any(matches(g, f) for g in gold) for f in scoped)
    found = sum(any(matches(g, f) for f in scoped) for g in gold)
    return Score.of(correct, len(scoped), found, len(gold))


def score_entities(facts: list[StoredFact], gold: list[GoldTriple]) -> Score:
    """Entity level: were the right things found, regardless of how they were connected?"""
    gold_names = {norm(name) for g in gold for name in (g.subject, g.object)}
    predicted = {
        frozenset(norm(n) for n in names)
        for f in in_scope(facts, gold)
        for names in (f.subject_names, f.object_names)
    }
    correct = sum(bool(names & gold_names) for names in predicted)
    found = sum(any(name in names for names in predicted) for name in gold_names)
    return Score.of(correct, len(predicted), found, len(gold_names))


def score_er(entity_names: list[list[str]], pairs: list[GoldPair]) -> float:
    """Share of gold pairs the graph gets right: `same` pairs share an entity, others do not."""
    normalised = [{norm(n) for n in names} for names in entity_names]

    def merged(pair: GoldPair) -> bool:
        return any(norm(pair.a) in names and norm(pair.b) in names for names in normalised)

    return sum(merged(p) == p.same for p in pairs) / len(pairs)


def run_questions(driver: Driver, questions: list[GoldQuestion]) -> list[QuestionResult]:
    """Run each question's Cypher in a READ transaction (a gold file can never modify the graph)."""
    results = []
    with driver.session() as session:
        for q in questions:
            rows = session.execute_read(lambda tx, cypher=q.cypher: [r[0] for r in tx.run(cypher)])
            answered = sorted({norm(str(v)) for v in rows})
            expected = sorted({norm(v) for v in q.expected})
            results.append(
                QuestionResult(
                    question=q.question, correct=answered == expected, expected=expected, answered=answered
                )
            )
    return results


def evaluate(driver: Driver, gold: GoldSet, verdicts: Verdicts | None = None) -> EvalReport:
    """Score every section present in the gold set; with `verdicts`, add the judge's validated scores.

    Raises `EvaluationError` when the verdicts do not cover exactly what the graph's judge sheet asks for.
    """
    facts = CheckContext(driver=driver).facts
    report = EvalReport()
    if gold.triples:
        report.triples = score_triples(facts, gold.triples)
        report.entities = score_entities(facts, gold.triples)
        report.judge_sheet = build_sheet(facts, gold.triples)
        if verdicts is not None:
            report.judge = score_verdicts(report.judge_sheet, verdicts)
    if gold.er_pairs:
        records, _, _ = driver.execute_query(
            "MATCH (e:Entity) RETURN [e.name] + coalesce(e.aliases, []) AS names"
        )
        report.er_accuracy = score_er([r["names"] for r in records], gold.er_pairs)
    if gold.questions:
        report.questions = run_questions(driver, gold.questions)
    return report
