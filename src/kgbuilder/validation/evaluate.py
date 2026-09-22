"""Score the graph against hand-labelled gold data: triples, entities, entity resolution, questions.

Role in the pipeline: `kg eval --gold gold.json`, and the accuracy check inside `kg validate --gold`.
This is the regression suite for comparing prompt, model and threshold variants in MLflow.
Design: pure scoring functions over `StoredFact` lists, so they are unit-tested without Neo4j; only
`run_questions` and `evaluate` touch the database.

Gold file format (every section optional; a bare list is read as `triples`):
    {"triples":   [{"subject": "...", "predicate": "HAS_PROBLEM", "object": "...", "doc_id": "a.md",
                    "evidence": "the sentence the fact comes from"}],
     "er_pairs":  [{"a": "Table", "b": "Tables", "same": true}],
     "questions": [{"question": "...", "cypher": "MATCH ... RETURN x", "expected": ["..."]}]}
Precision is only meaningful over text that was labelled exhaustively. When gold triples carry
`doc_id`, precision is computed over facts from those documents only; label whole documents.
The committed gold set is `tests/gold/text_gold.json`; a test keeps its quotes verbatim in the corpus.
"""

import json
from pathlib import Path

from neo4j import Driver
from pydantic import BaseModel

from ..core.text import norm
from .checks.base import CheckContext, StoredFact


class GoldTriple(BaseModel):
    subject: str
    predicate: str
    object: str
    doc_id: str | None = None
    # the verbatim sentence the label rests on: lets a reader check the label without re-reading the
    # document; not used by the scoring, which compares names only
    evidence: str | None = None


class GoldPair(BaseModel):
    """Two names that are (or are not) the same real-world thing, for scoring entity resolution."""

    a: str
    b: str
    same: bool


class GoldQuestion(BaseModel):
    question: str
    cypher: str  # read-only query; the first column of its rows is the answer
    expected: list[str]


class GoldSet(BaseModel):
    triples: list[GoldTriple] = []
    er_pairs: list[GoldPair] = []
    questions: list[GoldQuestion] = []


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
        return out


def load_gold(path: Path) -> GoldSet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return GoldSet(triples=data) if isinstance(data, list) else GoldSet.model_validate(data)


def _doc_of(fact: StoredFact) -> str | None:
    """Chunk ids are `<doc_id>#<index>`."""
    return fact.chunk_id.rsplit("#", 1)[0] if fact.chunk_id else None


def _matches(gold: GoldTriple, fact: StoredFact) -> bool:
    """Same predicate, and the gold names are among the entity's names or aliases (after `norm`)."""
    return (
        gold.predicate == fact.predicate
        and norm(gold.subject) in {norm(n) for n in fact.subject_names}
        and norm(gold.object) in {norm(n) for n in fact.object_names}
    )


def _in_scope(facts: list[StoredFact], gold: list[GoldTriple]) -> list[StoredFact]:
    """Facts from the labelled documents; all facts when the gold set names no documents."""
    labelled_docs = {g.doc_id for g in gold if g.doc_id}
    return [f for f in facts if _doc_of(f) in labelled_docs] if labelled_docs else facts


def score_triples(facts: list[StoredFact], gold: list[GoldTriple]) -> Score:
    scoped = _in_scope(facts, gold)
    correct = sum(any(_matches(g, f) for g in gold) for f in scoped)
    found = sum(any(_matches(g, f) for f in scoped) for g in gold)
    return Score.of(correct, len(scoped), found, len(gold))


def score_entities(facts: list[StoredFact], gold: list[GoldTriple]) -> Score:
    """Entity level: were the right things found, regardless of how they were connected?"""
    gold_names = {norm(name) for g in gold for name in (g.subject, g.object)}
    predicted = {
        frozenset(norm(n) for n in names)
        for f in _in_scope(facts, gold)
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


def evaluate(driver: Driver, gold: GoldSet) -> EvalReport:
    """Score every section present in the gold set."""
    facts = CheckContext(driver=driver).facts
    report = EvalReport()
    if gold.triples:
        report.triples = score_triples(facts, gold.triples)
        report.entities = score_entities(facts, gold.triples)
    if gold.er_pairs:
        records, _, _ = driver.execute_query(
            "MATCH (e:Entity) RETURN [e.name] + coalesce(e.aliases, []) AS names"
        )
        report.er_accuracy = score_er([r["names"] for r in records], gold.er_pairs)
    if gold.questions:
        report.questions = run_questions(driver, gold.questions)
    return report
