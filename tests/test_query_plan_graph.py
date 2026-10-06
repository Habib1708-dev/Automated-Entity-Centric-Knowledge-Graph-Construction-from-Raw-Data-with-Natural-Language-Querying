"""Query plans run against Neo4j (R74): each primitive and terminal on the hand-made graph of R71's tests
(two presses, a part with its maker, a ticket, documents, chunks, one claim), with the read_check reader and
the final reader scripted. Needs Neo4j."""

import pytest

from kgbuilder.core.errors import QueryPlanError
from kgbuilder.query.graph_store import Neo4jGraphStore
from kgbuilder.query.names import NameLinker
from kgbuilder.query.plan import PlanSchema, PlanStep, QueryPlan, check_plan
from kgbuilder.query.plan_run import PlanRunner, PlanSettings
from kgbuilder.query.read_check import CheckReply, ReadChecker
from kgbuilder.query.reader import Reader, ReaderAnswer, ReaderCitation
from kgbuilder.structured.plan import name_property

from .fakes import ScriptedLLM
from .test_query_graph import PLAN, AxisEmbedder, build

# Extra values the R71 graph lacks: a price written as text, a date written as text, and a property on a
# relationship, as the furniture and held-out graphs have them.
EXTRA = """
MATCH (q:Press {press_id: 'P1'}), (l:Press {press_id: 'P2'}), (s:Part {part_id: 'S1'})-[r:PART_OF]->(q)
SET q.list_price = '$1,200', l.list_price = '$950', q.filed = '11/07/2016', l.filed = '03/02/2015',
    r.fitted = 2019
"""


class NoSource:
    def ranked(self, question):
        return [], None


@pytest.fixture
def runner_parts(driver):
    build(driver)
    driver.execute_query(EXTRA)
    store = Neo4jGraphStore(driver, PLAN, hops=1, cypher_timeout_s=5)
    schema = PlanSchema(schema=store.schema(), record_labels=frozenset(rule.label for rule in PLAN.nodes))
    return store, schema


def runner(store, schema, checks=None, reply=None, check_limit=30, check=None) -> PlanRunner:
    """A plan runner on the test graph; read_check replies come from `checks` in order, or from `check`
    (prompt -> reply) when given."""
    check_llm = ScriptedLLM(
        lambda prompt, s: check(prompt) if check else checks.pop(0) if checks else CheckReply(supported=False)
    )
    read_llm = ScriptedLLM(lambda prompt, s: reply or ReaderAnswer(text="nothing"))
    return PlanRunner(
        store,
        AxisEmbedder(),
        NameLinker(store.node_names(), None, fuzzy=90.0, neighbours=0),
        schema,
        {rule.label: name_property(rule) for rule in PLAN.nodes},
        Reader(read_llm, "m"),
        ReadChecker(check_llm, "m"),
        NoSource(),
        PlanSettings(top_k=2, check_limit=check_limit),
    )


def run(runner_, schema, question, *steps):
    plan = QueryPlan(steps=[PlanStep(**s) for s in steps])
    checked = check_plan(plan, schema, question)
    assert checked.issues == []
    return runner_.run(checked.plan, question)


def test_records_are_filtered_by_type_and_followed_along_a_relationship_the_way_code_chose(runner_parts):
    store, schema = runner_parts
    result = run(
        runner(store, schema),
        schema,
        "Which parts belong to presses from 2019 on?",
        {"op": "filter_records", "label": "Press", "property": "year", "operator": ">=", "value": 2019},
        {"op": "related", "input": 0, "relationship": "PART_OF", "label": "Part"},
        {"op": "list", "input": 1},
    )
    assert result.entities == ["Spindle"]
    assert result.steps[1].note == "direction in"  # (Part)-[:PART_OF]->(Press), walked from the press


def test_a_number_written_as_text_and_years_in_dates_and_texts_are_compared_right(runner_parts):
    store, schema = runner_parts
    r = runner(store, schema)
    cheap = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Press", "property": "list_price", "operator": "<", "value": 1000},
        {"op": "list", "input": 0},
    )
    assert cheap.entities == ["Lark Press"]  # '$950' < 1000, '$1,200' is not
    dated = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Press", "property": "since", "operator": "year", "value": 2015},
        {"op": "list", "input": 0},
    )
    assert dated.entities == ["Lark Press"]
    written = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Press", "property": "filed", "operator": "year", "value": "2016"},
        {"op": "list", "input": 0},
    )
    assert written.entities == ["Quill Press"]


