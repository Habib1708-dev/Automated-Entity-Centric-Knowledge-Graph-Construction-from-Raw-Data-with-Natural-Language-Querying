"""Write the subject graph: `(:Mention)` names, `(Chunk)-[:MENTIONS]->(Mention)`, `(:Observation)` claims.

Role in the pipeline: second half of `kg extract`; input is the verified triples from extraction.py. The
derivation in the link stage writes its mentions and observations through this module too, so the graph
has one shape for every claim, whoever made it.
Design: a mention is one name of one type in one document (R75, layered-model Step 5), keyed through
`core.identity.mention_id`: two chunks of a document naming "the Linden Hive" give one mention, the same
name in another document another. What a mention stands for (a record, one particular thing, a kind
shared by many documents) is decided later by the identity stage (resolution/identity.py), which links each
mention to its canonical entity with an edge; nothing here merges or guesses. Until R75 an `:Entity` was one
node per type and name across all documents, so two people of one name were one node (task file, Step 5).
A claim is its own node (R64), pointing at the mentions of its two ends:
    (:Observation {id, predicate, chunk_id, evidence, subject_name, object_name, extractor,
                   polarity, time, truth, modality, condition, value, unit})
      -[:SUBJECT]->(:Mention)   -[:OBJECT]->(:Mention)   -[:FROM]->(:Chunk)
A claim's qualifiers (R66) are properties of its observation: its polarity, the time its sentence gives,
and for a `Value` object the parsed number and unit; since R77 also its assertion: whether the text states
or denies it (`truth`), whether it holds, may hold or holds under a condition (`modality`), and that
condition. A value's mention keeps the claim's own wording ("25kg"); the identity stage gives every wording
of one number one canonical concept ("25 kg").
The link stage later attaches each observation to the thing its document is about (`HAS_OBSERVATION`). The
observation keeps the names the extractor gave its two ends (`subject_name`, `object_name`), so a claim
keeps what its own document said, whatever its mentions are found to refer to (R44).
All writes are MERGE on the ids, so re-running extraction does not duplicate anything.
Not here: deciding which triples are valid (extraction.py), what a mention refers to (resolution/) and
attaching observations to things (resolution/linking.py).
"""

from neo4j import Driver
from pydantic import BaseModel

from ..core.identity import document_of, mention_id, observation_id
from ..core.values import VALUE_TYPE, Quantity, parse_quantity
from .extraction import Modality, Polarity, Triple, Truth


class SubjectGraphCounts(BaseModel):
    """What one extraction wrote; logged as metrics of the extract run."""

    mention_nodes: int  # one per type, name and document (R75; before, `entities`: one per type and name)
    facts: int  # observations written: one per distinct claim
    mentions: int  # MENTIONS edges: one per chunk and mention
    # the qualifiers of the observations written (R66): good, bad and neutral claims, numbers and times
    observations_positive: int = 0
    observations_neutral: int = 0
    observations_negative: int = 0
    observations_with_value: int = 0
    observations_with_time: int = 0
    # the assertion (R77): claims the text denies, claims that may hold, claims under a condition
    observations_negated: int = 0
    observations_possible: int = 0
    observations_conditional: int = 0


class MentionRow(BaseModel):
    """One mention to write: a name of one type in one document."""

    id: str
    name: str  # the first spelling seen in the document; other spellings differ in case or spacing only
    type: str
    doc_id: str


class ObservationRow(BaseModel):
    """One observation to write: its claim, its two mentions (by id) and its source."""

    id: str
    predicate: str
    subject: str  # mention ids
    object: str
    chunk_id: str
    evidence: str
    subject_name: str  # the claim's own wording of its two ends
    object_name: str
    polarity: Polarity = "neutral"
    time: str = ""  # verbatim from the evidence; empty when the sentence gives none
    truth: Truth = "affirmed"
    modality: Modality = "actual"
    condition: str = ""  # verbatim from the evidence; set only for a conditional claim
    value: float | None = None  # set only when the object is a `Value`
    unit: str | None = None


def mention_row(entity_type: str, name: str, chunk_id: str) -> MentionRow:
    """The mention a name in a chunk stands for: the same one in every chunk of the chunk's document."""
    doc_id = document_of(chunk_id)
    return MentionRow(
        id=mention_id(entity_type, name, doc_id), name=name.strip(), type=entity_type, doc_id=doc_id
    )


def observation_row(
    predicate: str,
    subject: str,
    obj: str,
    chunk_id: str,
    evidence: str,
    names: tuple[str, str],
    *,
    polarity: Polarity = "neutral",
    time: str = "",
    truth: Truth = "affirmed",
    modality: Modality = "actual",
    condition: str = "",
    quantity: Quantity | None = None,
) -> ObservationRow:
    """The row for one claim; `subject` and `obj` are mention ids, `names` the claim's own wording, and
    `quantity` the parsed number when the object is a `Value`."""
    subject_name, object_name = (n.strip() for n in names)
    return ObservationRow(
        id=observation_id(
            chunk_id,
            predicate,
            subject_name,
            object_name,
            time.strip(),
            truth=truth,
            modality=modality,
            condition=condition.strip(),
        ),
        predicate=predicate,
        subject=subject,
        object=obj,
        chunk_id=chunk_id,
        evidence=evidence,
        subject_name=subject_name,
        object_name=object_name,
        polarity=polarity,
        time=time.strip(),
        truth=truth,
        modality=modality,
        condition=condition.strip(),
        value=quantity.value if quantity else None,
        unit=quantity.unit if quantity else None,
    )


