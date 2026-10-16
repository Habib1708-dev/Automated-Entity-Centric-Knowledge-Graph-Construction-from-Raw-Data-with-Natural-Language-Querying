"""The committed inputs of R225: the lighthouses and NTSB builds' logged counts, samples and blind gold.

R225 measures the build accuracy of the two R155b builds with the suite the first three datasets got
(R101 mention gold, R77 reader-claim gold, R87 logged counts). The files are written once and judged
against later, so these tests pin what code can check: the gold answers its sample sentence by sentence,
the claim sample has R77's strata (40 random, 20 with a cue word), and the logged counts name the same
stage counts as the earlier builds' files, so the fidelity gate compares all of them. No Neo4j, no LLM,
no `out/` folder.
"""

import re
from pathlib import Path

import pytest

from kgbuilder.audit.fidelity import LoggedCounts
from kgbuilder.validation.assertion import load_assertion_gold
from kgbuilder.validation.mention_gold import load_mention_gold
from kgbuilder.validation.sentences import SentenceSample

GOLD = Path(__file__).resolve().parent / "gold"
R225 = GOLD / "r225"
DATASETS = ["lighthouses", "ntsb"]
# The cue words the sample was drawn with (REFACTOR_PLAN R225): a negation, modal or condition word
_CUE = re.compile(
    r"(?i)(n't\b|\b(?:no|not|never|none|nothing|nor|neither|without|cannot|may|might|could|would|should|"
    r"possibly|probably|likely|perhaps|maybe|apparently|reportedly|suspected|believed|if|unless|when|"
    r"whenever|until|once)\b)"
)


def _sample(name: str) -> SentenceSample:
    return SentenceSample.model_validate_json((R225 / name).read_text("utf-8"))


@pytest.mark.parametrize("dataset", DATASETS)
def test_the_mention_gold_answers_its_sample(dataset):
    gold = load_mention_gold(R225 / f"{dataset}_mentions.json", _sample(f"{dataset}_sample.json"))
    assert gold.dataset == dataset and gold.rules == "tests/gold/r101/rules.md"
    assert len(gold.sentences) == 40 and sum(len(s.mentions) for s in gold.sentences) > 0


@pytest.mark.parametrize("dataset", DATASETS)
def test_the_claim_gold_answers_its_sample_with_r77s_strata(dataset):
    sample = _sample(f"{dataset}_assertion_sample.json")
    gold = load_assertion_gold(R225 / f"{dataset}_assertion_gold.json")
    assert [s.id for s in gold.sentences] == [s.id for s in sample.sentences]
    strata = [s.stratum for s in gold.sentences]
    assert strata.count("natural") == 40 and strata.count("cue") == 20
    by_id = {s.id: s.text for s in sample.sentences}
    assert all(_CUE.search(by_id[s.id]) for s in gold.sentences if s.stratum == "cue")
    assert sum(len(s.claims) for s in gold.sentences) > 0


@pytest.mark.parametrize("dataset", DATASETS)
def test_the_logged_counts_name_every_count_the_earlier_builds_did(dataset):
    logged = LoggedCounts.model_validate_json((R225 / f"{dataset}_logged.json").read_text("utf-8"))
    earlier = LoggedCounts.model_validate_json((GOLD / "r108" / "generality_logged.json").read_text("utf-8"))
    assert logged.dataset == dataset and logged.build == f"out/r155b_{dataset}"
    assert logged.counts.keys() == earlier.counts.keys() and logged.runs.keys() == earlier.runs.keys()