def test_a_relationships_own_property_is_shown_and_can_be_filtered(runner_parts):
    store, schema = runner_parts
    part_of = next(r for r in schema.schema.relationships if r.type == "PART_OF" and r.source == "Part")
    assert [p.name for p in part_of.properties] == ["fitted"]
    assert "(:Part)-[:PART_OF]->(:Press) (1): fitted (INTEGER) e.g. 2019" in schema.schema.text()
    result = run(
        runner(store, schema),
        schema,
        "q",
        {"op": "find_entity", "name": "Quill Press"},
        {
            "op": "related",
            "input": 0,
            "relationship": "PART_OF",
            "property": "fitted",
            "operator": ">",
            "value": 2020,
        },
        {"op": "count", "input": 1},
    )
    assert result.number == 0.0


def test_claims_are_found_about_a_record_its_parts_or_an_entity_and_listed_or_counted(runner_parts):
    store, schema = runner_parts
    r = runner(store, schema)
    subjects = run(
        r,
        schema,
        "q",
        {"op": "find_entity", "name": "quill press", "label": "Press"},
        {"op": "find_claims", "input": 0, "predicate": "HAS_CONDITION"},
        {"op": "list", "input": 1, "what": "subject"},
    )
    # the subject's canonical entity: the notes' "spindle" refers to the part record "Spindle" (R75)
    assert subjects.entities == ["Spindle"]
    # the part's claim: its mention refers to the part, so the claim is found from the part record too
    from_part = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Part"},
        {"op": "find_claims", "input": 0},
        {"op": "list", "input": 1, "what": "about"},
    )
    assert from_part.entities == ["Quill Press"]
    # a record's parts are the records whose relationships point at it: the spindle part points at its
    # maker (MADE_BY), so the spindle's claim is a claim about the maker only with include_parts
    maker = {"op": "find_entity", "name": "Norcast"}
    alone = run(r, schema, "q", maker, {"op": "find_claims", "input": 0}, {"op": "count", "input": 1})
    parts = run(
        r,
        schema,
        "q",
        maker,
        {"op": "find_claims", "input": 0, "include_parts": True},
        {"op": "count", "input": 1},
    )
    assert (alone.number, parts.number) == (0.0, 1.0)
    by_kind = run(
        r,
        schema,
        "q",
        {"op": "find_entity", "name": "wobbles", "label": "Condition"},
        {"op": "find_claims", "input": 0},
        {"op": "count", "input": 1, "unit": "about"},
    )
    assert by_kind.number == 1.0


def test_about_goes_on_from_the_records_claims_are_about(runner_parts):
    # R86 (R83: F04 "the price of the product whose frame creaks"): the spindle's claim hangs on the Quill
    # Press, so its price and its parts are reached from the claim
    store, schema = runner_parts
    r = runner(store, schema)
    wobbles = {"op": "find_claims", "object_like": "wobbles"}
    priced = run(
        r,
        schema,
        "What does the press with a wobbling part cost?",
        wobbles,
        {"op": "about", "input": 0, "label": "Press"},
        {"op": "list", "input": 1, "property": "list_price"},
    )
    assert (priced.entities, priced.steps[1].items) == (["$1,200"], {"record": 1})
    parts = run(
        r,
        schema,
        "Which parts has the press with a wobbling part?",
        wobbles,
        {"op": "about", "input": 0, "label": "Press"},
        {"op": "related", "input": 1, "relationship": "PART_OF", "label": "Part"},
        {"op": "list", "input": 2},
    )
    assert parts.entities == ["Spindle"]


