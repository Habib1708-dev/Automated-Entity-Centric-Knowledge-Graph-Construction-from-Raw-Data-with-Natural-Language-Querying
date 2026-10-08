"""A hand-made graph shared by the Neo4j tests of the query stage (test_query_graph.py) and of the retrieval
units (test_hybrid_units.py, R118): four record labels, documents and chunks, a concept with two mentions, a
part record a mention refers to, and one claim held by a press. Invented words only (Press/Part style),
never a dataset's.
Needs Neo4j to be written; the module itself only holds the Cypher and the plan."""

from kgbuilder.structured.plan import ConstructionPlan

from .sample_plans import node

PLAN = ConstructionPlan(
    nodes=[
        node("presses.csv", "Press", "press_id", ["name"]),
        node("parts.csv", "Part", "part_id", ["name"]),
        node("makers.csv", "Maker", "maker_id", ["name"]),
        node("tickets.csv", "Ticket", "ticket_id"),
    ],
    relationships=[],
)

# A press with a part (one hop), the part's maker (two hops) and a ticket about the press (one hop); a second
# press shares only a document with the first, which must not make them related. Every chunk has one role.
GRAPH = """
CREATE (press:Press {press_id: 'P1', name: 'Quill Press', year: 2019, active: true,
                     since: date('2020-01-02')}),
       (other:Press {press_id: 'P2', name: 'Lark Press', year: 2016, active: false,
                     since: date('2015-06-30')}),
       (part:Part {part_id: 'S1', name: 'Spindle'}), (maker:Maker {maker_id: 'M1', name: 'Norcast'}),
       (ticket:Ticket {ticket_id: 'T-1'}),
       (part)-[:PART_OF]->(press), (part)-[:MADE_BY]->(maker), (ticket)-[:CONCERNS]->(press),
       (notes:Document {doc_id: 'notes.md'}), (record:Document {doc_id: 'record/Ticket/T-1'}),
       (log:Document {doc_id: 'log.md'}), (both:Document {doc_id: 'both.md'}),
       (notes)-[:ABOUT]->(press), (record)-[:ABOUT]->(ticket),
       (both)-[:ABOUT]->(press), (both)-[:ABOUT]->(other),
       (c1:Chunk {chunk_id: 'notes.md#0', text: 'The spindle wobbles.', context: 'Quill Press notes',
                  embedding: [1.0, 0.0]})-[:PART_OF]->(notes),
       (c2:Chunk {chunk_id: 'notes.md#1', text: 'Delivered late.', context: 'Quill Press notes',
                  embedding: [0.0, 1.0]})-[:PART_OF]->(notes),
       (c3:Chunk {chunk_id: 'record/Ticket/T-1#0', text: 'Ticket text.', context: 'T-1'})
         -[:PART_OF]->(record),
       (c4:Chunk {chunk_id: 'log.md#0', text: 'On the ticket.', context: 'Log'})-[:PART_OF]->(log),
       (c5:Chunk {chunk_id: 'log.md#1', text: 'Some wobbling seen.', context: 'Log'})-[:PART_OF]->(log),
       (c6:Chunk {chunk_id: 'log.md#2', text: 'On the maker.', context: 'Log'})-[:PART_OF]->(log),
       (c7:Chunk {chunk_id: 'log.md#3', text: 'On the part.', context: 'Log'})-[:PART_OF]->(log),
       (c8:Chunk {chunk_id: 'both.md#0', text: 'Both presses.', context: 'Both'})-[:PART_OF]->(both),
       (c9:Chunk {chunk_id: 'log.md#4', text: 'On the other press.', context: 'Log'})-[:PART_OF]->(log),
       (c4)-[:ABOUT]->(ticket), (c6)-[:ABOUT]->(maker), (c7)-[:ABOUT]->(part), (c9)-[:ABOUT]->(other),
       (wobble:Concept {id: 'k-wobble', name: 'wobbles', type: 'Condition'}),
       (wobbles:Mention {id: 'm-wobbles', name: 'wobbles', type: 'Condition', doc_id: 'notes.md'})
         -[:REFERS_TO {canonical: 'k-wobble', name: 'wobbles', kind: 'concept'}]->(wobble),
       (wobbling:Mention {id: 'm-wobbling', name: 'wobbling', type: 'Condition', doc_id: 'log.md'})
         -[:REFERS_TO {canonical: 'k-wobble', name: 'wobbles', kind: 'concept'}]->(wobble),
       (spindle:Mention {id: 'm-spindle', name: 'spindle', type: 'Component', doc_id: 'notes.md'})
         -[:REFERS_TO {canonical: 'Part:S1', name: 'Spindle', kind: 'record', reason: 'name'}]->(part),
       (o1:Observation {id: 'o1', predicate: 'HAS_CONDITION', polarity: 'negative'}),
       (o1)-[:SUBJECT]->(spindle), (o1)-[:OBJECT]->(wobbles), (o1)-[:FROM]->(c1),
       (press)-[:HAS_OBSERVATION]->(o1), (c1)-[:MENTIONS]->(spindle), (c1)-[:MENTIONS]->(wobbles),
       (c5)-[:MENTIONS]->(wobbling)
"""


def build(driver) -> dict[str, str]:
    """Write the graph; return the element id of each domain node by its name or key."""
    driver.execute_query(GRAPH)
    records, _, _ = driver.execute_query(
        "MATCH (n) WHERE n:Press OR n:Part OR n:Maker OR n:Ticket "
        "RETURN coalesce(n.name, n.ticket_id) AS name, elementId(n) AS id"
    )
    return {r["name"]: r["id"] for r in records}
