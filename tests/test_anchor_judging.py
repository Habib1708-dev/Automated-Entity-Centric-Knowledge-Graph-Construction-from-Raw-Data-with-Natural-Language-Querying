"""The judged anchor-graph criteria (R93): the blind sheets of C3, C4 and C6 with their code sides, the
verdict files and their review rules, the evidence check, the scores code computes from verdicts, and the
report and stage that log them on the reviewed and the blind labels.

The snapshot is the lamp-and-kettle one of tests/test_anchor.py, with a second kettle chunk in which Ana
Ruiz's kettle "switch" is linked to the lamp's switch (the planted cross-scope link), a kettle mention
named after the kettle's key but left unlinked (the planted label mismatch), and two Ana Ruiz nodes in two
documents (the planted split). The verdicts are invented to exercise each rule, not judged. The stage test
reuses the graph audit's invented build folder (tests/test_audit.py). The committed sheets of the three builds
(tests/gold/r93) must load, match their code sides and name the committed inputs they were built from, and
each committed verdict file must answer its sheet under the review rules, with every quote in its item. R94's
committed results (fix A replayed offline) must show only the four cross-product links changed, carry R93's
verdicts unchanged, score as reported, and pair with R92's vector rankings as reported. R95a's (tier 1 of
record linking) must unlink only what a retired rule had linked, carry R94's verdicts with only the two new
items judged, and score and pair as reported. R95b's (the LLM's choice among near misses, one paid replay)
must change only by a verified choice or a retired rule, judge only the new items, and score as reported.
R96's (the chooser's revised prompt) must differ from R95b in one decision only, and score as reported.
R99's (candidates outside a scope, with the individuals replayed) must carry every earlier verdict, judge
only its eight new items, and score as reported, held-out's standing C3 failure included.
R100's (the evidence-based adjudicator of individuals) must carry R99's verdicts, judge only the changed
items, make no wrong join, and score as reported.
R102's (the three rebuilds with the r77d claims and R97-R101) must carry R100's verdicts for byte-identical
items, have every other one reviewed or blind, and score as reported: no wrong join of individuals, and the
new wrong splits are the pass's same-named individuals.
R103's (the rebuilds with re-extracted claims and R97-R106) must carry R102's verdicts for byte-identical
items, keep every lead review without a change, and score as reported: held-out's wrong join of two
complaints' cars, its two record links, and furniture's "dimmer" join that R75's identity gold keeps apart.
R107's (R103's graphs resolved again, a stated kind no record fits a `Kind` concept) must carry R103's
verdicts for byte-identical items, keep every lead review without a change, and score as reported: the wrong
join of the two cars gone, held-out's two record links still failing C3, and one wrong split fewer.
No Neo4j, no LLM.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.anchor.compare import ArmComparison, compare_arms
from kgbuilder.anchor.judged import (
    check_evidence,
    evidence_issues,
    item_texts,
    rates,
    rescore_identity,
    score_c3,
    score_c4,
    score_c6,
)
from kgbuilder.anchor.judged_report import JudgedReport, blind_view, impact, score_all
from kgbuilder.anchor.navigation import Arm
from kgbuilder.anchor.report import AnchorReport
from kgbuilder.anchor.sheet_builder import build_sheets
from kgbuilder.anchor.sheets import C3Sheet, C4Sheet, C6Sheet, CodeSide
from kgbuilder.anchor.targets import PlacedTarget
from kgbuilder.anchor.vector import vector_reach
from kgbuilder.audit.checks import CodeChecks, Flag, SplitGroup
from kgbuilder.audit.fidelity import LoggedCounts, snapshot_counts
from kgbuilder.audit.reidentify import ReidentifyReport
from kgbuilder.audit.relink import RelinkReport
from kgbuilder.config import Settings
from kgbuilder.core.errors import EvaluationError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.anchor_stages import AnchorEvalStage, report_file
from kgbuilder.pipeline.inputs import digest
from kgbuilder.pipeline.judging_stages import (
    JUDGED_FILE,
    AnchorJudgedStage,
    AnchorSheetsStage,
    code_file,
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
from kgbuilder.validation.qa_gold import RecordEvidence, load_qa_gold
from kgbuilder.validation.target_gold import QuestionTargets, Target, TargetGold, load_target_gold
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


# --- the committed sheets of R93 part a ------------------------------------------------------------------

GOLD = Path(__file__).resolve().parent / "gold"
SHEET_RUNS = json.loads((GOLD / "r93" / "runs.json").read_text(encoding="utf-8"))["runs"]
SHEET_MODELS = {"c3": C3Sheet, "c4": C4Sheet, "c6": C6Sheet}
# the direction's denominators (R93): mention-to-record links per dataset, and the C6 census
LINKS = {"furniture": 50, "heldout": 76, "generality": 40}
PAIRS = {"furniture": 140, "heldout": 125, "generality": 90}


@pytest.mark.parametrize("run", SHEET_RUNS, ids=lambda r: r["dataset"])
def test_each_committed_sheet_loads_matches_its_code_side_and_names_its_inputs(run):
    ds, base = run["dataset"], GOLD / "r93" / run["dataset"]
    assert {name: digest(base / name) for name in run["files"]} == run["files"]
    assert digest(GOLD / "r87" / f"{ds}_logged.json") == run["logged_hash"]
    assert digest(GOLD / "r90" / ds / "anchor_anchor.json") == run["anchor_report_hash"]
    assert digest(GOLD / "r90" / ds / "anchor_layered.json") == run["layered_report_hash"]
    assert run["git_dirty_files"] in ("", ".claude/settings.json")  # the code was exactly `git_sha`
    ids = {}
    for name, model in SHEET_MODELS.items():
        sheet = model.model_validate_json((base / f"{name}_sheet.json").read_text(encoding="utf-8"))
        code = CodeSide.model_validate_json((base / f"{name}_code.json").read_text(encoding="utf-8"))
        assert sheet.snapshot_hash == run["snapshot_hash"] and sheet.dataset == ds
        ids[name] = {i.id for i in code.items}
        assert ids[name] == set(item_texts(sheet))  # every item has its code side, and only those
    assert sum(i.startswith("l:") for i in ids["c4"]) >= LINKS[ds] and len(ids["c6"]) == PAIRS[ds]
    c4 = CodeSide.model_validate_json((base / "c4_code.json").read_text(encoding="utf-8"))
    assert sum(i.kind == "link" for i in c4.items) == LINKS[ds]


# --- the committed verdicts of R93 part b ----------------------------------------------------------------

JUDGE = "claude-opus-5-5"


@pytest.mark.parametrize("run", SHEET_RUNS, ids=lambda r: r["dataset"])
@pytest.mark.parametrize("name", list(SHEET_MODELS))
def test_each_committed_verdict_file_answers_its_sheet_under_the_review_rules(run, name):
    base = GOLD / "r93" / run["dataset"]
    code = CodeSide.model_validate_json((base / f"{name}_code.json").read_text(encoding="utf-8"))
    verdicts = load_verdicts(base / f"{name}_verdicts.json", {i.id for i in code.items})
    sheet = SHEET_MODELS[name].model_validate_json((base / f"{name}_sheet.json").read_text(encoding="utf-8"))
    check_evidence(sheet, verdicts)  # every quote stands in what its item showed
    header = verdicts.judge
    assert (header.model, header.snapshot_hash) == (JUDGE, run["snapshot_hash"])
    assert header.sheet_hash == digest(base / f"{name}_sheet.json") == run["files"][f"{name}_sheet.json"]


# --- the judged report and its stage (R93 part c) ----------------------------------------------------------


def test_the_blind_view_scores_the_labels_before_the_lead_changed_them():
    change = Change(id=LINK_M7, before=Label.INCORRECT, after=Label.VALID, reason="r")
    file = verdicts("C4", {}, changes=[change])
    blind = blind_view(file)
    assert {v.id: v.label for v in blind.verdicts}[LINK_M7] is Label.INCORRECT and blind.changes == []
    assert score_c4(blind, SHEETS.code["C4"], 0.95).cross_scope_confirmed == [LINK_M7]
    assert score_c4(file, SHEETS.code["C4"], 0.95).hard_passed


def test_impact_names_the_questions_whose_targets_hold_a_failed_node():
    files = {
        "C3": verdicts("C3", {}),
        "C4": verdicts("C4", {LINK_M7: Label.INCORRECT}),
        "C6": verdicts("C6", {}),
    }
    placed = {"Q1": [PlacedTarget(name="lamp switch", aliases=[], nodes=[S1], missing=[])],
              "Q2": [PlacedTarget(name="kettle", aliases=[], nodes=[K1], missing=[])]}  # fmt: skip
    assert impact(files, SHEETS.code, placed) == {LINK_M7: ["Q1"]}


def test_the_report_logs_both_label_sets_and_the_hard_rules():
    files = {
        "C3": verdicts("C3", {}),
        "C4": verdicts("C4", {LINK_M7: Label.INCORRECT}),
        "C6": verdicts("C6", {}),
    }
    pairs = rescore_identity(S, [])
    scores = score_all(files, SHEETS.code, pairs, min_link_precision=0.95, min_purity=0.95)
    report = JudgedReport(dataset="t", judge_model="m", final=scores, blind=scores, impact={})
    metrics = report.metrics()
    assert metrics["c4_hard_passed"] == 0.0 and metrics["c4_precision_n"] == 3.0
    assert metrics["c4_flag_cross_scope_link_confirmed"] == 1.0 and metrics["c3_hard_passed"] == 0.0
    assert metrics["blind_c4_precision"] == metrics["c4_precision"]
    assert metrics["c6_anchor_purity"] == 1.0 and "blind_c6_layered_hard_passed" in metrics


def _judge_everything(folder: Path) -> None:
    """Verdict files for the stage test: every item VALID, quoting the first words its item shows."""
    for criterion in ("C3", "C4", "C6"):
        model = SHEET_MODELS[criterion.lower()]
        sheet = model.model_validate_json((folder / sheet_file(criterion)).read_text(encoding="utf-8"))
        texts = item_texts(sheet)
        quoted = [Verdict(id=i, label=Label.VALID, reason="r", evidence=t[0][:12]) for i, t in texts.items()]
        file = VerdictFile(
            judge=JudgeHeader(model="m", date="d", snapshot_hash=sheet.snapshot_hash, sheet="s",
                              sheet_hash=digest(folder / sheet_file(criterion)), sheets_git_sha="g"),
            criterion=criterion,
            verdicts=quoted,
        )  # fmt: skip
        file.reviewed = review_sample(file.blind())
        (folder / f"{criterion.lower()}_verdicts.json").write_text(file.model_dump_json(), encoding="utf-8")


def test_the_judged_stage_scores_the_committed_verdicts_and_refuses_another_snapshot(tmp_path):
    out, data, logged, reports = _eval_reports(tmp_path)
    folder = tmp_path / "sheets"
    ctx = PipelineContext(settings=Settings(), driver=None, out=folder, tracker=RecordingTracker())
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_reports=reports,
                          anchor_dataset="t")  # fmt: skip
    run_stages(ctx, state, [AnchorSheetsStage()])
    _judge_everything(folder)
    gold = tmp_path / "identity.json"
    gold.write_text(json.dumps({"identity_pairs": []}), encoding="utf-8")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "judged", tracker=tracker)
    state = PipelineState(audit_source=out, data_dir=data, audit_logged=logged, anchor_judged_dir=folder,
                          identity_gold=gold, anchor_placements=reports[0])  # fmt: skip
    state = run_stages(ctx, state, [AnchorJudgedStage()])
    run = tracker.run("anchor_judged")
    assert run.logged_params["anchor_min_purity"] == 0.95 and run.logged_params["judge_model"] == "m"
    assert "c4_verdicts_hash" in run.logged_params
    assert run.logged_metrics["c4_precision"] == 1.0 and run.logged_metrics["c4_hard_passed"] == 1.0
    assert (tmp_path / "judged" / JUDGED_FILE).is_file() and state.anchor_judged.dataset == "t"
    sheet = json.loads((folder / sheet_file("C6")).read_text(encoding="utf-8"))
    sheet["snapshot_hash"] = "another"
    (folder / sheet_file("C6")).write_text(json.dumps(sheet), encoding="utf-8")
    with pytest.raises(EvaluationError, match="C6 sheet was built from another snapshot"):
        run_stages(ctx, state, [AnchorJudgedStage()])


# --- the committed scores of R93 part c ------------------------------------------------------------------

JUDGED_RUNS = json.loads((GOLD / "r93" / "runs.json").read_text(encoding="utf-8"))["judged"]


@pytest.mark.parametrize("run", JUDGED_RUNS, ids=lambda r: r["dataset"])
def test_each_committed_report_is_the_score_of_the_committed_verdicts(run):
    repo, base = GOLD.parent.parent, GOLD / "r93" / run["dataset"]
    assert digest(repo / run["report"]) == run["report_hash"] and run["judge_model"] == JUDGE
    assert {name: digest(base / name) for name in run["verdicts"]} == run["verdicts"]
    assert digest(GOLD / "r75" / f"{run['dataset']}_gold.json") == run["identity_gold_hash"]
    assert run["git_dirty_files"] in ("", ".claude/settings.json")
    report = JudgedReport.model_validate_json((repo / run["report"]).read_text(encoding="utf-8"))
    code = {
        c: CodeSide.model_validate_json((base / f"{c}_code.json").read_text(encoding="utf-8"))
        for c in ("c4", "c6")
    }
    files = {c: load_verdicts(base / f"{c}_verdicts.json", {i.id for i in code[c].items}) for c in code}
    # C4 and C6 need no snapshot: recomputed from the committed files, they must equal the committed report
    assert score_c4(files["c4"], code["c4"], 0.95) == report.final.c4
    assert score_c6(files["c6"], code["c6"], 0.95) == report.final.c6
    assert score_c4(blind_view(files["c4"]), code["c4"], 0.95) == report.blind.c4


# --- the committed results of R94 (fix A, measured by the offline replay) ----------------------------------

R94 = GOLD / "r94"
R94_RUNS = json.loads((R94 / "runs.json").read_text(encoding="utf-8"))


def test_the_replay_changed_only_the_four_cross_product_links_of_furniture():
    for run in R94_RUNS["relink"]:
        assert digest(GOLD.parent.parent / run["file"]) == run["hash"]
        report = RelinkReport.model_validate_json(
            (GOLD.parent.parent / run["file"]).read_text(encoding="utf-8")
        )
        assert all(c.explained and c.after is None for c in report.changes)
        expected = {"furniture": {"drawer", "frame", "center support", "drawers"}}.get(run["dataset"], set())
        assert {c.name for c in report.changes} == expected


def test_the_replayed_furniture_verdicts_are_r93s_and_its_report_their_score():
    base, old = R94 / "furniture", GOLD / "r93" / "furniture"
    for name in SHEET_MODELS:
        code = CodeSide.model_validate_json((base / f"{name}_code.json").read_text(encoding="utf-8"))
        file = load_verdicts(base / f"{name}_verdicts.json", {i.id for i in code.items})
        check_evidence(
            SHEET_MODELS[name].model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file
        )
        r93 = {v.id: v for v in load_verdicts(old / f"{name}_verdicts.json", _ids(old, name)).verdicts}
        assert all(v == r93[v.id] for v in file.verdicts)  # carried, never re-judged
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert digest(base / "anchor_judged.json") == R94_RUNS["furniture"]["anchor_judged"]["hash"]
    links = report.final.c4.links.accepted
    assert (links.k, links.n, report.final.c4.cross_scope_confirmed) == (42, 45, [])
    assert all(report.final.c6.hard_passed.values())
    assert sorted(report.final.c3.record_wrong_merges) == [
        "l:2a95b70b5851b756:Assembly:A-1021",  # "drawer slide mechanism": a piece linked to its whole
        "l:7a9abd29af74f1d7:Assembly:A-1022",  # "pre-drilled holes for the drawer handle"
        "l:c73615052e811388:Component:S-1076",  # "drawer slides" -> Drawer Sides, spelling 96
    ]


def _ids(folder: Path, name: str) -> set[str]:
    return {
        i.id for i in CodeSide.model_validate_json((folder / f"{name}_code.json").read_text("utf-8")).items
    }


def _end_to_end_vs_vector(folder: Path) -> tuple[float, float]:
    """Arm A's end-to-end complete@5 against vector retrieval, and the paired p-value, recomputed from the
    committed anchor reports in `folder` and R92's own rankings of arm C (no new embedding)."""
    compare = ArmComparison.model_validate_json(
        (GOLD / "r92" / "furniture" / "anchor_compare.json").read_text("utf-8")
    )
    qa_gold = load_qa_gold(Path(load_target_gold(GOLD / "r89" / "furniture_targets.json").qa_gold))
    a, b = (
        AnchorReport.model_validate_json((folder / f"anchor_{arm}.json").read_text("utf-8")) for arm in Arm
    )
    vector = vector_reach(
        qa_gold, [q.question for q in a.reach["gold_start"].questions], compare.rankings, [5, 10]
    )
    m = compare_arms(a, b, vector, compare.rankings, [5, 10]).metrics()
    return m["c5_end_to_end_vs_vector_complete_at_5_a"], m["c5_end_to_end_vs_vector_complete_at_5_p_value"]


