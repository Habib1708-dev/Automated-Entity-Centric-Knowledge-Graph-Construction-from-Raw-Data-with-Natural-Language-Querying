"""The held-out NHTSA dataset (heldout/nhtsa/) and its gold set: the data is exactly what its build script
writes from the raw records, the raw records follow the selection rule, the pipeline's LLM-free first
stages accept it (three tables staged, the keys to the vehicles found, every document linkable to its
vehicle), and the gold quotes every document verbatim, once per claim.
No Neo4j, no network, no LLM.
"""

import importlib.util
import json
import re
from pathlib import Path

from kgbuilder.core.text import norm
from kgbuilder.resolution.linking import DomainNode, match_document
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured
from kgbuilder.text.schema import TextSchema, validate_text_schema
from kgbuilder.validation.gold import load_gold

REPO = Path(__file__).resolve().parent.parent
HELDOUT = REPO / "heldout" / "nhtsa"

_spec = importlib.util.spec_from_file_location("nhtsa_build", HELDOUT / "build.py")
nhtsa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nhtsa)


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_committed_dataset_is_exactly_what_build_writes_from_raw(tmp_path):
    # proves that nothing in data/ was edited by hand: the benchmark must not be shaped by its author
    nhtsa.build(HELDOUT / "raw", tmp_path)
    assert files(tmp_path) == files(HELDOUT / "data")


def complaint(odi: int, words: int, products: int = 1) -> dict:
    return {"odiNumber": odi, "summary": " ".join(["word"] * words), "products": [{}] * products}


def test_selection_keeps_the_earliest_complaints_of_40_to_110_words_about_one_product():
    candidates = [
        complaint(9, 50),
        complaint(1, 39),  # too short
        complaint(2, 111),  # too long
        complaint(3, 60, products=2),  # names a second product (a tire, a child seat)
        *[complaint(odi, 40 + odi) for odi in (8, 4, 7, 6, 5)],
    ]
    assert [c["odiNumber"] for c in nhtsa.select_complaints(candidates)] == [4, 5, 6, 7, 8]


def test_every_raw_file_follows_the_selection_rule():
    for make, model, _ in nhtsa.VEHICLES:
        record = json.loads((HELDOUT / "raw" / f"{make.lower()}_{model.lower()}.json").read_text("utf-8"))
        kept = record["complaints"]
        assert len(kept) == nhtsa.COMPLAINTS_PER_VEHICLE
        assert [c["odiNumber"] for c in kept] == sorted(c["odiNumber"] for c in kept)
        assert all(nhtsa.MIN_WORDS <= len(c["summary"].split()) <= nhtsa.MAX_WORDS for c in kept)
        assert record["recalls"], f"{model}: every vehicle has recalls"


def test_staging_and_profiling_find_the_keys_from_recalls_and_complaints_to_vehicles(tmp_path):
    report = stage_structured(HELDOUT / "data", tmp_path / "staging")
    assert sorted(report.tables) == ["complaints.ndjson", "recalls.json", "vehicles.csv"]
    assert report.skipped == []
    keys = {
        (fk.from_file, fk.from_column, fk.to_column)
        for fk in profile_directory(report.staged_dir).foreign_keys
    }
    assert ("recalls.csv", "Model", "model") in keys
    # the nested product object of a complaint is flattened into dotted columns by staging
    assert ("complaints.csv", "product.productModel", "model") in keys


def test_every_document_is_named_after_the_model_of_its_vehicle():
    # documents are linked to domain nodes by a name contained in the file name (linking.py)
    vehicles = [DomainNode(element_id=model, label="Vehicle", name=model) for _, model, _ in nhtsa.VEHICLES]
    documents = sorted((HELDOUT / "data" / "complaints").glob("*.md"))
    assert len(documents) == len(nhtsa.VEHICLES)
    for doc in documents:
        assert match_document(doc.stem, vehicles).name.lower() in doc.stem


GOLD = REPO / "tests" / "gold" / "heldout_nhtsa_gold.json"
# the one R51 relation the proposed schema cannot express (R52): its triples stay unmapped
NOT_EXPRESSIBLE = "CAUSES"


