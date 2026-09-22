"""Text path: document loading, chunking (sections, oversized cuts, overlap, packing, the document
context on every chunk), the text-schema proposer with its critic, and the lexical graph round trip
(needs Neo4j)."""

import pytest

from kgbuilder.llm.refine import Critique
from kgbuilder.text.chunking import chunk_document, document_context
from kgbuilder.text.documents import Document, load_documents
from kgbuilder.text.lexical import read_chunks, write_lexical_graph
from kgbuilder.text.schema import EntityType, FactType, TextSchema, propose_text_schema

from .fakes import ScriptedLLM


def doc(text: str, doc_id: str = "a.md") -> Document:
    return Document(doc_id=doc_id, title=doc_id.split(".")[0], text=text)


def test_sections_become_chunks_with_stable_ids():
    text = "\n\n---\n\n".join(f"Review {i}: " + "word " * 50 for i in range(3))
    chunks = chunk_document(doc(text))
    assert [c.chunk_id for c in chunks] == ["a.md#0", "a.md#1", "a.md#2"]
    assert all(c.text.startswith(f"Review {c.index}") for c in chunks)


def test_every_chunk_carries_the_documents_first_heading_as_context():
    # the heading is in section 0 only; sections 1 and 2 say "it", and the extractor must still be able
    # to name the product there
    text = "# Helsingborg Dresser Reviews\n\nScraped from a shop.\n\n---\n\nIt wobbles. " + "x " * 100
    chunks = chunk_document(doc(text), min_chars=10)
    assert len(chunks) == 2
    assert [c.context for c in chunks] == ["Helsingborg Dresser Reviews"] * 2
    assert "Helsingborg" not in chunks[1].text  # the context is metadata, the chunk text is unchanged


def test_context_falls_back_to_the_title_when_there_is_no_heading():
    assert document_context(doc("no heading here", doc_id="malmo_desk.md")) == "malmo_desk"
    assert document_context(doc("intro\n\n## Second-level heading\n\nbody")) == "Second-level heading"


def test_tiny_sections_are_packed_onto_a_neighbour():
    text = "# Title\n\n---\n\n" + "body " * 60 + "\n\n---\n\nThe end."
    chunks = chunk_document(doc(text), min_chars=100)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("# Title") and chunks[0].text.endswith("The end.")


def test_oversized_section_is_cut_on_paragraphs_and_long_paragraphs_on_whitespace():
    paragraphs = ["alpha " * 30, "beta " * 30, "gamma" * 10 + " " + "delta " * 80]
    chunks = chunk_document(doc("\n\n".join(paragraphs)), max_chars=250, min_chars=1)
    assert len(chunks) >= 4
    assert all(len(c.text) <= 250 for c in chunks)
    # nothing is lost and no word is split
    assert " ".join(c.text for c in chunks).split() == " ".join(paragraphs).split()


def test_overlap_repeats_the_tail_inside_a_section_but_never_across_sections():
    section = "\n\n".join(f"para{i} " + "x " * 60 for i in range(4))
    text = section + "\n\n---\n\n" + "Second review. " + "y " * 100
    plain = chunk_document(doc(text), max_chars=200, min_chars=1)
    overlapped = chunk_document(doc(text), max_chars=200, min_chars=1, overlap_chars=40)
    assert len(plain) == len(overlapped)
    assert overlapped[0].text == plain[0].text  # first piece has no predecessor
    assert overlapped[1].text.endswith(plain[1].text) and len(overlapped[1].text) > len(plain[1].text)
    second_review = next(c for c in overlapped if "Second review." in c.text)
    assert second_review.text.startswith("Second review.")  # no leak from the previous review


def test_load_documents_skips_empty_files_other_suffixes_and_the_output_dir(tmp_path):
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "a.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "empty.md").write_text("  \n", encoding="utf-8")
    (tmp_path / "table.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "notes.md").write_text("generated", encoding="utf-8")
    docs = load_documents(tmp_path, exclude=tmp_path / "out")
    assert [d.doc_id for d in docs] == ["a.md", "b.txt"]


GOOD_SCHEMA = TextSchema(
    entity_types=[EntityType(name="Product", description="x"), EntityType(name="Problem", description="y")],
    fact_types=[
        FactType(predicate="HAS_PROBLEM", subject_type="Product", object_type="Problem", description="z")
    ],
)


def test_text_schema_critic_can_send_a_valid_schema_back():
    verdicts = iter(
        [Critique(verdict="retry", issues=["Problem overlaps Defect"]), Critique(verdict="valid", issues=[])]
    )
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        return next(verdicts) if schema is Critique else GOOD_SCHEMA

    result = propose_text_schema("goal", chunk_document(doc("text " * 80)), ScriptedLLM(script), model="m")
    assert result.accepted and result.rounds == 2
    assert [r.source for r in result.history] == ["critic", "none"]
    assert "Problem overlaps Defect" in prompts[2]  # the critic's issue reached the second proposal


@pytest.mark.neo4j
def test_chunks_read_back_as_written_and_stale_chunks_are_replaced(driver):
    document = doc("\n\n---\n\n".join(f"Review {i}: " + "word " * 60 for i in range(4)))
    fine = chunk_document(document, max_chars=400, min_chars=50)
    assert write_lexical_graph(driver, [document], fine) == 0
    assert read_chunks(driver) == fine  # includes the context: the extractor reads chunks from the graph
    assert all(c.context == "a" for c in read_chunks(driver))

    # re-ingest with settings that produce fewer chunks: the surplus ids must not survive as ghosts
    coarse = chunk_document(document, max_chars=5000, min_chars=800)
    assert len(coarse) < len(fine)
    assert write_lexical_graph(driver, [document], coarse) == len(fine) - len(coarse)
    assert read_chunks(driver) == coarse
    links = driver.execute_query("MATCH (:Chunk)-[r:NEXT_CHUNK]->(:Chunk) RETURN count(r) AS c")[0][0]["c"]
    assert links == len(coarse) - 1