def test_after_the_fix_the_anchor_arm_pairs_with_vector_retrieval_as_reported():
    """The pairing of R92 recomputed from committed files: R92's own rankings of arm C (no new embedding)."""
    # R92's logged numbers, reproduced
    assert _end_to_end_vs_vector(GOLD / "r90" / "furniture") == (15.0, pytest.approx(0.0703, abs=1e-3))
    assert _end_to_end_vs_vector(R94 / "furniture") == (17.0, pytest.approx(0.2891, abs=1e-3))


# --- the committed results of R95a (tier 1 of record linking, measured by the offline replay) -------------

R95A = GOLD / "r95a"
R95A_RUNS = json.loads((R95A / "runs.json").read_text(encoding="utf-8"))

# The furniture links R95a's rules no longer make, by cause (R94's four cross-product links are `left_scope`)
R95A_UNLINKED = {
    "left_scope": {"drawer", "frame", "center support", "drawers"},
    "containment": {
        "drawer slide mechanism",
        "pre-drilled holes for the drawer handle",
        "weighted base",
        "Västerås Bookshelf Reviews",
        "adjustable shelves",
        "cable management system",
        "frame construction",
    },
    "spelling": {"drawer slides"},
}


def test_r95a_unlinked_only_what_a_retired_rule_had_linked():
    for run in R95A_RUNS["relink"]:
        path = GOLD.parent.parent / run["file"]
        assert digest(path) == run["hash"]
        report = RelinkReport.model_validate_json(path.read_text(encoding="utf-8"))
        assert all(c.explained and c.after is None for c in report.changes)
        by_cause: dict[str, set[str]] = {}
        for c in report.changes:
            by_cause.setdefault(c.cause, set()).add(c.name)
        assert by_cause == (R95A_UNLINKED if run["dataset"] == "furniture" else {})


