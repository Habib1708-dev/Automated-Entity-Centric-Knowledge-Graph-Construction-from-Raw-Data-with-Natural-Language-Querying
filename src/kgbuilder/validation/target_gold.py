"""The target gold of the anchor-graph evaluation (R89): for every question of a QA gold file, the names it
starts from, the other names the sources use for them, and the records and text mentions each should reach.

Role in the pipeline: the gold of criteria C2 (findability), C5 (evidence reach) and C8 (connectivity) of
the anchor-graph direction (docs/direction/2026-10-06_anchor-graph, section 7.4). The QA gold's questions
are read as information needs, not as answers: a target is where a reader's lookup should land, and the
question's own `chunks` and `records` (qa_gold.py) stay the evidence it should lead to, so they are not
copied here. Claude writes the file from the questions and the source data only, before any arm is measured
(`evaluation` skill).
Design: a target names what it should reach without any graph id, so the gold outlives every build. A
record is a staged row picked by its cells (several rows when the name stands for several records, "drawer
rails" for every Drawer Rails part); an individual or a concept is the thing its mentions refer to, given
as a document and the names written there (`MentionRef`, as in the R75 identity gold). `check_target_gold`
then ties the file to its QA gold and its corpus; the caller supplies the chunks and staged rows, so this
module stays pure.
Not here: the lookup and the walks that the gold measures (the navigation contract, a later step), and
the evidence of each question (qa_gold.py).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..core.errors import InvalidGoldError
from ..core.text import contains_words, norm
from .gold import MentionRef
from .qa_gold import QAGold, RecordEvidence, rows_matching


class Target(BaseModel):
    """One name a question starts from and what it should reach.

    `records` and `mentions` together are the target's nodes: a lookup of `name` or an alias finds the
    target when it returns one of them, and a walk starts from all of them. Both may be given when one name
    stands for a record and a thing named only in text ("Pike": a staff record and the Judith Pike of a
    letter).
    """

    name: str = Field(min_length=1)  # as the question writes it
    # other names the sources write for the same thing: a short form, a title, a key the text uses, a
    # record's own name ("ESCAPE" for "2015 Ford Escape"); inflections ("wobbles") are the lookup's concern
    aliases: list[str] = []
    records: list[RecordEvidence] = []  # every row a ref picks is meant
    mentions: list[MentionRef] = []  # the thing these mentions refer to, for individuals and concepts
    note: str = ""  # why the name reaches what it does, wherever the gold made a choice

    @model_validator(mode="after")
    def _reaches_something(self) -> "Target":
        if not self.records and not self.mentions:
            raise ValueError(f"target {self.name!r}: give the records or mentions it should reach")
        if any(not ref.names for ref in self.mentions):
            raise ValueError(f"target {self.name!r}: a mention needs at least one name")
        spellings = [norm(n) for n in (self.name, *self.aliases)]
        if len(spellings) != len(set(spellings)):
            raise ValueError(f"target {self.name!r}: an alias repeats the name or another alias")
        return self


class QuestionTargets(BaseModel):
    """The targets of one QA question; none, with the reason, when the question names no start
    ("Which products cost more than $500?" ranges over every product)."""

    id: str = Field(min_length=1)  # the QA question's id
    targets: list[Target] = []
    note: str = ""

    @model_validator(mode="after")
    def _empty_needs_reason(self) -> "QuestionTargets":
        if not self.targets and not self.note.strip():
            raise ValueError(f"question {self.id}: say why it has no target")
        return self


class TargetGold(BaseModel):
    """A target gold file: its dataset, the QA gold whose questions it covers, who wrote it and when."""

    dataset: str
    qa_gold: str  # the QA gold file, relative to the repository root
    written_by: str  # the model; the thesis states that gold and judge come from one model family
    date: str
    questions: list[QuestionTargets] = Field(min_length=1)


def load_target_gold(path: Path) -> TargetGold:
    """The gold file, or `InvalidGoldError` naming every field that is not valid on its own."""
    try:
        return TargetGold.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as e:
        raise InvalidGoldError(
            [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        ) from e


def check_target_gold(
    gold: TargetGold,
    qa: QAGold,
    chunk_texts: Mapping[str, str],
    rows: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    """Raise `InvalidGoldError` unless the file covers exactly the questions of `qa`, once each, every
    target's name is in its question, every alias is written somewhere in the corpus, every record ref
    picks at least one staged row, and every mention's document names it.

    `chunk_texts` maps chunk id (`<doc_id>#<index>`) to text, `rows` a staged file name to its rows: the
    corpus as the QA gold's tests rebuild it. Names are compared as whole words with `norm` (case, accents,
    markdown), not as plain substrings like the QA gold's quotes: a name is looked up, not quoted, so
    "BACK UP CAMERA" is still written differently from "back-up camera" but "Escape" is "ESCAPE".
    """
    questions = {q.id: q.question for q in qa.questions}
    ids = [q.id for q in gold.questions]
    issues = [f"question id {qid!r} is listed twice" for qid, n in Counter(ids).items() if n > 1]
    issues += [f"question {qid} is not in {gold.qa_gold}" for qid in ids if qid not in questions]
    issues += [f"question {qid} of {gold.qa_gold} has no entry" for qid in questions if qid not in ids]
    documents = _documents(chunk_texts)
    cells = [cell for table in rows.values() for row in table for cell in row.values() if cell]
    for entry in gold.questions:
        for target in entry.targets:
            where = f"question {entry.id}, target {target.name!r}"
            if entry.id in questions and not contains_words(questions[entry.id], target.name):
                issues.append(f"{where}: the name is not in the question")
            issues += [
                f"{where}: alias {alias!r} is written nowhere in the corpus"
                for alias in target.aliases
                if not _written(alias, documents, cells)
            ]
            issues += _record_issues(where, target.records, rows)
            issues += _mention_issues(where, target.mentions, documents)
    if issues:
        raise InvalidGoldError(issues)


def _documents(chunk_texts: Mapping[str, str]) -> dict[str, list[str]]:
    """The chunks of each document, by the document id before the chunk id's `#`."""
    documents: dict[str, list[str]] = {}
    for chunk_id, text in chunk_texts.items():
        documents.setdefault(chunk_id.rsplit("#", 1)[0], []).append(text)
    return documents


def _written(name: str, documents: Mapping[str, list[str]], cells: list[str]) -> bool:
    return any(contains_words(text, name) for texts in documents.values() for text in texts) or any(
        contains_words(cell, name) for cell in cells
    )


def _record_issues(
    where: str, records: list[RecordEvidence], rows: Mapping[str, Sequence[Mapping[str, str]]]
) -> list[str]:
    issues = []
    for ref in records:
        table = rows.get(ref.file)
        if table is None:
            issues.append(f"{where}: no staged file {ref.file!r}")
        elif not rows_matching(ref, table):
            issues.append(f"{where}: no row of {ref.file} has {ref.row}")
    return issues


def _mention_issues(where: str, mentions: list[MentionRef], documents: Mapping[str, list[str]]) -> list[str]:
    issues = []
    for ref in mentions:
        texts = documents.get(ref.doc_id)
        if texts is None:
            issues.append(f"{where}: no document {ref.doc_id!r} in the corpus")
        # chunks do not overlap and a name does not straddle a boundary, so checking each chunk is enough
        elif not any(contains_words(text, name) for text in texts for name in ref.names):
            issues.append(f"{where}: {ref.doc_id} names none of {ref.names}")
    return issues
