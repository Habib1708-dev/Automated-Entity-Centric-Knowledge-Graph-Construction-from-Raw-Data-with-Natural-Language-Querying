"""The mention gold of R101 (validation/mention_gold.py) and the sentence sample it answers, drawn offline
from a finished build's corpus (`kg coverage-sample --build`).

The checks run on an invented sample (an observatory log); the sampler on the graph audit's invented build
folder (tests/test_audit.py). The committed gold files (tests/gold/r101) must answer their committed samples
under the definition's code-visible rules. No Neo4j, no LLM.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import InvalidGoldError
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.stages import CoverageSampleStage
from kgbuilder.validation.mention_gold import (
    GoldMention,
    MentionGold,
    SentenceMentions,
    load_mention_gold,
    mention_gold_issues,
)
from kgbuilder.validation.sentences import SampledSentence, SentenceSample
from tests.fakes import RecordingTracker
from tests.test_audit import _build as audit_build

LOG = "notes/night_log.md"
S1 = SampledSentence(
    id="s1", doc_id=LOG, chunk_id=f"{LOG}#0", text="No condensation was found on the mirror of North Dome."
)
S2 = SampledSentence(id="s2", doc_id=LOG, chunk_id=f"{LOG}#0", text="It was quiet.")
SAMPLE = SentenceSample(seed=1, size=2, population=2, sentences=[S1, S2])


def gold(*sentences: SentenceMentions) -> MentionGold:
    return MentionGold(
        dataset="test", written_by="test", date="2026-10-06", rules="rules.md", sample="s.json",
        sentences=list(sentences),
    )  # fmt: skip


def answer(s: SampledSentence, *mentions: tuple[str, str]) -> SentenceMentions:
    return SentenceMentions(
        id=s.id, doc_id=s.doc_id, text=s.text, mentions=[GoldMention(name=n, kind=k) for n, k in mentions]
    )


def test_a_gold_that_answers_every_sentence_with_names_from_it_passes():
    good = gold(
        answer(S1, ("condensation", "kind"), ("mirror", "kind"), ("North Dome", "particular")),
        answer(S2),  # a sentence that names no thing is answered with no mention
    )
    assert mention_gold_issues(good, SAMPLE) == []


def test_every_code_visible_rule_of_the_definition_is_checked():
    bad = gold(
        answer(
            S1,
            ("condensate", "kind"),  # not in the sentence
            ("mirr", "kind"),  # a piece of a word
            ("the mirror", "kind"),  # with an article
            ("mirror", "kind"),
            ("Mirror", "kind"),  # the same name twice, after norm
        ),
        answer(S1),  # answered twice
    )
    issues = mention_gold_issues(bad, SAMPLE)
    assert "sentence s1 answered 2 times" in issues
    assert "sentence s2 of the sample has no answer" in issues
    assert "sentence s1: 'condensate' is not in its sentence as whole words" in issues
    assert "sentence s1: 'mirr' is not in its sentence as whole words" in issues
    assert "sentence s1: 'the mirror' starts with an article" in issues
    assert "sentence s1: 'mirror' written 2 times" in issues
    changed = answer(S2).model_copy(update={"text": "It was loud."})
    assert "sentence s2: its text or document differs from the sample's" in mention_gold_issues(
        gold(answer(S1), changed), SAMPLE
    )


def test_a_gold_file_that_does_not_fit_is_refused(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(gold(answer(S1)).model_dump_json(), encoding="utf-8")
    with pytest.raises(InvalidGoldError):
        load_mention_gold(path, SAMPLE)


def test_the_sample_is_drawn_offline_from_a_builds_corpus(tmp_path):
    out, data = audit_build(tmp_path)
    target = tmp_path / "sample.json"
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=None, out=tmp_path / "o", tracker=tracker)
    state = PipelineState(sample=target, sample_size=3, sample_seed=101, audit_source=out, data_dir=data)
    run_stages(ctx, state, [CoverageSampleStage()])
    sample = SentenceSample.model_validate_json(target.read_text(encoding="utf-8"))
    texts = {s.text for s in sample.sentences}
    assert len(sample.sentences) == 3 and texts <= {
        "The shade is cracked.", "The lid is loose.", "The lid fits well.", "The lid hinge is stiff.",
        "# Alder Lamp Reviews", "# Birch Kettle Reviews",
    }  # fmt: skip
    params = tracker.run("coverage_sample").logged_params
    assert params["build"] == out and params["chunk_max_chars"] == Settings().chunk_max_chars
    # the same seed draws the same sentences, with or without a graph in between
    run_stages(ctx, state, [CoverageSampleStage()])
    assert json.loads(target.read_text(encoding="utf-8"))["sentences"] == sample.model_dump()["sentences"]


GOLD = Path(__file__).resolve().parent / "gold" / "r101"


@pytest.mark.parametrize("dataset", ["furniture", "heldout", "generality"])
def test_each_committed_mention_gold_answers_its_sample(dataset):
    gold_path = GOLD / f"{dataset}_mentions.json"
    if not gold_path.exists():
        pytest.skip("the gold is written in R101 part a, after its sample")
    sample = SentenceSample.model_validate_json((GOLD / f"{dataset}_sample.json").read_text("utf-8"))
    file = load_mention_gold(gold_path, sample)
    assert file.rules == "tests/gold/r101/rules.md" and file.dataset == dataset
    assert sum(len(s.mentions) for s in file.sentences) > 0
