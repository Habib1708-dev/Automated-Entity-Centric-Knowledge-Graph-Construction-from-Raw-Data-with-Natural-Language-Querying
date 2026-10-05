"""The exact route (R71 part b) and the plan system (R74), without Neo4j: the text check of a model's Cypher,
the exact route's retry and give-up over a fake store, rows read as an answer; for records plus vector RAG
(R73), the record layer of the schema and the refusal of names outside it; the plan system's run, retry
and fallbacks (text2cypher, then reading) with its own name on every answer; and the planner, read_check
and Cypher prompts' domain-neutral wording. The database half of the check (EXPLAIN, the read
transaction) is tested in test_query_graph.py."""

import json

import pytest
from neo4j.time import Date

from kgbuilder.core.errors import QueryPlanError
from kgbuilder.query import exact, planner, read_check
from kgbuilder.query.answers import StepTrace
from kgbuilder.query.cypher_check import check_text, excluded_name_issues
from kgbuilder.query.exact import CypherParameter, CypherProposal, ExactRoute, rows_to_answer
from kgbuilder.query.graph_schema import (
    ClaimInfo,
    GraphSchema,
    LabelInfo,
    PropertyInfo,
    RelationshipInfo,
    cypher_literal,
)
from kgbuilder.query.graph_store import ReadResult, StoredChunk
from kgbuilder.query.plan import PlanSchema, PlanStep, QueryPlan
from kgbuilder.query.plan_run import PlanRun
from kgbuilder.query.planner import Planner
from kgbuilder.query.reader import Reader, ReaderAnswer, ReaderCitation
from kgbuilder.query.systems import RECORDS_VECTOR, PlanSystem

from .evaluation_corpora import quoted_four_grams
from .fakes import ScriptedLLM

READ = "MATCH (p:Press) WHERE toLower(p.name) CONTAINS toLower($name) RETURN p.name"


# --- the text check --------------------------------------------------------------------------------


def test_a_plain_read_passes_and_gets_a_limit():
    checked = check_text(READ, {"name": "quill"}, limit=50)
    assert checked.issues == [] and checked.cypher == READ + "\nLIMIT 50"
    assert check_text(READ + " LIMIT 10;", {"name": "q"}, limit=50).cypher == READ + " LIMIT 10"


@pytest.mark.parametrize(
    ("cypher", "word"),
    [
        ("MATCH (p:Press) SET p.name = $name RETURN p", "SET"),
        ("CREATE (p:Press {name: $name}) RETURN p", "CREATE"),
        ("MATCH (p:Press) DETACH DELETE p", "DETACH"),
        ("MERGE (p:Press {name: $name}) RETURN p", "MERGE"),
        ("MATCH (p) REMOVE p.name RETURN p", "REMOVE"),
        ("CALL db.labels() YIELD label RETURN label", "CALL"),
        ("LOAD CSV FROM $name AS row RETURN row", "LOAD"),
        ("MATCH (p) FOREACH (x IN [1] | SET p.n = x) RETURN p", "FOREACH"),
    ],
)
def test_anything_that_writes_calls_or_loads_is_refused(cypher, word):
    assert any(word in issue for issue in check_text(cypher, {"name": "x"}, limit=50).issues)


def test_a_keyword_inside_a_quoted_name_or_a_comment_is_not_a_clause():
    cypher = "MATCH (p:Press) RETURN p.`set` AS created // no SET here\n"
    assert check_text(cypher, {}, limit=50).issues == []


def test_values_must_be_parameters_and_every_parameter_must_be_given():
    quoted = check_text("MATCH (p:Press) WHERE p.name = 'Quill Press' RETURN p.name", {}, limit=50)
    assert any("parameter" in issue for issue in quoted.issues)
    missing = check_text(READ, {}, limit=50)
    assert missing.issues == ["parameters ['name'] are used but not given"]
    # numbers may stay in the text: they cannot break out of their place in a query
    assert check_text("MATCH (r:Record) WHERE r.year > 2015 RETURN count(r)", {}, limit=50).issues == []


def test_one_statement_and_a_limit_within_the_cap():
    assert "more than one statement" in check_text("MATCH (a) RETURN a; MATCH (b) RETURN b", {}, 50).issues[0]
    assert "at most 50" in check_text("MATCH (a) RETURN a LIMIT 500", {}, 50).issues[0]
    assert "at most 50" in check_text("MATCH (a) RETURN a LIMIT $n", {"n": 5.0}, 50).issues[0]


TEXT_LAYER = frozenset({"Chunk", "Observation", "ABOUT"})


