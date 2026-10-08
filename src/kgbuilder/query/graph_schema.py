"""What the graph holds, described for the model that writes query plans (R74) and the fallback Cypher.

Role in the pipeline: read once per `kg qa` system from the current graph (graph_store.py), rendered into the
planner and text2cypher prompts (planner.py, exact.py).
Design: read from the graph itself, not from the plan and schema files, because the graph is what a query
runs against: labels with their property keys and a few example values, relationship patterns, the entity
types and the claim patterns (subject type, predicate, object type) of the observations. Every value comes
from the dataset at hand, which the prompt-engineering rules allow; the fixed shape of the pipeline's own
nodes is described in the prompts. Chunk texts, vectors and evidence quotes are left out: long, and never
what a filter or count needs. `records_only` cuts the schema down to the plan's record layer, for the
records-plus-vector system (R73). The retrieval index layer (R119, graph/index_layer.py) is left out: no plan
queries it, and indexing a graph must not change the planner's prompt.
Not here: checking a query (cypher_check.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.cypher import cypher_ident
from ..graph.index_layer import RETRIEVAL_UNIT

# Properties never shown: text and vectors that no filter or count needs, and that would crowd out the rest
# The identity edges' audit fields (R75: reason, score, by) and the attachments' route (R76: how, on
# HAS_OBSERVATION and on a text ABOUT link) are left out for the same reason
_HIDDEN_PROPERTIES = {"embedding", "text", "evidence", "context", "reason", "score", "by", "how"}
_EXAMPLES = 3  # example values per property: enough to show a format ("2019", "POWER TRAIN:...")
_EXAMPLE_CHARS = 60  # an example longer than this is cut: it shows the format, not the content


class PropertyInfo(BaseModel):
    """A property key with its type and a few values, the values written as Cypher literals."""

    name: str
    # Neo4j's own type name (`valueType`, "NOT NULL" dropped): INTEGER, STRING, DATE, LIST<STRING>, ...
    # Shown because a query must compare a value with the property's type: an INTEGER year never equals
    # '2015', and a DATE does not start with '2016' (R71's held-out run: five filters returned nothing)
    type: str
    examples: list[str]


class LabelInfo(BaseModel):
    label: str
    count: int
    properties: list[PropertyInfo]


class RelationshipInfo(BaseModel):
    source: str
    type: str
    target: str
    count: int
    # the relationship's own properties (R74): a link table's columns live here (a supplier's lead time and
    # cost for one part), and a query that cannot see them cannot filter on them (R73's furniture misses)
    properties: list[PropertyInfo] = []


class ClaimInfo(BaseModel):
    subject_type: str
    predicate: str
    object_type: str
    count: int


class GraphSchema(BaseModel):
    """The labels, relationships and claim patterns of one graph, with counts."""

    labels: list[LabelInfo]
    relationships: list[RelationshipInfo]
    claims: list[ClaimInfo]

    def text(self) -> str:
        """The schema as the prompts show it: one line per label, relationship pattern and claim pattern."""
        lines = ["Node labels (count) and property keys (type) with example values as Cypher literals:"]
        for info in self.labels:
            props = "; ".join(f"{p.name} ({p.type}) e.g. {', '.join(p.examples)}" for p in info.properties)
            lines.append(f"- :{info.label} ({info.count}): {props}")
        lines.append("Relationships (count):")
        for r in self.relationships:
            props = "; ".join(f"{p.name} ({p.type}) e.g. {', '.join(p.examples)}" for p in r.properties)
            lines.append(
                f"- (:{r.source})-[:{r.type}]->(:{r.target}) ({r.count})" + (f": {props}" if props else "")
            )
        if self.claims:  # none in the record layer alone (records_only), where the heading would mislead
            lines.append(
                "Claim patterns of :Observation nodes, as subject entity type, predicate, object entity type:"
            )
            lines += [f"- {c.subject_type} {c.predicate} {c.object_type} ({c.count})" for c in self.claims]
        return "\n".join(lines)

    def records_only(self, record_labels: set[str]) -> "GraphSchema":
        """The record layer alone (R73, records plus vector RAG): the labels in `record_labels` (the plan's),
        the relationships between two of them, and no claim patterns."""
        return GraphSchema(
            labels=[info for info in self.labels if info.label in record_labels],
            relationships=[
                r for r in self.relationships if r.source in record_labels and r.target in record_labels
            ],
            claims=[],
        )

    def names_outside(self, record_labels: set[str]) -> frozenset[str]:
        """The labels and relationship types a query over the record layer alone may not name: every label
        outside `record_labels`, and every relationship type that never joins two of them. A type that does
        join two of them stays allowed even where the text layer uses it too (`PART_OF` joins a part to its
        product and a chunk to its document)."""
        inside = {
            r.type for r in self.relationships if r.source in record_labels and r.target in record_labels
        }
        labels = {info.label for info in self.labels} - record_labels
        return frozenset(labels | ({r.type for r in self.relationships} - inside))


def read_graph_schema(driver: Driver) -> GraphSchema:
    """The schema of the graph `driver` points at."""
    return GraphSchema(labels=_labels(driver), relationships=_relationships(driver), claims=_claims(driver))


def _labels(driver: Driver) -> list[LabelInfo]:
    records, _, _ = driver.execute_query(
        # the property keys of every node of a label, flattened in Python: nodes of one label may differ. The
        # layer's label is a value here, not a name, so a graph without the layer gets no server warning
        "MATCH (n) WHERE NOT $layer IN labels(n) UNWIND labels(n) AS label "
        "RETURN label, count(*) AS n, collect(keys(n)) AS keys ORDER BY label",
        layer=RETRIEVAL_UNIT,
    )
    labels = []
    for r in records:
        keys = sorted({k for ks in r["keys"] for k in ks} - _HIDDEN_PROPERTIES)
        labels.append(
            LabelInfo(
                label=r["label"], count=r["n"], properties=[_property(driver, r["label"], k) for k in keys]
            )
        )
    return labels


def _property(driver: Driver, label: str, key: str) -> PropertyInfo:
    # a label and a key cannot be parameters: they are escaped, like every identifier in the project
    return _examples(driver, f"(x:{cypher_ident(label)})", key)


def _relationship_property(driver: Driver, rel: RelationshipInfo, key: str) -> PropertyInfo:
    pattern = f"(:{cypher_ident(rel.source)})-[x:{cypher_ident(rel.type)}]->(:{cypher_ident(rel.target)})"
    return _examples(driver, pattern, key)


def _examples(driver: Driver, pattern: str, key: str) -> PropertyInfo:
    """The type and a few values of property `key` of the node or relationship bound to `x` in `pattern`."""
    prop = cypher_ident(key)
    # raw values, not toString(): a property may hold a list (aliases), which toString() refuses; the type
    # is the first value's, as nodes of one label written by one import rule share their types
    records, _, _ = driver.execute_query(
        f"MATCH {pattern} WHERE x.{prop} IS NOT NULL "
        f"RETURN DISTINCT x.{prop} AS v, valueType(x.{prop}) AS t ORDER BY v LIMIT {_EXAMPLES}"
    )
    value_type = records[0]["t"].replace(" NOT NULL", "") if records else "ANY"
    return PropertyInfo(name=key, type=value_type, examples=[cypher_literal(r["v"]) for r in records])


def cypher_literal(value: object) -> str:
    """`value` written as a Cypher literal ('text', 2015, true, date('2015-12-10'), ['a']); a long text is
    cut to its start, as it only shows the format."""
    if isinstance(value, bool):  # before numbers: a bool is an int in Python
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(cypher_literal(item) for item in value) + "]"
    if isinstance(value, str):
        return "'" + value[:_EXAMPLE_CHARS].replace("'", "\\'") + "'"
    # neo4j.time values (Date, DateTime, ...) print as ISO text; their Cypher function is their type's name
    return (
        f"{_temporal_function(value)}('{value.iso_format()}')" if hasattr(value, "iso_format") else str(value)
    )


def _temporal_function(value: object) -> str:
    """The Cypher function that builds a temporal value of this kind: date, datetime, localdatetime, ..."""
    return type(value).__name__.lower()


def _relationships(driver: Driver) -> list[RelationshipInfo]:
    records, _, _ = driver.execute_query(
        "MATCH (a)-[r]->(b) WHERE NOT $layer IN labels(a) AND NOT $layer IN labels(b) "
        "UNWIND labels(a) AS source UNWIND labels(b) AS target "
        "RETURN source, type(r) AS type, target, count(*) AS n, collect(DISTINCT keys(r)) AS keys "
        "ORDER BY type, source, target",
        layer=RETRIEVAL_UNIT,
    )
    relationships = []
    for r in records:
        info = RelationshipInfo(source=r["source"], type=r["type"], target=r["target"], count=r["n"])
        keys = sorted({k for ks in r["keys"] for k in ks} - _HIDDEN_PROPERTIES)
        info.properties = [_relationship_property(driver, info, k) for k in keys]
        relationships.append(info)
    return relationships


def _claims(driver: Driver) -> list[ClaimInfo]:
    records, _, _ = driver.execute_query(
        "MATCH (s:Mention)<-[:SUBJECT]-(o:Observation)-[:OBJECT]->(t:Mention) "
        # a claim missing a part names no pattern; extraction never writes one, a hand edit might
        "WHERE o.predicate IS NOT NULL AND s.type IS NOT NULL AND t.type IS NOT NULL "
        "RETURN s.type AS s, o.predicate AS p, t.type AS t, count(*) AS n ORDER BY n DESC, p"
    )
    return [
        ClaimInfo(subject_type=r["s"], predicate=r["p"], object_type=r["t"], count=r["n"]) for r in records
    ]
