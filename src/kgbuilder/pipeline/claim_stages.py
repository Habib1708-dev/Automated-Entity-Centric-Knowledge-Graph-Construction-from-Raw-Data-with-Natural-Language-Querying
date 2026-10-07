"""The claims' offline evaluation on a finished build: `kg claim-eval` (R110).

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
Not here: the sheet's contents and the scores (validation/claim_eval.py), the snapshot (audit/).
"""

from pathlib import Path

from ..core.errors import EvaluationError
from ..text.schema import TextSchema
from ..validation.anchor_verdicts import load_verdicts
from ..validation.claim_eval import (
    ClaimScores,
    claim_item,
    claim_sheet,
    claim_verdict_issues,
    score_claims,
)
from .inputs import digest, input_file
from .offline import build_params, gated_snapshot
from .stages import BaseStage

CLAIM_SHEET = "claim_sheet.json"
CLAIM_REPORT = "claim_report.json"


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