def test_read_check_keeps_only_candidates_with_a_verified_quote_and_code_counts_them(runner_parts):
    store, schema = runner_parts
    checks = [CheckReply(supported=True, chunk_id="notes.md#0", quote="the spindle wobbles")]
    result = run(
        runner(store, schema, checks=checks),
        schema,
        "How many claims say a spindle wobbles?",
        {"op": "find_claims", "predicate": "HAS_CONDITION"},
        {"op": "read_check", "input": 0, "statement": "A spindle wobbles."},
        {"op": "count", "input": 1, "unit": "documents"},
    )
    assert (result.number, result.checks, result.verified) == (1.0, 1, 1)
    assert [c.chunk_id for c in result.shown] == ["notes.md#0"]
    assert result.citations[0].quote == "the spindle wobbles"
    unverified = run(
        runner(
            store, schema, checks=[CheckReply(supported=True, chunk_id="notes.md#0", quote="it never moves")]
        ),
        schema,
        "q",
        {"op": "find_claims", "predicate": "HAS_CONDITION"},
        {"op": "read_check", "input": 0, "statement": "A spindle wobbles."},
        {"op": "count", "input": 1},
    )
    assert unverified.number == 0.0  # the quote is not in the chunk: the yes does not count


# R85: one chunk, two claims about the spindle in two sentences, each with its evidence
SHOP = """
MATCH (part:Part {part_id: 'S1'})
CREATE (c:Chunk {chunk_id: 'shop.md#0', text: 'Ada Rook oiled the spindle. The spindle squeaks.',
                 context: 'Shop log'})-[:PART_OF]->(:Document {doc_id: 'shop.md'}),
       (rook:Mention {id: 'm-rook', name: 'Ada Rook', type: 'Person', doc_id: 'shop.md'}),
       (spindle:Mention {id: 'm-spindle-shop', name: 'spindle', type: 'Component', doc_id: 'shop.md'})
         -[:REFERS_TO {canonical: 'Part:S1', name: 'Spindle', kind: 'record', reason: 'name'}]->(part),
       (squeaks:Mention {id: 'm-squeaks', name: 'squeaks', type: 'Condition', doc_id: 'shop.md'}),
       (o2:Observation {id: 'o2', predicate: 'SERVICES', evidence: 'Ada Rook oiled the spindle.'}),
       (o3:Observation {id: 'o3', predicate: 'HAS_CONDITION', evidence: 'The spindle squeaks.'}),
       (o2)-[:SUBJECT]->(rook), (o2)-[:OBJECT]->(spindle), (o2)-[:FROM]->(c),
       (o3)-[:SUBJECT]->(spindle), (o3)-[:OBJECT]->(squeaks), (o3)-[:FROM]->(c)
"""


def test_read_check_judges_each_claim_not_only_the_chunk_it_shares(runner_parts, driver):
    store, schema = runner_parts
    driver.execute_query(SHOP)
    prompts: list[str] = []

    def yes_to_the_oiling(prompt: str) -> CheckReply:
        # a reader that finds the oiling in any text it is shown; only code can keep it to its own claim
        prompts.append(prompt)
        return CheckReply(supported=True, chunk_id="shop.md#0", quote="Ada Rook oiled the spindle.")

    result = run(
        runner(store, schema, check=yes_to_the_oiling),
        schema,
        "Who oiled the spindle?",
        {"op": "find_entity", "name": "Spindle", "label": "Part"},
        {"op": "find_claims", "input": 0},
        {"op": "read_check", "input": 1, "statement": "The spindle was oiled."},
        {"op": "list", "input": 2, "what": "subject"},
    )
    # the notes' claim is in another chunk; the squeak shares the chunk but not the sentence
    assert (result.checks, result.verified, result.entities) == (3, 1, ["Ada Rook"])
    assert any(
        "claim: Ada Rook SERVICES spindle\nevidence: Ada Rook oiled the spindle.\n" in p for p in prompts
    )
    assert any("claim: spindle HAS_CONDITION squeaks\nevidence: The spindle squeaks.\n" in p for p in prompts)


def test_read_check_refuses_more_candidates_than_its_bound(runner_parts):
    store, schema = runner_parts
    with pytest.raises(QueryPlanError, match="read_check got 2 candidates, more than 1"):
        run(
            runner(store, schema, check_limit=1),
            schema,
            "q",
            {"op": "filter_records", "label": "Press"},
            {"op": "read_check", "input": 0, "statement": "It is fast."},
            {"op": "count", "input": 1},
        )


