"""The claims' evaluation (R110, validation/claim_eval.py and `kg claim-eval`): the sheet of every stored
claim (extracted and derived apart, each id once, every stored field, the chunks and the schema's
descriptions), the claim rules on a verdict file (a fault on every INCORRECT verdict, known faults only, every
quote in its chunk), the scores (strict and content precision per origin, faults, per relation), and the stage
on the graph audit's invented build (params, metrics, artifacts, its refusal of a file that breaks a rule).
The committed R110 sheets and verdicts (tests/gold/r110) must answer each other under the review rules and
score as reported. No Neo4j, no LLM.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.claim_stages import CLAIM_REPORT, CLAIM_SHEET, ClaimEvalStage
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.schema import TextSchema
from kgbuilder.text.subject_graph import ObservationRow
from kgbuilder.validation.anchor_verdicts import Label, Verdict, VerdictFile, load_verdicts, review_sample
from kgbuilder.validation.claim_eval import (
    ClaimSheet,
    claim_item,
    claim_sheet,
    claim_verdict_issues,
    score_claims,
)
from tests.fakes import RecordingTracker
from tests.test_audit import _logged, _schema
from tests.test_audit import _snapshot as audit_snapshot

LOG = "notes/night_log.md#0"
CHUNK = Chunk(
    chunk_id=LOG,
    doc_id="notes/night_log.md",
    index=0,
    text="The shutter motor did not stall. The dome may leak if the hatch is open.",
    context="Night log",
)
SCHEMA = TextSchema.model_validate(
    {
        "entity_types": [
            {"name": "Part", "description": "A piece of the observatory.", "identity": "concept"},
            {"name": "State", "description": "A condition.", "identity": "concept"},
        ],
        "fact_types": [
            {"predicate": "SHOWS", "subject_type": "Part", "object_type": "State", "description": "A state."},
        ],
    }
)
TYPES = {"m-motor": "Part", "m-stall": "State", "m-dome": "Part", "m-leak": "State"}


def _row(id_: str, subject: str, obj: str, evidence: str, **fields) -> ObservationRow:
    return ObservationRow(
        id=id_,
        predicate="SHOWS",
        subject=subject,
        object=obj,
        chunk_id=LOG,
        evidence=evidence,
        subject_name=subject.removeprefix("m-"),
        object_name=obj.removeprefix("m-"),
        **fields,
    )


STALL = _row("c1", "m-motor", "m-stall", "The shutter motor did not stall.", truth="negated", negation="not")
LEAK = _row(
    "c2", "m-dome", "m-leak", "The dome may leak if the hatch is open.", modality="conditional",
    hedge="may", condition="if the hatch is open",
)  # fmt: skip


def _sheet() -> ClaimSheet:
    items = [claim_item(STALL, "extracted", TYPES), claim_item(LEAK, "extracted", TYPES)]
    # a derived claim whose id an extracted one has: one node in the graph, shown once and counted
    items.append(claim_item(STALL, "derived", TYPES))
    return claim_sheet("test", "build", items, [CHUNK], SCHEMA)


def header() -> dict:
    return {
        "model": "m",
        "date": "d",
        "snapshot_hash": "x",
        "sheet": "s",
        "sheet_hash": "h",
        "sheets_git_sha": "g",
    }


def _file(*verdicts: Verdict) -> VerdictFile:
    return VerdictFile(judge=header(), criterion="claims", verdicts=list(verdicts), reviewed=[])


def test_the_sheet_shows_every_stored_field_each_id_once_and_the_schema():
    sheet = _sheet()
    assert [(c.id, c.origin) for c in sheet.claims] == [("c1", "extracted"), ("c2", "extracted")]
    assert sheet.merged == 1 and sheet.to_judge() == {"c1", "c2"}
    stall, leak = sheet.claims
    assert (stall.subject, stall.subject_type, stall.truth, stall.negation) == (
        "motor",
        "Part",
        "negated",
        "not",
    )
    assert (leak.modality, leak.hedge, leak.condition) == ("conditional", "may", "if the hatch is open")
    assert sheet.relations == {"Part -[SHOWS]-> State": "A state."}
    assert sheet.types["Part"] == "A piece of the observatory." and sheet.chunks[LOG].context == "Night log"


def test_the_sheet_shows_every_type_pair_a_relation_is_declared_for():
    # one predicate declared for a piece and for the whole thing: the judge must see both pairs (R110 showed
    # only the last, and judges read claims about the whole thing as outside the schema)
    whole = {"name": "Dome", "description": "The whole observatory dome.", "identity": "concept"}
    schema = SCHEMA.model_copy(
        update={
            "entity_types": [*SCHEMA.entity_types, type(SCHEMA.entity_types[0]).model_validate(whole)],
            "fact_types": [
                *SCHEMA.fact_types,
                type(SCHEMA.fact_types[0]).model_validate(
                    {
                        "predicate": "SHOWS",
                        "subject_type": "Dome",
                        "object_type": "State",
                        "description": "Its state.",
                    }
                ),
            ],
        }
    )
    sheet = claim_sheet("test", "build", [claim_item(STALL, "extracted", TYPES)], [CHUNK], schema)
    assert sheet.relations == {"Part -[SHOWS]-> State": "A state.", "Dome -[SHOWS]-> State": "Its state."}


def test_the_claim_rules_want_a_known_fault_on_every_incorrect_verdict_and_quotes_in_the_chunk():
    sheet = _sheet()
    good = _file(
        Verdict(id="c1", label=Label.VALID, reason="denied stall", evidence="did not stall"),
        Verdict(id="c2", label=Label.INCORRECT, reason="tone", evidence="may leak", faults=["polarity"]),
    )
    assert claim_verdict_issues(sheet, good) == []
    bad = _file(
        Verdict(id="c1", label=Label.INCORRECT, reason="?", evidence="did not stall"),
        Verdict(id="c2", label=Label.INCORRECT, reason="?", evidence="not shown", faults=["spelling"]),
    )
    assert claim_verdict_issues(sheet, bad) == [
        "c1: INCORRECT without a fault",
        "c2: unknown fault 'spelling'",
        "the quote of c2 is not in its chunk",
    ]
    with pytest.raises(ValueError, match="INCORRECT verdicts only"):
        Verdict(id="c1", label=Label.VALID, reason="r", evidence="e", faults=["time"])


def test_scores_split_content_faults_from_field_faults_per_origin_and_relation():
    sheet = _sheet()
    verdicts = _file(
        Verdict(
            id="c1", label=Label.INCORRECT, reason="denial lost", evidence="did not stall", faults=["truth"]
        ),
        Verdict(id="c2", label=Label.INCORRECT, reason="tone", evidence="may leak", faults=["polarity"]),
    )
    scores = score_claims(sheet, verdicts).by_origin["extracted"]
    # neither claim keeps every field; one triple (c2's) is what the text says
    assert (scores.precision.k, scores.precision.n) == (0, 2)
    assert (scores.content_precision.k, scores.content_precision.n) == (1, 2)
    assert scores.faults["truth"] == 1 and scores.faults["polarity"] == 1 and scores.faults["time"] == 0
    assert scores.incorrect == ["c1", "c2"]
    unclear = _file(
        Verdict(id="c1", label=Label.VALID, reason="r", evidence="did not stall"),
        Verdict(id="c2", label=Label.AMBIGUOUS, reason="r", evidence="may leak"),
    )
    all_scores = score_claims(sheet, unclear)
    extracted = all_scores.by_origin["extracted"]
    # AMBIGUOUS leaves the denominator and is counted; no derived claim is left on the sheet
    assert (extracted.precision.k, extracted.precision.n, extracted.ambiguous) == (1, 1, 1)
    assert all_scores.by_origin["derived"].precision.n == 0 and all_scores.by_relation["SHOWS"].n == 1


def test_the_stage_writes_the_sheet_of_a_build_and_scores_the_judges_file(tmp_path):
    snapshot, out, data = audit_snapshot(tmp_path)
    logged = _logged(tmp_path, snapshot)
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "eval", tracker=tracker)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_dataset="test")
    run_stages(ctx, state, [ClaimEvalStage()])
    run = tracker.run("claim_eval")
    m = run.logged_metrics
    # the invented build: four extracted EXHIBITS claims, four PART_OF claims derived from the documents
    assert (m["claims"], m["extracted_claims"], m["derived_claims"], m["chunks"]) == (8, 4, 4, 2)
    assert "extracted_precision" not in m and {"build", "logged_hash", "dataset"} <= set(run.logged_params)
    sheet = ClaimSheet.model_validate_json((tmp_path / "eval" / CLAIM_SHEET).read_text(encoding="utf-8"))
    fact_types = _schema(out).fact_types
    assert sheet.relations.keys() == {
        f"{f.subject_type} -[{f.predicate}]-> {f.object_type}" for f in fact_types
    }
    lamp_lid = next(c for c in sheet.claims if c.origin == "extracted" and c.evidence == "The lid is loose.")
    assert (lamp_lid.subject, lamp_lid.subject_type, lamp_lid.object_type) == ("lid", "Part", "Quality")
    # the judge's file: one extracted claim wrong, every verdict quoted from its chunk, reviewed as required
    verdicts = [
        Verdict(id=c.id, label=Label.VALID, reason="stated", evidence=c.evidence)
        if c.id != lamp_lid.id
        else Verdict(id=c.id, label=Label.INCORRECT, reason="r", evidence=c.evidence, faults=["polarity"])
        for c in sheet.claims
    ]
    blind = {v.id: v.label for v in verdicts}
    file = VerdictFile(
        judge=header(), criterion="claims", verdicts=verdicts, reviewed=[lamp_lid.id, *review_sample(blind)]
    )
    path = tmp_path / "verdicts.json"
    path.write_text(file.model_dump_json(), encoding="utf-8")
    run_stages(ctx, replace(state, claim_verdicts=path), [ClaimEvalStage()])
    m = tracker.runs[-1].logged_metrics
    assert (m["extracted_precision"], m["extracted_content_precision"], m["derived_precision"]) == (
        0.75,
        1.0,
        1.0,
    )
    assert m["extracted_fault_polarity"] == 1 and "verdicts_hash" in tracker.runs[-1].logged_params
    report = json.loads((tmp_path / "eval" / CLAIM_REPORT).read_text(encoding="utf-8"))
    assert report["by_origin"]["extracted"]["incorrect"] == [lamp_lid.id]
    # a quote from outside the claim's chunk is refused, and nothing is scored
    unquoted = [
        v.model_copy(update={"evidence": "not in the text"}) if v.id == lamp_lid.id else v for v in verdicts
    ]
    path.write_text(file.model_copy(update={"verdicts": unquoted}).model_dump_json(), encoding="utf-8")
    with pytest.raises(EvaluationError):
        run_stages(ctx, replace(state, claim_verdicts=path), [ClaimEvalStage()])


# What R110 reported for the three builds (REFACTOR_PLAN.md, R110 part b): strict and content precision per
# origin, as (k, n)
R110 = Path("tests/gold/r110")
REPORTED = {
    "furniture": {"extracted": ((470, 530), (490, 530)), "derived": ((138, 154), (140, 154))},
    "heldout": {"extracted": ((480, 530), (520, 530)), "derived": ((38, 135), (38, 135))},
    "generality": {"extracted": ((201, 216), (207, 216)), "derived": ((0, 0), (0, 0))},
}


@pytest.mark.parametrize("dataset", sorted(REPORTED))
def test_r110_verdicts_answer_their_sheets_and_score_as_reported(dataset):
    sheet = ClaimSheet.model_validate_json((R110 / dataset / "claim_sheet.json").read_text(encoding="utf-8"))
    verdicts = load_verdicts(R110 / dataset / "claim_verdicts.json", sheet.to_judge())
    assert claim_verdict_issues(sheet, verdicts) == []
    scores = score_claims(sheet, verdicts)
    # the committed report is the one `kg claim-eval --verdicts` wrote (MLflow claim_eval runs at 76cc57f)
    logged = json.loads((R110 / dataset / "claim_report.json").read_text(encoding="utf-8"))
    assert scores.model_dump(mode="json") == logged
    for origin, (strict, content) in REPORTED[dataset].items():
        o = scores.by_origin[origin]
        assert (o.precision.k, o.precision.n) == strict
        assert (o.content_precision.k, o.content_precision.n) == content
