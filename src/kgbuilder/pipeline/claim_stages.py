"""The claims' offline evaluation: `kg claim-eval` on a finished build (R110), `kg claim-recall` (R111).

Role in the pipeline: after a finished build; no graph, no model. It rebuilds the build's graph offline behind
R87's fidelity gate (pipeline/offline.py) and writes the sheet the judge answers: every claim the build
stores, extracted and derived, with every stored field (validation/claim_eval.py). Judging happens in
between, by Claude in the session, never in a stage. Given the judge's verdict file, it checks the file
against the sheet (the shared review rules, a fault on every INCORRECT verdict, every quote in its chunk)
and scores it.
Design: wiring and logging only, like mention_stages.py. One MLflow run per build: params name the build,
the dataset and the verdict file with its hash; metrics are the claim counts and, with verdicts, the strict
and content precision of each origin with their intervals and the count of every fault; artifacts the sheet
and the report.
`kg claim-recall` needs no build: it joins committed files (R77's sentence sample and gold, a claim sheet of
`kg claim-eval`) into the sheet the judge matches (validation/claim_recall.py), and scores the judge's file.
One MLflow run per dataset: params name the three files and the verdicts with their hashes; metrics are the
sheet's counts and, with verdicts, recall (overall, random strata, per stratum, within the schema), the
misses per cause and the stored fields of the matched claims; artifacts the sheet and the report.
Not here: the sheets' contents and the scores (validation/claim_eval.py, claim_recall.py), the snapshot.
"""

from pathlib import Path

from ..core.errors import EvaluationError
from ..text.schema import TextSchema
from ..validation.anchor_verdicts import load_verdicts
from ..validation.assertion import load_assertion_gold
from ..validation.claim_eval import (
    ClaimScores,
    ClaimSheet,
    claim_item,
    claim_sheet,
    claim_verdict_issues,
    score_claims,
)
from ..validation.claim_recall import RecallScores, load_recall_verdicts, recall_sheet, score_recall
from ..validation.sentences import SentenceSample
from .inputs import digest, input_file
from .offline import build_params, gated_snapshot
from .stages import BaseStage

CLAIM_SHEET = "claim_sheet.json"
CLAIM_REPORT = "claim_report.json"
RECALL_SHEET = "recall_sheet.json"
RECALL_REPORT = "recall_report.json"


class ClaimEvalStage(BaseStage):
    """Write one build's claim sheet; with the judge's verdicts, score them."""

    name = "claim_eval"

    def params(self, ctx, state):
        params: dict[str, object] = build_params(ctx, state) | {
            "dataset": state.need("anchor_dataset", "pass the dataset's name"),
        }
        if state.claim_verdicts is not None:
            verdicts = input_file(state.claim_verdicts, "claim verdicts")
            params |= {"verdicts": verdicts, "verdicts_hash": digest(verdicts)}
        return params

    def run(self, ctx, state, run):
        source = Path(state.audit_source)
        snapshot = gated_snapshot(ctx, state)
        schema = TextSchema.model_validate_json((source / "text_schema.json").read_text(encoding="utf-8"))
        types = {m.id: m.type for m in snapshot.mentions}
        items = [claim_item(c, "derived" if c.derived else "extracted", types) for c in snapshot.claims]
        sheet = claim_sheet(state.anchor_dataset, source.as_posix(), items, snapshot.chunks, schema)
        run.artifact(ctx.write(CLAIM_SHEET, sheet.model_dump_json(indent=1)))
        metrics: dict[str, float] = {
            "claims": len(sheet.claims),
            "claims_merged": sheet.merged,
            "chunks": len(sheet.chunks),
        }
        metrics |= {f"{o}_claims": sum(c.origin == o for c in sheet.claims) for o in ("extracted", "derived")}
        if state.claim_verdicts is not None:
            verdicts = load_verdicts(Path(state.claim_verdicts), sheet.to_judge())
            if issues := claim_verdict_issues(sheet, verdicts):
                raise EvaluationError(issues)
            scores = score_claims(sheet, verdicts)
            state.claim_scores = scores
            metrics |= _scored(scores)
            run.artifact(ctx.write(CLAIM_REPORT, scores.model_dump_json(indent=1)))
        run.metrics(**metrics)


