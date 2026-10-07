"""Recall of a build's claims against a reader's (R111, validation/claim_recall.py and `kg claim-recall`): the
sheet that joins a sample, its gold and a claim sheet by chunk (a chunk without stored claims shows none), the
verdict rules (matched or a cause, the schema type of a miss, the review of every miss and of the seeded
matches, changes that end at their final outcome), the scores (overall, random strata, per stratum, within
the schema, misses per cause, the stored fields of the matched claims by code) and the stage on invented
files (params, metrics, artifacts, its refusal of a file that breaks a rule). The committed R111 sheets
rebuild from their inputs, and their verdicts answer them and score as reported. No Neo4j, no LLM.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.claim_stages import RECALL_REPORT, RECALL_SHEET, ClaimRecallStage
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.schema import TextSchema
from kgbuilder.text.subject_graph import ObservationRow
from kgbuilder.validation.assertion import AssertionGold, GoldClaim, GoldSentence, load_assertion_gold
from kgbuilder.validation.claim_eval import ClaimSheet, claim_item, claim_sheet
from kgbuilder.validation.claim_recall import (
    RecallChange,
    RecallSheet,
    RecallVerdict,
    RecallVerdicts,
    load_recall_verdicts,
    recall_issues,
    recall_sheet,
    review_sample,
    score_recall,
)
from kgbuilder.validation.coverage import Cause
from kgbuilder.validation.judge import JudgeMeta
from kgbuilder.validation.sentences import SampledSentence, SentenceSample
from tests.fakes import RecordingTracker

LOG, QUIET = "notes/night_log.md#0", "notes/quiet_log.md#0"
CHUNKS = [
    Chunk(
        chunk_id=LOG,
        doc_id="notes/night_log.md",
        index=0,
        context="Night log",
        text="The shutter motor did not stall. The dome may leak if the hatch is open. Varga called.",
    ),
    Chunk(
        chunk_id=QUIET, doc_id="notes/quiet_log.md", index=0, context="Quiet log", text="Nothing happened."
    ),
]
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
# the stored claims: the stall stored affirmed (the text denies it), the leak stored possible without its
# condition
STALL = ObservationRow(
    id="c1",
    predicate="SHOWS",
    subject="m-motor",
    object="m-stall",
    chunk_id=LOG,
    evidence="The shutter motor did not stall.",
    subject_name="motor",
    object_name="stall",
)
LEAK = ObservationRow(
    id="c2",
    predicate="SHOWS",
    subject="m-dome",
    object="m-leak",
    chunk_id=LOG,
    evidence="The dome may leak if the hatch is open.",
    subject_name="dome",
    object_name="leak",
    modality="possible",
    hedge="may",
)
CLAIMS = claim_sheet(
    "test", "build", [claim_item(r, "extracted", TYPES) for r in (STALL, LEAK)], CHUNKS, SCHEMA
)
SAMPLE = SentenceSample(
    seed=1,
    size=3,
    population=4,
    sentences=[
        SampledSentence(
            id="s1", doc_id="notes/night_log.md", chunk_id=LOG, text="The shutter motor did not stall."
        ),
        SampledSentence(
            id="s2", doc_id="notes/night_log.md", chunk_id=LOG, text="The dome may leak if the hatch is open."
        ),
        SampledSentence(id="s3", doc_id="notes/quiet_log.md", chunk_id=QUIET, text="Nothing happened."),
    ],
)
GOLD = AssertionGold(
    labeller={"model": "m"},
    sentences=[
        GoldSentence(
            id="s1",
            stratum="r68",
            claims=[GoldClaim(claim="the shutter motor did not stall", truth="negated", modality="actual")],
        ),
        GoldSentence(
            id="s2",
            stratum="cue",
            claims=[
                GoldClaim(
                    claim="the dome may leak if the hatch is open",
                    truth="affirmed",
                    modality="conditional",
                    condition="if the hatch is open",
                ),
                GoldClaim(claim="the hatch can be opened", truth="affirmed", modality="actual"),
            ],
        ),
        GoldSentence(
            id="s3",
            stratum="r68",
            claims=[GoldClaim(claim="nothing happened", truth="affirmed", modality="actual")],
        ),
    ],
)


def _sheet() -> RecallSheet:
    return recall_sheet("test", SAMPLE, GOLD, CLAIMS, "claims.json")


def _verdicts() -> list[RecallVerdict]:
    return [
        RecallVerdict(
            key="s1#0", claim="the shutter motor did not stall", matched=["c1"], reason="the stall"
        ),
        RecallVerdict(
            key="s2#0", claim="the dome may leak if the hatch is open", matched=["c2"], reason="leak"
        ),
        RecallVerdict(
            key="s2#1",
            claim="the hatch can be opened",
            cause=Cause.EXTRACTION,
            schema_type="SHOWS",
            reason="nothing stored about the hatch",
        ),
        RecallVerdict(
            key="s3#0", claim="nothing happened", cause=Cause.NO_SCHEMA_TYPE, reason="no fact type"
        ),
    ]


def _file(verdicts: list[RecallVerdict], reviewed: list[str], changes=()) -> RecallVerdicts:
    meta = JudgeMeta(model="m", date="d")
    return RecallVerdicts(judge=meta, sheet="s", verdicts=verdicts, reviewed=reviewed, changes=list(changes))


def _reviewed(verdicts: list[RecallVerdict]) -> list[str]:
    """Every miss and the seeded sample of matches: what the review rules require."""
    blind = {v.key: v.outcome for v in verdicts}
    return sorted({k for k, o in blind.items() if o != "matched"} | set(review_sample(blind)))


def test_the_sheet_joins_sample_gold_and_stored_claims_by_chunk():
    sheet = _sheet()
    assert sheet.keys() == ["s1#0", "s2#0", "s2#1", "s3#0"]
    s1, s2, s3 = sheet.sentences
    assert (
        [c.id for c in s1.stored] == ["c1", "c2"] and s2.stratum == "cue" and s1.chunk.context == "Night log"
    )
    # the quiet chunk stores no claim: the claim sheet carries no text for it, and nothing is shown
    assert (s3.chunk, s3.stored) == (None, [])
    reordered = GOLD.model_copy(update={"sentences": list(reversed(GOLD.sentences))})
    with pytest.raises(EvaluationError):
        recall_sheet("test", SAMPLE, reordered, CLAIMS, "claims.json")


def test_a_verdict_is_matched_or_a_cause_and_a_miss_names_its_schema_type():
    with pytest.raises(ValueError, match="exactly one"):
        RecallVerdict(
            key="k", claim="c", matched=["c1"], cause=Cause.EXTRACTION, schema_type="SHOWS", reason="r"
        )
    with pytest.raises(ValueError, match="names its schema type"):
        RecallVerdict(key="k", claim="c", cause=Cause.EXTRACTION, reason="r")
    with pytest.raises(ValueError, match="names its schema type"):
        RecallVerdict(key="k", claim="c", cause=Cause.NO_SCHEMA_TYPE, schema_type="SHOWS", reason="r")


def test_the_rules_want_every_claim_once_as_written_and_every_miss_reviewed():
    sheet, verdicts = _sheet(), _verdicts()
    assert recall_issues(sheet, _file(verdicts, _reviewed(verdicts))) == []
    wrong = [
        verdicts[0].model_copy(update={"claim": "the motor stalled"}),
        verdicts[1].model_copy(update={"matched": ["c9"]}),
        verdicts[2],
    ]
    issues = recall_issues(sheet, _file(wrong, ["s2#1"]))
    assert "no verdict for s3#0" in issues
    assert "s1#0: the claim differs from the gold's" in issues
    assert "s2#0: c9 is no stored claim of the sentence's chunk" in issues
    # a miss the lead did not review, and a change that does not end where the verdict now stands
    unreviewed = recall_issues(sheet, _file(verdicts, ["s2#1"]))
    assert "s3#0 (no_schema_type) not reviewed" in unreviewed
    moved = RecallChange(key="s2#1", before="matched", after="role", reason="r")
    assert "change of s2#1 does not end at its final outcome" in recall_issues(
        sheet, _file(verdicts, _reviewed(verdicts), [moved])
    )


def test_scores_recall_by_stratum_within_the_schema_and_the_stored_fields_by_code():
    sheet, verdicts = _sheet(), _verdicts()
    s = score_recall(sheet, _file(verdicts, _reviewed(verdicts)))
    assert (s.recall.k, s.recall.n) == (2, 4)
    # the random strata (r68): s1's claim matched, s3's missed
    assert (s.recall_random.k, s.recall_random.n) == (1, 2)
    assert (s.recall_by_stratum["cue"].k, s.recall_by_stratum["cue"].n) == (1, 2)
    # within the schema: s3's claim, which no fact type could hold, leaves the denominator
    assert (s.recall_in_schema.k, s.recall_in_schema.n) == (2, 3)
    assert (s.misses["extraction"], s.misses["no_schema_type"], s.misses_random["extraction"]) == (1, 1, 0)
    # c1 is stored affirmed where the reader read a denial (its modality, actual, agrees); c2 is stored
    # possible, without a condition, where the reader read a conditional claim
    truth, modality, condition = (s.fields_exact[f] for f in ("truth", "modality", "condition"))
    assert (truth.k, truth.n, modality.k, modality.n, condition.k, condition.n) == (1, 2, 1, 2, 0, 1)


def test_the_stage_writes_the_sheet_and_scores_the_judges_file(tmp_path):
    files = {"claims": CLAIMS, "gold": GOLD, "sample": SAMPLE}
    for name, model in files.items():
        (tmp_path / f"{name}.json").write_text(model.model_dump_json(), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "out", tracker=tracker)
    state = PipelineState(
        claim_sheet=tmp_path / "claims.json",
        assertion_gold=tmp_path / "gold.json",
        sample=tmp_path / "sample.json",
        anchor_dataset="test",
    )
    run_stages(ctx, state, [ClaimRecallStage()])
    run = tracker.run("claim_recall")
    m = run.logged_metrics
    assert (m["sentences"], m["gold_claims"], m["stored_claims_shown"], m["sentences_without_stored"]) == (
        3,
        4,
        4,
        1,
    )
    assert "recall" not in m and {"claim_sheet_hash", "gold_hash", "sample_hash"} <= set(run.logged_params)
    assert RecallSheet.model_validate_json(
        (tmp_path / "out" / RECALL_SHEET).read_text(encoding="utf-8")
    ).keys()
    verdicts = _verdicts()
    path = tmp_path / "verdicts.json"
    path.write_text(_file(verdicts, _reviewed(verdicts)).model_dump_json(), encoding="utf-8")
    run_stages(ctx, replace(state, recall_verdicts=path), [ClaimRecallStage()])
    m = tracker.runs[-1].logged_metrics
    assert (m["recall"], m["recall_random"], m["miss_extraction"], m["exact_truth"]) == (0.5, 0.5, 1, 0.5)
    report = json.loads((tmp_path / "out" / RECALL_REPORT).read_text(encoding="utf-8"))
    assert report["recall_in_schema"]["n"] == 3
    path.write_text(_file(verdicts, ["s2#1"]).model_dump_json(), encoding="utf-8")  # s3#0 not reviewed
    with pytest.raises(EvaluationError):
        run_stages(ctx, replace(state, recall_verdicts=path), [ClaimRecallStage()])


# What R111 reported (REFACTOR_PLAN.md, R111 part b): recall overall and on the random strata, and within the
# schema on the random strata, as (k, n)
R111 = Path("tests/gold/r111")
REPORTED = {
    "furniture": ((74, 117), (42, 72), (42, 61)),
    "heldout": ((27, 100), (19, 67), (19, 35)),
    "generality": ((23, 108), (12, 66), (12, 27)),
}


@pytest.mark.parametrize("dataset", sorted(REPORTED))
def test_r111_verdicts_answer_their_sheets_and_score_as_reported(dataset):
    sheet = RecallSheet.model_validate_json(
        (R111 / dataset / "recall_sheet.json").read_text(encoding="utf-8")
    )
    # the sheet is the join of the committed sample, gold and claim sheet, rebuilt here
    claims = ClaimSheet.model_validate_json(
        Path(f"tests/gold/r110/{dataset}/claim_sheet.json").read_text(encoding="utf-8")
    )
    sample = SentenceSample.model_validate_json(
        Path(f"tests/gold/r77/{dataset}_assertion_sample.json").read_text(encoding="utf-8")
    )
    gold = load_assertion_gold(Path(f"tests/gold/r77/{dataset}_assertion_gold.json"))
    rebuilt = recall_sheet(dataset, sample, gold, claims, sheet.claim_sheet)
    assert rebuilt.model_dump() == sheet.model_dump()
    scores = score_recall(sheet, load_recall_verdicts(R111 / dataset / "recall_verdicts.json", sheet))
    logged = json.loads((R111 / dataset / "recall_report.json").read_text(encoding="utf-8"))
    assert scores.model_dump(mode="json") == logged
    overall, random_strata, in_schema = REPORTED[dataset]
    assert (scores.recall.k, scores.recall.n) == overall
    assert (scores.recall_random.k, scores.recall_random.n) == random_strata
    assert (scores.recall_in_schema_random.k, scores.recall_in_schema_random.n) == in_schema
