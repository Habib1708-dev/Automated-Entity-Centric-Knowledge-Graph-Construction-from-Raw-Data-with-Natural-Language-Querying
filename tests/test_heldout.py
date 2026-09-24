"""The held-out NHTSA dataset (heldout/nhtsa/): it is exactly what its build script writes from the raw
records, the raw records follow the selection rule, and the pipeline's LLM-free first stages accept it
(three tables staged, the keys to the vehicles found, every document linkable to its vehicle).
No Neo4j, no network, no LLM.
"""

import importlib.util
import json
from pathlib import Path

from kgbuilder.resolution.linking import DomainNode, match_document
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured

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