def test_r95a_carries_r94s_verdicts_judges_only_the_new_items_and_scores_as_reported():
    base, old = R95A / "furniture", R94 / "furniture"
    new_items = {
        "c3": {"s:Component:drawer slides"},  # the desk's and the bed's drawer slides, now two individuals
        "c4": set(),
        "c6": {
            "p:82ed63a727d84cee|product_reviews/norrkoping_nightstand_reviews.md#4"
        },  # the holes' own chunk
    }
    code, files = {}, {}
    for name in SHEET_MODELS:
        code[name] = CodeSide.model_validate_json((base / f"{name}_code.json").read_text(encoding="utf-8"))
        files[name] = load_verdicts(base / f"{name}_verdicts.json", {i.id for i in code[name].items})
        check_evidence(
            SHEET_MODELS[name].model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")),
            files[name],
        )
        r94 = {v.id: v for v in load_verdicts(old / f"{name}_verdicts.json", _ids(old, name)).verdicts}
        assert {v.id for v in files[name].verdicts if v.id not in r94} == new_items[name]
        assert all(v == r94[v.id] for v in files[name].verdicts if v.id in r94)  # carried, never re-judged
        assert new_items[name] <= set(files[name].reviewed)
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert digest(base / "anchor_judged.json") == R95A_RUNS["furniture"]["anchor_judged"]["hash"]
    assert score_c4(files["c4"], code["c4"], 0.95) == report.final.c4
    assert score_c6(files["c6"], code["c6"], 0.95) == report.final.c6
    links = report.final.c4.links.accepted
    assert (links.k, links.n, report.final.c4.hard_passed) == (38, 38, True)
    assert report.final.c3.record_wrong_merges == [] and report.final.c3.hard_passed
    assert all(report.final.c6.hard_passed.values())
    # the price: one end-to-end question (F17) whose "frame" now starts from the bed's lone frame
    assert _end_to_end_vs_vector(base) == (16.0, pytest.approx(0.1797, abs=1e-3))


