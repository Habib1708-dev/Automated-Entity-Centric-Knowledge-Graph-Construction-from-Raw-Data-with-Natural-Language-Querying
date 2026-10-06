"""Query plans (R74), without Neo4j: the code check of a plan (names, value types, inputs, the terminal, the
dropped optional filter, the records-only system), the Cypher each primitive compiles to (values as
parameters, comparisons by the property's type, the claim layer's fixed direction), the read_check
verification of quotes, and the name lookup of find_entity. The plans run against Neo4j in
test_query_plan_graph.py."""

import pytest

from kgbuilder.query import plan_cypher as cy
from kgbuilder.query.answers import ShownChunk
from kgbuilder.query.graph_schema import ClaimInfo, GraphSchema, LabelInfo, PropertyInfo, RelationshipInfo
from kgbuilder.query.names import NameLinker, NodeName
from kgbuilder.query.plan import PlanSchema, PlanStep, QueryPlan, as_number, check_plan
from kgbuilder.query.read_check import CheckReply, ReadChecker, verify

from .fakes import ScriptedLLM

YEAR = PropertyInfo(name="year", type="INTEGER", examples=["2016", "2019"])
SINCE = PropertyInfo(name="since", type="DATE", examples=["date('2015-06-30')"])
PRICE = PropertyInfo(name="list_price", type="STRING", examples=["'$1,200'"])
FILED = PropertyInfo(name="filed", type="STRING", examples=["'11/07/2016'"])
ACTIVE = PropertyInfo(name="active", type="BOOLEAN", examples=["false", "true"])
TAGS = PropertyInfo(name="tags", type="LIST<STRING>", examples=["['heavy']"])
NAME = PropertyInfo(name="name", type="STRING", examples=["'Quill Press'"])
SCHEMA = GraphSchema(
    labels=[
        LabelInfo(label="Press", count=2, properties=[ACTIVE, FILED, PRICE, NAME, SINCE, TAGS, YEAR]),
        LabelInfo(label="Part", count=1, properties=[NAME]),
        LabelInfo(label="Chunk", count=9, properties=[]),
    ],
    relationships=[
        RelationshipInfo(
            source="Part", type="PART_OF", target="Press", count=1,
            properties=[PropertyInfo(name="fitted", type="INTEGER", examples=["2019"])],
        ),
        RelationshipInfo(source="Chunk", type="ABOUT", target="Press", count=3),
    ],
    claims=[ClaimInfo(subject_type="Component", predicate="HAS_CONDITION", object_type="Condition", count=4)],
)  # fmt: skip
GRAPH = PlanSchema(schema=SCHEMA, record_labels=frozenset({"Press", "Part"}))
RECORDS_ONLY = PlanSchema(schema=SCHEMA, record_labels=frozenset({"Press", "Part"}), claims=False)


def plan(*steps: dict) -> QueryPlan:
    return QueryPlan(steps=[PlanStep(**s) for s in steps])


def issues(p: QueryPlan, schema: PlanSchema = GRAPH, question: str = "Which presses?") -> list[str]:
    return check_plan(p, schema, question).issues


# --- the plan check --------------------------------------------------------------------------------


def test_a_plan_of_known_names_and_typed_values_passes():
    good = plan(
        {"op": "filter_records", "label": "Press", "property": "year", "operator": ">=", "value": "2019"},
        {"op": "related", "input": 0, "relationship": "PART_OF", "label": "Part"},
        {"op": "list", "input": 1},
    )
    assert issues(good) == []


def test_unknown_labels_properties_relationships_and_predicates_are_refused_with_the_known_ones():
    found = issues(
        plan(
            {"op": "filter_records", "label": "Machine"},
            {"op": "filter_records", "label": "Press", "property": "colour", "operator": "=", "value": "red"},
            {"op": "related", "input": 1, "relationship": "MADE_BY"},
            {"op": "find_claims", "predicate": "HAS_DEFECT"},
            {"op": "list", "input": 1},
        )
    )
    text = "\n".join(found)
    assert "unknown label 'Machine'; use one of ['Part', 'Press']" in text
    assert "Press has no property 'colour'" in text and "'year'" in text
    assert "unknown relationship 'MADE_BY'; use one of ['PART_OF']" in text  # ABOUT joins no two records
    assert "unknown predicate 'HAS_DEFECT'; use one of ['HAS_CONDITION']" in text


