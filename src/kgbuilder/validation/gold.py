"""The gold file: its models, its loader, and what "an extracted fact matches a gold triple" means.

Role in the pipeline: read by `kg eval` and `kg validate --gold`; shared by the exact-match scoring
(evaluate.py) and the judge sheet (judge.py), which must agree on matching so that the judge only sees
what exact matching could not settle. Not here: any scoring or graph access.

Gold file format (every section optional; a bare list is read as `triples`):
    {"triples":   [{"subject": "...", "predicate": "HAS_PROBLEM", "object": "...", "doc_id": "a.md",
                    "evidence": "the sentence the fact comes from"}],
     "er_pairs":  [{"a": "Table", "b": "Tables", "same": true}],
     "questions": [{"question": "...", "cypher": "MATCH ... RETURN x", "expected": ["..."]}],
     "identity_pairs": [{"a": {"doc_id": "a.md", "names": ["J. Pike"]}, "b": {...}, "same": true,
                         "evidence": [{"doc_id": "a.md", "quote": "..."}], "note": "..."}]}
`identity_pairs` (R75) are mention pairs: a name in one document and a name in another (or the same)
document, the same individual, record or kind or not, with the sentences a reader needs to decide it.
Precision is only meaningful over text that was labelled exhaustively. When gold triples carry
`doc_id`, precision is computed over facts from those documents only; label whole documents.
The committed gold set is `tests/gold/text_gold.json`; a test keeps its quotes verbatim in the corpus.
"""

import json
from pathlib import Path

from pydantic import BaseModel

from ..core.text import norm
from .checks.base import StoredFact


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


class MentionRef(BaseModel):
    """A mention as the gold names it: a document and the names the text uses there for one thing (the
    extractor may keep or drop a title, so a side may list each spelling it accepts)."""

    doc_id: str
    names: list[str]


class Quote(BaseModel):
    doc_id: str
    quote: str  # verbatim from the document; a test checks it


class IdentityPair(BaseModel):
    """Two mentions that are, or are not, one canonical entity (R75, layered-model Step 5)."""

    a: MentionRef
    b: MentionRef
    same: bool
    evidence: list[Quote]  # the sentences that decide it, at least one per side
    note: str = ""  # why, and the trap: a same name, a variant, a part and its whole


class GoldQuestion(BaseModel):
    question: str
    cypher: str  # read-only query; the first column of its rows is the answer
    expected: list[str]


class GoldSet(BaseModel):
    triples: list[GoldTriple] = []
    er_pairs: list[GoldPair] = []
    questions: list[GoldQuestion] = []
    identity_pairs: list[IdentityPair] = []


def load_gold(path: Path) -> GoldSet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return GoldSet(triples=data) if isinstance(data, list) else GoldSet.model_validate(data)


def doc_of(fact: StoredFact) -> str | None:
    """Chunk ids are `<doc_id>#<index>`."""
    return fact.chunk_id.rsplit("#", 1)[0] if fact.chunk_id else None


def matches(gold: GoldTriple, fact: StoredFact) -> bool:
    """Same predicate, the gold names among the entity's names or aliases (after `norm`), and the fact
    from the gold triple's document when the triple names one.

    The document check matters because an entity is one node per type and name across all documents,
    and a merge adds every member's names as aliases: without it, a fact of one review matched another
    review's triple and marked it found (R42, found in R41).
    """
    return (
        (gold.doc_id is None or gold.doc_id == doc_of(fact))
        and gold.predicate == fact.predicate
        and norm(gold.subject) in {norm(n) for n in fact.subject_names}
        and norm(gold.object) in {norm(n) for n in fact.object_names}
    )


def in_scope(facts: list[StoredFact], gold: list[GoldTriple]) -> list[StoredFact]:
    """Facts from the labelled documents; all facts when the gold set names no documents."""
    labelled_docs = {g.doc_id for g in gold if g.doc_id}
    return [f for f in facts if doc_of(f) in labelled_docs] if labelled_docs else facts
