"""The numbers the R229 figures show: the build accuracy of the benchmarked graphs, read from the committed
judging records.

Sources, one build per dataset:
- **Furniture, held-out, generality:** the r108 builds.
  - claim precision: R110, `tests/gold/r110/<ds>/claim_report.json`;
  - claim recall: R111, `r111/<ds>/recall_report.json`;
  - mention recall and pass precision: R103, `r103/<ds>/mentions_r103_report.json` (r108 reused
    R103's mention pass from the cache);
  - the C3/C4/C6 audit: R108, `r108/<ds>/anchor_judged.json`.
- **Lighthouses, NTSB:** the r155b builds, judged with the same rules in R226 (claims, recall,
  mentions) and R228 (audit).

Role: the only reader of data for `r229_charts.py`, which draws the four `SELECTED` datasets (R230).
The test checks these functions against the counts the
roadmap quotes.
Design: pure functions over the committed reports. Every rate is rebuilt from its recorded k and n with
`Proportion.of`, so it carries its 95% Wilson interval and a figure can only show what the record holds.
Must not: read `out/`, MLflow or a database, or draw.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from kgbuilder.validation.interval import Proportion

from .r157_data import DATASETS, LABELS, NEW

GOLD = Path(__file__).resolve().parents[2] / "tests" / "gold"
__all__ = ["DATASETS", "LABELS", "NEW", "SELECTED", "BuildAccuracy", "excluded", "load", "load_all"]
# The datasets the figures compare (R230, the user's choice for the final presentation): a selected
# comparison of four. Lighthouses was measured with the same rules (R226, R228) and fails C4 (34/36) and so
# C3; it is left out of the figures, not out of the records, and every caption says so.
SELECTED = ("ntsb", "furniture", "heldout", "generality")
EXCLUDED = tuple(ds for ds in DATASETS if ds not in SELECTED)


@dataclass(frozen=True)
class Source:
    """Where one dataset's records live, and the build they judged."""

    build: str
    claims: str  # the step folder of claim_report.json and recall_report.json
    recall: str
    mentions: Path  # the mention report file
    audit: str  # the step folder of anchor_judged.json


def _source(ds: str) -> Source:
    if ds in NEW:
        return Source(f"r155b_{ds}", "r226", "r226", GOLD / "r226" / ds / "mention_report.json", "r228")
    return Source(f"r108_{ds}", "r110", "r111", GOLD / "r103" / ds / "mentions_r103_report.json", "r108")


# R111's miss causes the figures group: a miss with no fact type to hold it is the schema's; every other
# cause (the model did not store it, stored it on another thing, dropped a negation, a role...) is a miss a
# fact type could have held
SCHEMA_MISS = "no_schema_type"


@dataclass(frozen=True)
class BuildAccuracy:
    """One dataset's build accuracy, every measure as k/n with its Wilson interval."""

    dataset: str
    build: str
    strict: Proportion  # extracted claims with every stored field right (judged)
    content: Proportion  # extracted claims with the triple right (judged)
    recall: Proportion  # the reader's claims of the random sentences stored by the build (judged)
    misses: dict[str, int]  # those claims' misses per R111 cause
    mention_exact: Proportion  # gold mentions found by name (code)
    mention_judged: Proportion  # gold mentions found, near names mapped by the judge
    mention_precision: Proportion  # sampled pass mentions inside the definition (judged)
    c3: Proportion  # merges of individual and concept nodes judged one thing (accepted labels)
    c3_passed: bool  # C3's hard rule: no wrong individual or record merge
    c4: Proportion  # mention-to-record links right
    c4_passed: bool  # C4's hard rule: link precision at least the bound, no confirmed cross-scope link
    c6: Proportion  # anchor arm: (record or individual start, chunk) pairs about their start
    c6_passed: bool

    @property
    def schema_misses(self) -> int:
        return self.misses.get(SCHEMA_MISS, 0)

    @property
    def other_misses(self) -> int:
        """Misses a fact type could have held: every cause but `no_schema_type`."""
        return sum(n for cause, n in self.misses.items() if cause != SCHEMA_MISS)


def _p(record: dict) -> Proportion:
    return Proportion.of(record["k"], record["n"])


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load(ds: str) -> BuildAccuracy:
    """The dataset's committed build accuracy."""
    src = _source(ds)
    claims = _read(GOLD / src.claims / ds / "claim_report.json")["by_origin"]["extracted"]
    recall = _read(GOLD / src.recall / ds / "recall_report.json")
    mentions = _read(src.mentions)
    audit = _read(GOLD / src.audit / ds / "anchor_judged.json")["final"]
    merges = audit["c3"]["merges"]
    # C3 as one rate: the individual and the concept merges together (each judged by the same question)
    c3_k = sum(merges[kind]["accepted"]["k"] for kind in ("individual", "concept"))
    c3_n = sum(merges[kind]["accepted"]["n"] for kind in ("individual", "concept"))
    return BuildAccuracy(
        dataset=ds,
        build=src.build,
        strict=_p(claims["precision"]),
        content=_p(claims["content_precision"]),
        recall=_p(recall["recall_random"]),
        misses={c: n for c, n in recall["misses_random"].items() if n},
        mention_exact=_p(mentions["recall_exact"]),
        mention_judged=_p(mentions["recall"]),
        mention_precision=_p(mentions["precision"]),
        c3=Proportion.of(c3_k, c3_n),
        c3_passed=audit["c3"]["hard_passed"],
        c4=_p(audit["c4"]["links"]["accepted"]),
        c4_passed=audit["c4"]["hard_passed"],
        c6=_p(audit["c6"]["purity"]["anchor"]["record_individual"]["accepted"]),
        c6_passed=audit["c6"]["hard_passed"]["anchor"],
    )


def excluded() -> list[BuildAccuracy]:
    """The measured datasets the figures leave out (R230), read so every caption can state their result."""
    return [load(ds) for ds in EXCLUDED]


def load_all() -> list[BuildAccuracy]:
    """The selected datasets' build accuracy, in the benchmark figures' dataset order."""
    return [load(ds) for ds in SELECTED]
