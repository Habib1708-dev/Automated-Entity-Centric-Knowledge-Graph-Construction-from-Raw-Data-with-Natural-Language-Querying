"""The mention evaluation (R102, validation/mention_eval.py and `kg mention-eval`): recall of the R101 gold
(exact hits, near-name candidates for the judged mapping, misses), the seeded precision sample of the pass's
mentions, the scores with and without verdicts, the evidence check, and the stage on the graph audit's
invented build with a pass file (params, metrics, artifacts, and its refusal of a quote outside its item).
The committed R102 sheets and verdicts (tests/gold/r102) must answer each other under the review rules and
score as reported. No Neo4j, no LLM.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from kgbuilder.audit import build_snapshot
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.core.identity import mention_id
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.mention_stages import MENTION_REPORT, MENTION_SHEET, MentionEvalStage
from kgbuilder.validation.anchor_verdicts import Label, Verdict, VerdictFile, load_verdicts
from kgbuilder.validation.mention_eval import (
    PRECISION_SAMPLE,
    MentionSheet,
    PrecisionItem,
    mention_evidence_issues,
    precision_sample,
    recall_items,
    score_mentions,
)
from kgbuilder.validation.mention_gold import GoldMention, MentionGold, SentenceMentions
from kgbuilder.validation.sentences import SampledSentence, SentenceSample
from tests.fakes import RecordingTracker
from tests.test_audit import CHUNKING, KETTLE, LAMP, _logged
from tests.test_audit import _snapshot as audit_snapshot

LOG = "notes/night_log.md"
S1 = SampledSentence(id="s1", doc_id=LOG, chunk_id=f"{LOG}#0", text="The shutter motor of North Dome sticks.")
SAMPLE = SentenceSample(seed=1, size=1, population=1, sentences=[S1])
GOLD = MentionGold(
    dataset="test", written_by="t", date="2026-10-07", rules="r", sample="s",
    sentences=[SentenceMentions(id="s1", doc_id=LOG, text=S1.text, mentions=[
        GoldMention(name="shutter motor", kind="kind"),
        GoldMention(name="North Dome", kind="particular"),
        GoldMention(name="sticks", kind="kind"),
    ])],
)  # fmt: skip


def header() -> dict:
    return {
        "model": "m",
        "date": "d",
        "snapshot_hash": "x",
        "sheet": "s",
        "sheet_hash": "h",
        "sheets_git_sha": "g",
    }


def test_recall_is_a_hit_a_near_name_for_the_judge_or_a_miss():
    items = {i.name: i for i in recall_items(GOLD, SAMPLE, {f"{LOG}#0": ["North dome", "motor"]})}
    assert items["North Dome"].status == "hit"  # the same name after norm
    assert (items["shutter motor"].status, items["shutter motor"].candidates) == ("candidate", ["motor"])
    assert items["sticks"].status == "miss"
    assert items["shutter motor"].id == "s1|shutter motor"


def test_the_precision_sample_is_seeded_and_bounded():
    found = [
        PrecisionItem(id=f"m{i:03}", name="x", type="Kind", mention_class="kind", chunk_id="c", text="x")
        for i in range(PRECISION_SAMPLE + 15)
    ]
    first, second = precision_sample(found), precision_sample(list(reversed(found)))
    assert len(first) == PRECISION_SAMPLE and first == second  # the order given does not matter
    assert precision_sample(found[:3]) == found[:3]  # fewer than the sample size: every one


def test_scores_with_the_judged_mapping_and_precision():
    sheet = MentionSheet(
        dataset="test",
        build="b",
        recall=recall_items(GOLD, SAMPLE, {f"{LOG}#0": ["North Dome", "motor"]}),
        precision=[
            PrecisionItem(id="p1", name="shutter motor", type="Kind", mention_class="kind", chunk_id="c",
                          text=S1.text),
            PrecisionItem(id="p2", name="of", type="Kind", mention_class="kind", chunk_id="c", text=S1.text),
        ],
    )  # fmt: skip
    assert sheet.to_judge() == {"s1|shutter motor", "p1", "p2"}
    without = score_mentions(sheet, None)
    assert (without.recall_exact.k, without.recall_exact.n, without.recall, without.precision) == (
        1,
        3,
        None,
        None,
    )
    verdicts = VerdictFile(
        judge=header(), criterion="mentions",
        verdicts=[
            Verdict(id="s1|shutter motor", label=Label.VALID, reason="motor is the shutter motor",
                    evidence="shutter motor"),
            Verdict(id="p1", label=Label.VALID, reason="a piece", evidence="shutter motor"),
            Verdict(id="p2", label=Label.INCORRECT, reason="a function word", evidence="of North Dome"),
        ],
    )  # fmt: skip
    scores = score_mentions(sheet, verdicts)
    assert (scores.recall.k, scores.precision.k, scores.precision.n) == (2, 1, 2)
    assert scores.precision_incorrect == ["p2"] and scores.recall_by_class["particular"].k == 1
    assert mention_evidence_issues(sheet, verdicts) == []
    bad = verdicts.model_copy(update={"verdicts": [*verdicts.verdicts[:2], verdicts.verdicts[2].model_copy(
        update={"evidence": "not shown"})]})  # fmt: skip
    assert mention_evidence_issues(sheet, bad) == ["the quote of p2 is not in its item"]


def _with_gold(tmp_path):
    """The audit's invented build with one pass mention, and a gold of two sampled sentences."""
    _, out, data = audit_snapshot(tmp_path)
    crack = mention_id("Kind", "crack", LAMP)
    (out / "mentions.jsonl").write_text(
        json.dumps({"chunk_id": f"{LAMP}#0", "name": "crack", "type": "Kind"}), encoding="utf-8"
    )
    resolved = json.loads((out / "resolve.json").read_text(encoding="utf-8"))
    resolved["assignments"].append({"mention": crack, "said": "crack", "kind": "concept", "canonical": "c9",
                                    "name": "crack", "type": "Kind", "reason": "same_name"})  # fmt: skip
    (out / "resolve.json").write_text(json.dumps(resolved), encoding="utf-8")
    sentences = [
        SampledSentence(id="a", doc_id=LAMP, chunk_id=f"{LAMP}#0", text="The shade is cracked."),
        SampledSentence(id="b", doc_id=KETTLE, chunk_id=f"{KETTLE}#0", text="The lid hinge is stiff."),
    ]
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "test_sample.json").write_text(
        SentenceSample(seed=1, size=2, population=2, sentences=sentences).model_dump_json(), encoding="utf-8"
    )
    gold = MentionGold(dataset="test", written_by="t", date="d", rules="r", sample="s", sentences=[
        SentenceMentions(id="a", doc_id=LAMP, text=sentences[0].text, mentions=[
            GoldMention(name="shade", kind="kind"), GoldMention(name="cracked", kind="kind")]),
        SentenceMentions(id="b", doc_id=KETTLE, text=sentences[1].text, mentions=[
            GoldMention(name="hinge", kind="kind")]),
    ])  # fmt: skip
    (gold_dir / "test_mentions.json").write_text(gold.model_dump_json(), encoding="utf-8")
    logged = _logged(tmp_path, build_snapshot(out, data, CHUNKING))
    return out, data, gold_dir, logged