def test_rank_sum_and_property_lists_are_computed_by_code(runner_parts):
    store, schema = runner_parts
    r = runner(store, schema)
    presses = {"op": "filter_records", "label": "Press"}
    assert run(r, schema, "q", presses, {"op": "sum", "input": 0, "property": "year"}).number == 4035.0
    top = run(
        r, schema, "q", presses, {"op": "rank", "input": 0, "property": "list_price", "order": "highest"}
    )
    assert top.entities == ["Quill Press"]
    tickets = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Ticket"},
        {"op": "rank", "input": 0, "relationship": "CONCERNS", "order": "most"},
    )
    assert tickets.entities == ["Quill Press"]
    years = run(r, schema, "q", presses, {"op": "list", "input": 0, "property": "year"})
    assert sorted(years.entities) == ["2016", "2019"]


def test_answer_from_chunks_reads_the_inputs_own_text(runner_parts):
    store, schema = runner_parts
    reply = ReaderAnswer(text="It was late.", citations=[ReaderCitation(chunk_id="notes.md#1", quote="late")])
    result = run(
        runner(store, schema, reply=reply),
        schema,
        "What happened to the delivery?",
        {"op": "find_entity", "name": "Quill Press", "label": "Press"},
        {"op": "answer_from_chunks", "input": 0},
    )
    assert result.text == "It was late." and result.citations[0].chunk_id == "notes.md#1"
    assert len(result.shown) == 2 and {c.chunk_id for c in result.shown} <= {
        "notes.md#0",
        "notes.md#1",
        "both.md#0",
    }


class OneChunkSource:
    """The system's chunk source, giving one chunk whatever the question."""

    def __init__(self, store):
        self._store = store

    def ranked(self, question):
        return self._store.chunks(["notes.md#1"]), None


def test_a_reader_whose_input_has_no_text_reads_the_chunk_source(runner_parts):
    # R78: an earlier step that found nothing left the reader no text, and it answered "No text was
    # retrieved" where the system's own retrieval held the answer (R77 baseline: G08, G11, H37)
    store, schema = runner_parts
    r = runner(store, schema)
    r._source = OneChunkSource(store)
    result = run(
        r,
        schema,
        "What went wrong in 1999?",
        {"op": "find_claims", "predicate": "HAS_CONDITION", "time_words": "1999"},
        {"op": "answer_from_chunks", "input": 0},
    )
    assert result.steps[0].items == {"claim": 0}
    assert [c.chunk_id for c in result.shown] == ["notes.md#1"]
    assert result.steps[1].note == "1 chunks read (the input had no text: the chunk source)"


def test_claim_words_on_the_wrong_end_are_matched_on_either_end_before_giving_up(runner_parts):
    # R78: the planner asked for "mechanical seal" as a claim's object, the graph has it as the subject
    # (R77 baseline: G06); here "wobbles" is the object of the spindle's claim
    store, schema = runner_parts
    r = runner(store, schema)
    as_subject = run(
        r,
        schema,
        "Which part wobbles?",
        {"op": "find_claims", "subject_like": "wobbles"},
        {"op": "list", "input": 0, "what": "subject"},
    )
    assert as_subject.entities == ["Spindle"]
    assert as_subject.steps[0].note == "claim words on either end"
    on_its_end = run(
        r,
        schema,
        "Which part wobbles?",
        {"op": "find_claims", "object_like": "wobbles"},
        {"op": "list", "input": 0, "what": "subject"},
    )
    assert (on_its_end.entities, on_its_end.steps[0].note) == (["Spindle"], "")


def test_claim_words_reach_a_claim_end_whose_mention_refers_to_a_record(runner_parts):
    # R84: the notes' "spindle" refers to the part record "Spindle" (R75), so claim words naming it must
    # reach the claim on that end; before R84 they were looked up among concepts only and found nothing
    # (R83: furniture's "frame creaks", whose "frame" refers to an Assembly record)
    store, schema = runner_parts
    r = runner(store, schema)
    by_record = run(
        r,
        schema,
        "Which spindles wobble?",
        {"op": "find_claims", "subject_like": "spindle", "object_like": "wobbles"},
        {"op": "count", "input": 0},
    )
    assert (by_record.number, by_record.steps[0].note) == (1.0, "")


