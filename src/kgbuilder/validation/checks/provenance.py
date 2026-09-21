"""Provenance checks: can every entity and fact be traced back to the text it came from?

This re-verifies in the finished graph what extraction verified per chunk, so it also catches damage
done later (a re-ingest that replaced chunks, a merge, a manual edit).
"""

from ...core.text import norm
from ..report import CheckOutput
from .base import CheckContext


class ProvenanceCheck:
    def run(self, ctx: CheckContext) -> CheckOutput:
        out = CheckOutput()
        entities = ctx.scalar("MATCH (e:Entity) RETURN count(e)")
        if not entities:
            return out

        unmentioned = ctx.scalar("MATCH (e:Entity) WHERE NOT (:Chunk)-[:MENTIONS]->(e) RETURN count(e)")
        out.add(
            "provenance: every entity is mentioned in a chunk",
            unmentioned == 0,
            f"{unmentioned} entities without a source chunk",
            "provenance",
        )

        facts = ctx.facts
        missing = [f for f in facts if not f.chunk_id or not f.evidence]
        out.add(
            "provenance: every fact has chunk_id and evidence",
            not missing,
            f"{len(missing)} of {len(facts)} facts lack provenance",
            "provenance",
        )

        records, _, _ = ctx.driver.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.text AS text")
        chunk_text = {r["id"]: norm(r["text"]) for r in records}
        unverified = [
            f
            for f in facts
            if f.chunk_id and f.evidence and norm(f.evidence) not in chunk_text.get(f.chunk_id, "")
        ]
        out.add(
            "provenance: evidence quotes exist in their chunk",
            not unverified,
            f"{len(unverified)} of {len(facts)} quotes not found",
            "provenance",
        )
        # the headline number of the thesis' anti-hallucination claim
        out.metrics["evidence_verified_rate"] = 1 - len(unverified) / len(facts) if facts else 1.0
        return out
