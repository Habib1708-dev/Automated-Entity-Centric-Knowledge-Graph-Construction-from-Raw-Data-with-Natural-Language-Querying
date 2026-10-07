"""The mention pass's offline stages on a finished build: `kg mention-eval` (R102) and `kg mention-pass
--from-build` (R105).

Role in the pipeline: after a finished build; no graph. Both rebuild the build's graph offline behind R87's
fidelity gate (they refuse a build whose snapshot is not the build's).
  - `kg mention-eval` (no model) scores the build's mentions against R101's gold (validation/mention_eval.py)
    and writes the judge's sheet: the recall items whose only match is a near name (the judged mapping) and
    the seeded sample of the pass's mentions (judged precision). Given the judge's verdict file, it scores
    those too. Judging itself happens in between, by Claude in the session, never in a stage. A precision
    item shows the class the pass stated for the mention (R104), so a piece typed with a keyed type ("back
    rest" as a part record) is judged as the kind it is; a pass file written before R104 states none, and
    its class is read off the type as R102 did. With `--pass-file`, another pass's findings are scored in
    place of the build's own (R105).
  - `kg mention-pass --from-build` runs the pass (the LLM) on the build's graph as the pass found it, so a
    change to the pass is measured on the build's own claims, not on a new sample of the extractor (R105).
Design: wiring and logging only, like judging_stages.py. One MLflow run per build: params name the build, the
gold and its sample with their hashes, the verdict file when given, and the sample's size and seed; metrics
are recall (exact, and with the mapping), recall by class, precision, the pass's mentions and how many each
chunk gained (median and p90: the size bound of R101); artifacts the sheet and the report. The replay logs
what the live pass logs, plus the build.
Not here: the scores (validation/mention_eval.py), the pass (text/mention_pass.py).
"""

import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ..audit import GraphSnapshot, build_snapshot, check_fidelity, load_logged
from ..core.errors import EvaluationError
from ..text.mention_pass import PassFinding, read_findings
from ..text.schema import MentionClass, TextSchema
from ..validation.anchor_verdicts import load_verdicts
from ..validation.mention_eval import (
    PRECISION_SAMPLE,
    PRECISION_SEED,
    MentionScores,
    MentionSheet,
    PrecisionItem,
    mention_evidence_issues,
    precision_sample,
    recall_items,
    score_mentions,
)
from ..validation.mention_gold import load_mention_gold
from ..validation.sentences import SentenceSample
from .inputs import digest, input_file
from .stages import BaseStage, MentionPassStage, PassInputs

MENTION_SHEET = "mention_sheet.json"
MENTION_REPORT = "mention_report.json"
# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


class MentionEvalStage(BaseStage):
    """Score one build's mentions against the R101 gold, and the pass's mentions against the judge."""

    name = "mention_eval"

    def params(self, ctx, state):
        gold, sample = _gold_files(state)
        params: dict[str, object] = _build_params(ctx, state) | {
            "gold": input_file(gold, "mention gold"),
            "gold_hash": digest(gold),
            "sample": input_file(sample, "sentence sample"),
            "sample_hash": digest(sample),
            "precision_sample": PRECISION_SAMPLE,
            "precision_seed": PRECISION_SEED,
        }
        if state.mention_pass_file is not None:
            pass_file = input_file(state.mention_pass_file, "pass findings")
            params |= {"pass_file": pass_file, "pass_file_hash": digest(pass_file)}
        if state.mention_verdicts is not None:
            verdicts = input_file(state.mention_verdicts, "mention verdicts")
            params |= {"verdicts": verdicts, "verdicts_hash": digest(verdicts)}
        return params

    def run(self, ctx, state, run):
        source = Path(state.audit_source)
        findings = None if state.mention_pass_file is None else read_findings(Path(state.mention_pass_file))
        snapshot = _gated_snapshot(ctx, state, findings)
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        gold_file, sample_file = _gold_files(state)
        sample = SentenceSample.model_validate_json(sample_file.read_text(encoding="utf-8"))
        gold = load_mention_gold(gold_file, sample)
        names: dict[str, list[str]] = defaultdict(list)
        for m in snapshot.mentions:
            for chunk in m.chunks:
                names[chunk].append(m.name)
        text = {c.chunk_id: c.text for c in snapshot.chunks}
        passed = [m for m in snapshot.mentions if m.found_by_pass and m.chunks]
        found = [
            PrecisionItem(
                id=m.id,
                name=m.name,
                type=m.type,
                chunk_id=m.chunks[0],
                text=text[m.chunks[0]],
                mention_class=m.stated_class or _class_of_type(schema, m.type),
            )  # fmt: skip
            for m in passed
        ]
        mention_sheet = MentionSheet(
            dataset=gold.dataset,
            build=source.as_posix(),
            recall=recall_items(gold, sample, names),
            precision=precision_sample(found),
        )
        run.artifact(ctx.write(MENTION_SHEET, mention_sheet.model_dump_json(indent=1)))
        verdicts = None
        if state.mention_verdicts is not None:
            verdicts = load_verdicts(Path(state.mention_verdicts), mention_sheet.to_judge())
            if issues := mention_evidence_issues(mention_sheet, verdicts):
                raise EvaluationError(issues)
        scores = score_mentions(mention_sheet, verdicts)
        gained = _gained_per_chunk(passed, [c.chunk_id for c in snapshot.chunks])
        state.mention_scores = scores
        run.metrics(**_metrics(scores, len(passed), gained))
        run.artifact(ctx.write(MENTION_REPORT, scores.model_dump_json(indent=1)))