@pytest.mark.parametrize(
    ("prop", "operator", "value", "reason"),
    [
        ("year", "=", "many", "give a number"),
        ("since", "<", "June 2015", "compare a date with 'YYYY-MM-DD'"),
        ("year", "year", "15", "four-digit year"),
        ("year", "contains", "20", "contains needs a text property"),
        ("active", "=", "maybe", "give true or false"),
        ("list_price", "<", "cheap", "needs a number"),
    ],
)
def test_a_value_must_fit_its_propertys_type(prop, operator, value, reason):
    step = {"op": "filter_records", "label": "Press", "property": prop, "operator": operator, "value": value}
    assert any(reason in i for i in issues(plan(step, {"op": "list", "input": 0})))


def test_a_number_in_a_text_property_and_a_year_in_a_text_date_may_be_compared():
    for prop, operator, value in (
        ("list_price", "<", 1500),
        ("filed", "year", "2016"),
        ("since", "year", 2015),
    ):
        step = {
            "op": "filter_records",
            "label": "Press",
            "property": prop,
            "operator": operator,
            "value": value,
        }
        assert issues(plan(step, {"op": "count", "input": 0})) == []


def test_exactly_the_last_step_is_a_terminal_and_inputs_point_back_to_items_the_step_takes():
    found = "\n".join(
        issues(
            plan(
                {"op": "count", "input": 1},
                {"op": "filter_records", "label": "Press"},
                {"op": "retrieve_chunks"},
                {"op": "find_claims", "input": 2},
                {"op": "related", "relationship": "PART_OF"},
            )
        )
    )
    assert "step 0 (count): the last step, and only the last" in found
    assert "step 0 (count): input 1 is not an earlier step" in found
    assert "step 3 (find_claims): cannot work on ['chunk'] items" in found
    assert "step 4 (related): the last step" in found and "step 4 (related): needs an input step" in found


def test_an_optional_filter_without_the_questions_own_words_is_dropped_and_reported():
    # G12 (R72): a "positive" tone and a time the question never asked for emptied the answer
    invented = plan(
        {"op": "find_claims", "predicate": "HAS_CONDITION", "tone": "positive", "tone_words": "good news",
         "time_words": "March 2025"},
        {"op": "list", "input": 0, "what": "about"},
    )  # fmt: skip
    checked = check_plan(invented, GRAPH, "Which presses were found leaking?")
    assert (
        checked.issues == []
        and checked.plan.steps[0].tone is None
        and checked.plan.steps[0].time_words is None
    )
    assert len(checked.dropped) == 2 and invented.steps[0].tone == "positive"  # the model's plan is untouched
    asked = check_plan(invented, GRAPH, "Which presses got good news in March 2025?")
    assert asked.dropped == [] and asked.plan.steps[0].tone == "positive"


def test_a_records_only_system_cannot_read_claims_and_finds_records_only():
    claims = plan({"op": "find_claims", "predicate": "HAS_CONDITION"}, {"op": "count", "input": 0})
    assert any("reads the records only" in i for i in issues(claims, RECORDS_ONLY))
    found = plan(
        {"op": "find_entity", "name": "Quill"}, {"op": "find_claims", "input": 0}, {"op": "count", "input": 1}
    )
    assert issues(found, RECORDS_ONLY)  # find_claims refused
    assert (
        issues(plan({"op": "find_entity", "name": "Quill"}, {"op": "list", "input": 0}), RECORDS_ONLY) == []
    )


def test_terminals_check_what_they_read():
    records = {"op": "filter_records", "label": "Press"}
    assert issues(plan(records, {"op": "sum", "input": 0, "property": "year"})) == []
    assert (
        issues(plan(records, {"op": "rank", "input": 0, "property": "list_price", "order": "highest"})) == []
    )
    assert issues(plan(records, {"op": "rank", "input": 0, "relationship": "PART_OF", "order": "most"})) == []
    assert issues(plan(records, {"op": "list", "input": 0, "what": "subject"}))  # subjects need claims
    assert issues(plan(records, {"op": "count", "input": 0, "unit": "about"}))


def test_rank_by_the_value_records_share_takes_a_years_operator_for_dates_only():
    # refused until R79 ("rank by property orders highest or lowest"); since R79 most/fewest with a property
    # ranks its values by how many records share them
    records = {"op": "filter_records", "label": "Press"}

    def rank(**fields):
        return issues(plan(records, {"op": "rank", "input": 0, "order": "most", **fields}))

    assert rank(property="year") == []
    assert rank(property="since", operator="year") == []
    assert rank(property="year", operator="year") == [
        "step 1 (rank): rank: operator 'year' needs a date property, year is not one"
    ]
    assert rank(property="since", operator="=")


