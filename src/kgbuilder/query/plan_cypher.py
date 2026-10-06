"""The Cypher of query plans (R74): each primitive compiled by code to one parameterised, read-only fragment.

Role in the pipeline: plan_run.py runs a checked plan (plan.py) step by step; every query it sends comes
from a function here, never from a model. A step's items travel between steps as ids, one id space per
kind: a record's element id, an entity's canonical id (R75: an individual's or a concept's, which its
mentions carry on their identity edge, graph/canonical.py), a claim's (observation's) `id`, a chunk's
`chunk_id`.
Design: pure functions returning `(cypher, parameters)`, so each fragment is tested as text and once against
Neo4j. Labels, relationship types and property keys go through `cypher_ident`; every value is a parameter.
How a value is compared follows the property's own type (`PropertyInfo.type`): a DATE by `date()` or its
year, a text that holds a number (`'$246'`) by the number in it (R73's furniture prices), a text by
case-insensitive equality or containment, a list of texts by any of its members. The claim layer's shape
(`HAS_OBSERVATION`, `SUBJECT`, `OBJECT`, `FROM`) is written here once, so a claim's direction can never be
walked backwards (G14, R72).
Not here: which fragment a step needs and in what order (plan_run.py), checking a plan (plan.py).
"""

from typing import Literal

from ..core.cypher import cypher_ident
from ..graph.canonical import canonical_id, canonical_name
from .graph_schema import PropertyInfo
from .plan import Modality, Operator, Tone, Truth

Fragment = tuple[str, dict[str, object]]
Direction = Literal["out", "in", "both"]

# Everything that is not part of a number: removed from a text before it is compared as one ('$1,289' ->
# 1289 after the comma is dropped too). A parameter, so the query text holds no string literal.
_NON_NUMERIC = r"[^0-9.\-]"
_COMPARISONS = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

# The text of each kind of item: what read_check and answer_from_chunks read. A record's text: the chunks its
# claims come from, the documents about it, the sections about it and the claims of the mentions that refer
# to it (as traversal.py's thing patterns); an entity's: the chunks of the claims whose mentions refer to it
# or that hang on it (an individual, R76), and the chunks that mention it; a claim's: its own chunk.
_ITEM_CHUNKS: dict[str, list[str]] = {
    "record": [
        "MATCH (t) WHERE elementId(t) IN $ids "
        "MATCH (t)-[:HAS_OBSERVATION]->(:Observation)-[:FROM]->(c:Chunk) "
        "RETURN DISTINCT elementId(t) AS item, c.chunk_id AS chunk",
        "MATCH (t) WHERE elementId(t) IN $ids MATCH (t)<-[:ABOUT]-(:Document)<-[:PART_OF]-(c:Chunk) "
        "RETURN DISTINCT elementId(t) AS item, c.chunk_id AS chunk",
        "MATCH (t) WHERE elementId(t) IN $ids MATCH (t)<-[:ABOUT]-(c:Chunk) "
        "RETURN DISTINCT elementId(t) AS item, c.chunk_id AS chunk",
        "MATCH (t) WHERE elementId(t) IN $ids "
        "MATCH (t)<-[:REFERS_TO]-(:Mention)<-[:SUBJECT|OBJECT]-(:Observation)-[:FROM]->(c:Chunk) "
        "RETURN DISTINCT elementId(t) AS item, c.chunk_id AS chunk",
    ],
    "entity": [
        f"MATCH (m:Mention) WITH m, {canonical_id('m')} AS item WHERE item IN $ids "
        "MATCH (m)<-[:SUBJECT|OBJECT]-(:Observation)-[:FROM]->(c:Chunk) "
        "RETURN DISTINCT item, c.chunk_id AS chunk",
        f"MATCH (m:Mention) WITH m, {canonical_id('m')} AS item WHERE item IN $ids "
        "MATCH (m)<-[:MENTIONS]-(c:Chunk) RETURN DISTINCT item, c.chunk_id AS chunk",
        "MATCH (t:Individual)-[:HAS_OBSERVATION]->(:Observation)-[:FROM]->(c:Chunk) WHERE t.id IN $ids "
        "RETURN DISTINCT t.id AS item, c.chunk_id AS chunk",
    ],
    "claim": [
        "MATCH (o:Observation)-[:FROM]->(c:Chunk) WHERE o.id IN $ids "
        "RETURN o.id AS item, c.chunk_id AS chunk",
    ],
    "chunk": ["MATCH (c:Chunk) WHERE c.chunk_id IN $ids RETURN c.chunk_id AS item, c.chunk_id AS chunk"],
}


