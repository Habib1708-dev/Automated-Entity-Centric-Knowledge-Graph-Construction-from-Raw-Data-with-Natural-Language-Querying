"""Each question-answering system, built by name from the parts every system shares (R116).

Role in the pipeline: `kg ask` and `kg qa` (qa_stages.py) look a system up here by the name they are given,
log what decides its answers, and build it over the current graph.
Design: Factory. `SYSTEMS` maps a name to a `SystemSpec`: how to build the system from the shared parts, the
params and prompts that decide its answers beyond the shared ones, and whether it is a plan system (R74:
frozen-plan replay, plan metrics). The parts every system shares (the reader with its model, temperature and
prompt; k; the embedder; the graph store) are built once, by `qa_parts`, so two systems differ only in what
their spec builds on top of them: a fair comparison is a property of the construction, not a promise. A new
approach is one more spec here, not one more branch in the stages.
Not here: the systems themselves (query/systems.py), the stages and their metrics (qa_stages.py).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..config import Settings
from ..core.errors import ConfigurationError, LLMUnavailableError
from ..llm.base import Embedder, LLMClient, prompt_version
from ..llm.thinking import with_thinking
from ..query import exact, planner, read_check
from ..query.frozen import FrozenQuery
from ..query.graph_store import Neo4jGraphStore
from ..query.plan_run import ChunkSource, PlanStore
from ..query.reader import PROMPT as READER_PROMPT
from ..query.reader import Reader
from ..query.systems import (
    RECORDS_VECTOR,
    QASettings,
    QASystem,
    ReadingSystem,
    VectorBaseline,
    build_graph_system,
    build_records_vector,
)
from ..structured.plan import ConstructionPlan, name_property
from ..tracking.base import Run
from .stage import PipelineContext, PipelineState


@dataclass(frozen=True)
class QAParts:
    """What every system is built from, made once per stage by `qa_parts`."""

    settings: Settings
    store: PlanStore
    embedder: Embedder
    llm: LLMClient  # the QA model at the settings' thinking level: the reader, planner and checks call it
    reader: Reader
    plan: ConstructionPlan | None  # the construction plan: the record labels and their name properties
    frozen: Mapping[str, FrozenQuery] | None = None  # R80: an earlier run's plans, replayed by a plan system


def _no_params(settings: Settings) -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class SystemSpec:
    """How to build one system, and what decides its answers beyond the shared parts.

    `params` gives the settings the system reads besides the shared ones. `prompts` maps a short name to a
    prompt the system sends besides the reader's: it is logged as `prompts/qa_<name>.txt` and versioned as
    the param `<name>_prompt_version`. `planned` marks a plan system (R74), which replays `--plans` (R80)
    and logs the plan metrics."""

    name: str
    build: Callable[[QAParts], QASystem]
    params: Callable[[Settings], dict[str, object]] = _no_params
    prompts: Mapping[str, str] = field(default_factory=dict)
    planned: bool = False


def reading(name: str, source: Callable[[QAParts], ChunkSource]) -> SystemSpec:
    """A system that reads the best k chunks of the source `source` builds (`ReadingSystem`): with the shared
    reader and k, it can differ from another such system only in the chunks it chooses."""
    return SystemSpec(
        name, lambda parts: ReadingSystem(name, source(parts), parts.reader, parts.settings.qa_top_k)
    )


def _vector(parts: QAParts) -> ChunkSource:
    return VectorBaseline(parts.store, parts.embedder, parts.settings.qa_top_k)


def _graph(parts: QAParts) -> QASystem:
    labels, names = _record_names(parts.plan)
    return build_graph_system(
        parts.store, parts.embedder, parts.llm, parts.reader, _qa_settings(parts.settings), labels, names,
        parts.frozen,
    )  # fmt: skip


def _records_vector(parts: QAParts) -> QASystem:
    if parts.plan is None:
        raise ConfigurationError("records plus vector RAG needs the construction plan's record labels")
    labels, names = _record_names(parts.plan)
    return build_records_vector(
        parts.store, parts.embedder, parts.llm, parts.reader, _qa_settings(parts.settings), labels, names,
        parts.frozen,
    )  # fmt: skip


def _record_names(plan: ConstructionPlan | None) -> tuple[set[str], dict[str, str]]:
    """The plan's record labels, and the property that names each label's records."""
    if plan is None:
        return set(), {}
    return {rule.label for rule in plan.nodes}, {rule.label: name_property(rule) for rule in plan.nodes}


def _qa_settings(s: Settings) -> QASettings:
    return QASettings(
        model=s.qa_model,
        temperature=s.llm_temperature,
        top_k=s.qa_top_k,
        link_fuzzy=s.qa_link_fuzzy,
        link_neighbours=s.qa_link_neighbours,
        cypher_limit=s.qa_cypher_limit,
        step_cap=s.qa_step_cap,
        check_limit=s.qa_check_limit,
        check_chunks=s.qa_check_chunks,
    )


def _plan_params(s: Settings) -> dict[str, object]:
    return {
        "qa_cypher_limit": s.qa_cypher_limit,
        "qa_cypher_timeout_s": s.qa_cypher_timeout_s,
        "qa_step_cap": s.qa_step_cap,
        "qa_check_limit": s.qa_check_limit,
        "qa_check_chunks": s.qa_check_chunks,
        "qa_link_fuzzy": s.qa_link_fuzzy,
        "qa_link_neighbours": s.qa_link_neighbours,
    }


def _graph_params(s: Settings) -> dict[str, object]:
    # the graph system's chunk source follows the traversal patterns, which the hop limit bounds
    return {**_plan_params(s), "qa_hops": s.qa_hops}


# the prompts both plan systems send; read_check with the parts a claim candidate adds (R85), so a change to
# either is a new version
_PLAN_PROMPTS = {
    "planner": planner.PROMPT + planner.RETRY,
    "read_check": read_check.PROMPT + read_check.CLAIM_RULE + read_check.CANDIDATE,
}

SYSTEMS: Mapping[str, SystemSpec] = {
    spec.name: spec
    for spec in (
        SystemSpec(
            "graph",
            _graph,
            _graph_params,
            {**_PLAN_PROMPTS, "cypher": exact.PROMPT + exact.RETRY},
            planned=True,
        ),
        reading("vector", _vector),
        # its text2cypher fallback sees the record layer only (R73), so it has its own prompt
        SystemSpec(
            RECORDS_VECTOR,
            _records_vector,
            _plan_params,
            {**_PLAN_PROMPTS, "cypher": exact.RECORDS_PROMPT + exact.RETRY},
            planned=True,
        ),
    )
}


def check_system(name: str) -> SystemSpec:
    """The spec of the system `name`. Raises `ConfigurationError` for a name no spec has, before anything
    runs."""
    if name not in SYSTEMS:
        raise ConfigurationError(f"unknown system '{name}'; choose from {', '.join(SYSTEMS)}")
    return SYSTEMS[name]


def system_params(settings: Settings, name: str) -> dict[str, object]:
    """What decides a system's answers: the shared reader's model, prompt and k, the embedding model, then
    the versions of the system's own prompts and its own settings."""
    spec = check_system(name)
    return {
        "system": name,
        "model": settings.qa_model,
        "thinking": settings.qa_thinking,
        "temperature": settings.llm_temperature,
        "prompt_version": prompt_version(READER_PROMPT),
        "qa_top_k": settings.qa_top_k,
        "embed_model": settings.embed_model,
        **{f"{key}_prompt_version": prompt_version(text) for key, text in spec.prompts.items()},
        **spec.params(settings),
    }