def test_claims_about_records_are_narrowed_by_a_record_label_only():
    claims = {"op": "find_claims", "predicate": "HAS_CONDITION"}
    assert issues(plan(claims, {"op": "count", "input": 0, "unit": "about", "label": "Press"})) == []
    assert issues(plan(claims, {"op": "list", "input": 0, "what": "about", "label": "Part"})) == []
    assert issues(plan(claims, {"op": "list", "input": 0, "label": "Condition"})) == [
        "step 1 (list): list: unknown record label 'Condition'; use one of ['Part', 'Press']"
    ]
    assert issues(plan(claims, {"op": "count", "input": 0, "unit": "about", "label": "Condition"}))


def test_numbers_are_read_from_text():
    assert [as_number(v) for v in ("$1,289", "42", 3, True, "none")] == [1289.0, 42.0, 3.0, None, None]


# --- the compiled Cypher -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "operator", "value", "text", "params"),
    [
        (YEAR, ">=", "2019", "n.`year` >= $v", {"v": 2019.0}),
        (SINCE, "year", 2020, "n.`since`.year = $v", {"v": 2020}),
        (SINCE, "<", "2016-01-01", "n.`since` < date($v)", {"v": "2016-01-01"}),
        (FILED, "year", "2016", "toString(n.`filed`) =~ $v", {"v": ".*(?<![0-9])2016(?![0-9]).*"}),
        (ACTIVE, "=", "true", "n.`active` = $v", {"v": True}),
        (NAME, "=", "quill press", "toLower(toString(n.`name`)) = toLower($v)", {"v": "quill press"}),
        (NAME, "contains", "Quill", "toLower(toString(n.`name`)) CONTAINS toLower($v)", {"v": "Quill"}),
        (TAGS, "contains", "heavy", "any(x IN n.`tags` WHERE toLower(toString(x)) CONTAINS toLower($v))",
         {"v": "heavy"}),
    ],
)  # fmt: skip
def test_a_comparison_follows_the_propertys_type(prop, operator, value, text, params):
    assert cy.condition("n", prop, operator, value, "v") == (text, params)


def test_a_number_in_a_text_is_compared_as_that_number():
    # R73: the furniture graph stores prices as '$246', and "cost less than $200" found nothing
    text, params = cy.condition("n", PRICE, "<", "$200", "v")
    assert (
        text
        == "toFloat(apoc.text.regreplace(replace(toString(n.`list_price`), ',', ''), $v_pattern, '')) < $v"
    )
    assert params == {"v": 200.0, "v_pattern": r"[^0-9.\-]"}


def test_fragments_pass_every_value_as_a_parameter_and_bound_their_size():
    cypher, params = cy.filter_records("Press", NAME, "=", "Quill's", ["4:a:1"], cap=50)
    assert "'" not in cypher.replace("',', ''", "") and cypher.endswith("LIMIT 50")
    assert params == {"within": ["4:a:1"], "value": "Quill's"}
    assert (
        cy.filter_records("Press", None, None, None, None, 9)[0]
        == "MATCH (n:`Press`) RETURN elementId(n) AS id LIMIT 9"
    )


def test_related_follows_the_direction_code_chose():
    out, _ = cy.related("PART_OF", "out", "Press", None, None, None, ["p"], 10)
    back, _ = cy.related("PART_OF", "in", None, None, None, None, ["p"], 10)
    assert "(a)-[r:`PART_OF`]->(b)" in out and "b:`Press`" in out
    assert "(a)<-[r:`PART_OF`]-(b)" in back
    fitted = PropertyInfo(name="fitted", type="INTEGER", examples=[])
    filtered, params = cy.related("PART_OF", "in", None, fitted, ">", 2018, ["p"], 10)
    assert "r.`fitted` > $value" in filtered and params["value"] == 2018.0


