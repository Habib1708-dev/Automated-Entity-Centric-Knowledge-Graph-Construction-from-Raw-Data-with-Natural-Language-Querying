"""The security screen (R202): refuse a request to change the data before any model or database sees it.

Role in the pipeline: the first thing every question-answering system does. `build_system`
(pipeline/qa_systems.py) wraps each system in `ScreenedSystem`, so `kg ask` and `kg qa` both pass through
it: user -> screen -> planner -> restricted tools. It is one of two independent layers: the tools below the
planner read through read-only connections (R203), so a request the screen lets through still cannot write.
Design: Strategy. Each `ScreenRule` decides one kind of refusal from one sentence; `screen_request` splits
the request into sentences and asks every rule. A new kind of refusal is one more rule class. The screen is
pure code (no LLM, no network): the same text always gets the same verdict, and a refusal names its rule.
Rules (generic: no word of any dataset):
- `WriteCommandRule`: a sentence whose main verb is a write verb ("Delete all ...", "Please update ...",
  "Can you drop ...", "I want to remove ...");
- `WriteStatementRule`: a sentence that is a write statement in SQL or Cypher syntax ("DROP TABLE x",
  "MATCH (n) DETACH DELETE n", "Run DELETE FROM t").
Both look at how a sentence begins, never at a write word inside it, so a question passes: "What does SQL
DELETE mean?", "What does DROP TABLE t do?" and "Which parts were replaced?" ask about writing or about
changes in the data; they do not ask to write.
Known limit: a command phrased as a question ("Is it possible to delete ...?") passes the screen; the
read-only layer below makes it harmless.
Not here: the read-only connections (R203), the checks of LLM-written queries (agent/sql_check.py,
query/cypher_check.py).
"""

import re
from typing import Protocol

from ..llm.base import prompt_version
from .answers import ScreenVerdict, SystemAnswer
from .systems import QASystem

# Sentence boundaries: end punctuation followed by space, a semicolon (statement separator) or a new line.
# Punctuation without a following space ("n.name", "16V074000", "1.5") does not split.
_SENTENCES = re.compile(r"(?<=[.?!])\s+|;|\n")

# Words before the main verb of a polite or indirect command, or before a statement to run; removed one
# after another, so "please, could you just" is removed whole. Matched at the start of a sentence.
_REQUEST_PREFIXES = re.compile(
    r"^(?:please|kindly|now|then|also|just|and|so|ok(?:ay)?|go ahead and|let'?s|let us"
    r"|(?:can|could|would|will) you(?: please)?"
    r"|i(?:'d| would) like(?: you)? to|i (?:want|need)(?: you)? to|you (?:must|should|need to)"
    r"|(?:run|execute)(?: this| the following)?(?: query| statement| sql| cypher)?)\b[\s,:]*",
    re.IGNORECASE,
)

# Verbs that, as the main verb of a command, ask to change stored data. Base form only: "deleted",
# "updates", "replacement" in a question about the data are not commands.
WRITE_VERBS = frozenset(
    [
        "delete", "remove", "drop", "erase", "wipe", "purge", "destroy", "truncate", "update", "change",
        "modify", "edit", "set", "reset", "replace", "overwrite", "rename", "insert", "add", "create",
        "merge", "alter", "move", "grant", "revoke",
    ]
)  # fmt: skip
# A write verb followed by one of these is not a write: "add up the amounts" is arithmetic.
_NOT_WRITES = {"add": frozenset({"up"})}

