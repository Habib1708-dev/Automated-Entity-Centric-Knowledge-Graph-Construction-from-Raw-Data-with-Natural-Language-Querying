"""A claim's qualifiers (R66): polarity, a time from its sentence, and a number with a unit as its object.
Pure tests for parsing numbers, verification, the schema's built-in `Value`, observation ids, the
resolution guards and the judge's polarity verdicts; Neo4j tests for what the subject graph stores and what
entity resolution reads back.
"""

import hashlib

import pytest

from kgbuilder.core.errors import EvaluationError
from kgbuilder.core.identity import observation_id
from kgbuilder.core.text import norm
from kgbuilder.core.values import VALUE_TYPE, parse_quantity
from kgbuilder.resolution.concepts import concept_records
from kgbuilder.resolution.identity import IdentitySettings, resolve_identity
from kgbuilder.resolution.matchers import EntityRecord
from kgbuilder.resolution.mentions import read_mentions
from kgbuilder.resolution.resolver import find_candidates
from kgbuilder.text.extraction import RawTriple, RejectionReason, Triple, verify
from kgbuilder.text.schema import EntityType, FactType, TextSchema, validate_text_schema
from kgbuilder.text.subject_graph import write_subject_graph
from kgbuilder.validation.checks.base import CheckContext, StoredFact
from kgbuilder.validation.gold import GoldTriple
from kgbuilder.validation.judge import (
    JudgeMeta,
    PolarityVerdict,
    Verdicts,
    build_sheet,
    fact_id,
    score_verdicts,
)

SCHEMA = TextSchema(
    entity_types=[
        EntityType(name="Component", description="a part"),
        EntityType(name="Aspect", description="a property of a part, good or bad"),
    ],
    fact_types=[
        FactType(predicate="HAS_ASPECT", subject_type="Component", object_type="Aspect", description="d"),
        FactType(predicate="RATED_FOR", subject_type="Component", object_type=VALUE_TYPE, description="d"),
    ],
)
CHUNK = "The drawer slides are rated for 25kg loads. After just two months of use the slats crack."


def raw(obj: str, object_type: str = "Aspect", evidence: str = "", **extra) -> RawTriple:
    predicate = "RATED_FOR" if object_type == VALUE_TYPE else "HAS_ASPECT"
    return RawTriple(
        subject="drawer slides", subject_type="Component", predicate=predicate, object=obj,
        object_type=object_type, evidence=evidence or "The drawer slides are rated for 25kg loads", **extra,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("text", "value", "unit", "canonical"),
    [
        ("25kg", 25.0, "kg", "25 kg"),
        ("25 kilograms", 25.0, "kg", "25 kg"),
        ("3.2kg", 3.2, "kg", "3.2 kg"),
        ("30,000 Martindale rubs", 30000.0, "Martindale rubs", "30000 Martindale rubs"),  # unknown: verbatim
    ],
)
def test_a_number_is_parsed_and_only_known_units_are_normalised(text, value, unit, canonical):
    quantity = parse_quantity(text)
    assert quantity is not None and (quantity.value, quantity.unit, quantity.text) == (value, unit, canonical)


@pytest.mark.parametrize("text", ["two months", "3,5 kg", "kg 25", ""])
def test_text_that_does_not_start_with_a_number_is_no_quantity(text):
    assert parse_quantity(text) is None


def test_a_positive_claim_and_a_number_claim_pass_verification():
    assert verify(raw("rated", polarity="positive"), CHUNK, SCHEMA) is None
    assert verify(raw("25kg", VALUE_TYPE, polarity="positive"), CHUNK, SCHEMA) is None


def test_a_number_must_be_a_number_and_stated_in_the_quote():
    words = raw("rated", VALUE_TYPE)
    assert verify(words, CHUNK, SCHEMA).reason == RejectionReason.VALUE_NOT_A_NUMBER
    # "two" is in the chunk, but the quote about the slides does not state it
    elsewhere = raw("25kg", VALUE_TYPE, evidence="The drawer slides are rated for")
    assert verify(elsewhere, CHUNK, SCHEMA).reason == RejectionReason.VALUE_NOT_IN_EVIDENCE


def test_a_time_must_be_words_of_the_quote():
    crack = RawTriple(
        subject="slats", subject_type="Component", predicate="HAS_ASPECT", object="crack",
        object_type="Aspect", evidence="After just two months of use the slats crack", polarity="negative",
    )  # fmt: skip
    assert verify(crack.model_copy(update={"time": "After just two months of use"}), CHUNK, SCHEMA) is None
    invented = crack.model_copy(update={"time": "after a year"})
    assert verify(invented, CHUNK, SCHEMA).reason == RejectionReason.TIME_NOT_IN_EVIDENCE


