"""Structured path: the refinement loop, the plan proposer, staging (JSON conversion, skipped files, the
wipe guard), and reconciliation of the real `data/` set against the gold expectations (needs Neo4j)."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from kgbuilder.core.errors import KgBuilderError
from kgbuilder.llm.refine import Critique, refine
from kgbuilder.structured.importer import construct_domain_graph
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.proposer import propose_plan
from kgbuilder.structured.staging import json_to_csv, stage_structured

from .fakes import ScriptedLLM
from .sample_plans import GOOD_PLAN, node

ROOT = Path(__file__).parent.parent
GOLD = Path(__file__).parent / "gold"


class Guess(BaseModel):
    n: int


def test_refine_feeds_issues_back_until_the_proposal_is_valid():
    feedbacks: list[str] = []

    def propose(feedback: str) -> Guess:
        feedbacks.append(feedback)
        return Guess(n=len(feedbacks))

    result = refine(propose, validate=lambda g: [] if g.n >= 3 else [f"{g.n} is too small"])
    assert result.accepted and result.value == Guess(n=3) and result.rounds == 3
    assert feedbacks[0] == "" and "1 is too small" in feedbacks[1] and '"n": 2' in feedbacks[2]
    assert [r.source for r in result.history] == ["code", "code", "none"]


def test_refine_asks_the_critic_only_about_mechanically_valid_proposals():
    seen_by_critic: list[int] = []
    counter = iter(range(1, 10))

    def critique(g: Guess) -> list[str]:
        seen_by_critic.append(g.n)
        return ["prefer a bigger number"] if g.n < 3 else []

    result = refine(
        lambda _: Guess(n=next(counter)),
        validate=lambda g: ["must be at least 2"] if g.n < 2 else [],
        critique=critique,
        max_rounds=5,
    )
    assert seen_by_critic == [2, 3] and result.accepted and result.rounds == 3


def test_refine_gives_up_after_max_rounds_and_keeps_the_open_issues():
    result = refine(lambda _: Guess(n=0), validate=lambda g: ["never good"], max_rounds=2)
    assert not result.accepted and result.rounds == 2 and result.open_issues == ["never good"]


def test_propose_plan_retries_an_invalid_plan(data_dir):
    bad = ConstructionPlan(nodes=[node("products.csv", "Product", "no_such_column")], relationships=[])
    plans = iter([bad, GOOD_PLAN])

    def script(prompt, schema):
        return Critique(verdict="valid", issues=[]) if schema is Critique else next(plans)

    llm = ScriptedLLM(script)
    result = propose_plan("goal", profile_directory(data_dir), llm, model="m")
    assert result.accepted and result.value == GOOD_PLAN and result.rounds == 2
    # the critic was consulted once: only for the plan that passed code validation
    assert [name for name, _ in llm.calls] == ["ConstructionPlan", "ConstructionPlan", "Critique"]


def test_json_is_flattened_to_csv(tmp_path):
    src = tmp_path / "x.json"
    records = [{"id": 1, "o": {"k": "v"}, "l": [1, 2]}, {"id": 2, "o": {"k": "w"}, "l": []}]
    src.write_text(json.dumps(records), encoding="utf-8")
    assert json_to_csv(src, tmp_path / "x.csv") == 2
    assert (tmp_path / "x.csv").read_text().splitlines()[0] == "id,o.k,l"


def test_staging_reports_what_it_skipped_and_ignores_its_own_output(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "t.csv").write_text("id\n1\n", encoding="utf-8")
    (data / "rows.ndjson").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    (data / "config.json").write_text('{"debug": true}', encoding="utf-8")
    (data / "broken.json").write_text("{nope", encoding="utf-8")
    staging = data / "out" / "staging"  # output inside the data dir: must not be re-ingested

    for _ in range(2):  # the second run wipes and rebuilds its own staging dir
        report = stage_structured(data, staging)
        assert sorted(report.tables) == ["rows.ndjson", "t.csv"]
        assert sorted(s.file for s in report.skipped) == ["broken.json", "config.json"]
    assert sorted(p.name for p in staging.glob("*.csv")) == ["rows.csv", "t.csv"]


def test_staging_refuses_to_wipe_a_foreign_directory(tmp_path):
    precious = tmp_path / "thesis"
    precious.mkdir()
    (precious / "chapter1.docx").write_text("months of work", encoding="utf-8")
    with pytest.raises(KgBuilderError, match="refusing to clear"):
        stage_structured(tmp_path, precious)
    assert (precious / "chapter1.docx").exists()


@pytest.mark.neo4j
def test_data_set_reconciles_with_gold_expectations(driver, tmp_path):
    plan = ConstructionPlan.model_validate_json((GOLD / "domain_plan.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLD / "domain_expectations.json").read_text(encoding="utf-8"))
    staged = stage_structured(ROOT / "data", tmp_path / "staging").staged_dir

    report = construct_domain_graph(driver, staged, plan)

    assert report.clean
    assert report.written("node") == sum(expected["nodes"].values())
    assert report.written("relationship") == sum(expected["relationships"].values())
    for label, count in expected["nodes"].items():
        found = driver.execute_query(f"MATCH (n:`{label}`) RETURN count(n) AS c")[0][0]["c"]
        assert found == count, label
    for pattern, count in expected["relationships"].items():
        source, rest = pattern.split("-", 1)
        rel_type, target = rest.split("->")
        query = f"MATCH (:`{source}`)-[r:`{rel_type}`]->(:`{target}`) RETURN count(r) AS c"
        assert driver.execute_query(query)[0][0]["c"] == count, pattern
