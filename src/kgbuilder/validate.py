"""Validate the finished graph: structure, provenance, consistency, and (with a gold file) accuracy."""

import json
from pathlib import Path

from neo4j import Driver
from pydantic import BaseModel

from .core.cypher import cypher_ident
from .core.text import norm
from .plan import ConstructionPlan
from .textschema import TextSchema


class Check(BaseModel):
    name: str
    passed: bool
    detail: str
    category: str  # structure | provenance | consistency | accuracy


class ValidationReport(BaseModel):
    checks: list[Check]
    metrics: dict[str, float]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def _scalar(driver: Driver, query: str, **params) -> int:
    rows, _, _ = driver.execute_query(query, **params)
    return rows[0][0] if rows else 0


def validate_graph(
    driver: Driver,
    plan: ConstructionPlan | None,
    schema: TextSchema | None,
    expected_counts: dict[str, int] | None = None,
    gold_path: Path | None = None,
) -> ValidationReport:
    checks: list[Check] = []
    metrics: dict[str, float] = {}

    def add(name: str, ok: bool, detail: str, category: str):
        checks.append(Check(name=name, passed=ok, detail=detail, category=category))

    # --- structure: domain graph
    if plan:
        for rule in plan.nodes:
            n = _scalar(driver, f"MATCH (n:{cypher_ident(rule.label)}) RETURN count(n)")
            exp = (expected_counts or {}).get(rule.label)
            add(
                f"domain:{rule.label} count",
                exp is None or n == exp,
                f"{n} nodes" + (f", expected {exp}" if exp is not None else ""),
                "structure",
            )
            uniq = _scalar(
                driver,
                f"MATCH (n:{cypher_ident(rule.label)}) WITH n.{cypher_ident(rule.unique_column)} AS k, count(*) AS c WHERE c > 1 RETURN count(k)",
            )
            add(f"domain:{rule.label} unique keys", uniq == 0, f"{uniq} duplicated keys", "structure")
        isolated = sum(
            _scalar(driver, f"MATCH (n:{cypher_ident(r.label)}) WHERE NOT (n)--() RETURN count(n)")
            for r in plan.nodes
        )
        # unreferenced rows are a property of the data, not a build error, so this is reported but never fails
        add(
            "domain: isolated nodes (informational)",
            True,
            f"{isolated} domain nodes have no relationships",
            "structure",
        )
        metrics["domain_nodes"] = sum(
            _scalar(driver, f"MATCH (n:{cypher_ident(r.label)}) RETURN count(n)") for r in plan.nodes
        )

    # --- structure: lexical graph
    docs = _scalar(driver, "MATCH (d:Document) RETURN count(d)")
    chunks = _scalar(driver, "MATCH (c:Chunk) RETURN count(c)")
    metrics.update(documents=docs, chunks=chunks)
    if docs:
        orphan_chunks = _scalar(
            driver, "MATCH (c:Chunk) WHERE NOT (c)-[:PART_OF]->(:Document) RETURN count(c)"
        )
        add(
            "lexical: every chunk belongs to a document",
            orphan_chunks == 0,
            f"{orphan_chunks} orphan chunks",
            "structure",
        )
        empty_docs = _scalar(driver, "MATCH (d:Document) WHERE NOT (:Chunk)-[:PART_OF]->(d) RETURN count(d)")
        add(
            "lexical: every document has chunks",
            empty_docs == 0,
            f"{empty_docs} empty documents",
            "structure",
        )
        unlinked = _scalar(driver, "MATCH (d:Document) WHERE NOT (d)-[:ABOUT]->() RETURN count(d)")
        add(
            "link: every document is linked to the domain graph",
            unlinked == 0,
            f"{unlinked} of {docs} documents unlinked",
            "structure",
        )

    # --- structure + provenance: subject graph
    entities = _scalar(driver, "MATCH (e:Entity) RETURN count(e)")
    metrics["entities"] = entities
    if entities:
        no_mention = _scalar(driver, "MATCH (e:Entity) WHERE NOT (:Chunk)-[:MENTIONS]->(e) RETURN count(e)")
        add(
            "provenance: every entity is mentioned in a chunk",
            no_mention == 0,
            f"{no_mention} entities without a source chunk",
            "provenance",
        )

        facts, _, _ = driver.execute_query(
            "MATCH (s:Entity)-[r]->(o:Entity) "
            "RETURN type(r) AS pred, s.type AS st, o.type AS ot, r.chunk_id AS cid, r.evidence AS ev, "
            "s.name AS sname, o.name AS oname, coalesce(s.aliases, []) AS sal, coalesce(o.aliases, []) AS oal"
        )
        metrics["facts"] = len(facts)
        no_prov = [f for f in facts if not f["cid"] or not f["ev"]]
        add(
            "provenance: every fact has chunk_id and evidence",
            not no_prov,
            f"{len(no_prov)} of {len(facts)} facts lack provenance",
            "provenance",
        )

        chunk_text = {
            r["id"]: norm(r["t"])
            for r in driver.execute_query("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.text AS t")[0]
        }
        bad_ev = [
            f for f in facts if f["cid"] and f["ev"] and norm(f["ev"]) not in chunk_text.get(f["cid"], "")
        ]
        add(
            "provenance: evidence quotes exist in their chunk",
            not bad_ev,
            f"{len(bad_ev)} of {len(facts)} quotes not found",
            "provenance",
        )
        metrics["evidence_verified_rate"] = 1 - len(bad_ev) / len(facts) if facts else 1.0

        if schema:
            bad_type = [f for f in facts if not schema.allows(f["st"], f["pred"], f["ot"])]
            add(
                "consistency: every fact conforms to the text schema",
                not bad_type,
                f"{len(bad_type)} facts violate the schema",
                "consistency",
            )
            types_ok = _scalar(
                driver,
                "MATCH (e:Entity) WHERE e.type IN $ok RETURN count(e)",
                ok=sorted(schema.entity_names()),
            )
            add(
                "consistency: every entity type is in the schema",
                types_ok == entities,
                f"{entities - types_ok} entities of unknown type",
                "consistency",
            )

        dupes = _scalar(
            driver,
            "MATCH (e:Entity) WITH e.type AS t, toLower(e.name) AS n, count(*) AS c WHERE c > 1 RETURN count(n)",
        )
        add(
            "resolution: no exact-duplicate entities remain",
            dupes == 0,
            f"{dupes} duplicate name groups",
            "consistency",
        )
        loops = _scalar(driver, "MATCH (e:Entity)-[r]->(e) RETURN count(r)")
        add("consistency: no self-referencing facts", loops == 0, f"{loops} self loops", "consistency")
        linked = _scalar(driver, "MATCH (e:Entity)-[:REFERS_TO]->() RETURN count(DISTINCT e)")
        metrics["entities_linked_to_domain"] = linked

        if gold_path and Path(gold_path).exists():
            gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
            found = {
                (
                    frozenset(norm(a) for a in [f["sname"], *f["sal"]]),
                    f["pred"],
                    frozenset(norm(a) for a in [f["oname"], *f["oal"]]),
                )
                for f in facts
            }
            hit = 0
            for g in gold:
                if any(
                    norm(g["subject"]) in s and g["predicate"] == p and norm(g["object"]) in o
                    for s, p, o in found
                ):
                    hit += 1
            recall = hit / len(gold) if gold else 1.0
            metrics["gold_recall"] = recall
            add(
                "accuracy: gold recall", recall >= 0.5, f"recall {recall:.0%} ({hit}/{len(gold)})", "accuracy"
            )

    return ValidationReport(checks=checks, metrics=metrics)