def test_value_is_a_built_in_object_type_and_nothing_else():
    assert validate_text_schema(SCHEMA) == []
    value_subject = FactType(predicate="IS", subject_type=VALUE_TYPE, object_type="Aspect", description="d")
    defined = SCHEMA.model_copy(
        update={
            "entity_types": [*SCHEMA.entity_types, EntityType(name=VALUE_TYPE, description="a number")],
            "fact_types": [*SCHEMA.fact_types, value_subject],
        }
    )
    assert validate_text_schema(defined) == [
        "entity type 'Value' is built in; do not define it",
        "fact IS: a number cannot be a subject",
    ]


def test_a_time_makes_another_claim_and_no_time_keeps_the_id_from_before_r66():
    # the id as R44-R64 computed it: the R62 verdict files refer to claims by it
    before = hashlib.sha1(
        "|".join(["b.md#1", "HAS_DEFECT", norm("slats"), norm("crack")]).encode()
    ).hexdigest()[:12]
    assert observation_id("b.md#1", "HAS_DEFECT", "slats", "crack") == before
    assert observation_id("b.md#1", "HAS_DEFECT", "slats", "crack", "after two months") != before


def resolve(driver, auto_merge: float = 92) -> None:
    """The identity stage without a schema (every type a concept) and without an LLM."""
    settings = IdentitySettings(auto_merge=auto_merge, borderline=80, link_threshold=90)
    resolve_identity(driver, None, None, None, "m", settings)


def entity(eid: str, name: str, polarities: list[str], etype: str = "Aspect") -> EntityRecord:
    return EntityRecord(id=eid, name=name, type=etype, aliases=[name], mentions=1, polarities=polarities)


def test_kinds_used_with_opposite_polarity_are_never_candidates():
    pairs = [
        entity("a", "scratches easily", ["negative"]),
        entity("b", "scratch easily", ["positive"]),
        entity("c", "scratches easily!", []),  # neutral use only: no tone to contradict
    ]
    found = {frozenset((c.a, c.b)) for c in find_candidates(pairs, borderline=80)}
    assert found == {frozenset("ac"), frozenset("bc")}


def test_two_numbers_are_never_candidates():
    numbers = [entity("a", "25 kg", [], VALUE_TYPE), entity("b", "35 kg", [], VALUE_TYPE)]
    assert find_candidates(numbers, borderline=50) == []


def stored(obj: str, polarity: str = "neutral", time: str = "") -> StoredFact:
    return StoredFact(
        predicate="HAS_ASPECT", subject_type="Component", object_type="Aspect", chunk_id="a.md#0",
        evidence="quote", subject_names=["slats"], object_names=[obj], polarity=polarity, time=time,
    )  # fmt: skip


def tones(sheet_ids: list[str], correct: list[bool]) -> Verdicts:
    return Verdicts(
        judge=JudgeMeta(model="claude-test", date="2026-09-25"),
        gold="g.json",
        polarity=[
            PolarityVerdict(id=i, correct=c, reason="r") for i, c in zip(sheet_ids, correct, strict=True)
        ],
    )


def test_the_judge_checks_the_polarity_of_every_fact_exact_matches_included():
    facts = [stored("crack", "negative"), stored("sturdy", "negative", time="after a week")]
    gold = [GoldTriple(subject="slats", predicate="HAS_ASPECT", object="crack", doc_id="a.md", evidence="q")]
    sheet = build_sheet(facts, gold)
    assert [(f.polarity, f.time) for f in sheet.facts] == [("negative", ""), ("negative", "after a week")]
    assert sheet.facts[1].id == fact_id(facts[1])
    ids = [f.id for f in sheet.facts]
    judged = tones(ids, [True, False])
    judged.recall = []
    judged.facts = []
    sheet.facts[1].gold_index = 0  # settle both by exact match, so only the polarity is judged
    report = score_verdicts(sheet, judged)
    assert (report.polarity_judged, report.polarity_correct, report.polarity_accuracy) == (2, 1, 0.5)
    assert report.metrics()["polarity_accuracy"] == 0.5

    with pytest.raises(EvaluationError, match="no polarity verdict"):
        score_verdicts(sheet, tones(ids[:1], [True]))


def test_a_verdict_file_without_polarity_scores_no_polarity():
    gold = [GoldTriple(subject="slats", predicate="HAS_ASPECT", object="crack", doc_id="a.md", evidence="q")]
    sheet = build_sheet([stored("crack")], gold)  # settled by exact match: no fact verdict needed
    report = score_verdicts(sheet, Verdicts(judge=JudgeMeta(model="m", date="d"), gold="g.json"))
    assert report.polarity_accuracy is None and "polarity_accuracy" not in report.metrics()


def triple(obj: str, object_type: str = "Aspect", **extra) -> Triple:
    return Triple(**raw(obj, object_type, **extra).model_dump(), chunk_id="a.md#0")