def test_the_stage_scores_a_build_and_its_pass_against_the_gold(tmp_path):
    out, data, gold_dir, logged = _with_gold(tmp_path)
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "eval", tracker=tracker)
    state = PipelineState(
        audit_source=out, data_dir=data, audit_logged=logged, anchor_dataset="test", mention_gold_dir=gold_dir
    )
    run_stages(ctx, state, [MentionEvalStage()])
    run = tracker.run("mention_eval")
    m = run.logged_metrics
    # "shade" and "cracked" are mentions of their chunk; "hinge" has only a near name there, "lid hinge"
    assert (m["gold_mentions"], m["recall_hits"], m["recall_candidates"], m["pass_mentions"]) == (3, 2, 1, 1)
    assert m["pass_per_chunk_p90"] == 1 and "precision" not in m
    sheet = MentionSheet.model_validate_json((tmp_path / "eval" / MENTION_SHEET).read_text(encoding="utf-8"))
    assert [i.name for i in sheet.precision] == ["crack"] and sheet.precision[0].mention_class == "kind"
    assert {"gold_hash", "sample_hash", "precision_seed"} <= set(run.logged_params)
    # with the judge's file: the mapping and the precision, every quote checked
    verdicts = VerdictFile(judge=header(), criterion="mentions", verdicts=[
        Verdict(id="b|hinge", label=Label.VALID, reason="the hinge", evidence="The lid hinge is stiff."),
        Verdict(id=sheet.precision[0].id, label=Label.VALID, reason="a fault", evidence="cracked"),
    ], reviewed=["b|hinge", sheet.precision[0].id])  # fmt: skip
    path = tmp_path / "verdicts.json"
    path.write_text(verdicts.model_dump_json(), encoding="utf-8")
    run_stages(ctx, replace(state, mention_verdicts=path), [MentionEvalStage()])
    m = tracker.runs[-1].logged_metrics
    assert (m["recall"], m["precision"]) == (1.0, 1.0)
    report = json.loads((tmp_path / "eval" / MENTION_REPORT).read_text(encoding="utf-8"))
    assert report["recall"]["k"] == 3
    unquoted = verdicts.verdicts[1].model_copy(update={"evidence": "not shown"})
    path.write_text(
        verdicts.model_copy(update={"verdicts": [verdicts.verdicts[0], unquoted]}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(EvaluationError):
        run_stages(ctx, replace(state, mention_verdicts=path), [MentionEvalStage()])


# --- the committed results of R102 (the r77d builds and the rebuilds, against the R101 gold) ---------------

R102 = Path(__file__).resolve().parent / "gold" / "r102"
# (exact recall, recall with the judged mapping, judged precision) as k/n; the r77d builds have no pass
REPORTED = {
    ("furniture", "r77d"): ((23, 75), (39, 75), None),
    ("furniture", "r102"): ((57, 75), (70, 75), (45, 57)),
    ("heldout", "r77d"): ((32, 125), (45, 125), None),
    ("heldout", "r102"): ((94, 125), (111, 125), (51, 55)),
    ("generality", "r77d"): ((37, 109), (49, 109), None),
    ("generality", "r102"): ((80, 109), (97, 109), (41, 58)),
}


@pytest.mark.parametrize(("dataset", "build"), sorted(REPORTED))
def test_r102_mention_verdicts_answer_their_sheets_and_score_as_reported(dataset, build):
    sheet = MentionSheet.model_validate_json(
        (R102 / dataset / f"mentions_{build}_sheet.json").read_text(encoding="utf-8")
    )
    verdicts = load_verdicts(R102 / dataset / f"mentions_{build}_verdicts.json", sheet.to_judge())
    assert mention_evidence_issues(sheet, verdicts) == [] and verdicts.changes == []
    scores = score_mentions(sheet, verdicts)
    exact, mapped, precision = REPORTED[(dataset, build)]
    assert (scores.recall_exact.k, scores.recall_exact.n) == exact
    assert (scores.recall.k, scores.recall.n) == mapped
    assert (None if scores.precision is None else (scores.precision.k, scores.precision.n)) == precision
    report = json.loads((R102 / dataset / f"mentions_{build}_report.json").read_text(encoding="utf-8"))
    assert report == json.loads(scores.model_dump_json())  # the committed report is these scores
