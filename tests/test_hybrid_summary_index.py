"""LLM node summaries in the retrieval index layer (R123, `kg index --cards summary`), on the shared hand-made
graph of test_hybrid_units.py with a scripted model: summary cards written beside the template cards from
the same evidence (equal evidence hashes per node), with their cited facts and a fallback marked on the unit,
the claim sentences shared, the stage's summary params, metrics and prompt artifact; dropping one
representation's cards leaves the other's with their vectors; and `kg units --cards summary` refused without
a model call. Needs Neo4j."""

import re

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import LLMUnavailableError
from kgbuilder.graph.index_layer import card_indexes, index_names
from kgbuilder.hybrid.summaries import NodeSummary
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.index_stages import IndexStage, UnitsStage
from kgbuilder.pipeline.stage import PLAN_FILE

from .fakes import RecordingTracker, ScriptedLLM
from .test_hybrid_index import CARDS, CLAIMS
from .test_hybrid_units import RELATED_PLAN, load
from .test_query import FixedEmbedder


@pytest.fixture
def layer(driver):
    """The test database with the shared graph; the layer's indexes dropped afterwards."""
    load(driver)
    yield driver
    for name in index_names(["template", "summary"]):
        driver.execute_query(f"DROP INDEX {name} IF EXISTS")


def writer() -> ScriptedLLM:
    """A model that names each node, but writes the press without its name, so the press falls back."""

    def script(prompt, schema):
        title = re.search(r"The node: (.+) \(", prompt).group(1)  # the node's own line comes first
        if title == "Quill Press":
            return NodeSummary(text="A machine of some kind.", facts_used=[])
        return NodeSummary(text=f"{title} is a node of the graph.", facts_used=[])

    return ScriptedLLM(script)


def context(driver, tmp_path, llm=None):
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    (out / PLAN_FILE).write_text(RELATED_PLAN.model_dump_json(), encoding="utf-8")
    return PipelineContext(
        settings=Settings(), driver=driver, out=out, llm=llm, embedder=FixedEmbedder(),
        tracker=RecordingTracker(),
    )  # fmt: skip


def index(ctx, cards):
    run_stages(ctx, PipelineState(), [IndexStage(cards)])
    return ctx.tracker.runs[-1]


def counts(driver):
    rows, _, _ = driver.execute_query(
        "MATCH (u:RetrievalUnit) RETURN sum(toInteger(u:TemplateCard)) AS t, "
        "sum(toInteger(u:SummaryCard)) AS s, sum(toInteger(u:ClaimSentence)) AS c"
    )
    return rows[0]["t"], rows[0]["s"], rows[0]["c"]


def test_summary_cards_are_written_beside_the_template_cards_from_the_same_evidence(layer, tmp_path):
    index(context(layer, tmp_path), "template")
    run = index(context(layer, tmp_path, writer()), "summary")
    m, p = run.logged_metrics, run.logged_params
    # the claim sentences were embedded by the template run and are reused; only the summaries are new
    assert (m["units_written"], m["units_reused"]) == (CARDS, CLAIMS)
    assert (m["summaries_rejected"], m["summaries_fallback"]) == (1, 1)
    assert counts(layer) == (CARDS, CARDS, CLAIMS)
    assert (p["cards"], p["summary_model"], p["summary_max_chars"]) == (
        "summary", Settings().index_summary_model, Settings().index_summary_max_chars,
    )  # fmt: skip
    assert "prompts/summary.txt" in run.artifacts
    rows, _, _ = layer.execute_query(
        "MATCH (s:SummaryCard)-[:CARD_OF]->(n)<-[:CARD_OF]-(t:TemplateCard) "
        "RETURN s.ref AS ref, s.evidence_hash = t.evidence_hash AS same, s.text AS text, t.text AS card, "
        "s.fallback AS fallback, s.facts_used AS used, t.fallback AS t_fallback, t.facts_used AS t_used"
    )
    by_ref = {r["ref"]: r for r in rows}
    assert len(rows) == CARDS and all(r["same"] for r in rows)
    press = by_ref["Press:P1"]
    assert press["fallback"] is True and press["text"] == press["card"] and press["used"] == []
    other = by_ref["Press:P2"]
    assert other["fallback"] is False and other["text"].endswith("is a node of the graph.")
    assert all(r["t_fallback"] is None and r["t_used"] is None for r in rows)  # template units unchanged
    online, _, _ = layer.execute_query(
        "SHOW INDEXES YIELD name, state WHERE name IN $names RETURN count(*) AS n",
        names=list(card_indexes("summary").model_dump().values()),
    )
    assert online[0]["n"] == 2


def test_dropping_one_representation_leaves_the_other_with_its_vectors(layer, tmp_path):
    index(context(layer, tmp_path), "template")
    index(context(layer, tmp_path, writer()), "summary")
    layer.execute_query("MATCH (u:TemplateCard) DETACH DELETE u")
    assert counts(layer) == (0, CARDS, CLAIMS)
    again = index(context(layer, tmp_path, writer()), "summary").logged_metrics
    assert (again["units_written"], again["units_reused"]) == (0, CARDS + CLAIMS)


def test_kg_units_never_asks_a_model_for_summaries(layer, tmp_path):
    llm = writer()
    with pytest.raises(LLMUnavailableError, match="kg units never calls a model"):
        run_stages(context(layer, tmp_path, llm), PipelineState(), [UnitsStage("summary")])
    assert llm.calls == []
