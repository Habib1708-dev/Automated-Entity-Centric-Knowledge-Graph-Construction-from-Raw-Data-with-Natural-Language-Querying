"""The retrieval index layer in Neo4j (R119, hybrid/unit_graph.py and `kg index`), on the shared hand-made
graph with the claims of test_hybrid_units.py: units MERGEd with their vectors and linked to what they stand
for, the indexes created, a second run embedding nothing, a changed node re-embedding only the cards whose
text changed, two representations side by side with stale removal limited to one (the second representation
is a name given to `write_units` in this test only), Part 1 untouched (its nodes, the digest and the
planner's schema text the same before and after), a plan using the layer's names refused before anything is
written, and an index of other dimensions recreated. Needs Neo4j."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError
from kgbuilder.graph.digest import graph_digest
from kgbuilder.graph.index_layer import ANALYZER, card_indexes, index_names
from kgbuilder.hybrid import read_claim_sentences, read_targets
from kgbuilder.hybrid.unit_graph import card_rows, claim_rows, ensure_indexes, write_units
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import IndexStage, read_units
from kgbuilder.pipeline.stage import PLAN_FILE
from kgbuilder.query.graph_schema import read_graph_schema

from .fakes import RecordingTracker
from .sample_plans import node
from .test_hybrid_units import RELATED_PLAN, load
from .test_query import FixedEmbedder

CARDS, CLAIMS = 10, 5  # the graph's nodes with a card, and its claims (test_hybrid_units.py)


@pytest.fixture
def layer(driver):
    """The test database with the shared graph, and its index layer's indexes dropped afterwards (the
    driver fixture empties the nodes, not the indexes)."""
    load(driver)
    yield driver
    for name in index_names(["template", "other"]):
        driver.execute_query(f"DROP INDEX {name} IF EXISTS")


def index_context(driver, tmp_path, embedder, plan=RELATED_PLAN):
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    (out / PLAN_FILE).write_text(plan.model_dump_json(), encoding="utf-8")
    return PipelineContext(
        settings=Settings(), driver=driver, out=out, embedder=embedder, tracker=RecordingTracker()
    )


def run_index(ctx):
    run_stages(ctx, PipelineState(), [IndexStage("template")])
    return ctx.tracker.runs[-1]


def test_kg_index_writes_every_unit_with_its_vector_target_and_indexes(layer, tmp_path):
    embedder = FixedEmbedder()
    run = run_index(index_context(layer, tmp_path, embedder))
    m = run.logged_metrics
    assert (m["units_written"], m["units_reused"], m["stale_units_removed"]) == (CARDS + CLAIMS, 0, 0)
    assert m["embedded_texts"] == CARDS + CLAIMS and m["indexes_recreated"] == 0
    assert (
        run.logged_params["analyzer"] == ANALYZER
        and run.logged_params["embed_model"] == Settings().embed_model
    )
    rows, _, _ = layer.execute_query(
        "MATCH (u:RetrievalUnit:NodeCard:TemplateCard)-[:CARD_OF]->(n) "
        "RETURN u.id AS id, u.ref AS ref, coalesce(n.id, n.press_id) AS target, size(u.embedding) AS dims, "
        "u.embed_model AS model, u.evidence_hash AS h ORDER BY id"
    )
    by_id = {r["id"]: r for r in rows}
    assert len(rows) == CARDS and by_id["template:Press:P1"]["target"] == "P1"
    assert by_id["template:k-wobble"]["target"] == "k-wobble" and {r["dims"] for r in rows} == {2}
    assert {r["model"] for r in rows} == {Settings().embed_model} and all(len(r["h"]) == 16 for r in rows)
    claims, _, _ = layer.execute_query(
        "MATCH (u:RetrievalUnit:ClaimSentence)-[:SENTENCE_OF]->(o:Observation) RETURN u.id AS id, o.id AS o"
    )
    assert sorted((r["id"], r["o"]) for r in claims) == [(f"claim:o{i}", f"o{i}") for i in range(1, 6)]
    indexes, _, _ = layer.execute_query(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, options WHERE name IN $names "
        "RETURN name, type, labelsOrTypes[0] AS label, options.indexConfig AS config",
        names=index_names(["template"]),
    )
    found = {r["name"]: r for r in indexes}
    assert found[card_indexes("template").vector]["config"]["vector.dimensions"] == 2
    assert found["claim_sentence_text"]["config"]["fulltext.analyzer"] == ANALYZER
    assert found["chunk_text"]["label"] == "Chunk" and len(found) == 5


def test_a_second_run_embeds_nothing_and_a_changed_node_only_its_changed_cards(layer, tmp_path):
    run_index(index_context(layer, tmp_path, FixedEmbedder()))
    embedder = FixedEmbedder()
    again = run_index(index_context(layer, tmp_path, embedder)).logged_metrics
    assert embedder.batches == [] and (again["units_written"], again["units_reused"]) == (0, CARDS + CLAIMS)
    # renaming a part changes its own card and the press's PART_OF line; nothing else
    layer.execute_query("MATCH (p:Part {part_id: 'S2'}) SET p.name = 'Cog'")
    embedder = FixedEmbedder()
    changed = run_index(index_context(layer, tmp_path, embedder)).logged_metrics
    assert (changed["units_written"], changed["units_reused"]) == (2, CARDS + CLAIMS - 2)
    assert [text.splitlines()[0] for batch in embedder.batches for text in batch] == [
        "Quill Press (Press)", "Cog (Part)",
    ]  # fmt: skip


def test_two_representations_coexist_and_stale_removal_touches_only_the_one_written(layer, tmp_path):
    ctx = index_context(layer, tmp_path, FixedEmbedder())
    run_index(ctx)
    units = read_units(ctx, PipelineState(), "template", RELATED_PLAN)
    targets, claims = read_targets(layer, RELATED_PLAN), claim_rows(read_claim_sentences(layer))
    # a second representation, "other", of the same nodes: its own label, the shared claims reused
    embedder = FixedEmbedder()
    other = write_units(layer, "other", card_rows("other", "v1", units.cards, targets), claims, embedder, "m")
    assert other.written == CARDS + CLAIMS  # another embedding model: the claims are embedded for it
    ensure_indexes(layer, "other", 2)
    counts, _, _ = layer.execute_query(
        "MATCH (u:RetrievalUnit) RETURN sum(toInteger(u:TemplateCard)) AS t, sum(toInteger(u:OtherCard)) AS o"
    )
    assert (counts[0]["t"], counts[0]["o"]) == (CARDS, CARDS)
    # template written again without the press's card: only that card goes
    fewer = [row for row in card_rows("template", "v", units.cards, targets) if row.ref != "Press:P1"]
    gone = write_units(layer, "template", fewer, claims, FixedEmbedder(), "m")
    assert gone.stale_removed == 1
    counts, _, _ = layer.execute_query(
        "MATCH (u:RetrievalUnit) RETURN sum(toInteger(u:TemplateCard)) AS t, sum(toInteger(u:OtherCard)) AS o"
    )
    assert (counts[0]["t"], counts[0]["o"]) == (CARDS - 1, CARDS)


def test_indexing_leaves_part_one_its_digest_and_the_planner_schema_unchanged(layer, tmp_path):
    def part_one():
        rows, _, _ = layer.execute_query(
            "MATCH (n) WHERE NOT n:RetrievalUnit "
            "RETURN labels(n) AS labels, properties(n) AS props ORDER BY elementId(n)"
        )
        return [(sorted(r["labels"]), r["props"]) for r in rows]

    before = (part_one(), graph_digest(layer), read_graph_schema(layer).text())
    run_index(index_context(layer, tmp_path, FixedEmbedder()))
    assert (part_one(), graph_digest(layer), read_graph_schema(layer).text()) == before


@pytest.mark.parametrize(
    "plan",
    [
        RELATED_PLAN.model_copy(update={"nodes": [*RELATED_PLAN.nodes, node("t.csv", "TemplateCard", "id")]}),
        RELATED_PLAN.model_copy(
            update={"nodes": [*RELATED_PLAN.nodes, node("u.csv", "RetrievalUnit", "id")]}
        ),
    ],
)
def test_a_plan_using_the_layers_names_is_refused_before_anything_is_written(layer, tmp_path, plan):
    embedder = FixedEmbedder()
    with pytest.raises(ConfigurationError, match="names of the retrieval index layer"):
        run_index(index_context(layer, tmp_path, embedder, plan))
    units, _, _ = layer.execute_query("MATCH (u:RetrievalUnit) RETURN count(u) AS n")
    assert embedder.batches == [] and units[0]["n"] == 0


def test_an_index_of_other_dimensions_is_recreated(layer):
    assert ensure_indexes(layer, "template", 2) == []
    recreated = ensure_indexes(layer, "template", 3)
    assert recreated == [card_indexes("template").vector, "claim_sentence_embeddings"]
    rows, _, _ = layer.execute_query(
        "SHOW INDEXES YIELD name, options WHERE name = $name RETURN options.indexConfig AS config",
        name=card_indexes("template").vector,
    )
    assert rows[0]["config"]["vector.dimensions"] == 3
