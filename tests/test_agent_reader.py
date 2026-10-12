"""The agent's node reader against Neo4j (R133, agent/node_reader.py), on the shared hand-made graph with the
claims of test_hybrid_units.py: nodes found by ref in the order asked (records by `Label:key`, kinds by
canonical id, unknown refs left out), a node's other names and its plan columns with their source, where the
traversal starts, the record relationships and the claims a node is an end of grouped per other node with
the total kept past the limit, one relation type alone, the claims of given chunks a node holds or is an end
of, and claims by id with their sentences and stored truth fields. R141: the text reader's passages of
one document in order with the total, and every passage holding all given words. R149: a word held by a
passage's context (its document's title) counts for every passage of that document. Needs Neo4j."""

from kgbuilder.agent import Neo4jNodeReader, Neo4jTextReader

from .test_hybrid_units import RELATED_PLAN, load


def reader(driver) -> Neo4jNodeReader:
    load(driver)
    return Neo4jNodeReader(driver, RELATED_PLAN)


def test_nodes_are_found_by_ref_in_the_order_asked_and_unknown_refs_are_left_out(driver):
    found = reader(driver).nodes(["k-wobble", "Press:NONE", "Ticket:T-1", "i-ada", "Press:P1", "nothing"])
    assert [(n.ref, n.kind, n.label, n.title) for n in found] == [
        ("k-wobble", "concept", "Condition", "wobbles"),
        ("Ticket:T-1", "record", "Ticket", "T-1"),  # no name column: titled by its key
        ("i-ada", "individual", "Person", "Ada Brook"),
        ("Press:P1", "record", "Press", "Quill Press"),
    ]


def test_a_node_shows_its_other_names_and_its_plan_columns_with_their_source(driver):
    nodes = reader(driver)
    press = nodes.node("Press:P1")
    # the plan imports the key and the name; the name is the title, and year and active are no plan columns
    assert [(p.name, p.value, p.source) for p in press.properties] == [
        ("press_id", "P1", "presses.csv row press_id=P1")
    ]
    concept = nodes.node("k-wobble")
    assert (concept.aliases, concept.properties) == (["wobbling"], [])  # "wobbles" is its title
    assert nodes.node("Press:NONE") is None


def test_the_traversal_starts_from_a_record_by_element_id_and_from_a_kind_by_canonical_id(driver):
    nodes = reader(driver)
    press, _, _ = driver.execute_query("MATCH (p:Press {press_id: 'P1'}) RETURN elementId(p) AS id")
    assert nodes.start("Press:P1").model_dump() == {"kind": "thing", "node_id": press[0]["id"]}
    assert nodes.start("k-wobble").model_dump() == {"kind": "kind", "node_id": "k-wobble"}
    assert nodes.start("Press:NONE") is None


def test_relations_are_record_relationships_then_claims_grouped_per_other_node(driver):
    page = reader(driver).relations("Part:S1", None, 10)
    rows = [(r.via, r.type, r.outgoing, r.ref, r.name, r.label, r.count, r.claims) for r in page.relations]
    assert rows == [
        ("record", "MADE_BY", True, "Maker:M1", "Norcast", "Maker", 1, []),
        ("record", "PART_OF", True, "Press:P1", "Quill Press", "Press", 1, []),
        # o1, o2 (its denial) and o3 (under a condition) all join the spindle to the concept
        ("claim", "HAS_CONDITION", True, "k-wobble", "wobbles", "Condition", 3, ["o1", "o2", "o3"]),
        ("claim", "MADE_BY", True, "Maker:M1", "Norcast", "Maker", 1, ["o5"]),
    ]
    assert page.total == 4


def test_relations_keep_the_total_past_the_limit_and_one_type_can_be_asked_alone(driver):
    nodes = reader(driver)
    press = nodes.relations("Press:P1", None, 2)
    # four parts and a ticket: five record relationships, two shown
    assert (len(press.relations), press.total) == (2, 5)
    assert [r.ref for r in press.relations] == ["Ticket:T-1", "Part:S2"]  # CONCERNS, then PART_OF by name
    concept = nodes.relations("k-wobble", "REPORTS", 10)
    assert [(r.ref, r.outgoing, r.claims) for r in concept.relations] == [("i-ada", False, ["o4"])]
    assert concept.total == 1


def test_node_claims_are_those_of_the_given_chunks_it_holds_or_is_an_end_of(driver):
    nodes = reader(driver)
    held = nodes.node_claims("Press:P1", ["notes.md#0"])  # o2 is from notes.md#1
    assert [c.id for c in held] == ["o1", "o3", "o5"]
    assert held[0].sentence == "Spindle (Component) has condition wobbles (Condition)"
    ends = nodes.node_claims("k-wobble", ["notes.md#1", "log.md#1"])
    assert [(c.id, c.chunk_id) for c in ends] == [("o2", "notes.md#1"), ("o4", "log.md#1")]


def test_claims_by_id_carry_their_stored_truth_fields(driver):
    found = reader(driver).claims(["o2", "missing", "o4"])
    assert [(c.id, c.truth, c.triple_truth, c.modality, c.hedge) for c in found] == [
        ("o2", "negated", "negated", "actual", ""),
        ("o4", "affirmed", "affirmed", "possible", "may"),
    ]


def test_the_text_reader_reads_a_documents_passages_in_order_and_every_passage_holding_words(driver):
    load(driver)
    text = Neo4jTextReader(driver)
    passages, total = text.document("log.md", 3)
    assert [c.chunk_id for c in passages] == ["log.md#0", "log.md#1", "log.md#2"] and total == 5
    assert (passages[1].context, passages[1].text) == ("Log", "Some wobbling seen.")
    assert text.document("nothing.md", 3) == ([], 0)
    wobbling, total = text.matching(["wobbl"], 5)
    assert [c.chunk_id for c in wobbling] == ["log.md#1", "notes.md#0"] and total == 2  # by document
    on_the, total = text.matching(["on the", "press"], 5)
    assert [c.chunk_id for c in on_the] == ["log.md#4"] and total == 1  # every word in one passage


def test_a_word_of_a_documents_title_counts_for_every_passage_of_it(driver):
    # notes.md's passages carry the context 'Quill Press notes' and never name the press themselves
    load(driver)
    text = Neo4jTextReader(driver)
    late, total = text.matching(["quill", "late"], 5)
    assert [c.chunk_id for c in late] == ["notes.md#1"] and total == 1
    whole, total = text.matching(["quill press"], 5)
    assert [c.chunk_id for c in whole] == ["notes.md#0", "notes.md#1"] and total == 2
    # each word is held by the text or by the title, so no phrase spans the two
    assert text.matching(["notes the"], 5) == ([], 0)