# --- the committed results of R95b (tier 3: the LLM's choice among near misses, one paid replay) ----------

R95B = GOLD / "r95b"
R95B_RUNS = json.loads((R95B / "runs.json").read_text(encoding="utf-8"))

# The furniture mentions whose decision the paid replay changed, by cause. The chooser linked 17 (among them
# the Malmö desk's "drawer" to its own Drawer Unit, and three of the four right links R95a gave up); R95a's
# rules keep the other mentions unlinked: the two R93 judged wrong, the title, and "frame construction",
# which the LLM declined. R94's "frame" and "center support" have no near miss to choose.
R95B_CHANGED = {
    "chosen": {
        "drawer", "dresser", "Västerås Bookshelves", "metal rails", "removable covers", "nightstands",
        "shelf", "cord", "slat system", "weighted base", "sofa", "bookshelf", "adjustable shelves",
        "cable management system", "Västerås", "drawer slides", "drawers",
    },
    "containment": {
        "drawer slide mechanism", "pre-drilled holes for the drawer handle", "Västerås Bookshelf Reviews",
        "frame construction",
    },
    "left_scope": {"frame", "center support"},
}  # fmt: skip


def test_r95b_changed_only_by_a_verified_choice_or_a_retired_rule():
    run = R95B_RUNS["relink"]
    path = GOLD.parent.parent / run["file"]
    assert digest(path) == run["hash"] and run["llm_calls"] == 31
    report = RelinkReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert all(c.explained for c in report.changes)
    by_cause: dict[str, set[str]] = {}
    for c in report.changes:
        by_cause.setdefault(c.cause, set()).add(c.name)
    assert by_cause == R95B_CHANGED
    actions = [d.action for d in report.choices]
    assert (actions.count("chosen"), actions.count("none"), actions.count("no_sentence"), len(actions)) == (
        17, 14, 1, 32,
    )  # fmt: skip
    # every link the chooser made carries the model and a quote that names the mention
    chosen = [d for d in report.choices if d.action == "chosen"]
    assert all(d.by == run["model"] and d.name.lower() in d.evidence.lower() for d in chosen)
    [drawer] = [d for d in chosen if d.name == "drawer"]
    assert (
        drawer.record == "Assembly:A-1062"
    )  # the desk's Drawer Unit, which R94's fix A had to leave unlinked


