"""Consistency checks on the subject graph: schema conformance, the identity layer, self-references.

Since R75 the subject graph holds mentions (one per type, name and document) and an identity layer that says
what each refers to (graph/canonical.py). A claim whose two ends are one entity is left out by the fact
reader, not deleted, so it is counted here instead of failing a check.
"""

from ...core.values import VALUE_TYPE
from ...graph.canonical import canonical_id, canonical_kind
from ...text.schema import FALLBACK_TYPES
from ..report import CheckOutput
from .base import CheckContext


class SubjectConsistencyCheck:
    def run(self, ctx: CheckContext) -> CheckOutput:
        out = CheckOutput()
        mentions = ctx.scalar("MATCH (m:Mention) RETURN count(m)")
        out.metrics["mentions"] = mentions
        if not mentions:
            return out
        # the canonical entities the mentions stand for: what one node per thing was before R75
        out.metrics["entities"] = ctx.scalar(f"MATCH (m:Mention) RETURN count(DISTINCT {canonical_id('m')})")
        facts = ctx.facts
        out.metrics["facts"] = len(facts)
        # Descriptive (R57): how many relation names the graph uses. More names for the same number of
        # facts make a graph harder to query (a question has to know every synonym), so a change that
        # raises recall should not raise this number without a reason.
        out.metrics["predicates_distinct"] = len({f.predicate for f in facts})

        if ctx.schema is not None:
            off_schema = [
                f for f in facts if not ctx.schema.allows(f.subject_type, f.predicate, f.object_type)
            ]
            out.add(
                "consistency: every fact conforms to the text schema",
                not off_schema,
                f"{len(off_schema)} facts violate the schema",
                "consistency",
            )
            # the built-in types are the schema's too: `Value` (R66) and the mention pass's fallbacks (R101)
            known = ctx.scalar(
                "MATCH (m:Mention) WHERE m.type IN $types RETURN count(m)",
                types=sorted(ctx.schema.entity_names() | {VALUE_TYPE, *FALLBACK_TYPES}),
            )
            out.add(
                "consistency: every mention type is in the schema",
                known == mentions,
                f"{mentions - known} mentions of unknown type",
                "consistency",
            )

        # one concept per type and name is the identity stage's rule (concepts.py): two would split a kind
        duplicates = ctx.scalar(
            "MATCH (c:Concept) WITH c.type AS t, toLower(c.name) AS n, count(*) AS c "
            "WHERE c > 1 RETURN count(n)"
        )
        out.add(
            "identity: no two concepts of one type share a name",
            duplicates == 0,
            f"{duplicates} duplicate name groups",
            "consistency",
        )
        # "X relates to X" once both ends refer to one entity; the fact reader leaves these out (base.py)
        out.metrics["self_references_hidden"] = ctx.scalar(
            "MATCH (s:Mention)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Mention) "
            f"WHERE {canonical_id('s')} = {canonical_id('t')} RETURN count(o)"
        )
        out.metrics["mentions_linked_to_records"] = ctx.scalar(
            f"MATCH (m:Mention) WHERE {canonical_kind('m')} = 'record' RETURN count(m)"
        )
        # Descriptive, not a target: how much of the text knowledge can be joined to the structured data
        # (a fact with neither end linked, like a reviewer LOCATED_IN a city, cannot). Always 0 for text-only
        # data, and a goal may rightly want unlinked facts, so never optimise a prompt for this number alone.
        touching = ctx.scalar(
            f"MATCH (o:Observation)-[:SUBJECT|OBJECT]->(m:Mention) WHERE {canonical_kind('m')} = 'record' "
            "RETURN count(DISTINCT o)"
        )
        out.metrics["facts_touching_domain_rate"] = round(touching / len(facts), 3) if facts else 0.0
        return out