@pytest.mark.parametrize(
    ("cypher", "name"),
    [
        ("MATCH (c:Chunk)-[:PART_OF]->(d) RETURN c.chunk_id", "Chunk"),
        ("MATCH (p:Press)<-[:CONCERNS|ABOUT]-(x) RETURN x", "ABOUT"),
        ("MATCH (o:`Observation`) RETURN o.predicate", "Observation"),
        ("MATCH (p) WHERE p:Chunk RETURN p", "Chunk"),
        ("MATCH (p:Press:Chunk) RETURN p", "Chunk"),
    ],
)
def test_a_query_naming_a_label_or_type_outside_the_allowed_part_is_refused(cypher, name):
    assert excluded_name_issues(cypher, TEXT_LAYER) == [f"it uses {name}, which this system may not read"]


def test_record_names_values_and_comments_are_not_mistaken_for_excluded_names():
    record = "MATCH (p:Press {year: 2015})<-[:PART_OF]-(x:Part) WHERE p:Press RETURN x.name // not Chunk"
    assert excluded_name_issues(record, TEXT_LAYER) == []
    assert excluded_name_issues("MATCH (c:Chunk) RETURN c", frozenset()) == []  # the graph system: no limit


# --- rows read as an answer ------------------------------------------------------------------------


def test_rows_become_distinct_names_or_one_number():
    rows = [["Quill Press"], [["Lark Press", "Quill Press"]], [None], []]
    assert rows_to_answer(rows, "entities") == (["Quill Press", "Lark Press"], None)
    assert rows_to_answer([[3]], "number") == (None, 3.0)
    assert rows_to_answer([[True]], "number") == (None, None)  # a flag is no count
    assert rows_to_answer([], "number") == (None, None)
    assert rows_to_answer([], "entities") == ([], None)  # no row: the answer is "none"


# --- the exact route over a fake store --------------------------------------------------------------

SCHEMA = GraphSchema(
    labels=[
        LabelInfo(
            label="Press",
            count=2,
            properties=[PropertyInfo(name="name", type="STRING", examples=["'Quill Press'"])],
        )
    ],
    relationships=[RelationshipInfo(source="Part", type="PART_OF", target="Press", count=3)],
    claims=[ClaimInfo(subject_type="Part", predicate="HAS_CONDITION", object_type="Condition", count=4)],
)


class FakeCypherStore:
    """`CypherStore` whose EXPLAIN refuses the queries in `unplannable` and whose run returns `rows`."""

    def __init__(self, rows=None, unplannable=(), error=None):
        self._rows = rows or []
        self._unplannable = set(unplannable)
        self._error = error
        self.ran: list[tuple[str, dict]] = []

    def schema(self):
        return SCHEMA

    def explain(self, cypher, parameters):
        return ["the label `Nope` does not exist"] if any(u in cypher for u in self._unplannable) else []

    def run_read(self, cypher, parameters):
        self.ran.append((cypher, parameters))
        return ReadResult(error=self._error) if self._error else ReadResult(rows=self._rows)


def proposing(*proposals: CypherProposal) -> tuple[ScriptedLLM, list[str]]:
    prompts: list[str] = []
    queue = list(proposals)

    def script(prompt, schema):
        prompts.append(prompt)
        return queue.pop(0)

    return ScriptedLLM(script), prompts


def proposal(cypher: str, **parameters: str) -> CypherProposal:
    return CypherProposal(
        cypher=cypher,
        parameters=[CypherParameter(name=k, value=v) for k, v in parameters.items()],
        answer_form="entities",
    )


def test_a_passing_query_runs_once_and_its_rows_answer():
    store = FakeCypherStore(rows=[["Quill Press"]])
    llm, prompts = proposing(proposal(READ, name="quill"))
    outcome = ExactRoute(store, llm, "m", limit=100).answer("Which presses are named quill?")
    assert outcome.entities == ["Quill Press"] and outcome.trace.answered and outcome.trace.rows == 1
    assert store.ran == [(READ + "\nLIMIT 100", {"name": "quill"})]
    # the prompt shows the graph's schema and the question
    assert "- :Press (2): name (STRING) e.g. 'Quill Press'" in prompts[0] and "HAS_CONDITION" in prompts[0]


def test_parameters_keep_the_type_the_model_gave_them():
    # an INTEGER year compared with the text "2015" matches nothing; true read as 1 would not match a flag
    proposal_json = (
        '{"cypher": "MATCH (r) WHERE r.year = $y AND r.flag = $f RETURN count(r)", "answer_form": "number",'
        ' "parameters": [{"name": "y", "value": 2015}, {"name": "f", "value": true},'
        ' {"name": "t", "value": "x"}]}'
    )
    values = [p.value for p in CypherProposal.model_validate_json(proposal_json).parameters]
    assert values == [2015, True, "x"] and [type(v) for v in values] == [int, bool, str]


