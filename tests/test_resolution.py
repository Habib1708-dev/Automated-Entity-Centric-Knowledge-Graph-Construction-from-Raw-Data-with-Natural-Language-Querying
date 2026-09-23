"""Entity resolution: candidate finding, decisions and grouping as pure functions, the candidate preview,
the merge -> undo round trip against Neo4j, and the rule that merging keeps every separately stated fact."""

import json

import pytest

from kgbuilder.config import Settings
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.resolution.matchers import EmbeddingMatcher, EntityRecord
from kgbuilder.resolution.resolver import (
    SamePair,
    decide,
    find_candidates,
    group_merges,
    nominate,
    preview,
    resolve_entities,
    undo_merges,
)
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.subject_graph import write_subject_graph

from .fakes import RecordingTracker, ScriptedLLM


def entity(id: str, name: str, type: str = "Product", mentions: int = 1) -> EntityRecord:
    return EntityRecord(id=id, name=name, type=type, aliases=[name], mentions=mentions)


ENTITIES = [
    entity("t1", "Table", mentions=3),
    entity("t2", "Tables"),
    entity("t3", "Table Lamp"),
    entity("s1", "Sofa"),
    entity("x1", "Table", type="Problem"),  # same name, other type: never a candidate
]
BY_ID = {e.id: e for e in ENTITIES}


def pairs(candidates):
    return {(c.a, c.b) for c in candidates}


def test_candidates_are_same_type_pairs_above_the_borderline():
    assert pairs(find_candidates(ENTITIES, borderline=80)) == {("t1", "t2")}
    assert ("t1", "t3") in pairs(find_candidates(ENTITIES, borderline=50))
    assert not any("x1" in p for p in pairs(find_candidates(ENTITIES, borderline=0.1)))


class SynonymEmbedder:
    """Puts "Sofa" and "Couch" on one vector and every other name on another: cosine 100 or 0."""

    def embed(self, texts):
        return [[1.0, 0.0] if t in ("Sofa", "Couch") else [0.0, 1.0] for t in texts]


def test_embeddings_nominate_synonyms_but_never_auto_merge():
    records = [entity("s1", "Sofa"), entity("s2", "Couch"), entity("t1", "Table")]
    matcher = EmbeddingMatcher(SynonymEmbedder(), records)
    candidates = find_candidates(records, borderline=80, embedding=matcher, embedding_threshold=95)
    assert [(c.a, c.b, c.signal, c.score) for c in candidates] == [("s1", "s2", "embedding", 100.0)]
    by_id = {e.id: e for e in records}
    assert decide(candidates, by_id, auto_merge=92, adjudicate=None)[0].action == "skipped_borderline"
    assert decide(candidates, by_id, auto_merge=92, adjudicate=lambda a, b: True)[0].action == "llm_merge"


def test_meaning_based_candidates_need_an_embedder_and_a_threshold():
    records = [entity("s1", "Sofa"), entity("s2", "Couch")]
    assert nominate(records, 80, SynonymEmbedder(), embedding_threshold=0) == []  # 0 = switched off
    assert nominate(records, 80, None, embedding_threshold=95) == []  # no embedder, nothing to compare
    assert [c.signal for c in nominate(records, 80, SynonymEmbedder(), embedding_threshold=95)] == [
        "embedding"
    ]


def test_preview_names_each_pair_its_route_and_orders_by_score():
    records = [*ENTITIES, entity("s2", "Couch")]
    candidates = nominate(records, borderline=50, embedder=SynonymEmbedder(), embedding_threshold=95)
    rows = [(p.signal, p.a, p.b, p.route) for p in preview(records, candidates, auto_merge=90).pairs]
    assert rows[0] == ("embedding", "Sofa", "Couch", "llm")  # a meaning score always goes to the LLM
    fuzzy = [r for r in rows if r[0] == "fuzzy"]
    assert fuzzy[0] == ("fuzzy", "Table", "Tables", "auto")  # 90.9 >= 90: merged on spelling alone
    assert all(route == "llm" for *_, route in fuzzy[1:])


def test_decisions_auto_llm_and_skipped():
    candidates = find_candidates(ENTITIES, borderline=50)
    actions = {(d.a_id, d.b_id): d.action for d in decide(candidates, BY_ID, 90, lambda a, b: False)}
    assert actions[("t1", "t2")] == "auto" and actions[("t1", "t3")] == "llm_keep"


