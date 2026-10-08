"""The `hybrid` system on Neo4j (R120b): an H17-shaped synthetic case and the reader's prompt. Needs Neo4j.

The case (invented words, never a dataset's question): two presses each have a notice; the Lark Press's
notice is about its feed roller, the Quill Press's about a spindle bearing. An operator's ticket says the
Quill Press's feed roller stopped turning, a claim held by the Quill Press. The question names no press:
which notices concern the feed roller of a press whose operator says the feed roller stopped turning? The
words alone lead to the Lark Press's feed-roller notice; the claim and the Quill Press's card lead to the
ticket and to the Quill Press's own notice, which answers it (none concerns the feed roller). Also: no card
or claim sentence text ever reaches the reader, only source chunks."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.graph.index_layer import index_names
from kgbuilder.hybrid.unit_store import Neo4jUnitStore
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import IndexStage
from kgbuilder.pipeline.qa_systems import SYSTEMS, QAParts
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.graph_store import Neo4jGraphStore
from kgbuilder.query.reader import Reader, ReaderAnswer
from kgbuilder.structured.plan import ConstructionPlan

from .fakes import RecordingTracker, ScriptedLLM
from .sample_plans import node, rel
from .test_hybrid_units import RELATED_PLAN
from .test_hybrid_units import load as load_presses

PLAN = ConstructionPlan(
    nodes=[
        node("presses.csv", "Press", "press_id", ["name"]),
        node("notices.csv", "Notice", "notice_id"),
        node("tickets.csv", "Ticket", "ticket_id"),
    ],
    relationships=[
        rel("notices.csv", "CONCERNS", "Notice", "notice_id", "Press", "press_id"),
        rel("tickets.csv", "CONCERNS", "Ticket", "ticket_id", "Press", "press_id"),
    ],
)

GRAPH = """
CREATE (quill:Press {press_id: 'P1', name: 'Quill Press'}), (lark:Press {press_id: 'P2', name: 'Lark Press'}),
       (n1:Notice {notice_id: 'N1'})-[:CONCERNS]->(quill), (n2:Notice {notice_id: 'N2'})-[:CONCERNS]->(lark),
       (t1:Ticket {ticket_id: 'T1'})-[:CONCERNS]->(quill),
       (dn1:Document {doc_id: 'notices/n1.md'})-[:ABOUT]->(n1),
       (dn2:Document {doc_id: 'notices/n2.md'})-[:ABOUT]->(n2),
       (dt1:Document {doc_id: 'tickets/t1.md'})-[:ABOUT]->(t1),
       (cn1:Chunk {chunk_id: 'notices/n1.md#0', context: 'Notice N1',
                   text: 'Notice N1: the spindle bearing may seize.'})-[:PART_OF]->(dn1),
       (cn2:Chunk {chunk_id: 'notices/n2.md#0', context: 'Notice N2',
                   text: 'Notice N2: the feed roller may jam and stop turning.'})-[:PART_OF]->(dn2),
       (ct1:Chunk {chunk_id: 'tickets/t1.md#0', context: 'Ticket T1',
                   text: 'The operator says the feed roller stopped turning after a week.'})
         -[:PART_OF]->(dt1),
       (roller:Concept {id: 'k-roller', name: 'feed roller', type: 'Part'}),
       (stop:Concept {id: 'k-stop', name: 'stopped turning', type: 'Symptom'}),
       (mr:Mention {id: 'm-roller-t1', name: 'feed roller', type: 'Part', doc_id: 'tickets/t1.md'})
         -[:REFERS_TO {canonical: 'k-roller', name: 'feed roller', kind: 'concept'}]->(roller),
       (ms:Mention {id: 'm-stop-t1', name: 'stopped turning', type: 'Symptom', doc_id: 'tickets/t1.md'})
         -[:REFERS_TO {canonical: 'k-stop', name: 'stopped turning', kind: 'concept'}]->(stop),
       (mn:Mention {id: 'm-roller-n2', name: 'feed roller', type: 'Part', doc_id: 'notices/n2.md'})
         -[:REFERS_TO {canonical: 'k-roller', name: 'feed roller', kind: 'concept'}]->(roller),
       (ct1)-[:MENTIONS]->(mr), (ct1)-[:MENTIONS]->(ms), (cn2)-[:MENTIONS]->(mn),
       (o1:Observation {id: 'o1', predicate: 'AFFECTS_PART', truth: 'affirmed', triple_truth: 'affirmed',
                        modality: 'actual'}),
       (o1)-[:SUBJECT]->(ms), (o1)-[:OBJECT]->(mr), (o1)-[:FROM]->(ct1), (quill)-[:HAS_OBSERVATION]->(o1)
