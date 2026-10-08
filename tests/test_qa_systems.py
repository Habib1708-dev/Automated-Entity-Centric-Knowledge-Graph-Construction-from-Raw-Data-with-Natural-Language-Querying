"""How a QA system is chosen by name (R116), without Neo4j: the params and prompts each system logs, which
systems replay frozen plans, and the refusal of an unknown name. The expected values are written from the
prompt constants and the settings, not from the registry, so they pin what the runs logged before R116.
R117: `kg qa`'s vector system reads exactly k chunks, a source ranks as deep as it is asked, the graph route
is a system of its own (`graph_retrieval`), `kg qa` asks the systems before R117 by default, and only a
reading system has a source for `kg retrieve-eval`.
R123: the card systems of the A-against-B comparison keep their fixed retriever lists whatever the hybrid
settings, and search only their own representation's cards.
Answering end to end is tested in test_query.py (fake store) and test_query_graph.py (Neo4j)."""

import pytest

from kgbuilder import cli
from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError
from kgbuilder.graph.connection import open_driver
from kgbuilder.graph.index_layer import index_names
from kgbuilder.hybrid.cards import CARD_TEMPLATE
from kgbuilder.hybrid.unit_store import IndexState
from kgbuilder.llm.base import prompt_version
from kgbuilder.pipeline import PipelineContext, PipelineState
from kgbuilder.pipeline.index_stages import card_representation
from kgbuilder.pipeline.qa_stages import AskStage, QAStage
from kgbuilder.pipeline.qa_systems import (
    DEFAULT_SYSTEMS,
    SYSTEMS,
    QAParts,
    SourceParts,
    check_source,
    log_prompts,
    source_params,
)
from kgbuilder.pipeline.retrieval_stages import RetrieveEvalStage
from kgbuilder.query import exact, planner, read_check, reader

from .fakes import ScriptedLLM
from .test_hybrid_retrievers import FakeUnitStore
from .test_query import FakeStore, FixedEmbedder, chunk, citing_reader

READ_CHECK = read_check.PROMPT + read_check.CLAIM_RULE + read_check.CANDIDATE
CYPHER = {"graph": exact.PROMPT + exact.RETRY, "records_vector": exact.RECORDS_PROMPT + exact.RETRY}
PLANNED = ("graph", "records_vector")
EVERY_SYSTEM = ["graph", "vector", "graph_retrieval", "hybrid", "records_vector"]


@pytest.fixture
def ctx(tmp_path):
    # a driver to a port nothing listens on: params and prompts must not touch the graph
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")
    yield PipelineContext(settings=Settings(), driver=driver, out=tmp_path / "out")
    driver.close()


def expected_params(s: Settings, system: str) -> dict[str, object]:
    params: dict[str, object] = {
        "system": system,
        "model": s.qa_model,
        "thinking": s.qa_thinking,
        "temperature": s.llm_temperature,
        "prompt_version": prompt_version(reader.PROMPT),
        "qa_top_k": s.qa_top_k,
        "embed_model": s.embed_model,
    }
    if system in ("graph_retrieval", "hybrid"):  # the linker's settings and the hop limit
        params.update(
            qa_link_fuzzy=s.qa_link_fuzzy, qa_link_neighbours=s.qa_link_neighbours, qa_hops=s.qa_hops
        )
    if system == "hybrid":  # R120b: the fusion settings and the cards' representation with its version
        params.update(
            hybrid_retrievers=s.hybrid_retrievers, hybrid_rrf_k=s.hybrid_rrf_k, hybrid_depth=s.hybrid_depth,
            hybrid_cards="template", representation_version=prompt_version(CARD_TEMPLATE),
        )  # fmt: skip
    if system in PLANNED:
        params.update(
            planner_prompt_version=prompt_version(planner.PROMPT + planner.RETRY),
            read_check_prompt_version=prompt_version(READ_CHECK),
            cypher_prompt_version=prompt_version(CYPHER[system]),
            qa_cypher_limit=s.qa_cypher_limit,
            qa_cypher_timeout_s=s.qa_cypher_timeout_s,
            qa_step_cap=s.qa_step_cap,
            qa_check_limit=s.qa_check_limit,
            qa_check_chunks=s.qa_check_chunks,
            qa_link_fuzzy=s.qa_link_fuzzy,
            qa_link_neighbours=s.qa_link_neighbours,
        )
    if system == "graph":
        params.update(qa_hops=s.qa_hops)
    return params


@pytest.mark.parametrize("system", EVERY_SYSTEM)
def test_each_system_logs_exactly_the_params_that_decide_its_answers(ctx, system):
    logged = AskStage(system).params(ctx, PipelineState(question="Which press?"))
    assert logged == {**expected_params(ctx.settings, system), "question": "Which press?"}


class TextRun:
    """A run that keeps the text artifacts it is given, by path, in order."""

    def __init__(self):
        self.texts: dict[str, str] = {}

    def text(self, content, artifact_path):
        self.texts[artifact_path] = content


@pytest.mark.parametrize("system", EVERY_SYSTEM)
def test_each_system_logs_the_reader_prompt_and_a_plan_system_its_own_prompts(system):
    run = TextRun()
    log_prompts(run, system)
    expected = {"prompts/qa_reader.txt": reader.PROMPT}
    if system in PLANNED:
        expected.update(
            {
                "prompts/qa_planner.txt": planner.PROMPT + planner.RETRY,
                "prompts/qa_read_check.txt": READ_CHECK,
                "prompts/qa_cypher.txt": CYPHER[system],
            }
        )
    assert list(run.texts.items()) == list(expected.items())


