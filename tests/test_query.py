"""Question answering over the graph (R71), without Neo4j: linking a question's names to nodes, ranking the
reached chunks, the reader's prompt and its no-text answer, the graph route and the vector baseline over a
fake graph store, the answers file, and `kg qa-score` without a graph. The Cypher of the store and `kg qa`
end to end are tested in test_query_graph.py."""

import pytest

from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError, EvaluationError
from kgbuilder.graph.connection import open_driver
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline.qa_stages import QAScoreStage, QAStage
from kgbuilder.query.answers import SystemAnswer, load_system_answers, shown_texts
from kgbuilder.query.graph_store import StoredChunk
from kgbuilder.query.names import NameLinker, NodeName, spans, words
from kgbuilder.query.reader import NOTHING_TO_READ, PROMPT, Reader, ReaderAnswer, ReaderCitation, build_prompt
from kgbuilder.query.systems import GraphRetrieval, VectorBaseline, build_graph_retrieval, rank
from kgbuilder.validation.judge import JudgeMeta
from kgbuilder.validation.qa import AnswerVerdict, QAVerdicts
from kgbuilder.validation.qa_gold import QAGold

from .evaluation_corpora import quoted_four_grams
from .fakes import RecordingTracker, ScriptedLLM

DRESSER = NodeName(kind="thing", node_id="4:x:1", name="Quill Press")
RAILS = NodeName(kind="kind", node_id="k-rails", name="guide rails", aliases=["metal rails"])
SLEEVE = NodeName(kind="kind", node_id="k-sleeve", name="sleeve")
TINY = [NodeName(kind="kind", node_id=f"k-{n}", name=n) for n in ("the", "of it", "ab")]


def linker(
    nodes: list[NodeName], vectors: list[list[float]] | None = None, neighbours: int = 0
) -> NameLinker:
    return NameLinker(nodes, vectors, fuzzy=90.0, neighbours=neighbours)


# --- linking names -------------------------------------------------------------------------------


def test_words_keep_identifiers_whole_and_spans_skip_function_words():
    assert words("Which pump, HP40-1183?") == ["which", "pump", "hp40-1183"]
    assert spans(["press", "of", "the", "rails"], 2) == ["press of", "the rails"]  # "of the" names nothing


def test_a_run_of_words_links_a_node_spelled_alike_by_its_name_or_an_alias():
    nodes = [DRESSER, RAILS, SLEEVE]
    linked = linker(nodes).link("Who supplies the guide rail of the Quill Press?", None)
    assert {(n.node_id, n.by) for n in linked} == {("4:x:1", "spelling"), ("k-rails", "spelling")}
    # an alias counts like the name: the question says "metal rails", the node is "guide rails"
    assert [n.node_id for n in linker(nodes).link("Are the metal rails rough?", None)] == ["k-rails"]


def test_function_words_and_very_short_names_never_link_by_spelling():
    assert linker(TINY).link("Which of it is the ab test?", None) == []


def test_the_names_nearest_in_meaning_are_linked_by_rank_after_the_spelling_links():
    vectors = [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]  # Quill Press, guide rails, sleeve
    linked = linker([DRESSER, RAILS, SLEEVE], vectors, neighbours=2).link("Quill Press flaws?", [1.0, 0.1])
    # the press is spelled in the question; the rails are the next nearest in meaning; the sleeve is not
    assert [(n.node_id, n.by) for n in linked] == [("4:x:1", "spelling"), ("k-rails", "meaning")]


# --- ranking and reading -------------------------------------------------------------------------


def chunk(chunk_id: str, embedding: list[float] | None, text: str = "", context: str = "Doc") -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id, context=context, text=text or f"text of {chunk_id}", embedding=embedding
    )


def test_rank_orders_by_cosine_and_puts_chunks_without_a_vector_last():
    chunks = [chunk("c-none", None), chunk("c-far", [0.0, 1.0]), chunk("c-near", [2.0, 0.1])]
    assert [c.chunk_id for c in rank([1.0, 0.0], chunks)] == ["c-near", "c-far", "c-none"]


def test_the_reader_prompt_shows_the_question_and_every_chunk_with_its_document():
    prompt = build_prompt("Which part cracks?", [chunk("d.md#1", None, "It cracks.", "Quill Press notes")])
    assert "<question>Which part cracks?</question>" in prompt
    assert '<chunk id="d.md#1" document="Quill Press notes">\nIt cracks.\n</chunk>' in prompt


