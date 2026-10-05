"""The question-answer gold file (R70): questions with their type, expected answer, evidence and route,
and the check that ties every piece of evidence to the corpus word for word.

Role in the pipeline: the benchmark of the layered-model arm (docs/tasks/layered-knowledge-model.md,
Step 1). Claude writes the gold from whole files before any answer exists (`evaluation` skill); `kg qa`
(Step 2) answers its questions and qa.py scores the answers against it.
Design: the validators make each question checkable on its own (one answer form, evidence present, a
retrieval question has chunk evidence); `check_qa_gold` then checks the file against the corpus's chunks
and staged rows, which the caller supplies, so this module stays pure.
Not here: scoring (qa.py); building the chunks (the pipeline's own loader, record documents and chunker);
computing the answers of record questions from their query (qa_records.py, R73).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..core.errors import InvalidGoldError


class QuestionType(StrEnum):
    """The six question types of the benchmark (task file, section 1); every score is given per type."""

    MULTI_HOP = "multi_hop"  # joins two sources: a text and a record, or two documents
    # counts, ranks or collects every match across the corpus ("which component has the most complaints?")
    AGGREGATION = "aggregation"
    STRUCTURED_FILTER = "structured_filter"  # restricted by a record field ("2019 models only")
    DISAMBIGUATION = "disambiguation"  # right only if two things with similar names are kept apart
    NEGATION_SENSITIVE = "negation_sensitive"  # a negated or merely possible mention must not count
    LOOKUP = "lookup"  # what the sources say about one named thing


class Route(StrEnum):
    """The route of the query stage that should answer a question (Step 2 scores its router on it)."""

    EXACT = "exact"  # a checked Cypher query over the graph: counts, filters, record fields
    RETRIEVAL = "retrieval"  # the chunks the graph leads to, read by the answering model


class HardCase(StrEnum):
    """The identity and retrieval hard cases the generality corpus asks about, each at least once."""

    SAME_NAME = "same_name"  # two individuals with the same name
    TWO_NAMES = "two_names"  # one individual under two names
    SERIAL_NUMBERS = "serial_numbers"  # two instances of one model with different serial numbers
    TWO_THINGS = "two_things"  # one document about two things
    REPORTED_CLAIM = "reported_claim"  # a claim made by someone other than the author
    NEGATION = "negation"
    NUMBER_FILTER = "number_filter"  # a number to filter on
    NUANCE_CONTROL = "nuance_control"  # negative control: the answer is nuance the graph does not model


AnswerKind = Literal["entities", "number", "text"]


class ExpectedEntity(BaseModel):
    """One member of an expected answer set, with the other names an answer may give it."""

    name: str = Field(min_length=1)
    # names the sources or records also use ("CIVIC" for "2016 Honda Civic"); compared with `norm`
    aliases: list[str] = []


class Expected(BaseModel):
    """The expected answer in exactly one form: a set of names, a number, or a short text.

    Sets and numbers are compared by code, a text by the judge (qa.py). An empty set is a valid answer
    ("no product is reported with ...").
    """

    entities: list[ExpectedEntity] | None = None
    number: float | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> "Expected":
        if sum(form is not None for form in (self.entities, self.number, self.text)) != 1:
            raise ValueError("give exactly one of entities, number and text")
        if self.text is not None and not self.text.strip():
            raise ValueError("an expected text answer cannot be empty")
        return self

    @property
    def kind(self) -> AnswerKind:
        if self.entities is not None:
            return "entities"
        return "number" if self.number is not None else "text"


class ChunkEvidence(BaseModel):
    """A chunk that answers the question and the words in it that do, copied verbatim."""

    chunk_id: str  # `<doc_id>#<index>` under the gold's chunk settings (CorpusSpec)
    quote: str = Field(min_length=1)


class RecordEvidence(BaseModel):
    """A structured row that answers the question: its staged file and the cells that pick it out.

    `file` is the staged CSV relative to the staging dir: a JSON source `recalls.json` stages as
    `recalls.csv`, with nested keys flattened (`product.productYear`). Each cell must equal the row's
    value, so a relationship table row (a part and its supplier) is cited like any other.
    """

    file: str
    row: dict[str, str] = Field(min_length=1)


class QAQuestion(BaseModel):
    """One gold question with its expected answer, its evidence and the route that should answer it."""

    id: str = Field(min_length=1)
    type: QuestionType
    question: str = Field(min_length=1)
    expected: Expected
    route: Route
    chunks: list[ChunkEvidence] = []
    records: list[RecordEvidence] = []
    # a question over records (R73) carries a DuckDB query over the corpus's source files, from which code
    # computes its expected answer (qa_records.py): fixed decision 11, gold that code can compute is
    # computed, so a count over 25 complaints is not counted by hand
    sql: str | None = None
    hard_case: HardCase | None = None  # generality corpus only
    # an earlier gold question this one carries over with its answer unchanged, as `<file>#<index>`
    origin: str | None = None
    note: str = ""  # why the answer is what it is, wherever the gold made a choice

    @model_validator(mode="after")
    def _evidence(self) -> "QAQuestion":
        if not self.chunks and not self.records and self.sql is None:
            raise ValueError(f"question {self.id}: give its evidence (chunks, records or a query)")
        # code compares a query's rows with a set or a number; a text answer is the judge's
        if self.sql is not None and self.expected.kind == "text":
            raise ValueError(f"question {self.id}: a question with a query needs a set or a number")
        # recall@k is measured on retrieval questions against their chunks: without any it is undefined
        if self.route == Route.RETRIEVAL and not self.chunks:
            raise ValueError(f"question {self.id}: a retrieval question needs chunk evidence")
        return self


class CorpusSpec(BaseModel):
    """Where the cited chunks come from: the pipeline's own documents and chunker run on `data_dir`.

    Chunk ids (`<doc_id>#<index>`) depend on the chunk settings, so the gold pins them. `plan` is the
    frozen construction plan whose prose columns become record documents (R67); None when there are none.
    """

    data_dir: str  # relative to the repository root
    plan: str | None = None  # relative to the repository root
    chunk_max_chars: int
    chunk_min_chars: int
    chunk_overlap_chars: int


class QAGold(BaseModel):
    """A QA gold file: its dataset, its corpus, who wrote it and when, and its questions."""

    dataset: str
    written_by: str  # the model; the thesis states that gold and judge come from one model family
    date: str
    corpus: CorpusSpec
    questions: list[QAQuestion] = Field(min_length=1)


def load_qa_gold(path: Path) -> QAGold:
    """The gold file, or `InvalidGoldError` naming every field that is not valid on its own."""
    try:
        return QAGold.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise InvalidGoldError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def check_qa_gold(
    gold: QAGold, chunk_texts: Mapping[str, str], rows: Mapping[str, Sequence[Mapping[str, str]]]
) -> None:
    """Raise `InvalidGoldError` unless every question id is unique, every cited chunk exists and holds
    its quote word for word, and every cited record picks out a row of its staged file.

    `chunk_texts` maps chunk id to text, `rows` a staged file name to its rows. Word for word is a plain
    substring, stricter than the `norm` comparison used on model output (extraction's `verify`, the
    citation check in qa.py): the gold is copied from the file, so a quote that differs even in case or
    markup is a copying mistake to fix, not a paraphrase to accept.
    """
    issues = [
        f"question id {qid!r} is used twice"
        for qid, n in Counter(q.id for q in gold.questions).items()
        if n > 1
    ]
    for question in gold.questions:
        issues += _chunk_issues(question, chunk_texts)
        issues += _record_issues(question, rows)
    if issues:
        raise InvalidGoldError(issues)


def _chunk_issues(question: QAQuestion, chunk_texts: Mapping[str, str]) -> list[str]:
    issues = []
    for evidence in question.chunks:
        text = chunk_texts.get(evidence.chunk_id)
        if text is None:
            issues.append(f"question {question.id}: no chunk {evidence.chunk_id!r} in the corpus")
        elif evidence.quote not in text:
            issues.append(
                f"question {question.id}: {evidence.quote!r} is not verbatim in {evidence.chunk_id}"
            )
    return issues


def _record_issues(question: QAQuestion, rows: Mapping[str, Sequence[Mapping[str, str]]]) -> list[str]:
    issues = []
    for evidence in question.records:
        table = rows.get(evidence.file)
        if table is None:
            issues.append(f"question {question.id}: no staged file {evidence.file!r}")
        # strip only the cell: staged CSVs can carry padding the source had, the gold's value is exact
        elif not any(
            all((row.get(column) or "").strip() == value for column, value in evidence.row.items())
            for row in table
        ):
            issues.append(f"question {question.id}: no row of {evidence.file} has {evidence.row}")
    return issues
