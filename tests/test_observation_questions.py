"""The R65 gold questions walk the observation graph: thing -[:HAS_OBSERVATION]-> Observation -[:SUBJECT|
OBJECT]-> kind, never a document's ABOUT edge, so a claim reaches only the thing whose text states it.
The gold files themselves (no Neo4j) and the two shared-part questions on hand-made graphs (Neo4j).
Must not grow into a test of extraction or linking: the graphs here are written by hand in R64's shape.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.validation.evaluate import run_questions
from kgbuilder.validation.gold import GoldQuestion, load_gold

GOLD = Path(__file__).parent / "gold"
R65 = {"furniture": GOLD / "r65" / "furniture_gold.json", "heldout": GOLD / "r65" / "heldout_gold.json"}


def question(dataset: str, words: str) -> GoldQuestion:
    return next(q for q in load_gold(R65[dataset]).questions if words in q.question)


@pytest.mark.parametrize("dataset", R65)
def test_no_r65_question_reaches_a_thing_through_a_documents_about_edge(dataset):
    """Every question that reads the text graph starts at the thing and follows its observations. An ABOUT
    jump would let any chunk that mentions a shared kind answer for its document's thing (R58)."""
    for q in load_gold(R65[dataset]).questions:
        assert "ABOUT" not in q.cypher, q.question
        if ":Entity" in q.cypher or "Observation" in q.cypher:
            assert "-[:HAS_OBSERVATION]->" in q.cypher, q.question


@pytest.mark.parametrize("dataset", R65)
def test_r65_changes_only_the_questions_of_r58(dataset):
    """A gold change after seeing output is listed as a correction; the claims and pairs are not touched,
    so R65's triple scores stay comparable with R58-R64."""
    old = json.loads((GOLD / "r58" / R65[dataset].name).read_text("utf-8"))
    new = json.loads(R65[dataset].read_text("utf-8"))
    assert (new["triples"], new["er_pairs"]) == (old["triples"], old["er_pairs"])
    assert [q["expected"] for q in new["questions"][: len(old["questions"])]] == [
        q["expected"] for q in old["questions"]
    ]


@pytest.mark.neo4j
@pytest.mark.parametrize("dataset", R65)
def test_every_r65_question_is_valid_cypher(driver, dataset):
    """A syntax error would first show on a rebuilt graph; on an empty one every query answers nothing."""
    assert all(r.answered == [] for r in run_questions(driver, load_gold(R65[dataset]).questions))


@pytest.mark.neo4j
def test_defective_drawer_rails_answer_only_the_product_whose_reviews_say_so(driver):
    """The audit's leak: one "drawer rails" kind is a part of both products (the bed's derived part claim
    after its "drawer slides" merged into it), but only the dresser's reviews call the rails defective."""
    driver.execute_query(
        "CREATE (rails:Entity {type: 'Component', name: 'drawer rails', aliases: ['drawer slides']}), "
        "(:Product {product_name: 'Helsingborg Dresser'})-[:HAS_OBSERVATION]->"
        "(bad:Observation {predicate: 'HAS_DEFECT'})-[:SUBJECT]->(rails), "
        "(bad)-[:OBJECT]->(:Entity {type: 'Defect', name: 'defective'}), "
        "(bed:Product {product_name: 'Linköping Bed'})-[:HAS_OBSERVATION]->"
        "(part:Observation {predicate: 'PART_OF', extractor: 'derived'})-[:SUBJECT]->(rails), "
        "(part)-[:OBJECT]->(:Entity {type: 'Product', name: 'Linköping Bed'})"
    )
    rails = question("furniture", "defective drawer rails")
    assert run_questions(driver, [rails])[0].answered == ["helsingborg dresser"]


@pytest.mark.neo4j
def test_a_transmission_problem_answers_only_the_vehicle_whose_complaint_states_it(driver):
    """The held-out audit case: the RAV4 has a transmission too, but only the Rogue's complaint says it
    switched to reverse on its own."""
    driver.execute_query(
        "CREATE (t:Entity {type: 'Component', name: 'TRANSMISSION'}), "
        "(:Vehicle {model: 'ROGUE'})-[:HAS_OBSERVATION]->(o:Observation {predicate: 'AFFECTS_COMPONENT'}), "
        "(o)-[:SUBJECT]->(:Entity {type: 'Problem', name: 'TRANSMISSION ERRONEOUSLY SWITCHED TO REVERSE'}), "
        "(o)-[:OBJECT]->(t), "
        "(:Vehicle {model: 'RAV4'})-[:HAS_OBSERVATION]->(i:Observation {predicate: 'INSTALLED_IN'})"
        "-[:SUBJECT]->(t)"
    )
    reverse = question("heldout", "switched to reverse")
    assert run_questions(driver, [reverse])[0].answered == ["rogue"]