def test_values_are_shown_as_cypher_literals_of_their_type():
    assert [cypher_literal(v) for v in (2015, 2.5, True, "it's", ["a", 1])] == [
        "2015", "2.5", "true", "'it\\'s'", "['a', 1]",
    ]  # fmt: skip
    assert cypher_literal(Date(2015, 12, 10)) == "date('2015-12-10')"


def test_a_refused_query_gets_one_retry_with_its_reasons():
    store = FakeCypherStore(rows=[["Quill Press"]], unplannable=["Nope"])
    llm, prompts = proposing(proposal("MATCH (n:Nope) RETURN n.name"), proposal(READ, name="quill"))
    outcome = ExactRoute(store, llm, "m", limit=100).answer("Which presses?")
    assert outcome.trace.answered and len(outcome.trace.attempts) == 2
    assert "`Nope` does not exist" in prompts[1] and "MATCH (n:Nope)" in prompts[1]
    assert len(store.ran) == 1  # the refused query never ran


FULL = GraphSchema(
    labels=[
        *SCHEMA.labels,
        LabelInfo(label="Part", count=3, properties=[]),
        LabelInfo(label="Chunk", count=9, properties=[]),
        LabelInfo(label="Document", count=4, properties=[]),
    ],
    relationships=[
        *SCHEMA.relationships,
        RelationshipInfo(source="Chunk", type="PART_OF", target="Document", count=9),
        RelationshipInfo(source="Document", type="ABOUT", target="Press", count=2),
    ],
    claims=SCHEMA.claims,
)


def test_the_record_layer_keeps_the_plans_labels_and_the_relationships_between_them():
    records = FULL.records_only({"Press", "Part"})
    assert [i.label for i in records.labels] == ["Press", "Part"] and records.claims == []
    assert [(r.source, r.type, r.target) for r in records.relationships] == [("Part", "PART_OF", "Press")]
    text = records.text()
    assert "Claim patterns" not in text and "Chunk" not in text and "ABOUT" not in text
    # PART_OF also joins chunks to documents, but it joins two record labels too, so it stays allowed
    assert FULL.names_outside({"Press", "Part"}) == {"Chunk", "Document", "ABOUT"}


def test_a_records_only_route_refuses_the_text_layer_and_retries_without_running_it():
    store = FakeCypherStore(rows=[[2]])
    llm, prompts = proposing(
        proposal("MATCH (d:Document)-[:ABOUT]->(p:Press) RETURN count(d)"),
        proposal("MATCH (p:Press) RETURN count(p)"),
    )
    route = ExactRoute(
        store,
        llm,
        "m",
        100,
        schema=FULL.records_only({"Press", "Part"}),
        prompt=exact.RECORDS_PROMPT,
        excluded=FULL.names_outside({"Press", "Part"}),
    )
    outcome = route.answer("How many presses?")
    assert outcome.trace.answered and outcome.entities == ["2"]
    assert outcome.trace.attempts[0].issues == [
        "it uses ABOUT, which this system may not read",
        "it uses Document, which this system may not read",
    ]
    assert len(store.ran) == 1  # the refused query never reached the database
    # the prompt describes records only: no claim layer, no documents
    assert "Observation" not in prompts[0] and ":Document" not in prompts[0] and ":Press" in prompts[0]


def test_two_refusals_or_failing_runs_give_up_so_the_question_can_fall_back():
    store = FakeCypherStore(error="Transaction timed out")
    llm, _ = proposing(proposal(READ, name="a"), proposal(READ, name="b"))
    outcome = ExactRoute(store, llm, "m", limit=100).answer("Which presses?")
    assert not outcome.trace.answered and outcome.entities is None
    assert [a.issues for a in outcome.trace.attempts] == [["it failed when run: Transaction timed out"]] * 2


# --- the plan system: plan, retry, fallbacks (R74) ------------------------------------------------

PLAN_SCHEMA = PlanSchema(schema=SCHEMA, record_labels=frozenset({"Press", "Part"}))
COUNT_PRESSES = QueryPlan(steps=[PlanStep(op="filter_records", label="Press"), PlanStep(op="count", input=0)])
UNKNOWN = QueryPlan(steps=[PlanStep(op="filter_records", label="Machine"), PlanStep(op="count", input=0)])


