"""The question-answering systems: the graph system, the vector-only baseline, and records plus vector RAG.

Role in the pipeline: `kg ask` and `kg qa` build them by name (pipeline/qa_systems.py) and ask them the
questions.
Design: Strategy. The systems implement `QASystem.answer` and share the reader, the number of chunks shown
(k) and the embedder, so a difference in their scores comes only from how they choose:
- `ReadingSystem` (R116): the best k chunks of a chunk source, read by the reader. Two chunk sources:
  - `VectorBaseline`: the chunks nearest the question in the chunk vector index, nothing from the graph;
  - `GraphRetrieval`, the graph's retrieval route: link the question's names to nodes (names.py), follow
    the fixed traversal patterns (traversal.py), rank the chunks they reach by similarity to the question;
- `PlanSystem` (R74): the planner (planner.py) writes a query plan of fixed primitives, code checks it
  (plan.py) and runs it (plan_run.py); a refused or failing plan gets one retry with the reasons, then the
  question falls back to text2cypher (exact.py, logged so its use can be counted) and then to reading.
  Two plan systems: the graph system (`build_graph_system`: every primitive, the graph's retrieval route
  as its chunk source) and records plus vector RAG (`build_records_vector`: the record layer only, no claim
  primitives, vector search as its chunk source). Against each other they show what the extracted claims
  add; against the vector baseline, what the records add. The router of R71 is gone: a plan may combine
  records, claims and reading, so "exact or retrieval" is no longer chosen up front. A frozen run (R80,
  frozen.py) replays an earlier run's plans and text2cypher queries instead of asking the model, so that a
  graph change is measured by its answers alone.
Not here: the prompts (reader.py, planner.py, read_check.py, exact.py), Cypher (plan_cypher.py,
graph_store.py).
"""

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel

from ..core.errors import QueryPlanError
from ..llm.base import Embedder, LLMClient
from ..validation.qa import Citation
from .answers import ExactTrace, PlanAttempt, PlanTrace, RetrievalTrace, ShownChunk, SystemAnswer
from .exact import RECORDS_PROMPT, ExactOutcome, ExactRoute
from .frozen import FrozenQuery
from .graph_store import GraphStore, StoredChunk
from .names import NameLinker, NodeName
from .plan import PlanSchema, QueryPlan, check_plan
from .plan_run import ChunkSource, PlanRun, PlanRunner, PlanSettings, PlanStore
from .planner import Planner
from .ranking import rank
from .read_check import ReadChecker
from .reader import Reader

# the records-plus-vector system's name: its answers file, its MLflow run (`qa_records_vector`)
RECORDS_VECTOR = "records_vector"


class QASystem(Protocol):
    """Answers one question; `name` labels its answers file and its MLflow run."""

    name: str

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        """The answer, with the chunks shown to the reader. Raises `LLMResponseError` if the model fails."""
        ...


class ReadingSystem:
    """Answers by reading: the best k chunks of its chunk source, read by the shared reader, with the
    source's trace. Template Method: systems built on it differ only in their source, so a difference in
    their answers comes only from the chunks they choose."""

    def __init__(self, name: str, source: ChunkSource, reader: Reader, k: int):
        self.name = name
        self._source = source
        self._reader = reader
        self._k = k

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        ranked, trace = self._source.ranked(question)
        return _answer(self.name, question_id, question, ranked[: self._k], self._reader, trace)


class VectorBaseline:
    """The `depth` chunks nearest the question in the chunk vector index (a `ChunkSource`). The vector-only
    baseline's source, and the chunk source of records plus vector RAG. `kg qa` gives it the depth k, the
    chunks it reads; `kg retrieve-eval` the largest budget it scores (R117), so a budget beyond k is not
    cut short."""

    def __init__(self, store: GraphStore, embedder: Embedder, depth: int):
        self._store = store
        self._embedder = embedder
        self._depth = depth

    def ranked(self, question: str) -> tuple[list[StoredChunk], RetrievalTrace | None]:
        vector = self._embedder.embed([question])[0]
        return self._store.chunks(self._store.nearest_chunks(vector, self._depth)), None


class GraphRetrieval:
    """The graph's retrieval route (a `ChunkSource`): linked names -> traversal patterns -> ranked chunks.
    When nothing is linked or reached, it gives nothing: falling back to vector search would hide the
    graph's own failure. The chunk source of the graph system. It ranks every chunk it reaches, so it needs
    no depth; the linked nodes are its seeds (R117)."""

    def __init__(self, store: GraphStore, embedder: Embedder, linker: NameLinker):
        self._store = store
        self._embedder = embedder
        self.linker = linker
        # a link names its node by element id; the seeds name it by its stable ref
        self._refs = {(n.kind, n.node_id): n.ref for n in linker.nodes}

    def ranked(self, question: str) -> tuple[list[StoredChunk], RetrievalTrace | None]:
        vector = self._embedder.embed([question])[0]
        linked = self.linker.link(question, vector)
        reached = self._store.reach(
            [n.node_id for n in linked if n.kind == "thing"], [n.node_id for n in linked if n.kind == "kind"]
        )
        ranked = rank(vector, self._store.chunks(sorted(set().union(*reached.values()))))
        trace = RetrievalTrace(
            linked=linked,
            candidates=[c.chunk_id for c in ranked],
            reached_by={pattern: sorted(ids) for pattern, ids in reached.items()},
            # in link order: spelling links first, then the names nearest in meaning by rank
            seeds=[self._refs[(n.kind, n.node_id)] for n in linked],
        )
        return ranked, trace


