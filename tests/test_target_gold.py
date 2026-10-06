"""The target gold of the anchor-graph evaluation (R89, tests/gold/r89): the models reject a target that
reaches nothing, an empty question without a reason and an alias that repeats a name; the corpus check
reports each kind of misfit on an invented corpus; and the committed files cover their QA gold exactly and
fit their corpus as the pipeline chunks and stages it, with the identity traps of the corpora pinned (two
Maria Lopez, the spellings of Jonathan Pike, two parts named Drawer Rails). No Neo4j, no LLM.
"""

import pytest
from pydantic import ValidationError

from kgbuilder.core.errors import InvalidGoldError
from kgbuilder.validation.gold import MentionRef
from kgbuilder.validation.qa_gold import QAGold, RecordEvidence, load_qa_gold, rows_matching
from kgbuilder.validation.target_gold import (
    QuestionTargets,
    Target,
    TargetGold,
    check_target_gold,
    load_target_gold,
)

from .qa_corpus import REPO, Corpus, rebuild_corpus

TARGETS = REPO / "tests" / "gold" / "r89"
DATASETS = ["furniture", "heldout", "generality"]


@pytest.fixture(scope="module", params=DATASETS)
def committed(request, tmp_path_factory) -> tuple[TargetGold, QAGold, Corpus]:
    gold = load_target_gold(TARGETS / f"{request.param}_targets.json")
    qa = load_qa_gold(REPO / gold.qa_gold)
    return gold, qa, rebuild_corpus(qa.corpus, tmp_path_factory.mktemp("staging"))


def test_every_committed_file_covers_its_qa_gold_and_fits_its_corpus(committed):
    gold, qa, corpus = committed
    check_target_gold(gold, qa, corpus.chunks, corpus.rows)


def test_every_question_without_a_target_says_why_and_most_questions_have_one(committed):
    gold, _, _ = committed
    empty = [q for q in gold.questions if not q.targets]
    assert all(q.note for q in empty)
    # a question without a start leaves C2, C5 and C8: if most had none, the criteria would measure little
    assert len(empty) < len(gold.questions) / 3


def targets_of(dataset: str, qid: str) -> dict[str, Target]:
    gold = load_target_gold(TARGETS / f"{dataset}_targets.json")
    return {t.name: t for t in next(q for q in gold.questions if q.id == qid).targets}


def staff(target: Target) -> set[str]:
    return {r.row["staff_id"] for r in target.records if r.file == "institute/staff.csv"}


def test_each_maria_lopez_reaches_her_own_staff_record():
    assert staff(targets_of("generality", "G01")["Maria Lopez"]) == {"S-104"}  # the award winner
    assert staff(targets_of("generality", "G22")["Maria Lopez"]) == {"S-104"}  # the poster at Utrecht
    assert staff(targets_of("generality", "G02")["Maria Lopez"]) == {"S-219"}  # the grants accountant


def test_jonathan_pike_carries_every_spelling_and_the_ambiguous_pike_reaches_both_people():
    pike = targets_of("generality", "G05")["Jonathan Pike"]
    assert staff(pike) == {"S-131"}
    assert {"Jon Pike", "J. Pike", "Dr. J. Pike"} <= set(pike.aliases)
    which = targets_of("generality", "G36")["Pike"]
    assert staff(which) == {"S-131"}
    assert [m.names for m in which.mentions] == [["Judith Pike"]]


def test_unscoped_drawer_rails_reach_both_parts_and_drawer_slides_reach_no_record(tmp_path):
    qa = load_qa_gold(REPO / "tests" / "gold" / "qa" / "furniture_qa.json")
    components = rebuild_corpus(qa.corpus, tmp_path).rows["components.csv"]
    rails = targets_of("furniture", "F20")["drawer rails"].records
    assert {row["part_id"] for ref in rails for row in rows_matching(ref, components)} == {"S-1078", "S-1085"}
    assert {r.row["part_id"] for r in targets_of("furniture", "F01")["drawer rails"].records} == {"S-1085"}
    assert targets_of("furniture", "F36")["drawer slides"].records == []


# --- the models ---------------------------------------------------------------------------------------


def test_a_target_must_reach_a_record_or_a_mention():
    with pytest.raises(ValidationError, match="records or mentions"):
        Target(name="kettle")
    with pytest.raises(ValidationError, match="at least one name"):
        Target(name="kettle", mentions=[MentionRef(doc_id="notes.md", names=[])])


def test_an_alias_may_not_repeat_the_name():
    with pytest.raises(ValidationError, match="repeats"):
        Target(name="Kettle K-2", aliases=["kettle k-2"], mentions=[MentionRef(doc_id="a.md", names=["x"])])


