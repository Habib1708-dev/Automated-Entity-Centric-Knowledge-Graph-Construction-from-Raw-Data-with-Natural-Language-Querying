"""The security screen (R202: query/screen.py), pure code, without Neo4j or a model: a request to change the
data is refused, whether it is a command (however polite) or a write statement in SQL or Cypher; a question
about writing, or about changes recorded in the data, is allowed; no question of any committed QA gold file
is refused; the screened system refuses without calling the system it wraps, records the verdict on the
answer, and passes an allowed question through unchanged; the stages count the refusals per rule."""

import json
from pathlib import Path

import pytest

from kgbuilder.pipeline.qa_stages import _screen_metrics
from kgbuilder.query.answers import SystemAnswer
from kgbuilder.query.screen import ScreenedSystem, screen_request

GOLD = Path(__file__).parent / "gold"

# the user's examples first (2026-10-15), then polite, indirect and multi-sentence forms
REFUSED = [
    ("Delete all complaints.", "write_command"),
    ("Update the supplier's address.", "write_command"),
    ("Drop the vehicle table.", "write_command"),
    ("Please remove every record older than 2010.", "write_command"),
    ("Can you delete the duplicates?", "write_command"),
    ("Could you please, just rename the column to 'maker'", "write_command"),
    ("I want you to insert a new row for the Lark Kettle.", "write_command"),
    ("I'd like to change the price of K1 to 20.", "write_command"),
    ("Go ahead and merge the two makers.", "write_command"),
    ("Which kettles are there? Then wipe the table.", "write_command"),
    ("DROP TABLE kettles;", "write_statement"),
    ("delete from kettles where id = 'K1'", "write_statement"),
    ("UPDATE kettles SET maker = 'Elm'", "write_statement"),
    ("INSERT INTO kettles VALUES ('K4', 'Owl Kettle')", "write_statement"),
    ("MATCH (n) DETACH DELETE n", "write_statement"),
    ("MATCH (k:Kettle) WHERE k.made < 2020 DELETE k", "write_statement"),
    ("MATCH (k:Kettle {id: 'K1'}) SET k.maker = 'Elm'", "write_statement"),
    ("MERGE (k:Kettle {id: 'K9'})", "write_statement"),
    ("Run this query: CREATE TABLE copy AS SELECT * FROM kettles", "write_statement"),
    ("Which makers are listed?; DROP TABLE makers", "write_statement"),
    ("Execute: COPY kettles TO 'out.csv'", "write_statement"),
]

ALLOWED = [
    "What does SQL DELETE mean?",
    "What does DROP TABLE kettles do?",
    "Explain MERGE in Cypher.",
    "How many records were updated in 2010?",
    "Which kettles had their lids replaced?",
    "Which makers changed their address after 2019?",
    "Add up the prices of the kettles.",
    "Who removed the seal of kettle K2?",
    "The owner says the lid was replaced twice. Which kettle is it?",
    "MATCH (k:Kettle) RETURN k.name",
    "SELECT name FROM kettles WHERE maker = 'Ash'",
    "A copy of the manual was sent to the owner. Which kettle was it for?",
    "",
]


@pytest.mark.parametrize(("text", "rule"), REFUSED)
def test_a_request_to_change_the_data_is_refused_by_its_rule(text: str, rule: str):
    verdict = screen_request(text)
    assert not verdict.allowed
    assert verdict.rule == rule
    assert verdict.matched


@pytest.mark.parametrize("text", ALLOWED)
def test_a_question_about_writing_or_about_changes_is_allowed(text: str):
    assert screen_request(text).allowed


def _gold_questions() -> list[str]:
    """Every question of every committed QA gold file: a JSON object with a list of `questions`."""
    questions: list[str] = []
    for path in sorted(GOLD.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("questions"), list):
            questions += [q["question"] for q in data["questions"] if isinstance(q, dict) and "question" in q]
    return questions


def test_no_committed_gold_question_is_refused():
    questions = _gold_questions()
    # the gold files hold several hundred distinct questions; an empty list would make this test vacuous
    assert len(set(questions)) > 500
    refused = {q: screen_request(q) for q in set(questions) if not screen_request(q).allowed}
    assert refused == {}


class _Inner:
    """A system that records the questions it was asked and answers each with a fixed text."""

    name = "fake"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        self.asked.append(question)
        return SystemAnswer(question_id=question_id, system=self.name, text="answered")


def test_the_screened_system_refuses_without_asking_the_system_it_wraps():
    inner = _Inner()
    answer = ScreenedSystem(inner).answer("q1", "Delete all kettles.")
    assert inner.asked == []
    assert answer.system == "fake" and answer.question_id == "q1"
    assert answer.screen is not None and answer.screen.rule == "write_command"
    assert "does not change the data" in (answer.text or "")
    assert answer.retrieved == [] and answer.shown == []


def test_the_screened_system_passes_an_allowed_question_through():
    inner = _Inner()
    answer = ScreenedSystem(inner).answer("q2", "What does SQL DELETE mean?")
    assert inner.asked == ["What does SQL DELETE mean?"]
    assert answer.text == "answered" and answer.screen is None


def test_the_stages_count_refusals_per_rule_and_log_zero_when_none():
    refused = ScreenedSystem(_Inner()).answer("a", "DROP TABLE kettles")
    allowed = ScreenedSystem(_Inner()).answer("b", "Which kettles are there?")
    assert _screen_metrics([refused, allowed, refused]) == {
        "screen_refused": 2,
        "screen_refused_write_statement": 2,
    }
    assert _screen_metrics([allowed]) == {"screen_refused": 0}