@pytest.mark.neo4j
def test_the_subject_graph_stores_tone_time_and_number(driver):
    driver.execute_query("CREATE (:Chunk {chunk_id: 'a.md#0', text: $t})", t=CHUNK)
    counts = write_subject_graph(
        driver,
        [
            triple("25kg", VALUE_TYPE, polarity="positive"),
            triple("25 kilograms", VALUE_TYPE, evidence="rated for 25 kilograms"),
            triple(
                "crack",
                evidence="After just two months of use the slats crack",
                polarity="negative",
                time="After just two months of use",
            ),  # fmt: skip
        ],
        extractor="m",
    )
    assert (counts.observations_positive, counts.observations_neutral, counts.observations_negative) == (
        1,
        1,
        1,
    )
    assert (counts.observations_with_value, counts.observations_with_time) == (2, 1)
    # both spellings of the number refer to one Value concept (R75); each claim keeps its own wording
    resolve(driver)
    values, _, _ = driver.execute_query(
        "MATCH (o:Observation)-[:OBJECT]->(:Mention {type: $t})-[:REFERS_TO]->(v:Concept) "
        "RETURN v.name AS name, o.object_name AS own, o.value AS value, o.unit AS unit ORDER BY own",
        t=VALUE_TYPE,
    )
    assert [tuple(r.values()) for r in values] == [
        ("25 kg", "25 kilograms", 25.0, "kg"),
        ("25 kg", "25kg", 25.0, "kg"),
    ]
    facts = CheckContext(driver=driver).facts
    assert {(f.own_object, f.polarity, f.time, f.value) for f in facts} == {
        ("25kg", "positive", "", 25.0),
        ("25 kilograms", "neutral", "", 25.0),
        ("crack", "negative", "After just two months of use", None),
    }


@pytest.mark.neo4j
def test_resolution_reads_the_tones_and_keeps_opposite_kinds_apart(driver):
    driver.execute_query(
        "CREATE (c:Chunk {chunk_id: 'a.md#0', text: 'x'}), "
        "(s:Mention {id: 's', type: 'Component', name: 'top', doc_id: 'a.md'}), "
        "(good:Mention {id: 'g', type: 'Aspect', name: 'resistant to scratches', doc_id: 'a.md'}), "
        "(bad:Mention {id: 'b', type: 'Aspect', name: 'resistant to scratch', doc_id: 'a.md'}), "
        "(c)-[:MENTIONS]->(good), (c)-[:MENTIONS]->(bad), "
        "(:Observation {id: 'o1', polarity: 'positive'})-[:OBJECT]->(good), "
        "(:Observation {id: 'o2', polarity: 'negative'})-[:OBJECT]->(bad), "
        "(:Observation {id: 'o3', polarity: 'neutral'})-[:OBJECT]->(bad)"
    )
    mentions = read_mentions(driver)
    assert {m.id: m.polarities for m in mentions} == {"s": [], "g": ["positive"], "b": ["negative"]}
    concepts, _ = concept_records(mentions)
    assert sorted(c.polarities for c in concepts) == [[], ["negative"], ["positive"]]
    # the two names are 97 alike by spelling and would merge without the guard
    settings = IdentitySettings(auto_merge=90, borderline=80, link_threshold=90)
    assert resolve_identity(driver, None, None, None, "m", settings).merges == 0


@pytest.mark.neo4j
def test_a_number_written_two_ways_can_be_rescored_from_its_judge_sheet(driver):
    """Found in R66 part 2: the judge sheet shows a claim's own wording ("25kg"), the Value entity is named
    "25 kg", and numbers are never merged, so no alias carried the wording and `kg rescore` failed."""
    from kgbuilder.validation.er import ErSheet
    from kgbuilder.validation.evaluate import read_entities as sheet_entities
    from kgbuilder.validation.gold import GoldSet
    from kgbuilder.validation.rescore import rescore

    driver.execute_query("CREATE (:Chunk {chunk_id: 'a.md#0', text: $t})", t=CHUNK)
    claims = [
        triple("25kg", VALUE_TYPE),
        triple("25 kilograms", VALUE_TYPE, evidence="rated for 25 kilograms"),
    ]
    write_subject_graph(driver, claims, extractor="m")
    resolve(driver)
    value = next(e for e in sheet_entities(driver) if e.type == VALUE_TYPE)
    assert value.name == "25 kg" and {"25kg", "25 kilograms"} <= set(value.aliases)

    gold = [
        GoldTriple(
            subject="drawer slides", predicate="RATED_FOR", object="25 kg", doc_id="a.md", evidence="q"
        )
    ]
    sheet = build_sheet(CheckContext(driver=driver).facts, gold)
    sheet.er = ErSheet(entities=sheet_entities(driver), pairs=[])
    assert rescore(sheet, GoldSet(triples=gold)).triples.precision == 1.0