def _scored(scores: ClaimScores) -> dict[str, float]:
    """Per origin: both precisions with their Wilson bounds and n, the left-out labels, every fault."""
    out: dict[str, float] = {}
    for origin, s in scores.by_origin.items():
        for name, p in (("precision", s.precision), ("content_precision", s.content_precision)):
            if p.rate is not None:
                key = f"{origin}_{name}"
                out |= {key: p.rate, f"{key}_low": p.low, f"{key}_high": p.high, f"{key}_n": p.n}
        out |= {f"{origin}_ambiguous": s.ambiguous, f"{origin}_unjudgeable": s.unjudgeable}
        out |= {f"{origin}_fault_{f}": n for f, n in s.faults.items()}
    return out


class ClaimRecallStage(BaseStage):
    """Write the recall sheet of one dataset; with the judge's verdicts, score them (R111)."""

    name = "claim_recall"

    def params(self, ctx, state):
        files = {
            "claim_sheet": input_file(
                state.need("claim_sheet", "pass a claim sheet of kg claim-eval"), "claim sheet"
            ),
            "gold": input_file(state.need("assertion_gold", "pass the R77 gold"), "gold file"),
            "sample": input_file(state.need("sample", "pass the gold's sentence sample"), "sample file"),
        }
        params: dict[str, object] = {"dataset": state.need("anchor_dataset", "pass the dataset's name")}
        for name, path in files.items():
            params |= {name: path, f"{name}_hash": digest(path)}
        if state.recall_verdicts is not None:
            verdicts = input_file(state.recall_verdicts, "recall verdicts")
            params |= {"verdicts": verdicts, "verdicts_hash": digest(verdicts)}
        return params

    def run(self, ctx, state, run):
        claims = ClaimSheet.model_validate_json(Path(state.claim_sheet).read_text(encoding="utf-8"))
        sample = SentenceSample.model_validate_json(Path(state.sample).read_text(encoding="utf-8"))
        gold = load_assertion_gold(Path(state.assertion_gold))
        sheet = recall_sheet(state.anchor_dataset, sample, gold, claims, Path(state.claim_sheet).as_posix())
        run.artifact(ctx.write(RECALL_SHEET, sheet.model_dump_json(indent=1)))
        metrics: dict[str, float] = {
            "sentences": len(sheet.sentences),
            "gold_claims": len(sheet.keys()),
            "stored_claims_shown": sum(len(s.stored) for s in sheet.sentences),
            # a sentence whose chunk stores no claim: every gold claim of it is a miss
            "sentences_without_stored": sum(not s.stored for s in sheet.sentences),
        }
        if state.recall_verdicts is not None:
            scores = score_recall(sheet, load_recall_verdicts(Path(state.recall_verdicts), sheet))
            state.recall_scores = scores
            metrics |= _recall_metrics(scores)
            run.artifact(ctx.write(RECALL_REPORT, scores.model_dump_json(indent=1)))
        run.metrics(**metrics)


def _recall_metrics(scores: RecallScores) -> dict[str, float]:
    """Every rate with its Wilson bounds and n (a rate without n logs nothing), the misses per cause."""
    rates = {
        "recall": scores.recall,
        "recall_random": scores.recall_random,
        "recall_in_schema": scores.recall_in_schema,
        "recall_in_schema_random": scores.recall_in_schema_random,
    }
    rates |= {f"recall_{stratum}": p for stratum, p in scores.recall_by_stratum.items()}
    rates |= {f"exact_{field}": p for field, p in scores.fields_exact.items()}
    out: dict[str, float] = {}
    for name, p in rates.items():
        if p.rate is not None:
            out |= {name: p.rate, f"{name}_low": p.low, f"{name}_high": p.high, f"{name}_n": p.n}
    out |= {f"miss_{cause}": n for cause, n in scores.misses.items()}
    out |= {f"miss_random_{cause}": n for cause, n in scores.misses_random.items()}
    return out
