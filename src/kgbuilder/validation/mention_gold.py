"""The mention gold of R101: for a seeded sample of sentences, the things each one names or talks about.

Role in the pipeline: the gold of the mention pass (text/mention_pass.py): R102 scores a build's mention
recall against it, and the pass's precision is judged by the same definition
(tests/gold/r101/rules.md: In, Out, how a name is written). Claude writes the file from the sampled
sentences alone, before any pass output exists (`evaluation` skill); the sample is the coverage sampler's
(validation/sentences.py), drawn from a build's own corpus.
Design: a sentence is named by its sample id and wording, never by a graph id, so the gold outlives every
build. A mention is the name as the sentence writes it (verbatim, whole words, no leading article) and its
class: `particular` (a named person, place, organisation, event, work, award or identifier) or `kind` (an
object or a piece of one, a state, an event, an action). `mention_gold_issues` checks what code can see:
every sampled sentence answered once and unchanged, every name standing in its sentence as whole words,
none written twice, none starting with an article.
Not here: drawing the sample (sentences.py), the pass, and the scores (R102).
"""

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..core.errors import InvalidGoldError
from ..core.text import contains_words, norm
from ..text.schema import MentionClass
from .sentences import SentenceSample

# A name copied with its article: the definition writes names without one
_ARTICLE = re.compile(r"^(a|an|the)\s", re.IGNORECASE)


class GoldMention(BaseModel):
    """One thing a sentence names or talks about, as the sentence writes it."""

    name: str
    kind: MentionClass


class SentenceMentions(BaseModel):
    """The mentions of one sampled sentence (none is an answer too: the sentence names no thing)."""

    id: str  # the sample's sentence id
    doc_id: str
    text: str
    mentions: list[GoldMention]
    note: str = ""


class MentionGold(BaseModel):
    dataset: str
    written_by: str
    date: str
    rules: str  # the definition file, repo-relative
    sample: str  # the sample file, repo-relative
    sentences: list[SentenceMentions]


def mention_gold_issues(gold: MentionGold, sample: SentenceSample) -> list[str]:
    """Every way `gold` fails to answer `sample` under the definition's code-visible rules; empty when it
    fits."""
    wanted = {s.id: s for s in sample.sentences}
    counts = Counter(s.id for s in gold.sentences)
    issues = [f"sentence {i} answered {n} times" for i, n in sorted(counts.items()) if n > 1]
    issues += [f"sentence {i} of the sample has no answer" for i in sorted(wanted.keys() - counts.keys())]
    issues += [f"sentence {i} is not in the sample" for i in sorted(counts.keys() - wanted.keys())]
    for s in gold.sentences:
        if s.id in wanted and (s.text != wanted[s.id].text or s.doc_id != wanted[s.id].doc_id):
            issues.append(f"sentence {s.id}: its text or document differs from the sample's")
        names = Counter(norm(m.name) for m in s.mentions)
        issues += [f"sentence {s.id}: {n!r} written {k} times" for n, k in sorted(names.items()) if k > 1]
        for m in s.mentions:
            if m.name != m.name.strip() or not contains_words(s.text, m.name):
                issues.append(f"sentence {s.id}: {m.name!r} is not in its sentence as whole words")
            if _ARTICLE.match(m.name):
                issues.append(f"sentence {s.id}: {m.name!r} starts with an article")
    return issues


def load_mention_gold(path: Path, sample: SentenceSample) -> MentionGold:
    """Read a mention gold file and check it against its sample. Raises `InvalidGoldError` naming every
    issue: a malformed file, or one that does not answer the sample under the rules."""
    try:
        gold = MentionGold.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as e:
        raise InvalidGoldError([f"{path}: {e}"]) from e
    if issues := mention_gold_issues(gold, sample):
        raise InvalidGoldError([f"{path}: {i}" for i in issues])
    return gold