class ReplayMentionPassStage(MentionPassStage):
    """`kg mention-pass --from-build BUILD` (R105): the mention pass run on a finished build's graph as the
    pass found it: the claims' and derivation's mentions and their chunks, rebuilt offline behind the C0
    gate, the build's own pass left out, and the build's text schema. No graph is written: the findings go to
    out/ as `kg mention-pass` writes them, for `kg mention-eval BUILD --pass-file`. Its run is named
    `mention_pass` and logs what the live pass logs, plus the build it read."""

    def params(self, ctx, state):
        return super().params(ctx, state) | _build_params(ctx, state)

    def read_inputs(self, ctx, state):
        snapshot = _gated_snapshot(ctx, state, findings=[])
        source = Path(state.audit_source)
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        pairs = {(c, m.id) for m in snapshot.mentions for c in m.chunks}
        return PassInputs(snapshot.chunks, schema, snapshot.mentions, pairs)

    def write_rows(self, ctx, rows):
        return None  # offline: no graph to write; the findings files the stage writes are the output


def _build_params(ctx, state) -> dict[str, object]:
    """The build a stage rebuilds offline, the dataset and the logged counts that gate it, and the chunker
    (chunk ids depend on it)."""
    logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
    s = ctx.settings
    return {
        "build": input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder"),
        "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
        "logged": logged,
        "logged_hash": digest(logged),
        "chunk_max_chars": s.chunk_max_chars,
        "chunk_min_chars": s.chunk_min_chars,
        "chunk_overlap_chars": s.chunk_overlap_chars,
    }


def _gated_snapshot(ctx, state, findings: list[PassFinding] | None) -> GraphSnapshot:
    """The build's graph rebuilt offline, refused unless it is the graph the build wrote (R87's C0 gate).
    With `findings` it is then rebuilt again with those in place of the build's own pass (an empty list: the
    graph as the pass found it): the gate proves everything before the pass, the findings are the caller's.

    Raises EvaluationError when the gate fails."""
    source, s = Path(state.audit_source), ctx.settings
    chunking = (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
    snapshot = build_snapshot(source, Path(state.data_dir), chunking)
    sheet = source / _SHEET
    if not check_fidelity(
        snapshot, load_logged(Path(state.audit_logged)), sheet if sheet.exists() else None
    ).passed:
        raise EvaluationError(["the snapshot is not the build's graph (C0 failed)"])
    return snapshot if findings is None else build_snapshot(source, Path(state.data_dir), chunking, findings)


def _class_of_type(schema: TextSchema, type_name: str) -> MentionClass:
    """R102's class for a pass mention whose pass file states none (written before R104): a keyed or
    individual type names one particular thing, a concept type a kind. Kept so that R102's sheets rebuild as
    they were judged; it shows a piece of a keyed type ("back rest") as particular, which is why the pass now
    states the class itself."""
    return "kind" if schema.identity_of(type_name) == "concept" else "particular"


def _gold_files(state) -> tuple[Path, Path]:
    folder = Path(state.need("mention_gold_dir", "pass the mention gold folder (tests/gold/r101)"))
    dataset = state.need("anchor_dataset", "pass the dataset's name")
    return folder / f"{dataset}_mentions.json", folder / f"{dataset}_sample.json"


def _gained_per_chunk(passed, chunks: list[str]) -> list[int]:
    """How many pass mentions each chunk MENTIONS (0 for a chunk the pass added nothing to)."""
    per = Counter(c for m in passed for c in m.chunks)
    return [per[c] for c in chunks]


def _metrics(scores: MentionScores, passed: int, gained: list[int]) -> dict[str, float]:
    out: dict[str, float] = {
        "gold_mentions": scores.gold_mentions,
        "recall_hits": scores.hits,
        "recall_candidates": scores.candidates,
        "recall_misses": scores.misses,
        "pass_mentions": passed,
    }
    rates = {"recall_exact": scores.recall_exact, "recall": scores.recall, "precision": scores.precision}
    rates |= {f"recall_exact_{k}": p for k, p in scores.recall_by_class.items()}
    for name, p in rates.items():
        if p is not None and p.rate is not None:
            out |= {name: p.rate, f"{name}_low": p.low, f"{name}_high": p.high, f"{name}_n": p.n}
    if scores.precision is not None:
        out |= {
            "precision_incorrect": len(scores.precision_incorrect),
            "precision_ambiguous": scores.precision_ambiguous,
        }
    if gained:
        ordered = sorted(gained)
        out |= {
            "pass_per_chunk_median": statistics.median(ordered),
            # nearest rank, as C7's p90 (anchor/criteria.py)
            "pass_per_chunk_p90": ordered[math.ceil(0.9 * len(ordered)) - 1],
        }
    return out