# A statement opens with a clause keyword of SQL or Cypher; prose that happens to contain "copy ... to" or
# "set" does not.
_STATEMENT_START = re.compile(
    r"^(?:optional\s+match|match|with|unwind|call|merge|create|delete|detach|drop|insert|update|upsert"
    r"|replace|alter|truncate|set|remove|grant|revoke|copy|attach|load|select|foreach)\b",
    re.IGNORECASE,
)
# The write clauses, each with the syntax around its keyword, so a read statement ("MATCH (n) RETURN n",
# "SELECT ...") is not refused, and an English command ("Delete all ...") is left to the command rule:
# Cypher's "DELETE n" never opens a statement, it follows a MATCH.
_WRITE_CLAUSES = re.compile(
    r"\b(?:delete\s+from|insert\s+(?:or\s+\w+\s+)?into|update\s+\S+\s+set|truncate\b|upsert\b"
    r"|drop\s+(?:table|view|index|database|schema|constraint|graph)|alter\s+(?:table|database|index)"
    r"|create\s+(?:or\s+replace\s+)?(?:table|view|index|database|schema|constraint)|create\s*\("
    r"|merge\s*\(|merge\s+into|detach\s+delete|(?<=\s)delete\s+\w+|set\s+\w+(?:\.\w+)?\s*\+?=|set\s+\w+\s*:"
    r"|remove\s+\w+[.:]|grant\b|revoke\b|copy\s+\S+\s+(?:to|from)\b|attach\b|load\s+csv|foreach\b)",
    re.IGNORECASE,
)


class ScreenRule(Protocol):
    """One kind of refusal, decided from one sentence (already stripped of its request prefixes)."""

    name: str

    def match(self, sentence: str) -> str | None:
        """The words that make the sentence a refusal, or None when this rule lets it pass."""
        ...


class WriteCommandRule:
    """A sentence whose main verb is a write verb: an order to change the data, however polite."""

    name = "write_command"

    def match(self, sentence: str) -> str | None:
        words = re.findall(r"[a-z]+", sentence.lower())
        if not words or words[0] not in WRITE_VERBS:
            return None
        if len(words) > 1 and words[1] in _NOT_WRITES.get(words[0], frozenset()):
            return None
        return words[0]


class WriteStatementRule:
    """A sentence that is a write statement: it opens with a query keyword and has a write clause."""

    name = "write_statement"

    def match(self, sentence: str) -> str | None:
        if not _STATEMENT_START.match(sentence):
            return None
        found = _WRITE_CLAUSES.search(sentence)
        return found.group(0) if found else None


# the statement rule first: "DELETE FROM t" is reported as the statement it is, not as the verb "delete"
RULES: tuple[ScreenRule, ...] = (WriteStatementRule(), WriteCommandRule())

# The screen's version, logged with every run that answers questions: any change to its word lists or
# patterns changes it, so runs screened by different rules are never confused.
SCREEN_VERSION = prompt_version(
    "\n".join(
        [_SENTENCES.pattern, _REQUEST_PREFIXES.pattern, " ".join(sorted(WRITE_VERBS)), repr(_NOT_WRITES),
         _STATEMENT_START.pattern, _WRITE_CLAUSES.pattern]
    )
)  # fmt: skip

REFUSAL = "This system answers questions about the data; it does not change the data (refused: {rule})."


def _strip_prefixes(sentence: str) -> str:
    """The sentence from its main verb on: polite and indirect openings removed ("Could you please ...")."""
    previous = None
    while previous != sentence:
        previous = sentence
        sentence = _REQUEST_PREFIXES.sub("", sentence.strip(" \t,:-\"'`"), count=1)
    return sentence


def screen_request(text: str, rules: tuple[ScreenRule, ...] = RULES) -> ScreenVerdict:
    """Refuse `text` when any of its sentences asks to change the data; allow it otherwise.

    The first sentence refused decides, by the first rule that matches it, so the verdict names one rule
    and the words it matched. Never raises: an empty text is allowed (the system answers it as it would).
    """
    for raw in _SENTENCES.split(text):
        sentence = _strip_prefixes(raw)
        for rule in rules:
            matched = rule.match(sentence)
            if matched is not None:
                return ScreenVerdict(allowed=False, rule=rule.name, matched=matched)
    return ScreenVerdict(allowed=True)


class ScreenedSystem:
    """A question-answering system behind the screen (Decorator over `QASystem`): a refused request gets
    the fixed refusal and the outcome "refuse" (R204), with no model and no database call; an allowed one
    goes to the wrapped system."""

    def __init__(self, inner: QASystem):
        self._inner = inner
        self.name = inner.name

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        verdict = screen_request(question)
        if verdict.allowed:
            return self._inner.answer(question_id, question)
        return SystemAnswer(
            question_id=question_id,
            system=self.name,
            text=REFUSAL.format(rule=verdict.rule),
            screen=verdict,
            outcome="refuse",  # R204: the only place a request is refused
        )
