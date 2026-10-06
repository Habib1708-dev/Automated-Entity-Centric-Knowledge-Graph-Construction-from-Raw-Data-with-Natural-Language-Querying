"""The identity stage: what every mention refers to, written as one `REFERS_TO` edge per mention (R75).

Role in the pipeline: `kg resolve`, after `kg link` (the scopes of record matching are the things the
documents are ABOUT) and before validation and question answering. Every reader finds a mention's
canonical entity through graph/canonical.py.
Design: the text schema's identity class of a mention's type decides what it may refer to (fixed decision 8
of the layered-model task: identity is an edge, not a merged node):
  - keyed: a record of the type's plan labels, by key, name or a telling attribute (records.py), or by an
    LLM's choice among near misses that code verified (record_choice.py); a mention no record fits stands
    for one particular thing, an individual;
  - individual: an `:Individual`; the same name in two documents stays two unless the text gives evidence
    that they are one (individuals.py); records and individuals together are particulars.py;
  - concept: a `:Concept` per type and name across documents, joined by entity resolution (concepts.py).
Every decision is recomputed from scratch on each run and kept, with its reason, in the report
(`resolve.json`), then written by identity_graph.py, which replaces nothing but the identity layer itself.
Not here: records and individuals (particulars.py), the concept decisions (resolver.py, concepts.py),
writing and clearing the edges (identity_graph.py), reading them (graph/canonical.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..graph.canonical import CanonicalKind
from ..llm.base import Embedder, LLMClient
from ..structured.plan import ConstructionPlan
from ..text.schema import TextSchema
from .blocking import Blocking
from .concepts import ConceptResolution, resolve_concepts
from .identity_graph import Assignment, write_identity
from .individuals import IndividualDecision
from .matchers import EntityRecord
from .mentions import MentionRecord, read_mentions
from .particulars import AmbiguousMention, JoinSettings, resolve_particulars
from .record_choice import ChoiceDecision
from .resolver import Decision, MergeGroup


class IdentitySettings(BaseModel):
    """The thresholds of the identity stage, from the settings (logged as params of the resolve run)."""

    auto_merge: float  # a concept pair spelled this alike merges without the LLM
    borderline: float  # a concept pair spelled at least this alike is asked about
    link_threshold: float  # the fuzzy score a mention's name needs to match a record's (records.py)


class IdentityReport(BaseModel):
    """Result and audit log of one identity run: every assignment, and the decisions behind them."""

    mentions: int
    ambiguous: list[AmbiguousMention]
    concepts_before: int  # concepts by type and name, before resolution joined any
    concepts_after: int
    merges: int  # concepts absorbed into another
    passes: int
    decisions: list[Decision]  # concept pairs: auto, llm_merge, llm_keep, skipped_borderline
    groups: list[MergeGroup]
    blocked: dict[str, int] = {}  # concept guard -> the pairs it kept apart (guards.py)
    individual_decisions: list[IndividualDecision] = []  # individual pairs: joined, apart, refused
    record_choices: list[ChoiceDecision] = []  # keyed mentions with near misses: chosen, none, refused
    assignments: list[Assignment]

    def count(self, kind: CanonicalKind) -> int:
        return sum(a.kind == kind for a in self.assignments)


def resolve_identity(
    driver: Driver,
    schema: TextSchema | None,
    plan: ConstructionPlan | None,
    llm: LLMClient | None,
    model: str,
    settings: IdentitySettings,
    embedder: Embedder | None = None,
    blocking: Blocking | None = None,
) -> IdentityReport:
    """Decide what every mention refers to and write the identity layer. Without a schema every type is a
    concept (the rule before R75); without an LLM borderline concept pairs stay apart and no two
    individuals are joined. The schema's identity classes must already have passed `validate_text_schema`
    against `plan`."""
    mentions = read_mentions(driver)
    classes = {m.type: (schema.identity_of(m.type) if schema else "concept") for m in mentions}
    particulars = resolve_particulars(
        driver,
        [m for m in mentions if classes[m.type] == "keyed"],
        [m for m in mentions if classes[m.type] == "individual"],
        schema,
        plan,
        settings.link_threshold,
        llm,
        JoinSettings(borderline=settings.borderline, model=model),
        embedder,
        blocking,
    )
    concept_mentions = [m for m in mentions if classes[m.type] == "concept"]
    concepts = resolve_concepts(
        driver,
        concept_mentions,
        llm,
        model,
        (settings.auto_merge, settings.borderline),
        embedder,
        blocking,
        schema,
    )
    assignments = particulars.assignments + _concept_assignments(concept_mentions, concepts, model)
    write_identity(driver, assignments)
    merges = sum(len(g.absorbed) for g in concepts.groups)
    return IdentityReport(
        mentions=len(mentions),
        ambiguous=particulars.ambiguous,
        concepts_before=len(concepts.concepts),
        concepts_after=len(concepts.concepts) - merges,
        merges=merges,
        passes=concepts.passes,
        decisions=concepts.decisions,
        groups=concepts.groups,
        blocked=concepts.blocked,
        individual_decisions=particulars.decisions,
        record_choices=particulars.choices,
        assignments=sorted(assignments, key=lambda a: a.mention),
    )


def _concept_assignments(
    mentions: list[MentionRecord], concepts: ConceptResolution, model: str
) -> list[Assignment]:
    """One assignment per concept mention: to its own concept (`same_name`), or to the canonical concept of
    the group resolution joined it to, with the decision that joined it."""
    root = {i: g.canonical for g in concepts.groups for i in g.absorbed}
    by_id = {c.id: c for c in concepts.concepts}
    concept_of = {m: cid for cid, ids in concepts.members.items() for m in ids}
    out = []
    for m in mentions:
        own = concept_of[m.id]
        canonical = by_id[root.get(own, own)]
        # a member of the canonical concept itself shares its name; an absorbed one joined by a decision
        reason, score, evidence, by = (
            _concept_reason(own, concepts.decisions, by_id, model)
            if own in root
            else ("same_name", None, "", "code")
        )
        out.append(
            Assignment(
                mention=m.id,
                said=m.name,
                kind="concept",
                canonical=canonical.id,
                name=canonical.name,
                type=m.type,
                reason=reason,
                score=score,
                evidence=evidence,
                by=by,
            )  # fmt: skip
        )
    return out


def _concept_reason(
    concept: str, decisions: list[Decision], by_id: dict[str, EntityRecord], model: str
) -> tuple[str, float | None, str, str]:
    """Why a concept joined its group: its strongest merge decision, as (reason, score, evidence, by).
    "spelling" for an automatic merge, "adjudicated" for an LLM's yes."""
    merges = [d for d in decisions if d.action in ("auto", "llm_merge") and concept in (d.a_id, d.b_id)]
    best = max(merges, key=lambda d: d.score)
    other = by_id[best.b_id if best.a_id == concept else best.a_id].name
    if best.action == "auto":
        return "spelling", best.score, f"spelled like '{other}'", "code"
    return "adjudicated", best.score, f"the same kind as '{other}'", model