def condition(var: str, prop: PropertyInfo, operator: Operator, value: object, key: str) -> Fragment:
    """The WHERE condition comparing `var.prop` with `value` (parameter `$key`) as the property's type
    demands. The value was checked by plan.py; here it is only converted to its type."""
    ref = f"{var}.{cypher_ident(prop.name)}"
    temporal = prop.type.startswith(("DATE", "LOCAL", "ZONED"))
    if operator == "year":
        year = int(float(value))
        if temporal:
            return f"{ref}.year = ${key}", {key: year}
        # a text that holds a date ('11/07/2016'): the year as a whole number among its digits
        return f"toString({ref}) =~ ${key}", {key: f".*(?<![0-9]){year}(?![0-9]).*"}
    if operator == "contains":
        if prop.type.startswith("LIST"):
            return f"any(x IN {ref} WHERE toLower(toString(x)) CONTAINS toLower(${key}))", {key: str(value)}
        return f"toLower(toString({ref})) CONTAINS toLower(${key})", {key: str(value)}
    cmp = _COMPARISONS[operator]
    if temporal:
        return f"{ref} {cmp} date(${key})", {key: str(value)}
    if prop.type in ("INTEGER", "FLOAT"):
        return f"{ref} {cmp} ${key}", {key: _number(value)}
    if prop.type == "BOOLEAN":
        return f"{ref} {cmp} ${key}", {
            key: value if isinstance(value, bool) else str(value).lower() == "true"
        }
    if operator in ("<", "<=", ">", ">="):
        # a text holding a number ('$246', '1,289'): compared as that number; a text with none drops out
        clean = f"replace(toString({ref}), ',', '')"
        number = f"toFloat(apoc.text.regreplace({clean}, ${key}_pattern, ''))"
        return f"{number} {cmp} ${key}", {key: _number(value), f"{key}_pattern": _NON_NUMERIC}
    if prop.type.startswith("LIST"):
        return f"any(x IN {ref} WHERE toLower(toString(x)) {cmp} toLower(${key}))", {key: str(value)}
    return f"toLower(toString({ref})) {cmp} toLower(${key})", {key: str(value)}


