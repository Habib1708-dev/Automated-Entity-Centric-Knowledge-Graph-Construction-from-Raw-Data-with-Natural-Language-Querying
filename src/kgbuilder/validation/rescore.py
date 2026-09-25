"""Re-score a logged judge sheet with today's matching and gold set, without the graph it came from.

Role in the pipeline: `kg rescore SHEET GOLD [--verdicts FILE]` (R48). Scoring changed after results were
reported (R42: a fact matches only gold triples of its own document; R35-R46: gold corrections), so the
numbers of earlier runs are recomputed from the `judge_sheet.json` each eval run logged, and every
reported run is compared on one scorer and one gold version.
Design: a sheet keeps each fact's document, relation, display name and id, and (since R33) every entity
with its aliases; that is all matching needs. The facts are rebuilt as `StoredFact`s and passed through
the same `build_sheet`, `score_verdicts` and ER functions as `kg eval`, so the two cannot drift apart;
the logged fact ids are kept, so the run's own verdicts still apply. Path truth (R63) is computed from the
same rebuilt facts, since a sheet holds every fact of the documents the gold labels. Not here: questions
(they need the graph) and graph access of any kind.
"""

from ..core.errors import EvaluationError
from ..core.text import norm
from ..text.schema import TextSchema
from .checks.base import StoredFact
from .er import SheetEntity, build_er_sheet, score_er, score_er_verdicts
from .evaluate import EvalReport, score_entities, score_triples
from .gold import GoldSet
from .judge import JudgeSheet, SheetFact, Verdicts, build_sheet, score_verdicts
from .paths import score_paths


def _names_by_type(entities: list[SheetEntity]) -> dict[tuple[str, str], list[str]]:
    """(type, normalised name or alias) -> the entity's display name and aliases."""
    names: dict[tuple[str, str], list[str]] = {}
    for e in entities:
        for name in [e.name, *e.aliases]:
            names[(e.type, norm(name))] = [e.name, *e.aliases]
    return names


def _stored(fact: SheetFact, names: dict[tuple[str, str], list[str]]) -> StoredFact:
    """The fact as `kg eval` read it from the graph. The sheet keeps the document, not the chunk; matching
    and scoping only need the document, so the chunk index is left out."""
    try:
        subject_names = names[(fact.subject_type, norm(fact.subject))]
        object_names = names[(fact.object_type, norm(fact.object))]
    except KeyError as e:
        raise EvaluationError([f"fact {fact.id}: name {e.args[0]} is on no entity of the sheet"]) from e
    return StoredFact(
        predicate=fact.predicate,
        subject_type=fact.subject_type,
        object_type=fact.object_type,
        chunk_id=f"{fact.doc_id}#",
        evidence=fact.evidence,
        subject_names=subject_names,
        object_names=object_names,
        # the sheet shows the fact's own wording (R44) or, before, the entity's name: keep what it showed
        subject_name=fact.subject,
        object_name=fact.object,
        things=fact.things,
        about=fact.about,
        # the time is part of the fact id (R66): without it a timed fact would get another id
        polarity=fact.polarity,
        time=fact.time,
    )


def rescore(
    sheet: JudgeSheet, gold: GoldSet, verdicts: Verdicts | None = None, schema: TextSchema | None = None
) -> EvalReport:
    """The scores today's `kg eval` would give the graph the sheet was written for; with the text
    `schema` the sheet's graph was built with, path truth as well.

    Raises `EvaluationError` for a sheet without its entity list (written before R33: a fact's aliases
    are unknown), and when `verdicts` do not cover exactly what the re-scored sheet asks.
    """
    if sheet.er is None:
        raise EvaluationError(["the sheet has no entity list (written before R33): aliases are unknown"])
    names = _names_by_type(sheet.er.entities)
    facts = [_stored(f, names) for f in sheet.facts]
    rebuilt = build_sheet(facts, gold.triples)
    if len(rebuilt.facts) != len(sheet.facts):
        # the gold labels other documents than when the sheet was written: ids could not be carried over
        raise EvaluationError(["the gold set's documents differ from the sheet's: facts in scope changed"])
    carried = zip(rebuilt.facts, sheet.facts, strict=True)
    rebuilt.facts = [new.model_copy(update={"id": old.id}) for new, old in carried]
    rebuilt.er = build_er_sheet(sheet.er.entities, gold.er_pairs)

    report = EvalReport(
        triples=score_triples(facts, gold.triples),
        entities=score_entities(facts, gold.triples),
        er=score_er(rebuilt.er),
        judge_sheet=rebuilt,
        paths=score_paths(facts, schema) if schema is not None else None,
    )
    if verdicts is not None:
        report.judge = score_verdicts(rebuilt, verdicts)
        if verdicts.er is not None:
            report.er_valid = score_er_verdicts(rebuilt.er, verdicts.er)
    return report