def test_r95b_judges_only_the_new_items_and_scores_as_reported():
    base, earlier = R95B / "furniture", (R95A / "furniture", R94 / "furniture")
    judged_before = {
        name: {
            v.id: v
            for folder in earlier
            for v in load_verdicts(folder / f"{name}_verdicts.json", _ids(folder, name)).verdicts
        }
        for name in SHEET_MODELS
    }
    code, files = {}, {}
    for name in SHEET_MODELS:
        code[name] = CodeSide.model_validate_json((base / f"{name}_code.json").read_text(encoding="utf-8"))
        files[name] = load_verdicts(base / f"{name}_verdicts.json", {i.id for i in code[name].items})
        check_evidence(
            SHEET_MODELS[name].model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")),
            files[name],
        )
        assert all(
            v == judged_before[name][v.id] for v in files[name].verdicts if v.id in judged_before[name]
        )
    new = {
        name: [v for v in files[name].verdicts if v.id not in judged_before[name]] for name in SHEET_MODELS
    }
    assert {name: len(v) for name, v in new.items()} == {"c3": 0, "c4": 8, "c6": 11}
    assert all(v.label is Label.VALID for verdicts in new.values() for v in verdicts)
    assert {v.id for v in new["c4"]} <= set(files["c4"].reviewed)  # the lead reviewed every new link
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert digest(base / "anchor_judged.json") == R95B_RUNS["furniture"]["anchor_judged"]["hash"]
    assert score_c4(files["c4"], code["c4"], 0.95) == report.final.c4
    assert score_c6(files["c6"], code["c6"], 0.95) == report.final.c6
    links = report.final.c4.links.accepted
    assert (links.k, links.n, report.final.c4.hard_passed) == (49, 49, True)
    assert report.final.c3.record_wrong_merges == [] and report.final.c3.hard_passed
    assert report.final.c3.identity_pairs.joined == 11  # R75's same pairs: 8 of 13 after R95a
    assert all(report.final.c6.hard_passed.values())
    # F17's "frame construction" was declined, so the end-to-end pairing with vector retrieval is R95a's
    assert _end_to_end_vs_vector(base) == (16.0, pytest.approx(0.1797, abs=1e-3))


# --- the committed results of R96 (the chooser's revised prompt, one paid replay) -------------------------