def _linker(names: list[NodeName], embedder: Embedder, fuzzy: float, neighbours: int) -> NameLinker:
    """A linker over `names`, their display names embedded once (one batched call) when meaning is used."""
    vectors = embedder.embed([n.name for n in names]) if names and neighbours else None
    return NameLinker(names, vectors, fuzzy, neighbours)


def build_graph_retrieval(
    store: GraphStore, embedder: Embedder, fuzzy: float, neighbours: int
) -> GraphRetrieval:
    """The graph route over `store`, its node names read and embedded once."""
    return GraphRetrieval(store, embedder, _linker(store.node_names(), embedder, fuzzy, neighbours))


class QASettings(BaseModel):
    """The settings a plan system is built with (from config.Settings, passed down by the stage)."""

    model: str
    temperature: float
    top_k: int
    link_fuzzy: float
    link_neighbours: int
    cypher_limit: int
    step_cap: int = 200
    check_limit: int = 30
    check_chunks: int = 3


class PlanSystem:
    """A plan system: plan, check, run; one retry with the reasons; then text2cypher, then reading.

    The answer carries every plan tried (`PlanTrace`), the chunks read, the read_check quotes as citations,
    and the fallback's trace when one answered. Template shared by the graph system and records plus
    vector RAG, which differ in their schema, their primitives and their chunk source."""

    def __init__(
        self,
        name: str,
        planner: Planner,
        schema: PlanSchema,
        runner: PlanRunner,
        exact: ExactRoute,
        source: ChunkSource,
        reader: Reader,
        k: int,
        frozen: Mapping[str, FrozenQuery] | None = None,
    ):
        """`frozen` (R80): question id -> an earlier run's query decisions, replayed instead of planning."""
        self.name = name
        self._planner = planner
        self._schema = schema
        self._runner = runner
        self._exact = exact
        self._source = source
        self._reader = reader
        self._k = k
        self._frozen = frozen

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        if self._frozen is not None:
            return self._replay(question_id, question, self._frozen[question_id])
        attempts: list[PlanAttempt] = []
        refused: QueryPlan | None = None
        for _ in range(2):  # the plan and one retry
            proposal = self._planner.propose(question, refused, attempts[-1].issues if attempts else None)
            attempt, run = self._try(proposal, question)
            attempts.append(attempt)
            if run is not None:
                return self._planned(question_id, run, PlanTrace(attempts=attempts))
            refused = proposal
        return self._fall_back(question_id, question, attempts)

    def _replay(self, question_id: str, question: str, frozen: FrozenQuery) -> SystemAnswer:
        """The earlier run's decisions on the current graph, no model writing a query: its plan, else its
        text2cypher query, else reading. A frozen plan or query the current graph refuses or fails goes to
        reading; planning anew would bring back the confound the freeze removes."""
        attempts: list[PlanAttempt] = []
        exact: ExactTrace | None = None
        if frozen.plan is not None:
            attempt, run = self._try(frozen.plan, question)
            attempts.append(attempt)
            if run is not None:
                return self._planned(question_id, run, PlanTrace(attempts=attempts, frozen=True))
        elif frozen.cypher is not None:
            outcome = self._exact.replay(frozen.cypher, frozen.answer_form)
            if outcome.trace.answered:
                return self._exact_answer(question_id, outcome, PlanTrace(attempts=attempts, frozen=True))
            exact = outcome.trace
        return self._read(question_id, question, exact, PlanTrace(attempts=attempts, frozen=True))

    def _try(self, proposal: QueryPlan, question: str) -> tuple[PlanAttempt, PlanRun | None]:
        """Check the plan and run it: the attempt's trace, and the run when it reached its end."""
        checked = check_plan(proposal, self._schema, question)
        attempt = PlanAttempt(plan=proposal, issues=list(checked.issues), dropped=checked.dropped)
        if checked.issues:
            return attempt, None
        try:
            run = self._runner.run(checked.plan, question)
        except QueryPlanError as e:
            attempt.issues = [str(e)]
            return attempt, None
        attempt.steps = run.steps
        return attempt, run

    def _planned(self, question_id: str, run: PlanRun, trace: PlanTrace) -> SystemAnswer:
        return SystemAnswer(
            question_id=question_id,
            system=self.name,
            retrieved=[c.chunk_id for c in run.shown],
            entities=run.entities,
            number=run.number,
            text=run.text,
            citations=run.citations,
            shown=run.shown,
            plan=trace.model_copy(update={"checks": run.checks, "verified": run.verified}),
        )

    def _fall_back(self, question_id: str, question: str, attempts: list[PlanAttempt]) -> SystemAnswer:
        """No plan ran: text2cypher (logged, to be counted and later removed), then reading."""
        outcome = self._exact.answer(question)
        if outcome.trace.answered:
            return self._exact_answer(question_id, outcome, PlanTrace(attempts=attempts))
        return self._read(question_id, question, outcome.trace, PlanTrace(attempts=attempts))

    def _exact_answer(self, question_id: str, outcome: ExactOutcome, trace: PlanTrace) -> SystemAnswer:
        return SystemAnswer(
            question_id=question_id,
            system=self.name,
            entities=outcome.entities,
            number=outcome.number,
            exact=outcome.trace,
            plan=trace.model_copy(update={"fallback": "text2cypher"}),
        )

    def _read(
        self, question_id: str, question: str, exact: ExactTrace | None, trace: PlanTrace
    ) -> SystemAnswer:
        """Reading, the last fallback: the chunk source's best k chunks, read by the reader."""
        chunks, retrieval = self._source.ranked(question)
        read = _answer(self.name, question_id, question, chunks[: self._k], self._reader, retrieval)
        return read.model_copy(
            update={"exact": exact, "plan": trace.model_copy(update={"fallback": "retrieval"})}
        )