def test_with_no_chunk_the_reader_answers_without_asking_the_model():
    llm = ScriptedLLM(lambda prompt, schema: pytest.fail("no chunk, so no call"))
    assert Reader(llm, "m").read("Which part cracks?", []).text == NOTHING_TO_READ


def test_the_reader_prompt_speaks_no_corpus_language():
    # the rules must not steer toward a dataset under evaluation (prompt-engineering skill, R34)
    rules = PROMPT.split("Rules:")[1].split("<question>")[0]
    banned = ("product", "review", "vehicle", "complaint", "recall", "drawer", "defect", "pump", "staff")
    assert not [w for w in banned if w in rules.lower()]
    assert quoted_four_grams(rules) == []


# --- the two systems over a fake store -----------------------------------------------------------


class FakeStore:
    """`GraphStore` with fixed answers; records what the systems ask it."""

    def __init__(self, names=(), reached=None, chunks=(), nearest=()):
        self._names = list(names)
        self._reached = reached or {}
        self._chunks = {c.chunk_id: c for c in chunks}
        self._nearest = list(nearest)
        self.reach_calls: list[tuple[list[str], list[str]]] = []

    def node_names(self):
        return self._names

    def reach(self, things, kinds):
        self.reach_calls.append((things, kinds))
        return {pattern: set(ids) for pattern, ids in self._reached.items()}

    def chunks(self, chunk_ids):
        return [self._chunks[i] for i in chunk_ids if i in self._chunks]

    def nearest_chunks(self, vector, k):
        return self._nearest[:k]


class FixedEmbedder:
    """Every text on one vector, except those given; records the batches it embeds."""

    def __init__(self, vectors: dict[str, list[float]] | None = None):
        self._vectors = vectors or {}
        self.batches: list[list[str]] = []

    def embed(self, texts):
        self.batches.append(list(texts))
        return [self._vectors.get(t, [1.0, 0.0]) for t in texts]


def citing_reader(answer: ReaderAnswer | None = None) -> tuple[Reader, ScriptedLLM]:
    llm = ScriptedLLM(lambda prompt, schema: answer or ReaderAnswer(text="t"))
    return Reader(llm, "reader-model"), llm


def test_the_baseline_reads_the_k_nearest_chunks_and_nothing_from_the_graph():
    store = FakeStore(
        chunks=[chunk("c1", None), chunk("c2", None), chunk("c3", None)], nearest=["c2", "c1", "c3"]
    )
    reply = ReaderAnswer(entities=["Quill Press"], citations=[ReaderCitation(chunk_id="c2", quote="text")])
    reader, llm = citing_reader(reply)
    answer = VectorBaseline(store, FixedEmbedder(), reader, k=2).answer("Q1", "Which press?")
    assert answer.retrieved == ["c2", "c1"] and [c.chunk_id for c in answer.shown] == ["c2", "c1"]
    assert answer.entities == ["Quill Press"] and answer.citations[0].chunk_id == "c2"
    assert store.reach_calls == [] and answer.trace is None and answer.system == "vector"
    assert llm.calls == [("ReaderAnswer", "reader-model")]


def test_the_graph_route_reads_the_best_ranked_chunks_its_traversal_reached():
    store = FakeStore(
        reached={"thing_observations": {"c-far", "c-near"}, "kind_observations": {"c-near", "c-mid"}},
        chunks=[chunk("c-far", [0.0, 1.0]), chunk("c-near", [1.0, 0.0]), chunk("c-mid", [0.7, 0.7])],
    )
    reader, _ = citing_reader()
    system = GraphRetrieval(store, FixedEmbedder(), linker([DRESSER, RAILS]), reader, k=2)
    answer = system.answer("Q2", "What is wrong with the guide rails of the Quill Press?")
    # things and kinds go to the traversal separately: a thing starts by element id, a kind by entity id
    assert store.reach_calls == [(["4:x:1"], ["k-rails"])]
    assert answer.retrieved == ["c-near", "c-mid"]  # the ranking cut "c-far"
    assert answer.trace.candidates == ["c-near", "c-mid", "c-far"]
    assert answer.trace.reached_by == {
        "thing_observations": ["c-far", "c-near"],
        "kind_observations": ["c-mid", "c-near"],
    }


def test_the_graph_route_gives_the_reader_nothing_when_no_name_links():
    store = FakeStore(chunks=[chunk("c1", [1.0, 0.0])], nearest=["c1"])
    reader, llm = citing_reader()
    answer = GraphRetrieval(store, FixedEmbedder(), linker([DRESSER]), reader, k=2).answer("Q3", "Why?")
    # no fallback to vector search: the failure must show as the graph's own
    assert answer.retrieved == [] and answer.text == NOTHING_TO_READ and llm.calls == []
    assert answer.trace.linked == [] and answer.trace.candidates == []


