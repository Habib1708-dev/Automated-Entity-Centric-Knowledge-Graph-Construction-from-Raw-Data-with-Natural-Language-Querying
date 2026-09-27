"""Text path: document loading, chunking (sections, oversized cuts, overlap, packing, the document
context on every chunk), the text-schema proposer with its critic, its domain-neutral rules and its
unused-type check (R58), and the lexical graph round trip (needs Neo4j)."""

import re
from pathlib import Path

import pytest

from kgbuilder.core.text import norm
from kgbuilder.llm.refine import Critique
from kgbuilder.structured.plan import ConstructionPlan
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.text.chunking import chunk_document, document_context
from kgbuilder.text.documents import Document, load_documents
from kgbuilder.text.lexical import read_chunks, write_lexical_graph
from kgbuilder.text.record_documents import record_documents
from kgbuilder.text.schema import (
    CRITIC_PROMPT,
    PROMPT,
    EntityType,
    FactType,
    TextSchema,
    propose_text_schema,
    select_context,
    validate_text_schema,
)

from .fakes import ScriptedLLM
from .sample_plans import node

ROOT = Path(__file__).parent.parent


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


# Multi-sentence running text, above the profiler's prose bounds; the tagline below is not.
RECALL_PROSE = (
    "The piston rings may wear prematurely and allow oil to enter the combustion chamber. Excessive "
    "oil consumption can cause the engine to stall without warning. A stall increases the risk of a crash."
)


def record_fixture(tmp_path: Path) -> tuple[Path, ConstructionPlan]:
    (tmp_path / "recalls.csv").write_text(
        "recall_id,campaign,summary,consequence\n"
        f'16V074000,Piston Rings,"{RECALL_PROSE}","{RECALL_PROSE}"\n'
        f'16V075000,Brake Cables,"{RECALL_PROSE}",\n'
        "16V076000,Empty One,,\n",
        encoding="utf-8",
    )
    # tagline-length description: the furniture shape, which must yield no record documents
    (tmp_path / "products.csv").write_text(
        "product_id,product_name,description\nP1,Table,Sturdy oak table.\n", encoding="utf-8"
    )
    plan = ConstructionPlan(
        nodes=[
            node("recalls.csv", "Recall", "recall_id", ["campaign", "summary", "consequence"]).model_copy(
                update={"name_column": "campaign"}
            ),
            node("products.csv", "Product", "product_id", ["product_name", "description"]),
        ],
        relationships=[],
    )
    return tmp_path, plan


def test_records_with_prose_become_documents_and_taglines_do_not(tmp_path):
    staged, plan = record_fixture(tmp_path)
    docs = record_documents(staged, plan, profile_directory(staged))

    # only the two recalls with text; the empty record and the tagline-only products yield nothing
    assert [d.doc_id for d in docs] == ["record/Recall/16V074000", "record/Recall/16V075000"]
    first = docs[0]
    assert first.title == "Piston Rings"
    assert first.record.label == "Recall" and first.record.key_property == "recall_id"
    assert first.record.key == "16V074000"
    # one section per prose column, each named after its column; empty cells leave no section
    assert first.text.startswith("# Piston Rings\n") and "## summary" in first.text
    assert RECALL_PROSE in first.text
    assert "## consequence" not in docs[1].text


def test_record_document_chunks_carry_the_records_name_as_context(tmp_path):
    staged, plan = record_fixture(tmp_path)
    docs = record_documents(staged, plan, profile_directory(staged))
    chunks = chunk_document(docs[0], min_chars=10)
    # the extractor may only use names it is shown: "the engine may stall" must become a claim it can
    # tie to this recall's name, exactly like the review documents (R34)
    assert chunks and all(c.context == "Piston Rings" for c in chunks)


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


def chunks_of(sizes: list[int]) -> list:
    """One chunk per section, of about the given sizes (no packing, no cutting)."""
    text = "\n\n---\n\n".join(f"Section {i} " + "x" * (size - 10) for i, size in enumerate(sizes))
    return chunk_document(doc(text), max_chars=5000, min_chars=1)


def test_the_schema_context_is_every_chunk_when_the_corpus_fits_the_budget():
    chunks = chunks_of([900] * 20)
    assert select_context(chunks, budget_chars=20 * 1000) == chunks


def test_above_the_budget_the_context_is_an_even_sample_of_whole_chunks_that_fits():
    chunks = chunks_of([1000] * 40)
    context = select_context(chunks, budget_chars=10_000)
    assert sum(len(c.text) for c in context) <= 10_000
    assert len(context) >= 9  # as many as fit, not a fixed count
    assert context[0] == chunks[0] and context[-1].index >= 30  # spread over the whole corpus, not the start


def test_the_proposer_shows_every_context_chunk_whole():
    # the sampled context used to cut each chunk at 1,200 characters and keep 12 chunks (R55)
    chunks = chunks_of([1400] * 15)
    prompts: list[str] = []

    def script(prompt, schema):
        prompts.append(prompt)
        return Critique(verdict="valid", issues=[]) if schema is Critique else GOOD_SCHEMA

    propose_text_schema("goal", chunks, ScriptedLLM(script), model="m")
    assert all(c.text in prompts[0] and c.chunk_id in prompts[0] for c in chunks)


def test_an_entity_type_no_fact_type_uses_is_sent_back():
    unused = GOOD_SCHEMA.model_copy(
        update={"entity_types": [*GOOD_SCHEMA.entity_types, EntityType(name="Report", description="r")]}
    )
    assert validate_text_schema(unused) == ["entity type 'Report' is used by no fact type"]
    assert validate_text_schema(GOOD_SCHEMA) == []


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(text))


def test_schema_prompts_speak_no_corpus_language():
    """R58: the proposer and critic rules name no domain and quote neither development corpus: a rule in
    the corpus's words steers the schema toward that corpus (found for extraction in R34)."""
    rules = PROMPT.split("Rules:")[1] + CRITIC_PROMPT.split("<goal>")[0]
    assert "source a statement comes from" in rules and "every kind of claim" in rules
    assert "Reuse its concepts" not in PROMPT  # the wording that invited copying the plan's record nodes
    banned = ("complaint", "vehicle", "defect", "product", "furniture", "review", "recall", "component")
    # whole words ("You are reviewing a proposed schema" is about the critic's task, not the corpus)
    assert not [w for w in _words(rules) if w in banned or w.removesuffix("s") in banned]
    corpora = [ROOT / "data" / "product_reviews", ROOT / "heldout" / "nhtsa" / "data" / "complaints"]
    corpus = _words(" ".join(p.read_text(encoding="utf-8") for d in corpora for p in d.glob("*.md")))
    seen = {tuple(corpus[i : i + 4]) for i in range(len(corpus) - 3)}
    words = _words(rules)
    quoted = [" ".join(words[i : i + 4]) for i in range(len(words) - 3) if tuple(words[i : i + 4]) in seen]
    assert not quoted, f"schema prompt rules quote a corpus: {quoted}"
