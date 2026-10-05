"""The code-side check of a Cypher query a model wrote, before it may run: the text half.

Role in the pipeline: the exact route (exact.py) proposes a query; this module checks its text, then the
database checks its plan (`GraphStore.explain`: read-only, known labels, types and properties), and only
then does it run, in a read transaction with a timeout (`GraphStore.run_read`). Three independent guards,
so no single one has to be perfect.
Design: pure string rules (fixed decision 6: the LLM proposes, code decides, also when answering):
- nothing that writes, calls a procedure, reads a file or switches database, checked on the query with its
  quoted identifiers and comments taken out, so `n.`created`` or a comment is never mistaken for a clause;
- no quoted string: every value compared against travels as a parameter, and every `$name` used is given;
- one statement;
- a LIMIT: added when missing, refused when above the cap. Numbers may stay in the text (a year, `> 0`):
  a number cannot break out of its place in a query, a string can;
- for a system that may read only part of the graph (records plus vector RAG, R73): no label or
  relationship type outside that part. Names are read where Cypher writes them, after `:` or `|`
  (`(n:Label)`, `[:TYPE|OTHER]`, `WHERE n:Label`), backticked or not. A pattern with no label and no type
  could still step outside; the prompt never shows that part, and every query is kept in the answer's trace.
Not here: the database's own check and the run (graph_store.py).
"""

import re

from pydantic import BaseModel

# Clauses and keywords a read-only answer never needs. `CALL` covers procedures and subqueries alike, `LOAD`
# reads files, `USE` switches database; the rest write or administer.
_FORBIDDEN = re.compile(
    r"\b(CALL|LOAD|USE|CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|ALTER|RENAME|GRANT|DENY|REVOKE|"
    r"START|STOP|TERMINATE)\b",
    re.IGNORECASE,
)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")  # 'text' or "text", escapes allowed
_QUOTED_IDENTIFIER = re.compile(r"`(?:[^`]|``)*`")  # `a name`, with `` as an escaped backtick
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_PARAMETER = re.compile(r"\$(\w+)")
# a LIMIT that ends the query: "LIMIT 25", "LIMIT $n"; a trailing semicolon is allowed
_FINAL_LIMIT = re.compile(r"\bLIMIT\s+(\$?\w+)\s*;?\s*$", re.IGNORECASE)
# a label or relationship type as Cypher writes it: after ":" or "|", plain or in backticks
_NAME_AFTER_COLON = re.compile(r"[:|]\s*(?:`((?:[^`]|``)*)`|([A-Za-z_]\w*))")


class CheckedText(BaseModel):
    """A query after the text check: the text to run (LIMIT added if needed) and why it was refused."""

    cypher: str
    issues: list[str]


def check_text(cypher: str, parameters: dict[str, object], limit: int) -> CheckedText:
    """Check `cypher` against the text rules; the issues are worded for the model's retry."""
    bare = _COMMENT.sub(" ", _QUOTED_IDENTIFIER.sub("x", cypher))
    issues = [f"it uses {word.upper()}, which a read-only answer never needs" for word in _forbidden(bare)]
    if _STRING.search(bare):
        issues.append("it quotes a text value; pass every value as a parameter ($name)")
    if missing := sorted(set(_PARAMETER.findall(bare)) - parameters.keys()):
        issues.append(f"parameters {missing} are used but not given")
    statement = bare.strip().rstrip(";")
    if ";" in statement:
        issues.append("it holds more than one statement")
    return CheckedText(cypher=_with_limit(cypher.strip().rstrip(";").rstrip(), limit, issues), issues=issues)


def _forbidden(bare: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).upper() for m in _FORBIDDEN.finditer(bare)))


def _with_limit(cypher: str, limit: int, issues: list[str]) -> str:
    """`cypher` ending in a LIMIT of at most `limit`; appends one, or records an issue for a larger one."""
    match = _FINAL_LIMIT.search(cypher)
    if match is None:
        return f"{cypher}\nLIMIT {limit}"
    value = match.group(1)
    if not value.isdigit() or int(value) > limit:
        issues.append(f"its LIMIT must be a number of at most {limit}")
    return cypher


def excluded_name_issues(cypher: str, excluded: frozenset[str]) -> list[str]:
    """One issue per label or relationship type of `excluded` that `cypher` names; worded for the retry.

    Case-sensitive, as Neo4j's labels and types are. Comments are ignored; a value in a map (`{year: 2015}`)
    is read as a name too, which can only refuse more, never let an excluded name through.
    """
    if not excluded:
        return []
    bare = _COMMENT.sub(" ", cypher)
    named = {quoted.replace("``", "`") or plain for quoted, plain in _NAME_AFTER_COLON.findall(bare)}
    return [f"it uses {name}, which this system may not read" for name in sorted(named & excluded)]