class ScriptedRunner:
    """The plan runner, reduced to fixed results (or one failure) and a record of the plans it ran."""

    def __init__(self, *results):
        self.results = list(results)
        self.ran: list[QueryPlan] = []

    def run(self, plan, question):
        self.ran.append(plan)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TextSource:
    def ranked(self, question):
        return [StoredChunk(chunk_id="c1", context="Quill Press", text="Two presses run.")], None


def plan_system(plans, runner, proposals=(), name="graph", exact_store=None):
    """A plan system whose planner, text2cypher and reader are scripted by the reply schema they ask for."""
    plan_prompts: list[str] = []
    plans, proposals = list(plans), list(proposals)

    def script(prompt, schema):
        if schema is QueryPlan:
            plan_prompts.append(prompt)
            return plans.pop(0)
        if schema is CypherProposal:
            return proposals.pop(0)
        return ReaderAnswer(
            text="read from text", citations=[ReaderCitation(chunk_id="c1", quote="Two presses")]
        )

    llm = ScriptedLLM(script)
    store = exact_store or FakeCypherStore(rows=[[2]])
    system = PlanSystem(
        name, Planner(llm, "m", SCHEMA.text()), PLAN_SCHEMA, runner, ExactRoute(store, llm, "m", 100),
        TextSource(), Reader(llm, "m"), k=5,
    )  # fmt: skip
    return system, plan_prompts


def test_a_checked_plan_runs_and_its_result_is_the_answer():
    runner = ScriptedRunner(PlanRun(number=2.0, steps=[StepTrace(op="count", items={})]))
    system, prompts = plan_system([COUNT_PRESSES], runner)
    answer = system.answer("Q1", "How many presses are there?")
    assert answer.number == 2.0 and answer.plan.fallback is None and len(answer.plan.attempts) == 1
    assert answer.plan.attempts[0].steps[0].op == "count" and answer.route is None  # no router any more
    assert "<question>How many presses are there?</question>" in prompts[0] and ":Press" in prompts[0]


def test_a_refused_plan_gets_one_retry_with_its_reasons_and_never_runs():
    runner = ScriptedRunner(PlanRun(number=2.0))
    system, prompts = plan_system([UNKNOWN, COUNT_PRESSES], runner)
    answer = system.answer("Q1", "How many presses?")
    assert answer.number == 2.0 and runner.ran == [COUNT_PRESSES]  # the refused plan never ran
    assert "unknown label 'Machine'" in prompts[1] and "Your previous plan was refused" in prompts[1]
    assert answer.plan.attempts[0].issues and not answer.plan.attempts[1].issues


def test_a_plan_that_fails_while_running_is_retried_with_the_reason():
    runner = ScriptedRunner(QueryPlanError("read_check got 99 candidates, more than 30"), PlanRun(number=1.0))
    system, prompts = plan_system([COUNT_PRESSES, COUNT_PRESSES], runner)
    answer = system.answer("Q1", "How many presses?")
    assert answer.number == 1.0 and "read_check got 99 candidates" in prompts[1]


def test_two_failed_plans_fall_back_to_text2cypher_then_to_reading():
    counted = proposal("MATCH (p:Press) RETURN count(p)")
    exact_answer, _ = plan_system([UNKNOWN, UNKNOWN], ScriptedRunner(), proposals=[counted])
    answer = exact_answer.answer("Q1", "How many presses?")
    assert answer.plan.fallback == "text2cypher" and answer.exact.answered and answer.entities == ["2"]

    failing = [proposal("MATCH (n:Nope) RETURN n"), proposal("MATCH (n:Nope) RETURN n")]
    refusing = FakeCypherStore(unplannable=["Nope"])
    system, _ = plan_system([UNKNOWN, UNKNOWN], ScriptedRunner(), failing, RECORDS_VECTOR, refusing)
    read = system.answer("Q2", "What runs?")
    assert read.plan.fallback == "retrieval" and read.text == "read from text" and read.retrieved == ["c1"]
    assert read.system == RECORDS_VECTOR and not read.exact.answered  # the system's own name, the trace kept


# --- the prompts ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        planner.PROMPT + planner.RETRY,
        read_check.PROMPT,
        json.dumps(QueryPlan.model_json_schema()),  # the field descriptions reach the model too
        exact.PROMPT + exact.RETRY,
        exact.RECORDS_PROMPT + exact.RETRY,
    ],
)
def test_the_planner_read_check_and_cypher_prompts_speak_no_corpus_language(prompt):
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "pump", "staff")
    assert not [w for w in banned if w in prompt.lower()]
    assert quoted_four_grams(prompt) == []