def test_grouping_is_transitive_and_the_most_mentioned_entity_stays():
    records = [entity("a", "Desk"), entity("b", "Desks", mentions=5), entity("c", "Desk's")]
    by_id = {e.id: e for e in records}
    decisions = decide(find_candidates(records, borderline=80), by_id, auto_merge=80, adjudicate=None)
    (group,) = group_merges(by_id, decisions)
    assert group.canonical == "b" and sorted(group.absorbed) == ["a", "c"]


def dump(driver) -> dict:
    """Everything entity resolution may touch, in a comparable form."""

    def rows(query):
        return sorted(str(r.data()) for r in driver.execute_query(query)[0])

    return {
        "entities": rows("MATCH (e:Entity) RETURN properties(e) AS p"),
        "mentions": rows("MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) RETURN c.chunk_id AS c, e.id AS e"),
        "facts": rows(
            "MATCH (s:Entity)-[r]->(o:Entity) RETURN s.id AS s, type(r) AS t, o.id AS o, properties(r) AS p"
        ),
    }


def fact(subject, predicate, obj, obj_type, chunk, evidence=None):
    return Triple(
        subject=subject, subject_type="Product", predicate=predicate, object=obj, object_type=obj_type,
        evidence=evidence or f"{subject} {obj}", chunk_id=chunk,
    )  # fmt: skip


@pytest.mark.neo4j
def test_merging_keeps_every_separately_stated_fact_and_drops_exact_repeats(driver):
    """Three reviews saying "the drawers stick" are three pieces of evidence (subject_graph.py keeps one
    fact per chunk and quote). Merging "Table" into "Tables" must not fold them into one relationship; only
    a fact that is identical in type, ends, chunk and quote after the merge is a repeat."""
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "wobble", "Problem", "d.md#1"),
            # the same statement once more under the other spelling: identical after the merge
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#1", evidence="Tables wobble"),
        ],
        extractor="test",
    )
    report = resolve_entities(driver, None, model="m", auto_merge=90)

    records, _, _ = driver.execute_query(
        "MATCH (:Entity)-[f:HAS_PROBLEM]->(:Entity {name: 'wobble'}) "
        "RETURN f.chunk_id AS chunk, f.evidence AS evidence ORDER BY chunk"
    )
    assert [(r["chunk"], r["evidence"]) for r in records] == [
        ("d.md#0", "Table wobble"),
        ("d.md#1", "Tables wobble"),
    ]
    mentions, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity) WHERE e.name <> 'wobble' "
        "RETURN c.chunk_id AS c, count(m) AS n"
    )
    assert {r["c"]: r["n"] for r in mentions} == {"d.md#0": 1, "d.md#1": 1}  # one mention per chunk, not two
    assert report.merges == 1 and report.duplicate_facts_removed == 1


@pytest.mark.neo4j
def test_merge_then_undo_restores_the_graph(driver):
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "scratch", "Problem", "d.md#1"),
            fact("Table", "SIMILAR_TO", "Tables", "Product", "d.md#1"),  # becomes a self-loop when merged
        ],
        extractor="test",
    )
    before = dump(driver)

    llm = ScriptedLLM(lambda prompt, schema: SamePair(same=False))
    report = resolve_entities(driver, llm, model="m", auto_merge=90)  # "Table"/"Tables" scores 90.9
    assert report.merges == 1 and report.self_loops_removed == 1
    assert report.entities_after == report.entities_before - 1
    assert dump(driver) != before

    assert undo_merges(driver, report) == 1
    assert dump(driver) == before
    assert undo_merges(driver, report) == 1 and dump(driver) == before  # idempotent


@pytest.mark.neo4j
def test_preview_stage_lists_candidates_and_changes_nothing(driver, tmp_path):
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "scratch", "Problem", "d.md#1"),
        ],
        extractor="test",
    )
    before = dump(driver)
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path, tracker=tracker)

    result = run_stages(ctx, PipelineState(), [st.PreviewResolveStage()]).resolve_preview
    assert [{p.a, p.b} for p in result.pairs] == [{"Table", "Tables"}]  # order follows entity ids
    assert dump(driver) == before
    run = tracker.run("resolve_preview")
    assert run.logged_metrics["candidates"] == 1 and run.logged_metrics["candidates_embedding"] == 0
    assert run.logged_params["embed_model"] is None  # no embedder: meaning-based candidates impossible
    written = json.loads((tmp_path / st.PreviewResolveStage.PREVIEW_FILE).read_text(encoding="utf-8"))
    assert written["pairs"][0]["route"] in ("auto", "llm")