"""

QUESTION = (
    "Which notices concern the feed roller of a press whose operator says the feed roller stopped turning?"
)
# no chunk vector index in the test database: the chunks' dense list is left out, every other one is asked
RETRIEVERS = ["chunk_lexical", "claim_dense", "claim_lexical", "card_dense", "card_lexical"]


class KeywordEmbedder:
    """Texts naming the feed roller (or "wobble", for the shared graph) on one axis, the rest on another."""

    def embed(self, texts):
        return [[1.0, 0.0] if ("roller" in t.lower() or "wobbl" in t.lower()) else [0.0, 1.0] for t in texts]


def indexed(driver, tmp_path, plan) -> PipelineContext:
    """The graph's retrieval layer written by `kg index`; the context the system is then built in."""
    out = tmp_path / "build"
    out.mkdir()
    (out / PLAN_FILE).write_text(plan.model_dump_json(), encoding="utf-8")
    settings = Settings(hybrid_retrievers=RETRIEVERS, qa_top_k=3)
    ctx = PipelineContext(settings=settings, driver=driver, out=out, embedder=KeywordEmbedder(),
                          tracker=RecordingTracker())  # fmt: skip
    run_stages(ctx, PipelineState(), [IndexStage("template")])
    return ctx


def hybrid(ctx, plan, reader):
    s = ctx.settings
    parts = QAParts(settings=s, store=Neo4jGraphStore(ctx.driver, plan, s.qa_hops), embedder=ctx.embedder,
                    plan=plan, units=Neo4jUnitStore(ctx.driver), llm=None, reader=reader)  # fmt: skip
    return SYSTEMS["hybrid"].build(parts)


@pytest.fixture
def clean_indexes(driver):
    yield driver
    for name in index_names(["template"]):
        driver.execute_query(f"DROP INDEX {name} IF EXISTS")


def test_an_h17_shaped_question_reaches_the_right_press_through_its_claim_and_card(clean_indexes, tmp_path):
    driver = clean_indexes
    driver.execute_query(GRAPH)
    ctx = indexed(driver, tmp_path, PLAN)
    prompts: list[str] = []
    reader = Reader(ScriptedLLM(lambda prompt, schema: prompts.append(prompt) or ReaderAnswer(text="t")), "m")
    answer = hybrid(ctx, PLAN, reader).answer("Q", QUESTION)
    trace = answer.trace
    words = trace.lists["chunk_lexical"]
    # the words alone put the other press's feed-roller notice before the right press's own notice
    assert "notices/n2.md#0" in words and words.index("notices/n2.md#0") < words.index("notices/n1.md#0")
    # the claim leads to the ticket, the Quill Press's card to its own notice: both are read
    assert trace.lists["claim_lexical"][0] == "tickets/t1.md#0"
    assert {"tickets/t1.md#0", "notices/n1.md#0"} <= set(answer.retrieved)
    assert "Press:P1" in trace.seeds and "Press:P2" not in trace.seeds[:1]
    o1 = next(c for c in trace.claims if c.id == "o1")
    assert (o1.truth, o1.triple_truth, o1.modality, o1.chunk_id) == (
        "affirmed",
        "affirmed",
        "actual",
        "tickets/t1.md#0",
    )


def test_no_card_or_claim_text_reaches_the_reader_only_source_chunks(clean_indexes, tmp_path):
    driver = clean_indexes
    load_presses(driver)
    ctx = indexed(driver, tmp_path, RELATED_PLAN)
    prompts: list[str] = []
    reader = Reader(ScriptedLLM(lambda prompt, schema: prompts.append(prompt) or ReaderAnswer(text="t")), "m")
    answer = hybrid(ctx, RELATED_PLAN, reader).answer("Q", "Which press has a spindle that wobbles?")
    units, _, _ = driver.execute_query("MATCH (u:RetrievalUnit) RETURN u.text AS text")
    prompt = prompts[0]
    assert answer.shown and all(c.text in prompt for c in answer.shown)
    # not one card line or claim sentence: the cards and claims only chose which chunks come first
    assert not [
        line for u in units for line in u["text"].splitlines() if line.startswith("- ") and line in prompt
    ]
    assert not [u["text"] for u in units if u["text"] in prompt]
    assert "Also called:" not in prompt and "Claims (" not in prompt and " <- " not in prompt
