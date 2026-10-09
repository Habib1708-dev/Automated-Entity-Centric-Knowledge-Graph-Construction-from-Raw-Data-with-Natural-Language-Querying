"""The groundedness stages of the LLM node summaries (R124b): `kg summary-sheet` and `kg summary-judged`.

Role in the pipeline: after `kg index --cards summary`; no model. Judging happens in between, by Claude in the
session (the `evaluation` skill), never in a stage.
`kg summary-sheet UNITS` reads every node's evidence from the loaded graph, the summaries from a units file
(`index/units.jsonl` of a `kg index --cards summary` run, or its MLflow artifact), refuses a file whose
summaries were not written from this evidence (an evidence hash differs, a node is missing or extra), and
writes the seeded stratified sample as `summary_sheet.json` (validation/summary_judging.py).
`kg summary-judged SHEET VERDICTS` needs no graph: it refuses a verdict file that does not answer the sheet
exactly, counts grounded summaries and each fault, and writes `summary_groundedness.json`.
Design: wiring and logging only, like judging_stages.py. One MLflow run each: params name the files with
their hashes (and the judge model); metrics are the strata's sizes, then every count with its interval.
Not here: the sample, the checks and the counts (validation/summary_judging.py), the summaries
(hybrid/summaries.py).
"""

import json
from pathlib import Path

from ..core.errors import EvaluationError
from ..graph.digest import graph_digest
from ..hybrid import NodeEvidence, RenderedCard, evidence_hash
from ..hybrid.summaries import facts, numbered_facts
from ..validation.summary_judging import (
    Candidate,
    SummarySheet,
    draw_sheet,
    load_verdicts,
    score,
)
from .index_stages import card_representation, read_units
from .inputs import digest, input_file
from .stages import BaseStage

SHEET_FILE = "summary_sheet.json"
REPORT_FILE = "summary_groundedness.json"


class SummarySheetStage(BaseStage):
    """Draw the judging sample of a summary set over the loaded graph (`kg summary-sheet`)."""

    name = "summary_sheet"

    def __init__(self, units: Path):
        self.units = units

    def params(self, ctx, state):
        units = input_file(self.units, "units file")
        return {"units": units, "units_hash": digest(units)}

    def run(self, ctx, state, run):
        run.params(graph_digest=graph_digest(ctx.driver).value)
        rep = card_representation(ctx.settings, "template")  # the evidence only; its cards are not used
        evidence = read_units(ctx, state, rep, state.load_plan(ctx, required=False)).evidence
        pool = candidates(evidence, read_summaries(self.units))
        sheet = draw_sheet(pool, str(self.units), digest(self.units))
        run.metrics(rows=len(sheet.rows), **{f"population_{s}": n for s, n in sheet.population.items()})
        run.artifact(ctx.write(SHEET_FILE, sheet.model_dump_json(indent=1)))


class SummaryJudgedStage(BaseStage):
    """Check the judge's verdict file against its sheet and count (`kg summary-judged`)."""

    name = "summary_judged"

    def __init__(self, sheet: Path, verdicts: Path):
        self.sheet = sheet
        self.verdicts = verdicts

    def params(self, ctx, state):
        sheet = input_file(self.sheet, "summary sheet")
        verdicts = input_file(self.verdicts, "verdict file")
        model = json.loads(verdicts.read_text(encoding="utf-8")).get("judge", {}).get("model")
        return {
            "sheet": sheet,
            "sheet_hash": digest(sheet),
            "verdicts": verdicts,
            "verdicts_hash": digest(verdicts),
            "judge_model": model,
        }

    def run(self, ctx, state, run):
        sheet = SummarySheet.model_validate_json(self.sheet.read_text(encoding="utf-8"))
        report = score(sheet, load_verdicts(self.verdicts, sheet, digest(self.sheet)))
        run.metrics(**report.metrics())
        run.artifact(ctx.write(REPORT_FILE, report.model_dump_json(indent=1)))


def read_summaries(path: Path) -> list[RenderedCard]:
    """The cards of a units file, refused unless a model wrote them (a template card has no `fallback`)."""
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cards = [RenderedCard.model_validate(line) for line in lines if line.get("unit") == "card"]
    if not cards or any(c.fallback is None for c in cards):
        raise EvaluationError([f"{path} holds no summary cards (kg index --cards summary writes them)"])
    return cards


def candidates(evidence: list[NodeEvidence], summaries: list[RenderedCard]) -> list[Candidate]:
    """Every node's summary with the facts its prompt showed. Raises `EvaluationError` when the summaries were
    not written from this evidence: a node without a summary, a summary of no node, another evidence hash."""
    by_ref = {c.ref: c for c in summaries}
    issues = [f"{e.ref} has no summary" for e in evidence if e.ref not in by_ref]
    issues += [f"{ref} is no node of the graph" for ref in by_ref.keys() - {e.ref for e in evidence}]
    issues += [
        f"{e.ref}: the summary was written from other evidence" for e in evidence
        if e.ref in by_ref and by_ref[e.ref].evidence_hash != evidence_hash(e)
    ]  # fmt: skip
    if issues:
        raise EvaluationError(issues)
    return [
        Candidate(
            ref=e.ref,
            kind=e.kind,
            qualified=any(c.denied or c.modality != "actual" for c in e.claims),
            title=e.title,
            label=e.label,
            facts=numbered_facts(facts(e)),
            summary=by_ref[e.ref].text,
            fallback=bool(by_ref[e.ref].fallback),
        )
        for e in evidence
    ]