def build_graph_system(
    store: PlanStore,
    embedder: Embedder,
    llm: LLMClient,
    reader: Reader,
    settings: QASettings,
    record_labels: set[str],
    name_properties: dict[str, str],
    frozen: Mapping[str, FrozenQuery] | None = None,
) -> PlanSystem:
    """The graph system over `store`: every primitive over the whole graph, the graph's retrieval route as its
    chunk source, the schema read once and the node names embedded once."""
    schema = store.schema()
    retrieval = build_graph_retrieval(store, embedder, settings.link_fuzzy, settings.link_neighbours)
    exact = ExactRoute(store, llm, settings.model, settings.cypher_limit, settings.temperature, schema=schema)
    plan_schema = PlanSchema(schema=schema, record_labels=frozenset(record_labels))
    return _plan_system("graph", store, embedder, llm, reader, settings, plan_schema, retrieval.linker,
                        name_properties, retrieval, exact, frozen)  # fmt: skip


def build_records_vector(
    store: PlanStore,
    embedder: Embedder,
    llm: LLMClient,
    reader: Reader,
    settings: QASettings,
    record_labels: set[str],
    name_properties: dict[str, str],
    frozen: Mapping[str, FrozenQuery] | None = None,
) -> PlanSystem:
    """Records plus vector RAG over `store`: plans over the record layer alone (`record_labels`, the plan's
    labels; no claim primitives), vector search as its chunk source, and the records-only text2cypher of R73
    as its logged fallback, which refuses any query naming the text layer."""
    full = store.schema()
    records = full.records_only(record_labels)
    exact = ExactRoute(
        store,
        llm,
        settings.model,
        settings.cypher_limit,
        settings.temperature,
        schema=records,
        prompt=RECORDS_PROMPT,
        excluded=full.names_outside(record_labels),
    )
    things = [n for n in store.node_names() if n.kind == "thing"]
    linker = _linker(things, embedder, settings.link_fuzzy, settings.link_neighbours)
    plan_schema = PlanSchema(schema=records, record_labels=frozenset(record_labels), claims=False)
    vector = VectorBaseline(store, embedder, settings.top_k)
    return _plan_system(RECORDS_VECTOR, store, embedder, llm, reader, settings, plan_schema, linker,
                        name_properties, vector, exact, frozen)  # fmt: skip


def _plan_system(
    name: str,
    store: PlanStore,
    embedder: Embedder,
    llm: LLMClient,
    reader: Reader,
    settings: QASettings,
    plan_schema: PlanSchema,
    linker: NameLinker,
    name_properties: dict[str, str],
    source: ChunkSource,
    exact: ExactRoute,
    frozen: Mapping[str, FrozenQuery] | None,
) -> PlanSystem:
    runner = PlanRunner(
        store,
        embedder,
        linker,
        plan_schema,
        name_properties,
        reader,
        ReadChecker(llm, settings.model, settings.temperature),
        source,
        PlanSettings(
            top_k=settings.top_k,
            step_cap=settings.step_cap,
            check_limit=settings.check_limit,
            check_chunks=settings.check_chunks,
            neighbours=settings.link_neighbours,
        ),
    )
    planner = Planner(llm, settings.model, plan_schema.schema.text(), settings.temperature)
    return PlanSystem(name, planner, plan_schema, runner, exact, source, reader, settings.top_k, frozen)


def _answer(
    system: str,
    question_id: str,
    question: str,
    chosen: list[StoredChunk],
    reader: Reader,
    trace: RetrievalTrace | None = None,
) -> SystemAnswer:
    shown = [ShownChunk(chunk_id=c.chunk_id, context=c.context, text=c.text) for c in chosen]
    reply = reader.read(question, shown)
    return SystemAnswer(
        question_id=question_id,
        system=system,
        retrieved=[c.chunk_id for c in shown],
        entities=reply.entities,
        number=reply.number,
        text=reply.text,
        citations=[Citation(chunk_id=c.chunk_id, quote=c.quote) for c in reply.citations],
        shown=shown,
        trace=trace,
    )