def test_the_heldout_gold_quotes_every_document_verbatim_and_labels_each_claim_once():
    gold = load_gold(GOLD)
    data = HELDOUT / "data"
    documents = {p.relative_to(data).as_posix(): p.read_text("utf-8") for p in data.rglob("*.md")}
    assert {t.doc_id for t in gold.triples} == set(documents), "every document is labelled"
    for triple in gold.triples:
        assert triple.evidence and triple.evidence in documents[triple.doc_id], f"not verbatim: {triple}"
        assert triple.predicate.isupper(), triple.predicate
    keys = [(norm(t.subject), t.predicate, norm(t.object), t.doc_id) for t in gold.triples]
    assert len(keys) == len(set(keys)), "a claim is labelled twice"


def test_the_heldout_er_pairs_name_things_the_corpus_contains_once_each():
    corpus = " ".join(norm(p.read_text("utf-8")) for p in (HELDOUT / "data").rglob("*.md"))
    pairs = load_gold(GOLD).er_pairs
    # whole words only, as for the furniture gold: "brake" must not count as found inside "braked"
    missing = [
        name
        for pair in pairs
        for name in (pair.a, pair.b)
        if not re.search(rf"(?<![a-z0-9]){re.escape(norm(name))}(?![a-z0-9])", corpus)
    ]
    assert not missing, f"ER pair names not in the corpus: {missing}"
    keys = [frozenset((norm(p.a), norm(p.b))) for p in pairs]
    assert len(keys) == len(set(keys)), "an ER pair is listed twice"


def test_the_heldout_questions_expect_values_that_exist_in_the_data():
    # expected answers are vehicle models or recall campaign numbers, read from the source files
    recalls = json.loads((HELDOUT / "data" / "recalls.json").read_text("utf-8"))["results"]
    known = {model for _, model, _ in nhtsa.VEHICLES} | {r["NHTSACampaignNumber"] for r in recalls}
    questions = load_gold(GOLD).questions
    assert questions and all(q.expected and set(q.expected) <= known for q in questions)


def test_every_heldout_gold_predicate_is_in_the_frozen_proposed_schema_or_marked_not_expressible():
    # the automatic arm scores against the schema the pipeline proposed (R52); a predicate outside it can
    # only be missed, and that loss must be the schema's, recorded as such, never a mapping slip
    schema = TextSchema.model_validate_json(
        (REPO / "tests" / "gold" / "heldout_nhtsa_text_schema.json").read_text("utf-8")
    )
    raw = json.loads(GOLD.read_text("utf-8"))["triples"]
    allowed = {f.predicate for f in schema.fact_types}
    for triple in raw:
        assert triple["predicate"] in allowed or triple["predicate"] == NOT_EXPRESSIBLE, triple
        assert triple["labelled_as"]["predicate"] in {"HAS_PROBLEM", "CAUSES", "PART_OF", "COVERED_BY_RECALL"}


CONTROLLED_GOLD = REPO / "tests" / "gold" / "heldout_nhtsa_gold_controlled.json"
CONTROLLED_SCHEMA = REPO / "tests" / "gold" / "heldout_nhtsa_text_schema_controlled.json"


def test_the_controlled_gold_is_the_heldout_gold_with_its_labels_from_before_any_run():
    # the two arms must score the same claims: only the predicates' vocabulary may differ (R54)
    automatic = json.loads(GOLD.read_text("utf-8"))
    controlled = json.loads(CONTROLLED_GOLD.read_text("utf-8"))
    expected = [
        {**t["labelled_as"], "doc_id": t["doc_id"], "evidence": t["evidence"]} for t in automatic["triples"]
    ]
    assert controlled["triples"] == expected
    assert controlled["er_pairs"] == automatic["er_pairs"]
    assert [q["expected"] for q in controlled["questions"]] == [q["expected"] for q in automatic["questions"]]


def test_the_pinned_controlled_schema_is_valid_and_holds_every_controlled_gold_predicate():
    schema = TextSchema.model_validate_json(CONTROLLED_SCHEMA.read_text("utf-8"))
    assert validate_text_schema(schema) == []
    assert {t.predicate for t in load_gold(CONTROLLED_GOLD).triples} <= {
        f.predicate for f in schema.fact_types
    }