def test_kg_qa_reads_the_k_nearest_chunks_of_the_vector_system_and_searches_no_deeper():
    """R117 pins it before sources take a depth: `kg qa` gives its source the depth k."""
    store = FakeStore(chunks=[chunk(f"c{i}", None) for i in range(8)], nearest=[f"c{i}" for i in range(8)])
    reader, _ = citing_reader()
    parts = QAParts(
        settings=Settings(qa_top_k=3),
        store=store,
        embedder=FixedEmbedder(),
        llm=ScriptedLLM(lambda prompt, schema: None),
        reader=reader,
        plan=None,
        units=FakeUnitStore(),
    )
    answer = SYSTEMS["vector"].build(parts).answer("Q1", "Which press?")
    assert answer.retrieved == ["c0", "c1", "c2"] and store.nearest_calls == [3]


def test_a_source_ranks_as_deep_as_it_is_asked_so_a_budget_beyond_k_is_not_cut_short():
    store = FakeStore(chunks=[chunk(f"c{i}", None) for i in range(12)], nearest=[f"c{i}" for i in range(12)])
    parts = SourceParts(
        settings=Settings(qa_top_k=3), store=store, embedder=FixedEmbedder(), plan=None, units=FakeUnitStore()
    )
    ranked, trace = check_source("vector")(parts, 10).ranked("Which press?")
    assert len(ranked) == 10 and store.nearest_calls == [10] and trace is None


def test_kg_qa_asks_the_systems_before_r117_unless_told_otherwise():
    # a registered system never adds a paid run to `kg qa` by itself
    assert DEFAULT_SYSTEMS == ("graph", "vector", "records_vector") and "graph_retrieval" in SYSTEMS
    assert cli.QA_SYSTEMS.default == list(DEFAULT_SYSTEMS)


def test_only_a_reading_system_has_a_source_for_the_retrieval_benchmark():
    s = Settings()
    assert source_params(s, "graph_retrieval") == {
        "system": "graph_retrieval", "embed_model": s.embed_model, "qa_link_fuzzy": s.qa_link_fuzzy,
        "qa_link_neighbours": s.qa_link_neighbours, "qa_hops": s.qa_hops,
    }  # fmt: skip
    assert source_params(s, "vector") == {"system": "vector", "embed_model": s.embed_model}
    with pytest.raises(ConfigurationError, match="choose from vector, graph_retrieval, hybrid"):
        check_source("graph")
    with pytest.raises(ConfigurationError, match="no chunk source"):
        RetrieveEvalStage("records_vector")
    with pytest.raises(ConfigurationError, match="unknown system"):
        RetrieveEvalStage("oracle")


CARD_LISTS = {
    "card_dense": ["card_dense"],
    "card_lexical": ["card_lexical"],
    "card_seeds": ["card_dense", "card_lexical", "graph_route"],
}


@pytest.mark.parametrize("rep", ["template", "summary"])
@pytest.mark.parametrize("kind", list(CARD_LISTS))
def test_the_card_systems_lists_are_fixed_whatever_the_hybrid_settings(kind, rep):
    """R123: the card systems of the A-against-B comparison are untuned; the hybrid settings (the sealed ones
    or an override) change the hybrid system's list, never theirs. Fusion's k and the depth are the seal's."""
    override = Settings(hybrid_retrievers=["chunk_dense"])
    for s in (Settings(), override):
        params = source_params(s, f"{kind}_{rep}")
        assert (params["hybrid_retrievers"], params["hybrid_cards"]) == (CARD_LISTS[kind], rep)
        assert (params["hybrid_rrf_k"], params["hybrid_depth"]) == (s.hybrid_rrf_k, s.hybrid_depth)
        assert params["representation_version"] == card_representation(s, rep).version
    assert source_params(override, "hybrid")["hybrid_retrievers"] == ["chunk_dense"]


def test_a_card_system_searches_only_the_cards_of_its_representation():
    s = Settings()
    state = IndexState(
        cards=1, card_versions=[card_representation(s, "summary").version], claims=0, claim_versions=[],
        embed_models=[s.embed_model], online=index_names(["summary"]),
    )  # fmt: skip
    units = FakeUnitStore(state=state)
    parts = SourceParts(settings=s, store=FakeStore(), embedder=FixedEmbedder(), plan=None, units=units)
    check_source("card_dense_summary")(parts, 20).ranked("Which press?")
    assert units.calls == [("nearest_cards", "summary", 20)]


def test_only_the_plan_systems_replay_frozen_plans(ctx, tmp_path):
    gold = tmp_path / "gold.json"
    gold.write_text("{}", encoding="utf-8")
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    for system in ("graph", "records_vector"):
        (earlier / f"answers_{system}.jsonl").write_text("", encoding="utf-8")
    state = PipelineState(gold=gold, frozen_plans=earlier)
    frozen = {s: QAStage(s).params(ctx, state)["frozen_plans"] for s in EVERY_SYSTEM}
    # the reading systems have no plans to freeze, so --plans leaves them as they are
    assert frozen == {
        "graph": earlier / "answers_graph.jsonl",
        "vector": None,
        "graph_retrieval": None,
        "hybrid": None,
        "records_vector": earlier / "answers_records_vector.jsonl",
    }
