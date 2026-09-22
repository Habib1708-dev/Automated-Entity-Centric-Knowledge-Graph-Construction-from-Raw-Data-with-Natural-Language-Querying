"""Run the validation check families, and the accuracy check when gold data is available.

Role in the pipeline: `kg validate`, and the last stage of `kg run`.
Design: the families are Strategy objects (checks/); this module only iterates them, so adding a
check never means editing a long function.
"""

from neo4j import Driver

from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from .checks import DEFAULT_CHECKS, CheckContext, GraphCheck
from .evaluate import evaluate
from .gold import GoldSet
from .report import CheckOutput, ValidationReport


def _accuracy(ctx: CheckContext, gold: GoldSet, min_recall: float) -> CheckOutput:
    """The gold-set scores as metrics, and one pass/fail check on triple recall."""
    out = CheckOutput()
    report = evaluate(ctx.driver, gold)
    out.metrics.update(report.metrics())
    if report.triples is not None:
        found = round(report.triples.recall * report.triples.gold)
        out.add(
            "accuracy: gold recall",
            report.triples.recall >= min_recall,
            f"recall {report.triples.recall:.0%} ({found}/{report.triples.gold}), minimum {min_recall:.0%}",
            "accuracy",
        )
    return out


def validate_graph(
    driver: Driver,
    plan: ConstructionPlan | None,
    schema: TextSchema | None,
    expected_counts: dict[str, int] | None = None,
    gold: GoldSet | None = None,
    min_recall: float = 0.5,
    checks: list[GraphCheck] | None = None,
) -> ValidationReport:
    """Run every check family against the graph. Families that do not apply yet contribute nothing."""
    ctx = CheckContext(driver=driver, plan=plan, schema=schema, expected_counts=expected_counts)
    outputs = [check.run(ctx) for check in (checks or DEFAULT_CHECKS)]
    if gold is not None:
        outputs.append(_accuracy(ctx, gold, min_recall))
    return ValidationReport(
        checks=[c for o in outputs for c in o.checks],
        metrics={k: v for o in outputs for k, v in o.metrics.items()},
    )