def test_the_claim_layer_is_walked_in_its_own_direction_only():
    # G14 (R72) walked SUBJECT backwards; a plan names no arrow, the fragment fixes it
    cypher, params = cy.find_claims(["r1"], ["e1"], "HAS_CONDITION", ["s1"], ["x1"], "negative", "march", 20)
    assert "(t)-[:HAS_OBSERVATION]->(o)" in cypher and "(o)-[:SUBJECT]->(s:Mention)" in cypher
    assert (
        "(o)-[:OBJECT]->(x:Mention)" in cypher
        and "(o)-[:SUBJECT|OBJECT]->(:Mention)-[:REFERS_TO]->(t)" in cypher
    )
    # an entity is addressed by its canonical id, which the mention's identity edge carries (R75)
    assert "ref_s.canonical" in cypher and "ref_x.canonical" in cypher
    assert params == {
        "records": ["r1"], "entities": ["e1"], "predicate": "HAS_CONDITION", "subjects": ["s1"],
        "objects": ["x1"], "tone": "negative", "time": "march", "truth": "affirmed",
        "modalities": ["actual", "conditional"],
    }  # fmt: skip
    # changed on purpose in R77: even a plan that names nothing finds only the claims that hold; and in R77
    # part d: whether a claim holds is its stored triple's truth (a graph before part d has only `truth`)
    assert cy.find_claims(None, None, None, None, None, None, None, 5)[0] == (
        "MATCH (o:Observation) WHERE coalesce(coalesce(o.triple_truth, o.truth), 'affirmed') = $truth AND "
        "coalesce(o.modality, 'actual') IN $modalities RETURN DISTINCT o.id AS id LIMIT 5"
    )


# --- read_check ---------------------------------------------------------------------------------------

CHUNKS = [
    ShownChunk(chunk_id="a#0", context="Quill Press", text="The **spindle** wobbles badly."),
    ShownChunk(chunk_id="b#0", context="Lark Press", text="The spindle does not wobble."),
]


def test_a_yes_counts_only_with_its_quote_in_the_chunk_it_names():
    assert verify(CheckReply(supported=True, chunk_id="a#0", quote="the spindle wobbles"), CHUNKS)
    assert not verify(CheckReply(supported=True, chunk_id="b#0", quote="the spindle wobbles"), CHUNKS)
    assert not verify(
        CheckReply(supported=True, chunk_id="a#0", quote="it shakes"), CHUNKS
    )  # not in the text
    assert not verify(CheckReply(supported=False, chunk_id="a#0", quote="the spindle wobbles"), CHUNKS)
    assert not verify(CheckReply(supported=True, chunk_id="z#0", quote="wobbles"), CHUNKS)  # never shown


def test_read_check_asks_nothing_without_text_and_keeps_a_verified_quote():
    llm = ScriptedLLM(
        lambda prompt, schema: CheckReply(supported=True, chunk_id="a#0", quote="spindle wobbles")
    )
    checker = ReadChecker(llm, "m")
    assert not checker.check("The spindle wobbles.", []).called and llm.calls == []
    result = checker.check("The spindle wobbles.", CHUNKS)
    assert result.verified and result.called and result.chunk_id == "a#0"


# --- find_entity's name lookup ---------------------------------------------------------------------

NODES = [
    NodeName(kind="thing", node_id="s-104", name="Rosa Vey", label="Staff"),
    NodeName(kind="thing", node_id="s-219", name="Rosa Vey", label="Staff"),
    NodeName(kind="thing", node_id="d-1", name="Dock Office", label="Team"),
    NodeName(kind="kind", node_id="k-seal", name="seal failure", aliases=["failed seal"], label="Condition"),
]


def test_find_returns_every_node_a_name_spells_and_narrows_by_label():
    linker = NameLinker(NODES, [[1.0, 0.0]] * 4, fuzzy=90.0, neighbours=1)
    asked: list[str] = []

    def embed(text):
        asked.append(text)
        return [1.0, 0.0]

    found = linker.find("rosa vey", embed, {"thing", "kind"}, None, union=False)
    assert [n.node_id for n in found] == ["s-104", "s-219"] and asked == []  # both, and no embedding needed
    spelling_only = NameLinker(NODES, None, fuzzy=90.0, neighbours=0)
    assert spelling_only.find("Rosa Vey", embed, {"thing"}, "Team", union=False) == []  # the label narrows
    assert len(spelling_only.find("Rosa Vey", embed, {"thing"}, "Staff", union=False)) == 2
    assert [n.node_id for n in linker.find("the failed seal", embed, {"kind"}, None, union=False)] == [
        "k-seal"
    ]


def test_find_falls_back_to_the_nearest_in_meaning_and_adds_them_on_request():
    vectors = [[0.0, 1.0], [0.0, 1.0], [1.0, 0.0], [0.6, 0.8]]
    linker = NameLinker(NODES, vectors, fuzzy=90.0, neighbours=1)
    nearest = linker.find("harbour office", lambda t: [1.0, 0.0], {"thing"}, None, union=False)
    assert [n.node_id for n in nearest] == ["d-1"]  # nothing spelled alike: the nearest
    both = linker.find("seal failure", lambda t: [0.0, 1.0], {"kind", "thing"}, None, union=True)
    assert [n.node_id for n in both] == ["k-seal", "s-104"]  # spelled, plus the nearest
