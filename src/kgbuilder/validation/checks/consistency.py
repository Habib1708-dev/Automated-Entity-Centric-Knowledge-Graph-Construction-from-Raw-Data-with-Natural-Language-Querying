"""Consistency checks on the subject graph: schema conformance, leftover duplicates, self-loops."""

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
        loops = ctx.scalar("MATCH (e:Entity)-[r]->(e) RETURN count(r)")
        out.add("consistency: no self-referencing facts", loops == 0, f"{loops} self loops", "consistency")
        out.metrics["entities_linked_to_domain"] = ctx.scalar(
            "MATCH (e:Entity)-[:REFERS_TO]->() RETURN count(DISTINCT e)"
        )
        return out
