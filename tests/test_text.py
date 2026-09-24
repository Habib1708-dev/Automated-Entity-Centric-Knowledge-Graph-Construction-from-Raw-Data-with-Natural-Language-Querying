"""Text path: document loading, chunking (sections, oversized cuts, overlap, packing, the document
context on every chunk), the text-schema proposer with its critic, and the lexical graph round trip
(needs Neo4j)."""

import re
from pathlib import Path

import pytest

from kgbuilder.core.text import norm
from kgbuilder.llm.refine import Critique
from kgbuilder.text.chunking import chunk_document, document_context
from kgbuilder.text.documents import Document, load_documents
from kgbuilder.text.lexical import read_chunks, write_lexical_graph
from kgbuilder.text.schema import (
    PROMPT,
    EntityType,
    FactType,
    GoalQuestion,
    PathStep,
    TextSchema,
    propose_text_schema,
    select_context,
    validate_text_schema,
)

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
    goal_questions=[
        GoalQuestion(
            question="Which problems does each product have?",
            path=[PathStep(subject_type="Product", predicate="HAS_PROBLEM", object_type="Problem")],
        )
    ],
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


def step(subject: str, predicate: str, obj: str) -> PathStep:
    return PathStep(subject_type=subject, predicate=predicate, object_type=obj)


def with_paths(*paths: list[PathStep]) -> TextSchema:
    """GOOD_SCHEMA plus a Part type and two fact types, with the given goal-question paths."""
    return GOOD_SCHEMA.model_copy(
        update={
            "entity_types": [*GOOD_SCHEMA.entity_types, EntityType(name="Part", description="p")],
            "fact_types": [
                *GOOD_SCHEMA.fact_types,
                FactType(predicate="OCCURS_IN", subject_type="Problem", object_type="Part", description="o"),
            ],
            "goal_questions": [GoalQuestion(question=f"q{i}", path=path) for i, path in enumerate(paths)],
        }
    )


def test_a_goal_path_must_be_a_connected_chain_of_the_schemas_fact_types():
    # R56: the proposer names the path that answers each goal question; code checks that the path exists
    good = [step("Product", "HAS_PROBLEM", "Problem"), step("Problem", "OCCURS_IN", "Part")]
    assert validate_text_schema(with_paths(good)) == []
    unknown = validate_text_schema(with_paths([step("Part", "SUPPLIED_BY", "Supplier")]))
    assert any("not a fact type" in issue for issue in unknown)
    gap = [
        step("Product", "HAS_PROBLEM", "Problem"),
        step("Product", "HAS_PROBLEM", "Problem"),
        step("Part", "OCCURS_IN", "Part"),
    ]
    assert any("does not connect" in issue for issue in validate_text_schema(with_paths(gap)))
    assert any("empty" in issue for issue in validate_text_schema(with_paths([])))


def test_a_pinned_schema_without_goal_questions_stays_valid_but_a_proposal_needs_them():
    pinned = GOOD_SCHEMA.model_copy(update={"goal_questions": []})
    assert validate_text_schema(pinned) == []  # the reviewed schemas of earlier steps carry no questions
    replies = iter([pinned, GOOD_SCHEMA])

    def script(prompt, schema):
        return Critique(verdict="valid", issues=[]) if schema is Critique else next(replies)

    result = propose_text_schema("goal", chunk_document(doc("text " * 80)), ScriptedLLM(script), model="m")
    assert result.accepted and result.rounds == 2
    assert result.history[0].source == "code" and "goal question" in result.history[0].issues[0]


def test_the_schema_prompt_puts_the_goal_first_in_domain_neutral_words():
    # R56: questions, then paths, then types; and no word of either evaluation domain in the instructions
    work = PROMPT.split("Work in this order:")[1]
    assert work.index("goal_questions") < work.index("path") < work.index("entity types")
    rules = PROMPT.split("Rules:")[1].lower()
    assert "source the text comes from" in rules
    assert not any(
        word in PROMPT.lower()
        for word in (
            "vehicle",
            "complaint",
            "recall",
            "furniture",
            "review",
            "defect",
            "component",
            "product",
        )
    )


def test_no_schema_prompt_rule_quotes_an_evaluation_corpus():
    """As for the extraction rules (R34): four consecutive words of the instructions may not occur in the
    furniture reviews or the held-out narratives, or the prompt would steer toward the gold's wording."""

    def words(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", norm(text))

    root = Path(__file__).parent.parent
    texts = [
        *(root / "data" / "product_reviews").glob("*.md"),
        *(root / "heldout" / "nhtsa" / "data").rglob("*.md"),
    ]
    corpus = words(" ".join(t.read_text(encoding="utf-8") for t in texts))
    seen = {tuple(corpus[i : i + 4]) for i in range(len(corpus) - 3)}
    instructions = words(PROMPT.replace("{goal}", "").replace("{domain}", "").replace("{chunks}", ""))
    quoted = [
        " ".join(instructions[i : i + 4])
        for i in range(len(instructions) - 3)
        if tuple(instructions[i : i + 4]) in seen
    ]
    assert not quoted, f"schema prompt quotes the corpus: {quoted}"