def test_a_listed_property_of_one_number_is_also_the_answers_number(runner_parts):
    # R78: "What does the Quill Press cost?" answered "$1,200" as a name, never as the number 1200 (R77
    # baseline: F63 "$289", H33, H65); several values stay a list without a number
    store, schema = runner_parts
    r = runner(store, schema)
    one = run(
        r,
        schema,
        "q",
        {"op": "find_entity", "name": "Quill Press", "label": "Press"},
        {"op": "list", "input": 0, "property": "list_price"},
    )
    assert (one.entities, one.number) == (["$1,200"], 1200.0)
    both = run(
        r,
        schema,
        "q",
        {"op": "filter_records", "label": "Press"},
        {"op": "list", "input": 0, "property": "year"},
    )
    assert both.number is None


def _with(driver, store, query) -> PlanSchema:
    """Add to the test graph, then read the schema again, as a plan sees the graph it runs on."""
    driver.execute_query(query)
    return PlanSchema(schema=store.schema(), record_labels=frozenset(rule.label for rule in PLAN.nodes))


def test_claims_are_counted_and_listed_by_the_records_of_one_label_they_are_about(runner_parts, driver):
    # R79: "How many complaints report X?" counted the complaints and their vehicles (R78 held-out H12:
    # 4 for 2); here the spindle's claim hangs on the press and on the part
    store, _ = runner_parts
    schema = _with(
        driver,
        store,
        "MATCH (s:Part {part_id: 'S1'}), (o:Observation {id: 'o1'}) MERGE (s)-[:HAS_OBSERVATION]->(o)",
    )
    r = runner(store, schema)
    claims = {"op": "find_claims", "predicate": "HAS_CONDITION"}
    every = run(r, schema, "q", claims, {"op": "count", "input": 0, "unit": "about"})
    parts = run(r, schema, "q", claims, {"op": "count", "input": 0, "unit": "about", "label": "Part"})
    presses = run(r, schema, "q", claims, {"op": "list", "input": 0, "what": "about", "label": "Press"})
    assert (every.number, parts.number, presses.entities) == (2.0, 1.0, ["Quill Press"])


def test_rank_finds_the_value_the_most_records_share_or_its_year(runner_parts, driver):
    # R79: "Which component appears in the most recalls?" and "In which year ... the most?" (held-out H55,
    # H53) had no primitive; a tie names every value, as rank does
    store, _ = runner_parts
    schema = _with(
        driver,
        store,
        "MATCH (q:Press {press_id: 'P1'}), (l:Press {press_id: 'P2'}) "
        "SET q.kind = 'manual', l.kind = 'manual', l.since = date('2020-06-30')",
    )
    r = runner(store, schema)
    presses = {"op": "filter_records", "label": "Press"}
    kind = run(r, schema, "q", presses, {"op": "rank", "input": 0, "property": "kind", "order": "most"})
    year = run(
        r, schema, "q", presses,
        {"op": "rank", "input": 0, "property": "since", "operator": "year", "order": "most"},
    )  # fmt: skip
    tie = run(r, schema, "q", presses, {"op": "rank", "input": 0, "property": "year", "order": "fewest"})
    assert (kind.entities, kind.number) == (["manual"], None)
    assert (year.entities, year.number) == (["2020"], 2020.0)
    assert sorted(tie.entities) == ["2016", "2019"] and tie.number is None


def test_one_listed_claim_end_that_is_a_number_is_also_the_answers_number(runner_parts, driver):
    # R79: "What was the mileage in complaint X?" listed the claim's object "2361", never the number
    # (held-out H33); R78 did this for record properties only
    store, _ = runner_parts
    schema = _with(
        driver,
        store,
        "MATCH (q:Press {press_id: 'P1'}), (c:Chunk {chunk_id: 'notes.md#0'}), (s:Mention {id: 'm-spindle'}) "
        "CREATE (o:Observation {id: 'o2', predicate: 'HAS_SPEED', polarity: 'neutral'}), "
        "(n:Mention {id: 'm-42', name: '42', type: 'Speed', doc_id: 'notes.md'}), "
        "(o)-[:SUBJECT]->(s), (o)-[:OBJECT]->(n), (o)-[:FROM]->(c), (q)-[:HAS_OBSERVATION]->(o)",
    )
    result = run(
        runner(store, schema),
        schema,
        "q",
        {"op": "find_entity", "name": "Quill Press", "label": "Press"},
        {"op": "find_claims", "input": 0, "predicate": "HAS_SPEED"},
        {"op": "list", "input": 1, "what": "object"},
    )
    assert (result.entities, result.number) == (["42"], 42.0)