def write_mentions(driver: Driver, rows: list[MentionRow], mentioned_in: set[tuple[str, str]]) -> None:
    """MERGE the mentions and one MENTIONS edge per (chunk id, mention id) pair; the chunks must exist."""
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (m:Mention) REQUIRE m.id IS UNIQUE")
    # the type is a property, not a label: a `Hive` label here would collide with the domain graph's
    driver.execute_query("CREATE INDEX mention_type IF NOT EXISTS FOR (m:Mention) ON (m.type)")
    driver.execute_query(
        # ON CREATE only: a mention's display name is its first spelling, on every rerun
        "UNWIND $rows AS r MERGE (m:Mention {id: r.id}) "
        "ON CREATE SET m.name = r.name, m.type = r.type, m.doc_id = r.doc_id",
        rows=[r.model_dump() for r in rows],
    )
    driver.execute_query(
        "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.c}), (m:Mention {id: r.m}) MERGE (c)-[:MENTIONS]->(m)",
        rows=[{"c": c, "m": m} for c, m in sorted(mentioned_in)],
    )


def write_observations(driver: Driver, rows: list[ObservationRow], extractor: str) -> None:
    """MERGE each observation with its SUBJECT, OBJECT and FROM edges. The mentions and chunks must exist.

    `extractor` (the model id, or "derived") is stored on every observation.
    """
    driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE")
    if not rows:
        return
    driver.execute_query(
        "UNWIND $rows AS r MATCH (s:Mention {id: r.subject}), (t:Mention {id: r.object}), "
        "(c:Chunk {chunk_id: r.chunk_id}) "
        # the id is the whole MERGE key: the same claim stated in two chunks is two observations on purpose,
        # because each statement is separate evidence (three reviews saying "drawers stick" count as three)
        "MERGE (o:Observation {id: r.id}) "
        # ON CREATE only: a rerun must not rewrite a claim that is already stored
        "ON CREATE SET o.predicate = r.predicate, o.chunk_id = r.chunk_id, o.evidence = r.evidence, "
        "o.subject_name = r.subject_name, o.object_name = r.object_name, o.polarity = r.polarity, "
        # a null value or unit sets nothing: only a claim about a number has them
        "o.time = r.time, o.truth = r.truth, o.modality = r.modality, o.condition = r.condition, "
        "o.value = r.value, o.unit = r.unit "
        "SET o.extractor = $extractor "
        "MERGE (o)-[:SUBJECT]->(s) MERGE (o)-[:OBJECT]->(t) MERGE (o)-[:FROM]->(c)",
        rows=[r.model_dump() for r in rows],
        extractor=extractor,
    )


def _collect(
    triples: list[Triple],
) -> tuple[dict[str, MentionRow], set[tuple[str, str]], list[ObservationRow]]:
    """The rows to write, without touching the graph: mentions by id, (chunk id, mention id) pairs, and one
    observation per distinct claim."""
    mentions: dict[str, MentionRow] = {}
    mentioned_in: set[tuple[str, str]] = set()
    observations: dict[str, ObservationRow] = {}
    for t in triples:
        # extraction verified that a Value object parses; the observation carries the parsed number
        quantity = parse_quantity(t.object) if t.object_type == VALUE_TYPE else None
        ends = []
        for name, entity_type in ((t.subject, t.subject_type), (t.object, t.object_type)):
            mention = mention_row(entity_type, name, t.chunk_id)
            mentions.setdefault(mention.id, mention)
            mentioned_in.add((t.chunk_id, mention.id))
            ends.append(mention.id)
        row = observation_row(
            t.predicate,
            ends[0],
            ends[1],
            t.chunk_id,
            t.evidence,
            (t.subject, t.object),
            polarity=t.polarity,
            time=t.time,
            truth=t.truth,
            modality=t.modality,
            condition=t.condition,
            quantity=quantity,
        )
        # one id is one claim: a triple repeated in its chunk (a second pass restating the first) adds no
        # node, and the first one's mentions are kept, so an observation never gets two subjects
        observations.setdefault(row.id, row)
    return mentions, mentioned_in, list(observations.values())


def write_subject_graph(driver: Driver, triples: list[Triple], extractor: str) -> SubjectGraphCounts:
    """MERGE mentions and observations. `extractor` (the model id) is stored on each observation."""
    mentions, mentioned_in, rows = _collect(triples)
    write_mentions(driver, list(mentions.values()), mentioned_in)
    write_observations(driver, rows, extractor)
    return SubjectGraphCounts(
        mention_nodes=len(mentions),
        facts=len(rows),
        mentions=len(mentioned_in),
        observations_positive=sum(r.polarity == "positive" for r in rows),
        observations_neutral=sum(r.polarity == "neutral" for r in rows),
        observations_negative=sum(r.polarity == "negative" for r in rows),
        observations_with_value=sum(r.value is not None for r in rows),
        observations_with_time=sum(bool(r.time) for r in rows),
        observations_negated=sum(r.truth == "negated" for r in rows),
        observations_possible=sum(r.modality == "possible" for r in rows),
        observations_conditional=sum(r.modality == "conditional" for r in rows),
    )