def test_a_question_without_targets_needs_a_reason():
    with pytest.raises(ValidationError, match="why"):
        QuestionTargets(id="Q1")
    assert QuestionTargets(id="Q1", note="ranges over every lamp").targets == []


# --- the corpus check on an invented corpus ------------------------------------------------------------

CHUNKS = {
    "notes/kitchen.md#0": "The Kettle K-2 hums when it boils. Its lid is loose.",
    "notes/kitchen.md#1": "The lamp flickers at night.",
}
ROWS = {
    "devices.csv": [{"device_id": "D-1", "name": "Kettle K-2"}, {"device_id": "D-2", "name": "Desk Lamp"}]
}


def qa_gold(*questions: tuple[str, str]) -> QAGold:
    return QAGold.model_validate(
        {
            "dataset": "kitchen",
            "written_by": "test",
            "date": "2026-10-06",
            "corpus": {
                "data_dir": "x",
                "chunk_max_chars": 1500,
                "chunk_min_chars": 200,
                "chunk_overlap_chars": 0,
            },
            "questions": [
                {
                    "id": qid,
                    "type": "lookup",
                    "question": text,
                    "expected": {"text": "x"},
                    "route": "exact",
                    "records": [{"file": "devices.csv", "row": {"device_id": "D-1"}}],
                }
                for qid, text in questions
            ],
        }
    )


def target_gold(*entries: QuestionTargets) -> TargetGold:
    return TargetGold(
        dataset="kitchen", qa_gold="qa.json", written_by="test", date="2026-10-06", questions=list(entries)
    )


KETTLE = Target(
    name="kettle",
    aliases=["Kettle K-2"],
    records=[RecordEvidence(file="devices.csv", row={"device_id": "D-1"})],
)
LID = Target(name="lid", mentions=[MentionRef(doc_id="notes/kitchen.md", names=["lid"])])
QA = qa_gold(("Q1", "Why does the kettle hum?"), ("Q2", "Is the lid of the kettle loose?"))


def test_a_file_that_fits_passes():
    gold = target_gold(
        QuestionTargets(id="Q1", targets=[KETTLE]), QuestionTargets(id="Q2", targets=[KETTLE, LID])
    )
    check_target_gold(gold, QA, CHUNKS, ROWS)


def issues(gold: TargetGold, qa: QAGold = QA) -> list[str]:
    with pytest.raises(InvalidGoldError) as e:
        check_target_gold(gold, qa, CHUNKS, ROWS)
    return e.value.issues


def test_every_question_is_covered_once_and_no_unknown_one_is_listed():
    found = issues(
        target_gold(
            QuestionTargets(id="Q1", targets=[KETTLE]),
            QuestionTargets(id="Q1", targets=[KETTLE]),
            QuestionTargets(id="Q9", note="no such question"),
        )
    )
    assert found == [
        "question id 'Q1' is listed twice",
        "question Q9 is not in qa.json",
        "question Q2 of qa.json has no entry",
    ]


def test_a_name_must_be_in_its_question_and_an_alias_in_the_corpus():
    lamp = Target(
        name="lamp",
        aliases=["Ceiling Lamp"],
        mentions=[MentionRef(doc_id="notes/kitchen.md", names=["lamp"])],
    )
    found = issues(
        target_gold(QuestionTargets(id="Q1", targets=[lamp]), QuestionTargets(id="Q2", targets=[LID]))
    )
    assert found == [
        "question Q1, target 'lamp': the name is not in the question",
        "question Q1, target 'lamp': alias 'Ceiling Lamp' is written nowhere in the corpus",
    ]


def test_a_record_ref_must_pick_a_row_of_a_staged_file():
    wrong = Target(
        name="kettle",
        records=[
            RecordEvidence(file="devices.csv", row={"device_id": "D-9"}),
            RecordEvidence(file="kettles.csv", row={"device_id": "D-1"}),
        ],
    )
    found = issues(
        target_gold(QuestionTargets(id="Q1", targets=[wrong]), QuestionTargets(id="Q2", targets=[LID]))
    )
    assert found == [
        "question Q1, target 'kettle': no row of devices.csv has {'device_id': 'D-9'}",
        "question Q1, target 'kettle': no staged file 'kettles.csv'",
    ]


def test_a_mention_must_name_the_thing_in_an_existing_document():
    wrong = Target(
        name="lid",
        mentions=[
            MentionRef(doc_id="notes/garage.md", names=["lid"]),
            MentionRef(doc_id="notes/kitchen.md", names=["cover", "cap"]),
        ],
    )
    found = issues(
        target_gold(QuestionTargets(id="Q1", targets=[KETTLE]), QuestionTargets(id="Q2", targets=[wrong]))
    )
    assert found == [
        "question Q2, target 'lid': no document 'notes/garage.md' in the corpus",
        "question Q2, target 'lid': notes/kitchen.md names none of ['cover', 'cap']",
    ]
