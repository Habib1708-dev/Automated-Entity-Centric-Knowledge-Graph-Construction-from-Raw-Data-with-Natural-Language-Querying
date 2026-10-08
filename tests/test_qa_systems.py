"""How a QA system is chosen by name (R116), without Neo4j: the params and prompts each system logs, which
systems replay frozen plans, and the refusal of an unknown name. The expected values are written from the
prompt constants and the settings, not from the registry, so they pin what the runs logged before R116.
Answering end to end is tested in test_query.py (fake store) and test_query_graph.py (Neo4j)."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.graph.connection import open_driver
from kgbuilder.llm.base import prompt_version
from kgbuilder.pipeline import PipelineContext, PipelineState
from kgbuilder.pipeline.qa_stages import AskStage, QAStage
from kgbuilder.pipeline.qa_systems import log_prompts
from kgbuilder.query import exact, planner, read_check, reader

READ_CHECK = read_check.PROMPT + read_check.CLAIM_RULE + read_check.CANDIDATE
CYPHER = {"graph": exact.PROMPT + exact.RETRY, "records_vector": exact.RECORDS_PROMPT + exact.RETRY}


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
    if system != "vector":
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


@pytest.mark.parametrize("system", ["graph", "vector", "records_vector"])
def test_each_system_logs_exactly_the_params_that_decide_its_answers(ctx, system):
    logged = AskStage(system).params(ctx, PipelineState(question="Which press?"))
    assert logged == {**expected_params(ctx.settings, system), "question": "Which press?"}


class TextRun:
    """A run that keeps the text artifacts it is given, by path, in order."""

    def __init__(self):
        self.texts: dict[str, str] = {}

    def text(self, content, artifact_path):
        self.texts[artifact_path] = content


@pytest.mark.parametrize("system", ["graph", "vector", "records_vector"])
def test_each_system_logs_the_reader_prompt_and_a_plan_system_its_own_prompts(system):
    run = TextRun()
    log_prompts(run, system)
    expected = {"prompts/qa_reader.txt": reader.PROMPT}
    if system != "vector":
        expected.update(
            {
                "prompts/qa_planner.txt": planner.PROMPT + planner.RETRY,
                "prompts/qa_read_check.txt": READ_CHECK,
                "prompts/qa_cypher.txt": CYPHER[system],
            }
        )
    assert list(run.texts.items()) == list(expected.items())


def test_only_the_plan_systems_replay_frozen_plans(ctx, tmp_path):
    gold = tmp_path / "gold.json"
    gold.write_text("{}", encoding="utf-8")
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    for system in ("graph", "records_vector"):
        (earlier / f"answers_{system}.jsonl").write_text("", encoding="utf-8")
    state = PipelineState(gold=gold, frozen_plans=earlier)
    frozen = {s: QAStage(s).params(ctx, state)["frozen_plans"] for s in ("graph", "vector", "records_vector")}
    # the vector baseline has no plans to freeze, so --plans leaves it as it is
    assert frozen == {
        "graph": earlier / "answers_graph.jsonl",
        "vector": None,
        "records_vector": earlier / "answers_records_vector.jsonl",
    }
