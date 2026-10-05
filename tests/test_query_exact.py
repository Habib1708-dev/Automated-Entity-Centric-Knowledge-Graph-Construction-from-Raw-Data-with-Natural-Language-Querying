"""The exact route and the router (R71 part b), without Neo4j: the text check of a model's Cypher, the
exact route's retry and give-up over a fake store, rows read as an answer, the router, the graph system's
fallback to retrieval, and the prompts' domain-neutral wording; for records plus vector RAG (R73), the
record layer of the schema, the refusal of names outside it and the system's own name on every answer.
The database half of the check (EXPLAIN, the read transaction) is tested in test_query_graph.py."""

import pytest
from neo4j.time import Date

from kgbuilder.query import exact, router
from kgbuilder.query.answers import SystemAnswer
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
from kgbuilder.query.graph_store import ReadResult
from kgbuilder.query.router import RouteChoice, Router
from kgbuilder.query.systems import RECORDS_VECTOR, RoutedGraph
from kgbuilder.validation.qa_gold import Route

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


# --- the router and the routed graph system --------------------------------------------------------


def test_the_router_returns_the_models_route_and_shows_it_the_schema():
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        return RouteChoice(route="exact", reason="it counts")

    assert Router(ScriptedLLM(script), "m", SCHEMA.text()).route("How many presses?") == Route.EXACT
    assert "<question>How many presses?</question>" in prompts[0] and ":Press" in prompts[0]


class FixedRouter:
    def __init__(self, route: Route):
        self._route = route

    def route(self, question: str) -> Route:
        return self._route


class RecordingRetrieval:
    """The retrieval route, reduced to an answer and a record of who asked."""

    def __init__(self):
        self.asked: list[str] = []

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        self.asked.append(question_id)
        return SystemAnswer(question_id=question_id, system="graph", text="from the text", retrieved=["c1"])


def test_the_graph_system_answers_exactly_when_it_can_and_falls_back_to_retrieval_when_not():
    retrieval = RecordingRetrieval()
    counted = ExactRoute(
        FakeCypherStore(rows=[[2]]), proposing(proposal("MATCH (p:Press) RETURN count(p)"))[0], "m", 100
    )
    answer = RoutedGraph(FixedRouter(Route.EXACT), counted, retrieval).answer("Q1", "How many presses?")
    assert answer.route == Route.EXACT and answer.exact.answered and retrieval.asked == []

    failing = ExactRoute(
        FakeCypherStore(error="boom"),
        proposing(proposal(READ, name="a"), proposal(READ, name="b"))[0],
        "m",
        100,
    )
    fallback = RoutedGraph(FixedRouter(Route.EXACT), failing, retrieval).answer("Q2", "Which presses?")
    # the router's label is kept for route accuracy, the exact trace for the failure analysis
    assert fallback.route == Route.EXACT and not fallback.exact.answered and fallback.text == "from the text"

    read = RoutedGraph(FixedRouter(Route.RETRIEVAL), counted, retrieval).answer("Q3", "What do texts say?")
    assert read.route == Route.RETRIEVAL and read.exact is None and retrieval.asked == ["Q2", "Q3"]


def test_a_routed_system_signs_every_answer_with_its_own_name_whichever_route_answered():
    # records plus vector RAG falls back to the vector baseline, whose answers say "vector" (here "graph")
    retrieval = RecordingRetrieval()
    failing = ExactRoute(
        FakeCypherStore(error="boom"),
        proposing(proposal(READ, name="a"), proposal(READ, name="b"))[0],
        "m",
        100,
    )
    fallback = RoutedGraph(FixedRouter(Route.EXACT), failing, retrieval, name=RECORDS_VECTOR).answer(
        "Q1", "?"
    )
    read = RoutedGraph(FixedRouter(Route.RETRIEVAL), failing, retrieval, name=RECORDS_VECTOR).answer(
        "Q2", "?"
    )
    assert (fallback.system, read.system) == (RECORDS_VECTOR, RECORDS_VECTOR)


# --- the prompts ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt", [router.PROMPT, exact.PROMPT + exact.RETRY, exact.RECORDS_PROMPT + exact.RETRY]
)
def test_the_router_and_cypher_prompts_speak_no_corpus_language(prompt):
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "pump", "staff")
    assert not [w for w in banned if w in prompt.lower()]
    assert quoted_four_grams(prompt) == []