def test_the_graph_route_embeds_the_node_names_once_and_each_question_once():
    embedder = FixedEmbedder()
    reader, _ = citing_reader()
    system = build_graph_retrieval(FakeStore(names=[DRESSER, RAILS]), embedder, reader, 5, 90.0, neighbours=1)
    system.answer("Q1", "a question")
    system.answer("Q2", "another question")
    assert embedder.batches == [["Quill Press", "guide rails"], ["a question"], ["another question"]]


# --- the answers file ----------------------------------------------------------------------------


def test_an_answers_file_round_trips_and_keeps_the_texts_shown(tmp_path):
    store = FakeStore(chunks=[chunk("c1", None, "The rails are rough.")], nearest=["c1"])
    reader, _ = citing_reader(
        ReaderAnswer(text="rough", citations=[ReaderCitation(chunk_id="c1", quote="rough")])
    )
    answer = VectorBaseline(store, FixedEmbedder(), reader, k=1).answer("Q1", "How are the rails?")
    path = tmp_path / "answers_vector.jsonl"
    path.write_text(answer.model_dump_json() + "\n\n", encoding="utf-8")
    loaded = load_system_answers(path)
    assert loaded == [answer] and isinstance(loaded[0], SystemAnswer)
    assert shown_texts(loaded) == {"c1": "The rails are rough."}
    path.write_text('{"question_id": "Q1"}\n', encoding="utf-8")  # no system
    with pytest.raises(EvaluationError, match="answers line 1"):
        load_system_answers(path)


CORPUS = {"data_dir": "x", "chunk_max_chars": 1500, "chunk_min_chars": 200, "chunk_overlap_chars": 0}


def test_qa_score_scores_an_answers_file_with_the_judges_verdicts_and_reads_no_graph(tmp_path):
    gold = QAGold.model_validate(
        {
            "dataset": "t",
            "written_by": "claude",
            "date": "2026-09-30",
            "corpus": CORPUS,
            "questions": [
                {"id": "Q1", "type": "lookup", "question": "How are the rails?",
                 "expected": {"text": "Rough."}, "route": "retrieval",
                 "chunks": [{"chunk_id": "c1", "quote": "rough"}]},
            ],
        }
    )  # fmt: skip
    store = FakeStore(chunks=[chunk("c1", None, "The rails are rough.")], nearest=["c1"])
    reader, _ = citing_reader(
        ReaderAnswer(text="rough", citations=[ReaderCitation(chunk_id="c1", quote="rough")])
    )
    answer = VectorBaseline(store, FixedEmbedder(), reader, k=1).answer("Q1", "How are the rails?")
    files = {"gold.json": gold.model_dump_json(), "answers_vector.jsonl": answer.model_dump_json()}
    verdicts = QAVerdicts(
        judge=JudgeMeta(model="claude-fable-5-1", date="2026-09-30"),
        gold="gold.json",
        answers="answers_vector.jsonl",
        verdicts=[AnswerVerdict(question_id="Q1", correct=True, reason="says the rails are rough")],
    )
    files["verdicts.json"] = verdicts.model_dump_json()
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    # a driver to a port nothing listens on: any query would fail, so passing proves no graph is read
    driver = open_driver("bolt://localhost:1", "neo4j", "unused")
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(qa_top_k=1), driver=driver, out=tmp_path / "out", tracker=tracker)
    state = PipelineState(
        gold=tmp_path / "gold.json",
        answers=tmp_path / "answers_vector.jsonl",
        verdicts=tmp_path / "verdicts.json",
    )
    try:
        report = run_stages(ctx, state, [QAScoreStage()]).qa_reports["vector"]
    finally:
        driver.close()
    assert (report.overall.correct.k, report.overall.correct.n, report.overall.unjudged) == (1, 1, 0)
    run = tracker.run("qa_score")
    assert run.logged_params["judge_model"] == "claude-fable-5-1" and "answers_hash" in run.logged_params
    assert run.logged_metrics["answer_accuracy"] == 1.0 and run.logged_metrics["citation_faithfulness"] == 1.0


def test_an_unknown_system_is_refused_before_anything_runs():
    with pytest.raises(ConfigurationError, match="unknown system 'hybrid'"):
        QAStage("hybrid")
