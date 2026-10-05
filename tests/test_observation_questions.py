"""The gold questions walk the observation graph: thing -[:HAS_OBSERVATION]-> Observation -[:SUBJECT|OBJECT]->
the claim's ends, never a document's ABOUT edge, so a claim reaches only the thing whose text states it.
R65 wrote them for the `:Entity` graph; R75 carries them to the mention graph (the label alone changes).
The gold files themselves (no Neo4j) and the two shared-part questions on hand-made graphs (Neo4j).
Must not grow into a test of extraction or linking: the graphs here are written by hand in R75's shape.
"""

import json
from pathlib import Path

import pytest

from kgbuilder.validation.evaluate import run_questions
from kgbuilder.validation.gold import GoldQuestion, load_gold

GOLD = Path(__file__).parent / "gold"
DATASETS = ("furniture", "heldout")
R65 = {d: GOLD / "r65" / f"{d}_gold.json" for d in DATASETS}
R75 = {d: GOLD / "r75" / f"{d}_gold.json" for d in DATASETS}


def question(dataset: str, words: str) -> GoldQuestion:
    return next(q for q in load_gold(R75[dataset]).questions if words in q.question)


@pytest.mark.parametrize("dataset", DATASETS)
def test_no_question_reaches_a_thing_through_a_documents_about_edge(dataset):
    """Every question that reads the text graph starts at the thing and follows its observations. An ABOUT
    jump would let any chunk that mentions a shared kind answer for its document's thing (R58)."""
    for q in load_gold(R75[dataset]).questions:
        assert "ABOUT" not in q.cypher and ":Entity" not in q.cypher, q.question
        if ":Mention" in q.cypher or "Observation" in q.cypher:
            assert "-[:HAS_OBSERVATION]->" in q.cypher, q.question


@pytest.mark.parametrize("dataset", DATASETS)
def test_r65_changes_only_the_questions_of_r58(dataset):
    """A gold change after seeing output is listed as a correction; the claims and pairs are not touched,
    so R65's triple scores stay comparable with R58-R64."""
    old = json.loads((GOLD / "r58" / R65[dataset].name).read_text("utf-8"))
    new = json.loads(R65[dataset].read_text("utf-8"))
    assert (new["triples"], new["er_pairs"]) == (old["triples"], old["er_pairs"])
    assert [q["expected"] for q in new["questions"][: len(old["questions"])]] == [
        q["expected"] for q in old["questions"]
    ]


@pytest.mark.parametrize("dataset", DATASETS)
def test_r75_changes_only_the_label_of_the_claims_ends(dataset):
    """R75's gold correction is the graph's new shape and nothing else: triples, pairs, questions and
    expected answers are R65's, with ':Entity' read as ':Mention'."""
    old = json.loads(R65[dataset].read_text("utf-8"))
    new = json.loads(R75[dataset].read_text("utf-8"))
    assert (new["triples"], new["er_pairs"]) == (old["triples"], old["er_pairs"])
    for before, after in zip(old["questions"], new["questions"], strict=True):
        assert after == {**before, "cypher": before["cypher"].replace(":Entity", ":Mention")}


@pytest.mark.neo4j
@pytest.mark.parametrize("dataset", DATASETS)
def test_every_question_is_valid_cypher(driver, dataset):
    """A syntax error would first show on a rebuilt graph; on an empty one every query answers nothing."""
    assert all(r.answered == [] for r in run_questions(driver, load_gold(R75[dataset]).questions))


@pytest.mark.neo4j
def test_defective_drawer_rails_answer_only_the_product_whose_reviews_say_so(driver):
    """The audit's leak: the bed's review names its "drawer slides", which a concept merge joined to the
    dresser's "drawer rails" (the R64 merge), but only the dresser's reviews call the rails defective."""
    driver.execute_query(
        "CREATE (kind:Concept {id: 'k', type: 'Component', name: 'drawer rails'}), "
        "(rails:Mention {id: 'm1', type: 'Component', name: 'drawer rails', doc_id: 'h.md'})"
        "-[:REFERS_TO {canonical: 'k', name: 'drawer rails', kind: 'concept'}]->(kind), "
        "(slides:Mention {id: 'm2', type: 'Component', name: 'drawer slides', doc_id: 'l.md'})"
        "-[:REFERS_TO {canonical: 'k', name: 'drawer rails', kind: 'concept'}]->(kind), "
        "(:Product {product_name: 'Helsingborg Dresser'})-[:HAS_OBSERVATION]->"
        "(bad:Observation {predicate: 'HAS_DEFECT'})-[:SUBJECT]->(rails), "
        "(bad)-[:OBJECT]->(:Mention {type: 'Defect', name: 'defective'}), "
        "(bed:Product {product_name: 'Linköping Bed'})-[:HAS_OBSERVATION]->"
        "(part:Observation {predicate: 'PART_OF', extractor: 'derived'})-[:SUBJECT]->(slides), "
        "(part)-[:OBJECT]->(:Mention {type: 'Product', name: 'Linköping Bed'})"
    )
    rails = question("furniture", "defective drawer rails")
    assert run_questions(driver, [rails])[0].answered == ["helsingborg dresser"]


@pytest.mark.neo4j
def test_a_transmission_problem_answers_only_the_vehicle_whose_complaint_states_it(driver):
    """The held-out audit case: the RAV4 has a transmission too, but only the Rogue's complaint says it
    switched to reverse on its own."""
    driver.execute_query(
        "CREATE (t:Mention {type: 'Component', name: 'TRANSMISSION', doc_id: 'rogue.md'}), "
        "(u:Mention {type: 'Component', name: 'TRANSMISSION', doc_id: 'rav4.md'}), "
        "(:Vehicle {model: 'ROGUE'})-[:HAS_OBSERVATION]->(o:Observation {predicate: 'AFFECTS_COMPONENT'}), "
        "(o)-[:SUBJECT]->(:Mention {type: 'Problem', name: 'TRANSMISSION ERRONEOUSLY SWITCHED TO REVERSE'}), "
        "(o)-[:OBJECT]->(t), "
        "(:Vehicle {model: 'RAV4'})-[:HAS_OBSERVATION]->(i:Observation {predicate: 'INSTALLED_IN'})"
        "-[:SUBJECT]->(u)"
    )
    reverse = question("heldout", "switched to reverse")
    assert run_questions(driver, [reverse])[0].answered == ["rogue"]
