"""`kg extract --from-build` (R102, pipeline/stages.py ReplayExtractStage): an earlier build's claims written
again without the LLM, each verified again, a build of another text schema refused; the run is named
`extract`, so the build's logged counts and the audit's fidelity gate read it as any extract run.

The build is the graph audit's invented one (tests/test_audit.py: two reviews, four claims); its chunks are
written to Neo4j as `kg ingest-text` writes them.
"""

import json

import pytest

from kgbuilder.audit.inputs import read_corpus
from kgbuilder.config import Settings
from kgbuilder.core.errors import ProposalRejectedError
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.inputs import digest
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import TEXT_SCHEMA_FILE, PipelineContext, PipelineState
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.structured.profiler import DataProfile
from kgbuilder.text.lexical import write_lexical_graph

from .fakes import RecordingTracker
from .test_audit import CHUNKING, KETTLE
from .test_audit import _build as audit_build


def _graph(driver, tmp_path):
    """The invented build, its chunks in Neo4j, and an out folder holding the build's text schema."""
    build, data = audit_build(tmp_path)
    plan = ConstructionPlan.model_validate_json((build / "plan.json").read_text(encoding="utf-8"))
    profile = DataProfile.model_validate_json((build / "profile.json").read_text(encoding="utf-8"))
    corpus = read_corpus(data, build / "staging", plan, profile, CHUNKING)
    write_lexical_graph(driver, corpus.documents, corpus.chunks)
    out = tmp_path / "rebuild"
    out.mkdir()
    (out / TEXT_SCHEMA_FILE).write_bytes((build / TEXT_SCHEMA_FILE).read_bytes())
    settings = Settings(
        chunk_max_chars=CHUNKING[0], chunk_min_chars=CHUNKING[1], chunk_overlap_chars=CHUNKING[2]
    )
    tracker = RecordingTracker()
    return build, PipelineContext(settings=settings, driver=driver, out=out, tracker=tracker), tracker


@pytest.mark.neo4j
def test_a_builds_claims_are_written_again_reverified_without_an_llm(driver, tmp_path):
    build, ctx, tracker = _graph(driver, tmp_path)
    # one claim whose quote is not in its chunk: today's verify refuses it, the others are written
    with (build / "triples.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"subject": "lid", "subject_type": "Part", "predicate": "EXHIBITS",
                            "object": "rusty", "object_type": "Quality", "evidence": "The lid is rusty.",
                            "chunk_id": f"{KETTLE}#0"}) + "\n")  # fmt: skip
    state = run_stages(ctx, PipelineState(extract_source=build), [st.ReplayExtractStage()])
    run = tracker.run("extract")  # the extract run, as `kg extract` names it
    assert run.logged_params["source"] == build
    assert run.logged_params["triples_hash"] == digest(build / "triples.jsonl")
    m = run.logged_metrics
    assert (m["replayed"], m["facts"], m["rejected"]) == (5, 4, 1)
    assert [r.reason.value for r in state.extraction.rejected] == ["evidence_not_verbatim"]
    [row] = driver.execute_query("MATCH (o:Observation) RETURN count(o) AS n")[0]
    assert row["n"] == 4
    assert len((ctx.out / "triples.jsonl").read_text(encoding="utf-8").splitlines()) == 4


@pytest.mark.neo4j
def test_a_build_of_another_text_schema_is_refused(driver, tmp_path):
    build, ctx, _ = _graph(driver, tmp_path)
    schema = json.loads((ctx.out / TEXT_SCHEMA_FILE).read_text(encoding="utf-8"))
    schema["entity_types"][2]["description"] = "Another meaning."
    (ctx.out / TEXT_SCHEMA_FILE).write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ProposalRejectedError):
        run_stages(ctx, PipelineState(extract_source=build), [st.ReplayExtractStage()])
    [row] = driver.execute_query("MATCH (o:Observation) RETURN count(o) AS n")[0]
    assert row["n"] == 0  # nothing written
