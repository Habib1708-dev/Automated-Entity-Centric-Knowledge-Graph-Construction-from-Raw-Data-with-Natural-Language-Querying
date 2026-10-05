"""Identity classes in the text schema (R75 part a): every entity type is keyed (with the plan labels its
records carry and its key attributes), individual or concept; code checks the classes against the plan; the
proposer, its critic and every field description that reaches a model speak no corpus language. Pure: no
Neo4j, no network.
"""

import json

import pytest

from kgbuilder.llm.refine import Critique
from kgbuilder.structured import proposer
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.schema import (
    CRITIC_PROMPT,
    PROMPT,
    EntityType,
    FactType,
    TextSchema,
    propose_text_schema,
    validate_text_schema,
)

from .evaluation_corpora import quoted_four_grams
from .fakes import ScriptedLLM
from .sample_plans import node

# Two keyed tables of an invented domain: hives by id with a name and an apiary, keepers by id
PLAN = ConstructionPlan(
    nodes=[
        node("hives.csv", "Hive", "hive_id", ["hive_name", "apiary"]),
        node("keepers.csv", "Keeper", "keeper_id", ["name", "team"]),
    ],
    relationships=[],
)


def schema(*types: EntityType) -> TextSchema:
    """A schema whose one fact type uses every given type, so only the identity rules can complain."""
    names = [t.name for t in types]
    facts = [FactType(predicate="NEAR", subject_type=names[0], object_type=n, description="d") for n in names]
    return TextSchema(entity_types=list(types), fact_types=facts)


def keyed(name: str, labels: list[str], attributes: tuple[str, ...] = ()) -> EntityType:
    return EntityType(
        name=name, description="d", identity="keyed", record_labels=labels, key_attributes=list(attributes)
    )


def test_a_schema_without_identity_classes_reads_every_type_as_a_concept():
    """Schemas written before R75 still load: one node per type and name, merged as before."""
    old = {"entity_types": [{"name": "Colony", "description": "d"}], "fact_types": []}
    loaded = TextSchema.model_validate_json(json.dumps(old))
    assert loaded.entity_types[0].identity == "concept" and loaded.identity_of("Colony") == "concept"
    assert loaded.identity_of("Value") == "concept"  # the built-in type is never keyed


def test_a_keyed_type_with_plan_labels_and_their_properties_is_valid():
    valid = schema(
        keyed("Hive", ["Hive"], ("apiary",)),
        keyed("Person", ["Keeper"], ("team", "keeper_id")),  # the key column itself may be an attribute
        EntityType(name="Visitor", description="d", identity="individual"),
        EntityType(name="Ailment", description="d"),
    )
    assert validate_text_schema(valid, PLAN) == []


@pytest.mark.parametrize(
    ("entity_type", "issue"),
    [
        (keyed("Hive", []), "keyed entity type 'Hive' names no record label"),
        (keyed("Hive", ["Apiary"]), "keyed entity type 'Hive': 'Apiary' is not a label of the domain graph"),
        (
            keyed("Hive", ["Hive"], ("colour",)),
            "keyed entity type 'Hive': key attribute 'colour' is not a property",
        ),
        (
            EntityType(name="Swarm", description="d", identity="individual", record_labels=["Hive"]),
            "entity type 'Swarm' is individual: only a keyed type names records",
        ),
        (
            EntityType(name="Hive", description="d"),
            "entity type 'Hive' has the name of a domain label but is not keyed",
        ),
    ],
)
def test_identity_classes_are_checked_against_the_plan(entity_type, issue):
    assert validate_text_schema(schema(entity_type), PLAN) == [issue]


def test_without_structured_data_no_type_can_be_keyed():
    assert validate_text_schema(schema(keyed("Hive", ["Hive"]))) == [
        "keyed entity type 'Hive': 'Hive' is not a label of the domain graph"
    ]


def test_the_proposer_is_sent_back_when_a_keyed_type_names_a_label_the_plan_lacks():
    """The refine loop validates with the plan, so a wrong label costs a round, not a broken build."""
    proposals = iter([schema(keyed("Hive", ["Apiary"])), schema(keyed("Hive", ["Hive"]))])
    prompts: list[str] = []

    def script(prompt: str, response_schema: type) -> object:
        prompts.append(prompt)
        return Critique(verdict="valid", issues=[]) if response_schema is Critique else next(proposals)

    chunks = [Chunk(chunk_id="a.md#0", doc_id="a.md", index=0, text="The Linden Hive swarmed.")]
    result = propose_text_schema("goal", chunks, ScriptedLLM(script), model="m", plan=PLAN)
    assert result.accepted and result.rounds == 2
    assert "'Apiary' is not a label of the domain graph" in prompts[1]


# Every text that reaches a model: the templates and the response schemas (field descriptions are prompt
# text too, prompt-engineering skill). Whole words, so "the critic's review" of a schema is not a hit.
_BANNED = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "component", "pump")


@pytest.mark.parametrize(
    "text",
    [
        PROMPT.split("Rules:")[1] + CRITIC_PROMPT.split("<goal>")[0],
        json.dumps(TextSchema.model_json_schema()),
        proposer.PROPOSER_PROMPT.split("<profile>")[0] + proposer.PROPOSER_PROMPT.split("</profile>")[1],
        proposer.CRITIC_PROMPT.split("<goal>")[0],
        json.dumps(ConstructionPlan.model_json_schema()),
    ],
)
def test_schema_and_plan_prompts_and_their_field_descriptions_speak_no_corpus_language(text):
    words = set(text.lower().replace("_", " ").replace('"', " ").split())
    assert not [w for w in _BANNED if w in words or f"{w}s" in words]
    assert quoted_four_grams(text) == []