def _number(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return float(str(value).replace(",", "").strip().lstrip("$€£"))


def filter_records(
    label: str,
    prop: PropertyInfo | None,
    operator: Operator | None,
    value: object,
    within: list[str] | None,
    cap: int,
) -> Fragment:
    """The records of `label` (among `within` when given) whose property meets the condition; every
    record of the label without a property."""
    where: list[str] = []
    params: dict[str, object] = {}
    if within is not None:
        where.append("elementId(n) IN $within")
        params["within"] = within
    if prop is not None and operator is not None:
        text, extra = condition("n", prop, operator, value, "value")
        where.append(text)
        params.update(extra)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return f"MATCH (n:{cypher_ident(label)}){clause} RETURN elementId(n) AS id LIMIT {int(cap)}", params


def _arrow(relationship: str, direction: Direction) -> str:
    rel = f"[r:{cypher_ident(relationship)}]"
    return {"out": f"-{rel}->", "in": f"<-{rel}-", "both": f"-{rel}-"}[direction]


def related(
    relationship: str,
    direction: Direction,
    label: str | None,
    rel_prop: PropertyInfo | None,
    operator: Operator | None,
    value: object,
    ids: list[str],
    cap: int,
) -> Fragment:
    """The records at the other end of `relationship` from the records `ids`, in the direction code chose
    from the schema; optionally of `label` and with the relationship's property meeting a condition."""
    arrow = _arrow(relationship, direction)
    where = ["elementId(a) IN $ids", "elementId(b) <> elementId(a)"]
    params: dict[str, object] = {"ids": ids}
    if label is not None:
        where.append(f"b:{cypher_ident(label)}")
    if rel_prop is not None and operator is not None:
        text, extra = condition("r", rel_prop, operator, value, "value")
        where.append(text)
        params.update(extra)
    cypher = (
        f"MATCH (a){arrow}(b) WHERE {' AND '.join(where)} RETURN DISTINCT elementId(b) AS id LIMIT {int(cap)}"
    )
    return cypher, params


def parts_of(ids: list[str], labels: list[str], cap: int) -> Fragment:
    """The records whose relationships point, within two hops through records only, at the records `ids`
    (a product's assemblies and their components; the complaints and recalls of a vehicle)."""
    cypher = (
        "MATCH p = (x)-[*1..2]->(t) WHERE elementId(t) IN $ids "
        "AND all(n IN nodes(p) WHERE any(l IN labels(n) WHERE l IN $labels)) "
        f"RETURN DISTINCT elementId(x) AS id LIMIT {int(cap)}"
    )
    return cypher, {"ids": ids, "labels": labels}


# The stored modalities a plan's `modality` finds. "actual", the default, means the claims that hold: a
# claim that holds whenever its condition holds ("the frame creaks whenever someone sits down") reports that
# the thing happens, and leaving it out lost 5 answers in R77 part b (the user's decision for part c); only a
# possible claim says that it may not happen. Asked for by name, "conditional" and "possible" stay narrow.
# Kept in code, not in the plan's field or the prompt, so the planner's requests and their cache stay as
# they were and the change is measured alone.
_MODALITIES: dict[Modality, list[str]] = {
    "actual": ["actual", "conditional"],
    "possible": ["possible"],
    "conditional": ["conditional"],
}


def find_claims(
    records: list[str] | None,
    entities: list[str] | None,
    predicate: str | None,
    subjects: list[str] | None,
    objects: list[str] | None,
    tone: Tone | None,
    time_words: str | None,
    cap: int,
    either_end: bool = False,
    truth: Truth = "affirmed",
    modality: Modality = "actual",
    read_all: bool = False,
) -> Fragment:
    """The claims about the records (attached to them, or with a mention that refers to one) or about the
    entities (a mention that refers to one, or attached to an individual, R76), narrowed by predicate,
    subject and object entities, tone and time; None leaves a part out. With `either_end`, the subject and
    the object entities may each be on either end of the claim (R78). Only claims of the given `truth` and
    `modality` come back (R77; by default the claims whose stored triple holds, `_MODALITIES`; "negated"
    is the statement's truth, part d); a claim stored before R77 has neither and counts as affirmed and
    actual. With `read_all` (claims that will be read as text, R77 part e) the defaults filter nothing: only a
    truth or modality the question asked for narrows the claims."""
    where: list[str] = []
    params: dict[str, object] = {}
    about: list[str] = []
    if records is not None:
        about.append("EXISTS { MATCH (t)-[:HAS_OBSERVATION]->(o) WHERE elementId(t) IN $records }")
        about.append(
            "EXISTS { MATCH (o)-[:SUBJECT|OBJECT]->(:Mention)-[:REFERS_TO]->(t) "
            "WHERE elementId(t) IN $records }"
        )
        params["records"] = records
    if entities is not None:
        about.append(
            f"EXISTS {{ MATCH (o)-[:SUBJECT|OBJECT]->(e:Mention) WHERE {canonical_id('e')} IN $entities }}"
        )
        about.append("EXISTS { MATCH (t:Individual)-[:HAS_OBSERVATION]->(o) WHERE t.id IN $entities }")
        params["entities"] = entities
    if about:
        where.append("(" + " OR ".join(about) + ")")
    if predicate is not None:
        where.append("o.predicate = $predicate")
        params["predicate"] = predicate
    subject_end, object_end = ("SUBJECT|OBJECT", "SUBJECT|OBJECT") if either_end else ("SUBJECT", "OBJECT")
    if subjects is not None:
        where.append(
            f"EXISTS {{ MATCH (o)-[:{subject_end}]->(s:Mention) WHERE {canonical_id('s')} IN $subjects }}"
        )
        params["subjects"] = subjects
    if objects is not None:
        where.append(
            f"EXISTS {{ MATCH (o)-[:{object_end}]->(x:Mention) WHERE {canonical_id('x')} IN $objects }}"
        )
        params["objects"] = objects
    if tone is not None:
        where.append("o.polarity = $tone")
        params["tone"] = tone
    if time_words is not None:
        where.append("toLower(coalesce(o.time, '')) CONTAINS toLower($time)")
        params["time"] = time_words
    # R77 part d: "negated", asked for by the question, is said of the statement, so a denial in either
    # form comes back ("leak", negated, and a state named "will not switch off"); the default keeps the
    # claims whose stored triple holds, which counts such a named state with the others. A graph from before
    # part d has no triple truth; its truth was said of the triple.
    if truth == "negated" or not read_all:
        truth_of = "o.truth" if truth == "negated" else "coalesce(o.triple_truth, o.truth)"
        where.append(f"coalesce({truth_of}, 'affirmed') = $truth")
        params["truth"] = truth
    if modality != "actual" or not read_all:
        where.append("coalesce(o.modality, 'actual') IN $modalities")
        params["modalities"] = _MODALITIES[modality]
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return f"MATCH (o:Observation){clause} RETURN DISTINCT o.id AS id LIMIT {int(cap)}", params


def item_chunks(kind: str, ids: list[str]) -> list[Fragment]:
    """The fragments that give each item of `kind` its text: rows of (item, chunk)."""
    return [(query, {"ids": ids}) for query in _ITEM_CHUNKS[kind]]


def item_documents(kind: str, ids: list[str]) -> Fragment:
    """Rows of (item, document id): the documents a claim's or a chunk's text belongs to; a record or an
    entity is its own unit."""
    if kind == "claim":
        return (
            "MATCH (o:Observation)-[:FROM]->(:Chunk)-[:PART_OF]->(d:Document) WHERE o.id IN $ids "
            "RETURN o.id AS item, d.doc_id AS document",
            {"ids": ids},
        )
    if kind == "chunk":
        return (
            "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE c.chunk_id IN $ids "
            "RETURN c.chunk_id AS item, d.doc_id AS document",
            {"ids": ids},
        )
    return "UNWIND $ids AS id RETURN id AS item, id AS document", {"ids": ids}


def claims_about(ids: list[str], label: str | None = None) -> Fragment:
    """Rows of (claim, record): the records the claims hang on (`HAS_OBSERVATION`), only those of `label`
    when given (R79)."""
    only = f" AND t:{cypher_ident(label)}" if label is not None else ""
    return (
        f"MATCH (t)-[:HAS_OBSERVATION]->(o:Observation) WHERE o.id IN $ids{only} "
        "RETURN o.id AS item, elementId(t) AS about",
        {"ids": ids},
    )


def value_groups(ids: list[str], prop: str, by_year: bool) -> Fragment:
    """Rows of (value, count): how many of the records `ids` share each value of `prop`, a date grouped by its
    year with `by_year` (R79: the component in the most recalls, the year with the most of them)."""
    ref = f"n.{cypher_ident(prop)}"
    value = f"{ref}.year" if by_year else ref
    return (
        f"MATCH (n) WHERE elementId(n) IN $ids AND {ref} IS NOT NULL "
        f"RETURN {value} AS value, count(DISTINCT n) AS n",
        {"ids": ids},
    )


def claim_ends(ids: list[str], end: Literal["subject", "object"]) -> Fragment:
    """Rows of (claim, name): the canonical name of each claim's subject or object (R75: the entity its
    mention refers to)."""
    rel = "SUBJECT" if end == "subject" else "OBJECT"
    return (
        f"MATCH (o:Observation)-[:{rel}]->(e:Mention) WHERE o.id IN $ids "
        f"RETURN o.id AS item, {canonical_name('e')} AS name",
        {"ids": ids},
    )


def record_values(ids: list[str], prop: str) -> Fragment:
    """Rows of (record, value) of property `prop`."""
    return (
        f"MATCH (n) WHERE elementId(n) IN $ids RETURN elementId(n) AS item, n.{cypher_ident(prop)} AS value",
        {"ids": ids},
    )


def related_groups(relationship: str, direction: Direction, label: str | None, ids: list[str]) -> Fragment:
    """Rows of (group record, count): how many of the records `ids` each record at the other end of
    `relationship` is linked to (rank: which vehicle has the most recalls)."""
    arrow = _arrow(relationship, direction)
    target = f" AND b:{cypher_ident(label)}" if label is not None else ""
    return (
        f"MATCH (a){arrow}(b) WHERE elementId(a) IN $ids{target} "
        "RETURN elementId(b) AS item, count(DISTINCT a) AS n",
        {"ids": ids},
    )


def record_names(ids: list[str], name_properties: dict[str, str]) -> Fragment:
    """Rows of (record, name): each record's display name, by its label's name property."""
    return (
        "MATCH (n) WHERE elementId(n) IN $ids "
        "RETURN elementId(n) AS item, toString(n[$props[head(labels(n))]]) AS name",
        {"ids": ids, "props": name_properties},
    )
