"""The committed R226 records: the lighthouses and NTSB builds' claims and mentions, judged.

R226 judged `out/r155b_<ds>` with the suite of R110 (claim precision), R111 (claim recall against R225's
reader claims) and R105 (mention recall against R225's mention gold, pass precision). These tests pin the
records as the thesis reports them: each verdict file answers its sheet under the review rules, the
committed report is exactly what the scorer computes from them, and the k/n of every reported number. The
recall sheet is rebuilt from the committed claim sheet, sample and gold, so it cannot drift from its
inputs. No Neo4j, no LLM, no `out/` folder.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.validation.anchor_verdicts import load_verdicts
from kgbuilder.validation.assertion import load_assertion_gold
from kgbuilder.validation.claim_eval import ClaimSheet, claim_verdict_issues, score_claims
from kgbuilder.validation.claim_recall import RecallSheet, load_recall_verdicts, recall_sheet, score_recall
from kgbuilder.validation.mention_eval import MentionSheet, mention_evidence_issues, score_mentions
from kgbuilder.validation.sentences import SentenceSample

GOLD = Path(__file__).resolve().parent / "gold"
R226 = GOLD / "r226"
# (strict, content) precision of the extracted claims; there are no derived claims in either build
PRECISION = {"lighthouses": ((145, 160), (152, 160)), "ntsb": ((285, 293), (289, 293))}
# (overall, random strata, random strata within the schema) recall
RECALL = {"lighthouses": ((21, 130), (11, 73), (11, 43)), "ntsb": ((18, 106), (13, 74), (13, 68))}
# (exact recall, recall with the judged mapping, judged precision of the pass's sample)
MENTIONS = {"lighthouses": ((119, 167), (138, 167), (55, 57)), "ntsb": ((133, 184), (153, 184), (51, 57))}
# the lead's changes, by the rulings recorded in REFACTOR_PLAN R226
CLAIM_CHANGES = {"lighthouses": ["7f978309562f", "1d8ce1677db4"], "ntsb": ["c1f5cff30321"]}
RECALL_CHANGES = {"lighthouses": ["57694ec2437b#0", "79abd0261623#0", "df3310267d1b#0"], "ntsb": []}


def _kn(p) -> tuple[int, int]:
    return (p.k, p.n)


@pytest.mark.parametrize("dataset", sorted(PRECISION))
def test_the_claim_verdicts_answer_their_sheet_and_score_as_reported(dataset):
    sheet = ClaimSheet.model_validate_json((R226 / dataset / "claim_sheet.json").read_text("utf-8"))
    verdicts = load_verdicts(R226 / dataset / "claim_verdicts.json", sheet.to_judge())
    assert claim_verdict_issues(sheet, verdicts) == []
    assert [c.id for c in verdicts.changes] == CLAIM_CHANGES[dataset]
    scores = score_claims(sheet, verdicts)
    assert scores.model_dump(mode="json") == json.loads(
        (R226 / dataset / "claim_report.json").read_text("utf-8")
    )
    extracted = scores.by_origin["extracted"]
    assert (_kn(extracted.precision), _kn(extracted.content_precision)) == PRECISION[dataset]


@pytest.mark.parametrize("dataset", sorted(RECALL))
def test_the_recall_verdicts_answer_their_rebuilt_sheet_and_score_as_reported(dataset):
    sheet = RecallSheet.model_validate_json((R226 / dataset / "recall_sheet.json").read_text("utf-8"))
    claims = ClaimSheet.model_validate_json((R226 / dataset / "claim_sheet.json").read_text("utf-8"))
    sample = SentenceSample.model_validate_json(
        (GOLD / "r225" / f"{dataset}_assertion_sample.json").read_text("utf-8")
    )
    gold = load_assertion_gold(GOLD / "r225" / f"{dataset}_assertion_gold.json")
    assert recall_sheet(dataset, sample, gold, claims, sheet.claim_sheet).model_dump() == sheet.model_dump()
    verdicts = load_recall_verdicts(R226 / dataset / "recall_verdicts.json", sheet)
    assert [c.key for c in verdicts.changes] == RECALL_CHANGES[dataset]
    scores = score_recall(sheet, verdicts)
    assert scores.model_dump(mode="json") == json.loads(
        (R226 / dataset / "recall_report.json").read_text("utf-8")
    )
    assert (_kn(scores.recall), _kn(scores.recall_random), _kn(scores.recall_in_schema_random)) == RECALL[
        dataset
    ]


@pytest.mark.parametrize("dataset", sorted(MENTIONS))
def test_the_mention_verdicts_answer_their_sheet_and_score_as_reported(dataset):
    sheet = MentionSheet.model_validate_json((R226 / dataset / "mention_sheet.json").read_text("utf-8"))
    verdicts = load_verdicts(R226 / dataset / "mention_verdicts.json", sheet.to_judge())
    assert mention_evidence_issues(sheet, verdicts) == [] and verdicts.changes == []
    scores = score_mentions(sheet, verdicts)
    assert json.loads(scores.model_dump_json()) == json.loads(
        (R226 / dataset / "mention_report.json").read_text("utf-8")
    )
    assert (_kn(scores.recall_exact), _kn(scores.recall), _kn(scores.precision)) == MENTIONS[dataset]