R96 = GOLD / "r96"
R96_RUNS = json.loads((R96 / "runs.json").read_text(encoding="utf-8"))


def test_r96_changed_one_decision_and_scores_as_reported():
    """The revised prompt links "frame construction" to the sofa's Frame, the link R93 judged right and R95b's
    chooser declined; the other 31 decisions, the declined wrong readings among them, are R95b's."""
    run = R96_RUNS["relink"]
    assert digest(GOLD.parent.parent / run["file"]) == run["hash"]
    before, after = (
        {
            d.mention: (d.action, d.record)
            for d in RelinkReport.model_validate_json(path.read_text("utf-8")).choices
        }
        for path in (R95B / "furniture" / "relink.json", GOLD.parent.parent / run["file"])
    )
    changed = {m: (before[m], after[m]) for m in after if before[m] != after[m]}
    assert changed == {"c21ab5530ef8c787": (("none", None), ("chosen", "Assembly:A-1012"))}
    report = JudgedReport.model_validate_json((R96 / "furniture" / "anchor_judged.json").read_text("utf-8"))
    assert digest(R96 / "furniture" / "anchor_judged.json") == R96_RUNS["furniture"]["anchor_judged"]["hash"]
    links = report.final.c4.links.accepted
    assert (links.k, links.n, report.final.c4.hard_passed, report.final.c3.hard_passed) == (
        50,
        50,
        True,
        True,
    )
    assert all(report.final.c6.hard_passed.values())
    # F17 starts at the sofa's Frame again (two mentions, so it wins W1's tie): R94's pairing comes back
    assert _end_to_end_vs_vector(R96 / "furniture") == (17.0, pytest.approx(0.2891, abs=1e-3))


# --- the committed results of R99 (record candidates outside a scope; the individuals replayed too) -------

R99 = GOLD / "r99"
R99_RUNS = json.loads((R99 / "runs.json").read_text(encoding="utf-8"))
REPO = GOLD.parent.parent


@pytest.mark.parametrize("dataset", sorted(R99_RUNS["datasets"]))
def test_r99_carries_every_verdict_judges_only_the_new_items_and_scores_as_reported(dataset):
    run, base = R99_RUNS["datasets"][dataset], R99 / dataset
    old = REPO / run["verdicts"]["carried_from"]
    for name, model in SHEET_MODELS.items():
        assert digest(base / f"{name}_verdicts.json") == run["verdicts"]["files"][f"{name}_verdicts.json"]
        file = load_verdicts(base / f"{name}_verdicts.json", _ids(base, name))
        check_evidence(model.model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file)
        old_file = load_verdicts(old / f"{name}_verdicts.json", _ids(old, name))
        earlier = {v.id: v for v in old_file.verdicts}
        new = set(run["verdicts"]["new_items"][name])
        assert {v.id for v in file.verdicts if v.id not in earlier} <= new  # the rest are byte-identical
        assert all(v == earlier[v.id] for v in file.verdicts if v.id not in new)  # carried, never re-judged
        assert new <= set(file.reviewed) and not any(c.id in new for c in file.changes)  # the lead agreed
        kept = {v.id for v in file.verdicts}
        assert sorted(file.changes, key=lambda c: c.id) == sorted(
            (c for c in old_file.changes if c.id in kept), key=lambda c: c.id
        )
        assert all(v.label == "VALID" for v in file.verdicts if v.id in new)
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert digest(base / "anchor_judged.json") == run["anchor_judged"]["hash"]
    assert report.final.c4.hard_passed and all(report.final.c6.hard_passed.values())
    assert report.final.c3.wrong_merges.get("individual", []) == []
    # held-out's C3 hard rule fails since R93 on one record link no R94-R99 step touched (Found along the way)
    assert report.final.c3.record_wrong_merges == (
        ["l:8c6f9fa48d339e85:Vehicle:ROGUE"] if dataset == "heldout" else []
    )


def test_r99_links_the_pikes_and_the_kestrel_only_where_the_chooser_quoted_them():
    """Generality's new out-of-scope candidates: four chosen (three Pikes, one Kestrel V-12), the open day's
    Kestrel V-12 answered none, the two Maria Lopez records refused as twins; R75's identity recall 13/13."""
    report = RelinkReport.model_validate_json((R99 / "generality" / "relink.json").read_text("utf-8"))
    decided = sorted((c.name, c.action, c.record) for c in report.choices)
    assert decided == [
        ("J. Pike", "chosen", "Staff:S-131"),
        ("J. Pike", "chosen", "Staff:S-131"),
        ("Jon Pike", "chosen", "Staff:S-131"),
        ("Kestrel V-12", "chosen", "Pump:KV12-0457"),
        ("Kestrel V-12", "none", None),
        ("M. Lopez", "twin", None),
    ]
    assert all(c.cause == "chosen" and c.explained for c in report.changes) and len(report.changes) == 4
    judged = JudgedReport.model_validate_json((R99 / "generality" / "anchor_judged.json").read_text("utf-8"))
    pairs = judged.final.c3.identity_pairs
    assert (pairs.recall, pairs.same_scored, pairs.precision, pairs.apart) == (
        1.0,
        13,
        1.0,
        1.0,
    )  # R93: 10/13


# --- the committed results of R100 (the evidence-based adjudicator of individuals) -------------------------

