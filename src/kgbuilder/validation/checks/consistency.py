"""Consistency checks on the subject graph: schema conformance, leftover duplicates, self-references."""

from ..report import CheckOutput
from .base import CheckContext


class SubjectConsistencyCheck:
    def run(self, ctx: CheckContext) -> CheckOutput:
        out = CheckOutput()
        entities = ctx.scalar("MATCH (e:Entity) RETURN count(e)")
        out.metrics["entities"] = entities
        if not entities:
            return out
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
            known = ctx.scalar(
                "MATCH (e:Entity) WHERE e.type IN $types RETURN count(e)",
                types=sorted(ctx.schema.entity_names()),
            )
            out.add(
                "consistency: every entity type is in the schema",
                known == entities,
                f"{entities - known} entities of unknown type",
                "consistency",
            )

        # same type and same name, ignoring case: entity resolution should have merged these
        duplicates = ctx.scalar(
            "MATCH (e:Entity) WITH e.type AS t, toLower(e.name) AS n, count(*) AS c "
            "WHERE c > 1 RETURN count(n)"
        )
        out.add(
            "resolution: no exact-duplicate entities remain",
            duplicates == 0,
            f"{duplicates} duplicate name groups",
            "consistency",
        )
        # an observation whose subject and object are one node says "X relates to X"; resolution removes them
        loops = ctx.scalar("MATCH (e:Entity)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(e) RETURN count(o)")
        out.add("consistency: no self-referencing facts", loops == 0, f"{loops} self loops", "consistency")
        out.metrics["entities_linked_to_domain"] = ctx.scalar(
            "MATCH (e:Entity)-[:REFERS_TO]->() RETURN count(DISTINCT e)"
        )
        # Descriptive, not a target: how much of the text knowledge can be joined to the structured data
        # (a fact with neither end linked, like a reviewer LOCATED_IN a city, cannot). Always 0 for text-only
        # data, and a goal may rightly want unlinked facts, so never optimise a prompt for this number alone.
        # Only facts count, not REFERS_TO itself, which points from an entity to a domain node.
        touching = ctx.scalar(
            "MATCH (o:Observation)-[:SUBJECT|OBJECT]->(e:Entity) WHERE (e)-[:REFERS_TO]->() "
            "RETURN count(DISTINCT o)"
        )
        out.metrics["facts_touching_domain_rate"] = round(touching / len(facts), 3) if facts else 0.0
        return out
