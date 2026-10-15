"""The agent as a question-answering system (R135): its tools built from the shared stores, and its loop's
run turned into the answer `kg qa` scores.

Role in the pipeline: `kg ask` and `kg qa` build it by name (`agent`, pipeline/qa_systems.py) like every
other system and ask it the questions; its answers are scored by the same code (sets and numbers, recall@k,
citation faithfulness) and judged by the same judge.
Design: Adapter from `AgentRun` to `SystemAnswer`. `shown` holds every chunk whose text the agent read, in
the order it read them, then the chunks it cited that it saw only by id (a claim's chunk), read from the
graph so the judge and the faithfulness check see their text; `retrieved` follows that order. The answer's
forms are the model's, but an entity written as a node's ref is given that node's name; its citations are
the checked ones (citations.py). `build_tools` is a Factory over
injected stores: the composition root (pipeline/qa_systems.py) opens the adapters and passes them in.
`AGENT_TOOL_TYPES` names the same tools by class, so what the model reads of them can be versioned without
building them (R146). R150 adds `rank_by_documents` after `find_passages`, reading the node reader with
the `rows` and `names` caps. R183: the system takes its name, so a variant of the agent with other tools
(pipeline/qa_systems.py's `AgentVariant`) writes its own answers file and MLflow run. R204: the answer
carries the outcome the loop settled on.
Not here: the loop (loop.py), the tools, opening any database.
"""

from pydantic import BaseModel, Field

from ..hybrid.passage_rerank import PassageReranker
from ..hybrid.seed_fusion import SeedSetting
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder
from ..query.answers import ShownChunk, SystemAnswer
from ..query.graph_schema import GraphSchema
from ..query.graph_store import StoredChunk
from ..query.plan_run import PlanStore
from ..structured.plan import ConstructionPlan
from .graph_tools import FindNodes, GetNode, Neighbours, NodeEvidence, RankByDocuments
from .loop import AgentLoop
from .node_reader import NodeReader
from .structured_tools import QueryRecords, QueryTables, Schema
from .tables import TableStore
from .text_reader import TextReader
from .text_tools import FindPassages, ReadDocument, SearchClaims, SearchText
from .tool import Tool

AGENT = "agent"  # the agent's name: its answers file and its MLflow run (`qa_agent`); a variant has its own

# The agent's tools by class, in `build_tools`' order. What the model reads of a tool (its name, description
# and arguments' schema) is set by its class, so the composition root versions that text from here (R146):
# before, a changed tool description reached the model with no change to any logged prompt version. A test
# holds this tuple equal to the classes `build_tools` builds, so a new tool cannot be left out.
AGENT_TOOL_TYPES: tuple[type[Tool], ...] = (
    FindNodes, GetNode, Neighbours, NodeEvidence, SearchText, SearchClaims, ReadDocument, FindPassages,
    RankByDocuments, Schema, QueryRecords, QueryTables,
)  # fmt: skip


class ToolCaps(BaseModel):
    """How much each tool returns at most, and how the fused lists are fused."""

    nodes: int = Field(ge=1)  # find_nodes: the seeding's K (R131b: 15)
    relations: int = Field(ge=1)  # neighbours: relation groups
    chunks: int = Field(ge=1)  # node_evidence: passages
    text_candidates: int = Field(ge=1)  # search_text: each passage list's depth before reranking (R199)
    text_shown: int = Field(ge=1)  # search_text: the reranked passages shown, whole (R199)
    claims: int = Field(ge=1)  # search_claims: claims, siblings included
    rows: int = Field(ge=1)  # query_records, query_tables and rank_by_documents: rows, groups or things
    value_chars: int = Field(ge=1)  # get_node and the queries: characters of one value
    names: int = Field(ge=1)  # get_node: other names; rank_by_documents: spellings per thing
    document_chunks: int = Field(ge=1)  # read_document: passages of one document (R141)
    matches: int = Field(ge=1)  # find_passages: passages holding the words (R141)
    text_rrf_k: int = Field(ge=0)  # the fusion constant of search_text and search_claims


def build_tools(
    store: PlanStore, units: UnitStore, nodes: NodeReader, text: TextReader, embedder: Embedder,
    tables: TableStore, schema: GraphSchema, plan: ConstructionPlan | None, seeding: SeedSetting,
    caps: ToolCaps, reranker: PassageReranker | None,
) -> list[Tool]:  # fmt: skip
    """The agent's tools, in the order the model reads them: seeding, the graph walk, the text (ranked,
    then exhaustive), the count of what the text names, the structured queries. `reranker` orders
    `search_text`'s pool (R199); without one the pool keeps RRF's order."""
    return [
        FindNodes(units, nodes, embedder, seeding.representation, seeding, caps.nodes),
        GetNode(nodes, caps.value_chars, caps.names),
        Neighbours(nodes, caps.relations),
        NodeEvidence(nodes, store, embedder, caps.chunks),
        SearchText(store, units, embedder, caps.text_rrf_k, caps.text_candidates, caps.text_shown, reranker),
        SearchClaims(units, nodes, embedder, caps.text_rrf_k, caps.claims),
        ReadDocument(text, caps.document_chunks),
        FindPassages(text, caps.matches, caps.value_chars),
        RankByDocuments(nodes, caps.rows, caps.names),
        Schema(schema, plan, tables),
        QueryRecords(store, schema, plan, nodes, caps.rows, caps.value_chars),
        QueryTables(tables, caps.rows, caps.value_chars),
    ]


class AgentSystem:
    """A `QASystem` answering each question with one run of the agent loop; `name` is the system's, `agent`
    or a variant's (R183), and is written into every answer."""

    def __init__(self, loop: AgentLoop, store: PlanStore, name: str = AGENT):
        self.name = name
        self._loop = loop
        self._store = store

    def answer(self, question_id: str, question: str) -> SystemAnswer:
        run = self._loop.run(question)
        cited_chunks = [i for i in run.citations.kept if run.ledger.is_chunk(i)]
        # R209: every cited chunk's whole stored text, read once: a cited chunk read only as a cut view is
        # recorded whole with that view beside it, and one never read is recorded for the judge
        whole = {c.chunk_id: c for c in self._store.chunks(cited_chunks)}
        read = [_shown(c.chunk_id, c.context, c.text, whole.get(c.chunk_id)) for c in run.ledger.read()]
        seen = {c.chunk_id for c in read}
        cited = [_shown(c.chunk_id, c.context, c.text, None) for i, c in whole.items() if i not in seen]
        shown = read + cited
        step = run.answer
        entities = None if step.entities is None else [run.ledger.name_of(e) for e in step.entities]
        # R142: the named entities and the number checked against what the kept citations' results hold
        support = run.ledger.support(entities, step.number, run.citations.kept)
        return SystemAnswer(
            question_id=question_id, system=self.name, retrieved=[c.chunk_id for c in shown],
            entities=entities, number=step.number, text=step.text, citations=run.citations.chunks,
            shown=shown, agent=run.trace.model_copy(update=support.model_dump()), outcome=step.outcome,
        )  # fmt: skip


def _shown(chunk_id: str, context: str, text: str, stored: StoredChunk | None) -> ShownChunk:
    """A chunk as the answer records it: the `text` the agent read, or, when `stored` (the whole passage of a
    cited chunk) differs from it, the whole passage with the read view as its `excerpt` (R209)."""
    if stored is None or stored.text == text:
        return ShownChunk(chunk_id=chunk_id, context=context, text=text)
    return ShownChunk(chunk_id=chunk_id, context=context, text=stored.text, excerpt=text)