R100 = GOLD / "r100"
R100_RUNS = json.loads((R100 / "runs.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("dataset", sorted(R100_RUNS["datasets"]))
def test_r100_carries_r99s_verdicts_judges_only_the_changed_items_and_scores_as_reported(dataset):
    run, base = R100_RUNS["datasets"][dataset], R100 / dataset
    old = REPO / run["verdicts"]["carried_from"]
    new = set(R100_RUNS["new_items"])
    for name, model in SHEET_MODELS.items():
        assert digest(base / f"{name}_verdicts.json") == run["verdicts"]["files"][f"{name}_verdicts.json"]
        file = load_verdicts(base / f"{name}_verdicts.json", _ids(base, name))
        check_evidence(model.model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file)
        earlier = {v.id: v for v in load_verdicts(old / f"{name}_verdicts.json", _ids(old, name)).verdicts}
        assert all(v == earlier[v.id] for v in file.verdicts if v.id not in new)  # carried, never re-judged
        assert {v.id for v in file.verdicts if v.id not in earlier} <= new
        assert {v.id for v in file.verdicts} & new <= set(file.reviewed)  # the lead reviewed every one
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert digest(base / "anchor_judged.json") == run["anchor_judged"]["hash"]
    assert report.final.c4.hard_passed and all(report.final.c6.hard_passed.values())
    assert report.final.c3.wrong_merges.get("individual", []) == []  # no new join is wrong
    pairs = report.final.c3.identity_pairs
    assert (pairs.precision, pairs.apart) == (1.0, 1.0)  # R75's apart pairs stay apart
    recall = {"furniture": 10 / 13, "heldout": 5 / 7, "generality": 1.0}[dataset]
    assert pairs.recall == pytest.approx(recall)  # furniture loses "cushions" / "cushion" to unsure


def test_r100_joins_seven_of_the_nine_generality_splits_and_answers_unsure_where_evidence_is_missing():
    splits = {
        s["id"]
        for s in json.loads((R100 / "generality" / "c3_sheet.json").read_text(encoding="utf-8"))["splits"]
    }
    # R93's nine splits of one thing in several nodes; seven are one node now, room B12 is newly split
    assert splits == {"s:Place:mill lane", "s:Place:room b12", "s:Place:town hall"}
    unsure = {}
    for dataset in R100_RUNS["datasets"]:
        report = ReidentifyReport.model_validate_json((R100 / dataset / "reidentify.json").read_text("utf-8"))
        unsure[dataset] = sum(d.action == "unsure" for d in report.decisions)
        assert not report.faithful and all(c.explained for c in report.changes)
    assert unsure == {"furniture": 6, "heldout": 20, "generality": 39}


# --- the committed results of R102 (the rebuilds with the r77d claims and R97-R101) ------------------------

R102 = GOLD / "r102"
_LISTS = {"c3": ("merges", "splits"), "c4": ("links",), "c6": ("pairs",)}


def _sheet_items(folder: Path, name: str) -> dict[str, str]:
    """Each judging item of a committed sheet as its JSON and the texts of the chunks it names: two equal
    entries are byte-identical items, whose verdict carries."""
    sheet = json.loads((folder / f"{name}_sheet.json").read_text("utf-8"))
    out = {}
    for key in _LISTS[name]:
        for item in sheet[key]:
            text = json.dumps(item, sort_keys=True, ensure_ascii=False)
            used = sorted(c for c in sheet["chunks"] if f'"{c}"' in text)
            out[item["id"]] = text + json.dumps([sheet["chunks"][c] for c in used], ensure_ascii=False)
    return out


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r102_verdicts_answer_their_sheets_carry_r100s_and_score_as_reported(dataset):
    base = R102 / dataset
    for name, model in SHEET_MODELS.items():
        file = load_verdicts(base / f"{name}_verdicts.json", _ids(base, name))
        check_evidence(model.model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file)
        old = load_verdicts(R100 / dataset / f"{name}_verdicts.json", _ids(R100 / dataset, name))
        before, after = _sheet_items(R100 / dataset, name), _sheet_items(base, name)
        same = {i for i in after if before.get(i) == after[i]}
        final, earlier = file.final(), old.final()
        assert all(final[i] == earlier[i] for i in same)  # a byte-identical item keeps its verdict
        # load_verdicts checked the review rules; the lead changed no verdict of a new or changed item
        assert not [c for c in file.changes if c.id not in same]
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert report.final.c4.hard_passed and all(report.final.c6.hard_passed.values())
    assert report.final.c3.wrong_merges.get("individual", []) == []
    assert report.final.c3.record_wrong_merges == (
        ["l:8c6f9fa48d339e85:Vehicle:ROGUE"] if dataset == "heldout" else []
    )
    pairs = report.final.c3.identity_pairs
    assert (pairs.precision, pairs.apart) == (1.0, 1.0)
    assert pairs.recall == pytest.approx({"furniture": 11 / 14, "heldout": 5 / 7, "generality": 1.0}[dataset])
    wrong_splits = {
        "furniture": {"s:Particular:austin", "s:Particular:seattle"},
        "heldout": {"s:Particular:toyota"},
        "generality": {"s:Place:mill lane", "s:Place:room b12", "s:Place:town hall", "s:Person:tomasz wren",
                       "s:Place:children's section", "s:Place:harbour street", "s:Place:riverton"},
    }[dataset]  # fmt: skip
    assert set(report.final.c3.wrong_splits) == wrong_splits


# --- the committed results of R103 (the rebuilds with re-extracted claims and R97-R106) ---------------------

R103 = GOLD / "r103"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r103_verdicts_answer_their_sheets_carry_r102s_and_score_as_reported(dataset):
    base = R103 / dataset
    for name, model in SHEET_MODELS.items():
        file = load_verdicts(base / f"{name}_verdicts.json", _ids(base, name))
        check_evidence(model.model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file)
        old = load_verdicts(R102 / dataset / f"{name}_verdicts.json", _ids(R102 / dataset, name))
        before, after = _sheet_items(R102 / dataset, name), _sheet_items(base, name)
        same = {i for i in after if before.get(i) == after[i]}
        final, earlier = file.final(), old.final()
        assert all(final[i] == earlier[i] for i in same)  # a byte-identical item keeps its verdict
        assert file.changes == []  # the lead reviewed and changed no C3, C4 or C6 verdict
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert report.final.c4.hard_passed and all(report.final.c6.hard_passed.values())
    # held-out's one wrong individual merge: two complaints' cars, pass mentions stated kind of a keyed type
    assert report.final.c3.wrong_merges.get("individual", []) == (
        ["m:f235a1f58888d87e"] if dataset == "heldout" else []
    )
    assert report.final.c3.record_wrong_merges == (
        ["l:46d8fbe6280c2ded:Vehicle:OUTBACK", "l:8c6f9fa48d339e85:Vehicle:ROGUE"]
        if dataset == "heldout"
        else []
    )
    pairs = report.final.c3.identity_pairs
    # furniture: "dimmer" now joins "dimmer switch" and "dimmer function", which R75's gold keeps apart
    expected = {
        "furniture": (11 / 12, 32 / 33, 11 / 14),
        "heldout": (1.0, 1.0, 4 / 7),
        "generality": (1.0, 1.0, 1.0),
    }
    assert (pairs.precision, pairs.apart, pairs.recall) == pytest.approx(expected[dataset])
    wrong_splits = {
        "furniture": {"s:Particular:austin", "s:Particular:seattle"},
        "heldout": {"s:Particular:nissan customer service", "s:Vehicle:legacy"},
        "generality": {"s:Place:mill lane", "s:Place:room b12", "s:Place:town hall", "s:Person:tomasz wren",
                       "s:Place:riverton"},
    }[dataset]  # fmt: skip
    assert set(report.final.c3.wrong_splits) == wrong_splits


# --- the committed results of R107 (R103's graphs resolved again with the kind rule) -----------------------

R107 = GOLD / "r107"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_r107_verdicts_carry_r103s_and_the_kind_rule_removes_the_wrong_join_of_two_cars(dataset):
    base = R107 / dataset
    for name, model in SHEET_MODELS.items():
        file = load_verdicts(base / f"{name}_verdicts.json", _ids(base, name))
        check_evidence(model.model_validate_json((base / f"{name}_sheet.json").read_text("utf-8")), file)
        old = load_verdicts(R103 / dataset / f"{name}_verdicts.json", _ids(R103 / dataset, name))
        before, after = _sheet_items(R103 / dataset, name), _sheet_items(base, name)
        same = {i for i in after if before.get(i) == after[i]}
        final, earlier = file.final(), old.final()
        assert all(final[i] == earlier[i] for i in same)  # a byte-identical item keeps its verdict
        assert file.changes == []  # the lead reviewed and changed no C3, C4 or C6 verdict
    report = JudgedReport.model_validate_json((base / "anchor_judged.json").read_text(encoding="utf-8"))
    assert report.final.c4.hard_passed and all(report.final.c6.hard_passed.values())
    assert report.final.c3.wrong_merges.get("individual", []) == []  # R103's "CARS" / "CAR" is a concept now
    # held-out's C3 hard rule still fails on two record links, which the kind rule does not touch
    assert report.final.c3.record_wrong_merges == (
        ["l:46d8fbe6280c2ded:Vehicle:OUTBACK", "l:8c6f9fa48d339e85:Vehicle:ROGUE"]
        if dataset == "heldout"
        else []
    )
    pairs = report.final.c3.identity_pairs
    expected = {
        "furniture": (11 / 12, 32 / 33, 11 / 14),  # the "dimmer" join, as in R103
        "heldout": (1.0, 1.0, 4 / 7),
        "generality": (1.0, 1.0, 1.0),
    }
    assert (pairs.precision, pairs.apart, pairs.recall) == pytest.approx(expected[dataset])
    wrong_splits = {
        "furniture": {"s:Particular:austin", "s:Particular:seattle"},
        "heldout": {"s:Particular:nissan customer service"},  # the two "Legacy" nodes are one now
        "generality": {"s:Place:mill lane", "s:Place:room b12", "s:Place:town hall", "s:Person:tomasz wren",
                       "s:Place:riverton"},
    }[dataset]  # fmt: skip
    assert set(report.final.c3.wrong_splits) == wrong_splits