def log_prompts(run: Run, name: str) -> None:
    """Log the reader's prompt and the system's own prompts as text artifacts of `run`."""
    run.text(READER_PROMPT, "prompts/qa_reader.txt")
    for key, text in check_system(name).prompts.items():
        run.text(text, f"prompts/qa_{key}.txt")


def qa_parts(
    ctx: PipelineContext, state: PipelineState, frozen: Mapping[str, FrozenQuery] | None = None
) -> QAParts:
    """The shared parts over the current graph, with the settings' model, thinking level, reader and k.
    Raises `LLMUnavailableError` without a model or an embedder."""
    s = ctx.settings
    llm = with_thinking(ctx.require_llm(), s.qa_thinking)
    plan = state.load_plan(ctx, required=False)
    if ctx.embedder is None:
        raise LLMUnavailableError("question answering needs an embedding model: set GEMINI_API_KEY")
    return QAParts(
        settings=s,
        store=Neo4jGraphStore(ctx.driver, plan, s.qa_hops, s.qa_cypher_timeout_s),
        embedder=ctx.embedder,
        llm=llm,
        reader=Reader(llm, s.qa_model, s.llm_temperature),
        plan=plan,
        frozen=frozen,
    )


def build_system(
    ctx: PipelineContext, state: PipelineState, name: str, frozen: Mapping[str, FrozenQuery] | None = None
) -> QASystem:
    """The system `name` over the current graph; a plan system replays `frozen` (R80) when given."""
    return check_system(name).build(qa_parts(ctx, state, frozen))
