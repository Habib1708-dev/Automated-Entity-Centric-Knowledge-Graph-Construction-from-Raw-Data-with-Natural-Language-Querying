"""The judged anchor-graph criteria (R93): the blind sheets of C3, C4 and C6 with their code sides, the
verdict files and their review rules, the evidence check, and the scores code computes from verdicts.

The snapshot is the lamp-and-kettle one of tests/test_anchor.py, with a second kettle chunk in which Ana
Ruiz's kettle "switch" is linked to the lamp's switch (the planted cross-scope link), a kettle mention
named after the kettle's key but left unlinked (the planted label mismatch), and two Ana Ruiz nodes in two
documents (the planted split). The verdicts are invented to exercise each rule, not judged. The stage test
reuses the graph audit's invented build folder (tests/test_audit.py). No Neo4j, no LLM.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.anchor.judged import (
    check_evidence,
    evidence_issues,
    rates,
    rescore_identity,
    score_c3,
    score_c4,
    score_c6,
)
from kgbuilder.anchor.navigation import Arm
from kgbuilder.anchor.sheet_builder import build_sheets
from kgbuilder.audit.checks import CodeChecks, Flag, SplitGroup
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.anchor_stages import (
    AnchorEvalStage,
    AnchorSheetsStage,
    code_file,
    report_file,
    sheet_file,
)
from kgbuilder.resolution.identity_graph import Assignment
from kgbuilder.text.chunking import Chunk
from kgbuilder.validation.anchor_verdicts import (
    Change,
    JudgeHeader,
    Label,
    Verdict,
    VerdictFile,
    load_verdicts,
    review_sample,
    verdict_issues,
)
from kgbuilder.validation.gold import IdentityPair, MentionRef, Quote
from kgbuilder.validation.qa_gold import RecordEvidence
from kgbuilder.validation.target_gold import QuestionTargets, Target, TargetGold
from tests.fakes import RecordingTracker
from tests.test_anchor import K1, KETTLE, L1, LAMP, S1, S2, SNAPSHOT, STICKING, chunks, mention, qa
from tests.test_audit import KETTLE as AUDIT_KETTLE
from tests.test_audit import _snapshot as audit_build

ANA_K, ANA_L, KEY = "ind:ana-k", "ind:ana-l", "ind:k1"
LAMP_2, KETTLE_1 = "Ana Ruiz bought the lamp. Ruiz likes it.", "Ana Ruiz says the switch on her K1 stuck."


def person(mid: str, said: str, canonical: str) -> Assignment:
    return Assignment(
        mention=mid, said=said, kind="individual", canonical=canonical, name="Ana Ruiz", type="Person",
        reason="no_record",
    )  # fmt: skip


S = SNAPSHOT.model_copy(
    update={
        "chunks": [
            *SNAPSHOT.chunks,
            Chunk(chunk_id=f"{LAMP}#2", doc_id=LAMP, index=2, text=LAMP_2),
            Chunk(chunk_id=f"{KETTLE}#1", doc_id=KETTLE, index=1, text=KETTLE_1),
        ],
        "mentions": [
            *SNAPSHOT.mentions,
            mention("m7", KETTLE, "switch", 1),
            mention("m8", KETTLE, "Ana Ruiz", 1),
            mention("m9", LAMP, "Ana Ruiz", 2),
            mention("m10", KETTLE, "stuck", 1),
            mention("m11", KETTLE, "K1", 1),
            mention("m12", LAMP, "Ruiz", 2),
        ],
        "references": [
            *SNAPSHOT.references,
            Assignment(mention="m7", said="switch", kind="record", canonical=S1, name="Switch", type="Thing",
                       reason="name", score=100.0),
            person("m8", "Ana Ruiz", ANA_K),
            person("m9", "Ana Ruiz", ANA_L),
            Assignment(mention="m10", said="stuck", kind="concept", canonical=STICKING, name="sticking",
                       type="Thing", reason="same_name"),
            Assignment(mention="m11", said="K1", kind="individual", canonical=KEY, name="K1", type="Thing",
                       reason="no_record"),
            person("m12", "Ruiz", ANA_L),
        ],
    }
)  # fmt: skip
CHECKS = CodeChecks(
    provenance={},
    provenance_misses={},
    flags=[
        Flag(kind="cross_scope_link", item="m7", graph="", text=""),
        Flag(kind="label_mismatch", item="m11", graph="", text=""),
        Flag(kind="compound_name", item="o1 -> Product:L1", graph="", text=""),  # an attachment flag: no item
    ],
    flag_counts={},
    record_links=3,
    record_links_without_scope=0,
    non_end_attachments=0,
    key_in_sentence_attachments=0,
    splits=[SplitGroup(type="Person", name="ana ruiz", canonicals=2, documents=[KETTLE, LAMP])],
)
SHEETS = build_sheets(S, CHECKS, [L1, S1, STICKING, ANA_L], "t")
LINK_M7, LINK_M1, LINK_M11 = f"l:m7:{S1}", f"l:m1:{S1}", f"l:m11:{K1}"
FOREIGN, SCOPE_ONLY = f"p:{L1}|{KETTLE}#0", f"p:{S1}|{KETTLE}#1"


def code_of(criterion: str) -> dict:
    return {i.id: i for i in SHEETS.code[criterion].items}


# --- the sheets -----------------------------------------------------------------------------------------


def test_c3_asks_about_individual_and_concept_nodes_with_several_mentions_and_the_split_groups():
    merges = {i.id: [m.id for m in i.node.mentions] for i in SHEETS.c3.merges}
    assert merges == {f"m:{ANA_L}": ["m12", "m9"], f"m:{STICKING}": ["m10", "m3"]}  # records are C4's
    (split,) = SHEETS.c3.splits
    assert [n.id for n in split.nodes] == [ANA_K, ANA_L]
    assert split.nodes[1].mentions[1].sentence == "Ana Ruiz bought the lamp."
    assert {f"{LAMP}#0", f"{KETTLE}#1", f"{LAMP}#2"} <= SHEETS.c3.chunks.keys()


def test_c4_asks_the_same_question_of_links_and_of_unlinked_mentions_named_after_a_key():
    items = {i.id: i for i in SHEETS.c4.links}
    assert set(items) == {LINK_M1, f"l:m2:{S2}", LINK_M7, LINK_M11}
    assert [r.ref for r in items[LINK_M7].same_name] == [S2]  # the other "Switch", to name the right one
    assert items[LINK_M7].record.relations == [f"PART_OF -> {L1} (Desk Lamp)"]
    code = code_of("C4")
    assert (code[LINK_M7].kind, code[LINK_M7].flags) == ("link", ["cross_scope_link"])
    assert (code[LINK_M11].kind, code[LINK_M11].flags) == ("unlinked", ["label_mismatch"])


def test_the_judge_never_sees_a_flag_an_arm_or_whether_a_link_exists():
    for sheet in (SHEETS.c3, SHEETS.c4, SHEETS.c6):
        shown = sheet.model_dump_json()
        for hidden in ("cross_scope_link", "label_mismatch", "same_label_about", "scope_foreign", "unlinked",
                       "layered", "no_record"):  # fmt: skip
            assert hidden not in shown


def test_c6_is_a_census_of_the_pairs_either_arm_reaches_with_both_flags():
    code = code_of("C6")
    assert {i.id for i in SHEETS.c6.pairs} == set(code)
    # the planted claim leak: only arm B reaches the kettle chunk from the lamp
    assert code[FOREIGN].arms == ["layered"]
    assert code[FOREIGN].flags == ["same_label_about", "scope_foreign"]
    # the lamp's switch in the kettle chunk: another label's document, so only R87's scope rule fires
    assert (code[SCOPE_ONLY].arms, code[SCOPE_ONLY].flags) == (["anchor", "layered"], ["scope_foreign"])
    assert code[f"p:{L1}|{LAMP}#1"].flags == []
    start = next(i.start for i in SHEETS.c6.pairs if i.id == f"p:{ANA_L}|{LAMP}#2")
    assert [m.id for m in start.mentions] == ["m12", "m9"]  # no other chunk: its own mentions describe it


# --- verdict files ----------------------------------------------------------------------------------------


def verdicts(criterion: str, labels: dict[str, Label], **extra) -> VerdictFile:
    """A file with VALID for every item not in `labels`, reviewed as the rules demand."""
    ids = {"C3": code_of("C3"), "C4": code_of("C4"), "C6": code_of("C6")}[criterion]
    out = [Verdict(id=i, label=labels.get(i, Label.VALID), reason="r", evidence="e") for i in ids]
    file = VerdictFile(
        judge=JudgeHeader(
            model="m", date="d", snapshot_hash="h", sheet="s", sheet_hash="h", sheets_git_sha="g"
        ),
        criterion=criterion,
        verdicts=out,
        **extra,
    )
    must = [v.id for v in out if v.label is not Label.VALID]
    file.reviewed = sorted({*file.reviewed, *must, *review_sample(file.blind())})
    return file


def test_a_complete_reviewed_file_has_no_issues():
    file = verdicts("C4", {LINK_M7: Label.INCORRECT})
    assert verdict_issues(file, set(code_of("C4"))) == []


def test_missing_duplicate_and_unknown_ids_are_refused():
    file = verdicts("C4", {})
    missing = file.verdicts[0].id
    file.verdicts = [
        *file.verdicts[1:],
        file.verdicts[1],
        Verdict(id="x", label=Label.VALID, reason="r", evidence="e"),
    ]
    issues = verdict_issues(file, set(code_of("C4")))
    assert f"no verdict for {missing}" in issues
    assert any(i.startswith("duplicate verdict") for i in issues) and "verdict for unknown item x" in issues


def test_every_incorrect_and_the_seeded_valid_sample_must_be_reviewed():
    file = verdicts("C4", {LINK_M7: Label.INCORRECT})
    file.reviewed = []
    issues = verdict_issues(file, set(code_of("C4")))
    assert f"{LINK_M7} (INCORRECT) not reviewed" in issues
    # 10 % of the three blind VALID verdicts, rounded up: one, the same on every run
    assert (
        review_sample(file.blind()) == review_sample(file.blind()) and len(review_sample(file.blind())) == 1
    )
    assert any(i.startswith("VALID sample item") for i in issues)


def test_a_change_keeps_the_blind_label_and_must_end_at_the_final_one():
    change = Change(id=LINK_M7, before=Label.VALID, after=Label.INCORRECT, reason="the text means the kettle")
    file = verdicts("C4", {LINK_M7: Label.INCORRECT}, changes=[change])
    assert file.blind()[LINK_M7] is Label.VALID and verdict_issues(file, set(code_of("C4"))) == []
    file.changes = [change.model_copy(update={"after": Label.AMBIGUOUS})]
    assert f"change of {LINK_M7} does not end at its final label" in verdict_issues(file, set(code_of("C4")))


def test_a_verdict_needs_a_reason_and_a_quote_and_only_incorrect_names_outliers():
    with pytest.raises(ValueError, match="evidence"):
        Verdict(id="a", label=Label.VALID, reason="r")
    Verdict(id="a", label=Label.UNJUDGEABLE, reason="the chunk is empty")
    with pytest.raises(ValueError, match="reason"):
        Verdict(id="a", label=Label.VALID, reason=" ", evidence="e")
    with pytest.raises(ValueError, match="INCORRECT"):
        Verdict(id="a", label=Label.VALID, reason="r", evidence="e", outliers=["m1"])


def test_load_verdicts_refuses_a_file_that_does_not_fit_its_sheet(tmp_path):
    path = tmp_path / "v.json"
    path.write_text(verdicts("C4", {}).model_dump_json(), encoding="utf-8")
    assert len(load_verdicts(path, set(code_of("C4"))).verdicts) == 4
    with pytest.raises(EvaluationError, match="unknown item"):
        load_verdicts(path, set(code_of("C4")) - {LINK_M1})


def test_a_quote_must_stand_in_its_item_and_outliers_must_be_its_mentions():
    file = verdicts("C3", {})
    by_id = {v.id: v for v in file.verdicts}
    by_id[f"m:{STICKING}"].evidence = "The Desk Lamp switch sticks."
    by_id[f"m:{ANA_L}"].evidence = "The kettle switch works."  # another item's chunk
    by_id[f"m:{ANA_L}"].label, by_id[f"m:{ANA_L}"].outliers = Label.INCORRECT, ["m1"]
    issues = evidence_issues(SHEETS.c3, file)
    assert f"m:{ANA_L}: the evidence is not in the item" in issues
    assert f"m:{ANA_L}: names ['m1'], which the item does not show" in issues
    assert not any(i.startswith(f"m:{STICKING}") for i in issues)
    with pytest.raises(EvaluationError):
        check_evidence(SHEETS.c3, file)


# --- scores ------------------------------------------------------------------------------------------------


def test_rates_leave_ambiguous_and_unjudgeable_out_and_give_strict_and_worst_case():
    r = rates([Label.VALID, Label.VALID_ALTERNATIVE, Label.INCORRECT, Label.AMBIGUOUS, Label.UNJUDGEABLE])
    assert (r.accepted.k, r.accepted.n, r.strict.k, r.worst_case.n) == (2, 3, 1, 5)
    assert r.counts["AMBIGUOUS"] == 1 and r.accepted.low is not None


def test_c4_fails_on_a_confirmed_cross_scope_link_and_reports_unlinked_mentions_apart():
    result = score_c4(verdicts("C4", {LINK_M7: Label.INCORRECT}), SHEETS.code["C4"], 0.95)
    assert (result.links.accepted.k, result.links.accepted.n) == (2, 3)
    assert result.cross_scope_confirmed == [LINK_M7] and not result.hard_passed
    assert (result.unlinked.accepted.k, result.unlinked.accepted.n) == (1, 1)  # a missed link
    flags = {f.flag: f for f in result.flags}
    assert (flags["cross_scope_link"].confirmed, flags["label_mismatch"].refuted) == (1, 1)
    assert score_c4(verdicts("C4", {}), SHEETS.code["C4"], 0.95).hard_passed


def test_c4_names_an_incorrect_link_no_flag_raised():
    result = score_c4(verdicts("C4", {LINK_M1: Label.INCORRECT}), SHEETS.code["C4"], 0.5)
    assert result.incorrect_unflagged == [LINK_M1] and result.hard_passed  # 2/3 >= 0.5, no cross-scope


def test_c3_counts_record_merges_from_c4_and_fails_on_a_wrong_individual_merge():
    pairs = rescore_identity(S, [])
    c4_bad = verdicts("C4", {LINK_M7: Label.INCORRECT})
    c3 = verdicts("C3", {"s:Person:ana ruiz": Label.INCORRECT})
    c3.final()["s:Person:ana ruiz"].together = [[ANA_K, ANA_L]]
    result = score_c3(c3, SHEETS.code["C3"], c4_bad, SHEETS.code["C4"], pairs)
    assert result.record_wrong_merges == [LINK_M7] and not result.hard_passed
    assert result.wrong_splits == ["s:Person:ana ruiz"] and result.splits.accepted.k == 0
    assert score_c3(
        verdicts("C3", {}), SHEETS.code["C3"], verdicts("C4", {}), SHEETS.code["C4"], pairs
    ).hard_passed
    wrong = verdicts("C3", {f"m:{ANA_L}": Label.INCORRECT})
    result = score_c3(wrong, SHEETS.code["C3"], verdicts("C4", {}), SHEETS.code["C4"], pairs)
    assert result.wrong_merges == {"individual": [f"m:{ANA_L}"], "concept": []} and not result.hard_passed
    concept = verdicts("C3", {f"m:{STICKING}": Label.INCORRECT})
    assert score_c3(concept, SHEETS.code["C3"], verdicts("C4", {}), SHEETS.code["C4"], pairs).hard_passed


def test_c6_scores_each_arm_on_the_pairs_it_reaches():
    file = verdicts("C6", {FOREIGN: Label.INCORRECT, SCOPE_ONLY: Label.AMBIGUOUS})
    result = score_c6(file, SHEETS.code["C6"], 0.95)
    anchor, layered = (
        result.purity["anchor"]["record_individual"],
        result.purity["layered"]["record_individual"],
    )
    assert anchor.accepted.k == anchor.accepted.n and result.hard_passed["anchor"]
    assert layered.accepted.n == anchor.accepted.n + 1 and not result.hard_passed["layered"]
    assert [s.start for s in result.starts_below["layered"]] == [L1] and result.starts_below["anchor"] == []
    assert (result.only_layered.accepted.k, result.only_layered.accepted.n) == (0, 1)
    flags = {f.flag: f for f in result.flags}
    assert (
        flags["scope_foreign"].flagged,
        flags["scope_foreign"].confirmed,
        flags["scope_foreign"].open,
    ) == (2, 1, 1)
    assert result.purity["anchor"]["concept"].accepted.n == len(
        [i for i in code_of("C6").values() if i.kind == "concept"]
    )


def test_r75_pairs_are_rescored_on_the_snapshot():
    quote, ana = [Quote(doc_id=LAMP, quote="Ana Ruiz")], MentionRef(doc_id=LAMP, names=["Ana Ruiz"])
    pairs = [
        IdentityPair(a=ana, b=MentionRef(doc_id=LAMP, names=["Ruiz"]), same=True, evidence=quote),
        IdentityPair(a=ana, b=MentionRef(doc_id=KETTLE, names=["Ana Ruiz"]), same=True, evidence=quote),
    ]  # fmt: skip
    score = rescore_identity(S, pairs)
    assert (score.scored, score.joined, score.recall) == (2, 1, 0.5)  # the split Ana Ruiz is a missed join


# --- the stage ------------------------------------------------------------------------------------------


def _eval_reports(tmp_path: Path):
    """The audit's invented build with both arms' anchor reports, as `kg anchor-eval` writes them."""
    s, out, data = audit_build(tmp_path)
    logged = tmp_path / "logged.json"
    logged.write_text(
        LoggedCounts(
            dataset="t", build="b", git_sha="abc", runs={}, counts=snapshot_counts(s)
        ).model_dump_json(),
        encoding="utf-8",
    )
    qa_file, targets_file = tmp_path / "qa.json", tmp_path / "targets.json"
    question = {
        "id": "Q1",
        "chunks": chunks(f"{AUDIT_KETTLE}#0"),
        "question": "Is the Birch Kettle lid stiff?",
    }
    qa_file.write_text(qa(question).model_dump_json(), encoding="utf-8")
    kettle = Target(
        name="Birch Kettle", records=[RecordEvidence(file="products.csv", row={"product_id": "P-2"})]
    )
    targets_file.write_text(
        TargetGold(dataset="t", qa_gold=qa_file.as_posix(), written_by="test", date="2026-10-06",
                   questions=[QuestionTargets(id="Q1", targets=[kettle])]).model_dump_json(),
        encoding="utf-8",
    )  # fmt: skip
    ctx = PipelineContext(
        settings=Settings(), driver=None, out=tmp_path / "anchor", tracker=RecordingTracker()
    )
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_targets=targets_file)
    run_stages(ctx, state, [AnchorEvalStage(Arm.ANCHOR), AnchorEvalStage(Arm.LAYERED)])
    reports = tuple(tmp_path / "anchor" / report_file(arm) for arm in Arm)
    return out, data, logged, reports


def test_the_stage_writes_the_sheets_and_their_code_sides_and_logs_their_sizes(tmp_path):
    out, data, logged, reports = _eval_reports(tmp_path)
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "sheets", tracker=tracker)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_reports=reports,
                          anchor_dataset="t")  # fmt: skip
    state = run_stages(ctx, state, [AnchorSheetsStage()])
    run = tracker.run("anchor_sheets")
    assert run.logged_params["dataset"] == "t" and "layered_report_hash" in run.logged_params
    assert run.logged_metrics["c6_pairs"] == len(state.anchor_sheets.c6.pairs) > 0
    assert run.logged_metrics["c4_link_items"] == 6  # every record link of the invented build
    for criterion in ("C3", "C4", "C6"):
        assert (
            json.loads((tmp_path / "sheets" / sheet_file(criterion)).read_text(encoding="utf-8"))["criterion"]
            == criterion
        )
        assert (tmp_path / "sheets" / code_file(criterion)).is_file()


def test_the_stage_writes_no_sheet_for_a_snapshot_that_fails_the_gate(tmp_path):
    out, data, logged, reports = _eval_reports(tmp_path)
    counts = json.loads(logged.read_text(encoding="utf-8"))
    counts["counts"]["ingest_text.chunks"] += 1
    logged.write_text(json.dumps(counts), encoding="utf-8")
    ctx = PipelineContext(
        settings=Settings(), driver=None, out=tmp_path / "sheets", tracker=RecordingTracker()
    )
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_reports=reports,
                          anchor_dataset="t")  # fmt: skip
    with pytest.raises(EvaluationError, match="C0 failed"):
        run_stages(ctx, state, [AnchorSheetsStage()])
    assert not (tmp_path / "sheets" / sheet_file("C3")).exists()
