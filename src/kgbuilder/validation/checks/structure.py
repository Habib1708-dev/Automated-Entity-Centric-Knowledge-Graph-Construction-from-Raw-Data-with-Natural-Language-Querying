"""Structure checks: is each graph layer complete and shaped as intended?

DomainStructureCheck   node counts match the source rows, keys are unique (needs the plan).
LexicalStructureCheck  every chunk has a document, every document has chunks and a link to the domain.
"""

from ...core.cypher import cypher_ident
from ..report import CheckOutput
from .base import CheckContext


class DomainStructureCheck:
    """The imported domain graph against the plan and the profiled row counts."""

    def run(self, ctx: CheckContext) -> CheckOutput:
        out = CheckOutput()
        if ctx.plan is None:
            return out
        expected = ctx.expected_counts or {}
        total = isolated = 0
        for rule in ctx.plan.nodes:
            label, key = cypher_ident(rule.label), cypher_ident(rule.unique_column)
            count = ctx.scalar(f"MATCH (n:{label}) RETURN count(n)")
            total += count
            want = expected.get(rule.label)
            detail = f"{count} nodes" + (f", expected {want}" if want is not None else "")
            out.add(f"domain:{rule.label} count", want is None or count == want, detail, "structure")

            duplicated = ctx.scalar(
                f"MATCH (n:{label}) WITH n.{key} AS k, count(*) AS c WHERE c > 1 RETURN count(k)"
            )
            out.add(
                f"domain:{rule.label} unique keys",
                duplicated == 0,
                f"{duplicated} duplicated keys",
                "structure",
            )
            isolated += ctx.scalar(f"MATCH (n:{label}) WHERE NOT (n)--() RETURN count(n)")

        # unreferenced rows are a property of the data, not a build error: reported, never failing
        out.add(
            "domain: isolated nodes (informational)",
            True,
            f"{isolated} domain nodes have no relationships",
            "structure",
        )
        out.metrics.update(domain_nodes=total, domain_isolated_nodes=isolated)
        return out


class LexicalStructureCheck:
    """Documents and chunks, and the document-to-domain links."""

    def run(self, ctx: CheckContext) -> CheckOutput:
        out = CheckOutput()
        documents = ctx.scalar("MATCH (d:Document) RETURN count(d)")
        chunks = ctx.scalar("MATCH (c:Chunk) RETURN count(c)")
        out.metrics.update(documents=documents, chunks=chunks)
        if not documents:
            return out

        orphans = ctx.scalar("MATCH (c:Chunk) WHERE NOT (c)-[:PART_OF]->(:Document) RETURN count(c)")
        out.add(
            "lexical: every chunk belongs to a document",
            orphans == 0,
            f"{orphans} orphan chunks",
            "structure",
        )
        empty = ctx.scalar("MATCH (d:Document) WHERE NOT (:Chunk)-[:PART_OF]->(d) RETURN count(d)")
        out.add("lexical: every document has chunks", empty == 0, f"{empty} empty documents", "structure")
        if ctx.plan is not None:  # without a domain graph there is nothing to link to
            unlinked = ctx.scalar("MATCH (d:Document) WHERE NOT (d)-[:ABOUT]->() RETURN count(d)")
            out.add(
                "link: every document is linked to the domain graph",
                unlinked == 0,
                f"{unlinked} of {documents} documents unlinked",
                "structure",
            )
        return out
