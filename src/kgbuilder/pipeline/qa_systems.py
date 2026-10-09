"""Each question-answering system, built by name from the parts every system shares (R116).

Role in the pipeline: `kg ask` and `kg qa` (qa_stages.py) look a system up here by the name they are given,
log what decides its answers, and build it over the current graph; `kg retrieve-eval` (retrieval_stages.py)
builds a reading system's chunk source alone, without the reader.
Design: Factory. `SYSTEMS` maps a name to a `SystemSpec`: how to build the system from the shared parts, the
params and prompts that decide its answers beyond the shared ones, whether it is a plan system (R74:
frozen-plan replay, plan metrics), and for a reading system its chunk source. The parts every system shares
(the reader with its model, temperature and prompt; k; the embedder; the graph store) are built once, by
`qa_parts`, so two systems differ only in what their spec builds on top of them: a fair comparison is a
property of the construction, not a promise. A new approach is one more spec here, not one more branch in
the stages.
Not here: the systems themselves (query/systems.py), the stages and their metrics (qa_stages.py,
retrieval_stages.py), the check that the loaded graph fits the gold (qa_graph.py).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..config import Settings
from ..core.errors import ConfigurationError, LLMUnavailableError
from ..hybrid.source import HybridSettings, build_hybrid
from ..hybrid.unit_store import Neo4jUnitStore, UnitStore
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
    build_graph_retrieval,
    build_graph_system,
    build_records_vector,
)
from ..structured.plan import ConstructionPlan, name_property
from ..tracking.base import Run
from .index_stages import card_representation
from .stage import PipelineContext, PipelineState


@dataclass(frozen=True)
class SourceParts:
    """What every chunk source is built from, made once per stage by `source_parts` (R117): `kg
    retrieve-eval` builds a source from these alone, with no model and no reader."""

    settings: Settings
    store: PlanStore
    embedder: Embedder
    plan: ConstructionPlan | None  # the construction plan: the record labels and their name properties
    units: UnitStore  # the retrieval index layer (R119), which the hybrid sources search (R120b)


@dataclass(frozen=True)
class QAParts(SourceParts):
    """What every system is built from, made once per stage by `qa_parts`: the source parts, and the model
    and the reader every system answers with."""

    llm: LLMClient  # the QA model at the settings' thinking level: the reader, planner and checks call it
    reader: Reader
    frozen: Mapping[str, FrozenQuery] | None = None  # R80: an earlier run's plans, replayed by a plan system


# a reading system's chunk source, built from the source parts and asked to rank at least `depth` chunks:
# `kg qa` asks for k, the chunks the reader reads; `kg retrieve-eval` for its largest budget (R117)
SourceBuilder = Callable[[SourceParts, int], ChunkSource]


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
    source: SourceBuilder | None = None  # a reading system's chunk source, which kg retrieve-eval ranks alone


def reading(
    name: str, source: SourceBuilder, params: Callable[[Settings], dict[str, object]] = _no_params
) -> SystemSpec:
    """A system that reads the best k chunks of the source `source` builds (`ReadingSystem`): with the shared
    reader and k, it can differ from another such system only in the chunks it chooses. `params` gives the
    settings its source reads."""

    def build(parts: QAParts) -> QASystem:
        k = parts.settings.qa_top_k
        return ReadingSystem(name, source(parts, k), parts.reader, k)

    return SystemSpec(name, build, params, source=source)


def _vector(parts: SourceParts, depth: int) -> ChunkSource:
    return VectorBaseline(parts.store, parts.embedder, depth)


def _graph_retrieval(parts: SourceParts, depth: int) -> ChunkSource:
    # the route ranks every chunk its traversal reaches, so it fills any depth as far as the graph allows
    s = parts.settings
    return build_graph_retrieval(parts.store, parts.embedder, s.qa_link_fuzzy, s.qa_link_neighbours)


def hybrid_spec(
    name: str, cards: str, retrievers: tuple[str, ...] = (), node_seeds: bool = False
) -> SystemSpec:
    """A hybrid system (R120b): the hybrid source over the cards of the representation `cards`, read like
    every reading system. Its settings are the `hybrid_*` ones, but for a fixed retriever list `retrievers`
    (R123's card systems, R126's single techniques), which no setting changes; its depth is at least
    `hybrid_depth`, so fusion sees as deep as tuned whatever k or budget the caller reads. `node_seeds`
    gives its chunk and claim retrievers seeds (R126)."""

    def listed(s: Settings) -> list[str]:
        return list(retrievers) or s.hybrid_retrievers

    def source(parts: SourceParts, depth: int) -> ChunkSource:
        s = parts.settings
        settings = HybridSettings(
            retrievers=listed(s),
            rrf_k=s.hybrid_rrf_k,
            depth=max(depth, s.hybrid_depth),
            cards=cards,
            node_seeds=node_seeds,
        )
        # the name-linker route embeds every node name once: built only when the settings list it
        route = _graph_retrieval(parts, depth) if "graph_route" in settings.retrievers else None
        version = card_representation(s, cards).version
        return build_hybrid(settings, parts.store, parts.units, parts.embedder, route, version, s.embed_model)

    def params(s: Settings) -> dict[str, object]:
        return {
            "hybrid_retrievers": listed(s),
            "hybrid_rrf_k": s.hybrid_rrf_k,
            "hybrid_depth": s.hybrid_depth,
            "hybrid_cards": cards,
            "representation_version": card_representation(s, cards).version,
            **_graph_retrieval_params(s),  # the cards' traversal and the name-linker route, when listed
            # only where it is on, so every system registered before R126 logs the params it logged then
            **({"hybrid_node_seeds": True} if node_seeds else {}),
        }

    return reading(name, source, params)


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


def _graph_retrieval_params(s: Settings) -> dict[str, object]:
    # the name linker's spelling score and meaning neighbours, then the traversal's hop limit
    return {
        "qa_link_fuzzy": s.qa_link_fuzzy,
        "qa_link_neighbours": s.qa_link_neighbours,
        "qa_hops": s.qa_hops,
    }


# The card systems of the A-against-B comparison (the plan change after R121), one of each per representation
# (A "template", B "summary"): `card_dense_<rep>` (the headline: its seeds; and its chunks),
# `card_lexical_<rep>` (the seeds of a summary's words), `card_seeds_<rep>` (both card lists fused with the
# name linker at the sealed rrf_k, the seeds of R121's M5). Their lists are fixed here, untuned, and no
# HYBRID_RETRIEVERS setting changes them, so a held-out run of them takes no override.
CARD_RETRIEVERS: Mapping[str, tuple[str, ...]] = {
    "card_dense": ("card_dense",),
    "card_lexical": ("card_lexical",),
    "card_seeds": ("card_dense", "card_lexical", "graph_route"),
}
CARD_SYSTEMS = tuple(
    hybrid_spec(f"{kind}_{rep}", rep, retrievers)
    for kind, retrievers in CARD_RETRIEVERS.items()
    for rep in ("template", "summary")
)

# Each chunk and claim retriever alone (plan R126-R128), with the nodes its chunks concern or its claims join
# as seeds, so all four techniques are scored on evidence and on start nodes beside the card systems above.
# One list each, fixed here like the card systems'; the cards they name are never searched.
SINGLE_SYSTEMS = tuple(
    hybrid_spec(name, "template", (name,), node_seeds=True)
    for name in ("chunk_dense", "chunk_lexical", "claim_dense", "claim_lexical")
)

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
        # the graph system's retrieval route alone (R117): its chunks read directly, no plan
        reading("graph_retrieval", _graph_retrieval, _graph_retrieval_params),
        # hybrid retrieval over the deterministic node cards (R120b). Sealed by R121 without a card retriever,
        # it ranks the same chunks over either representation, so no hybrid over the summaries is registered
        hybrid_spec("hybrid", "template"),
        *CARD_SYSTEMS,
        *SINGLE_SYSTEMS,
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


# what `kg qa` asks without --system: the systems before R117, so registering a system never adds a paid run
DEFAULT_SYSTEMS = ("graph", "vector", "records_vector")


def check_system(name: str) -> SystemSpec:
    """The spec of the system `name`. Raises `ConfigurationError` for a name no spec has, before anything
    runs."""
    if name not in SYSTEMS:
        raise ConfigurationError(f"unknown system '{name}'; choose from {', '.join(SYSTEMS)}")
    return SYSTEMS[name]


def check_source(name: str) -> SourceBuilder:
    """The chunk source of the system `name`. Raises `ConfigurationError` for an unknown name and for a plan
    system, which chooses chunks per plan step and has no single ranked list to score."""
    source = check_system(name).source
    if source is None:
        ranked = ", ".join(n for n, spec in SYSTEMS.items() if spec.source is not None)
        raise ConfigurationError(
            f"system '{name}' answers by query plans and has no chunk source; choose from {ranked}"
        )
    return source


def source_params(settings: Settings, name: str) -> dict[str, object]:
    """What decides a source's ranking: the embedding model and the settings the source reads."""
    check_source(name)
    return {"system": name, "embed_model": settings.embed_model, **SYSTEMS[name].params(settings)}


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


def source_parts(ctx: PipelineContext, state: PipelineState) -> SourceParts:
    """The parts a chunk source needs over the current graph. Raises `LLMUnavailableError` without an
    embedder."""
    s = ctx.settings
    plan = state.load_plan(ctx, required=False)
    if ctx.embedder is None:
        raise LLMUnavailableError("question answering needs an embedding model: set GEMINI_API_KEY")
    store = Neo4jGraphStore(ctx.driver, plan, s.qa_hops, s.qa_cypher_timeout_s)
    units = Neo4jUnitStore(ctx.driver)
    return SourceParts(settings=s, store=store, embedder=ctx.embedder, plan=plan, units=units)


def qa_parts(
    ctx: PipelineContext, state: PipelineState, frozen: Mapping[str, FrozenQuery] | None = None
) -> QAParts:
    """The shared parts over the current graph, with the settings' model, thinking level, reader and k.
    Raises `LLMUnavailableError` without a model or an embedder."""
    s = ctx.settings
    llm = with_thinking(ctx.require_llm(), s.qa_thinking)
    src = source_parts(ctx, state)
    return QAParts(
        settings=s,
        store=src.store,
        embedder=src.embedder,
        plan=src.plan,
        units=src.units,
        llm=llm,
        reader=Reader(llm, s.qa_model, s.llm_temperature),
        frozen=frozen,
    )


def build_system(
    ctx: PipelineContext, state: PipelineState, name: str, frozen: Mapping[str, FrozenQuery] | None = None
) -> QASystem:
    """The system `name` over the current graph; a plan system replays `frozen` (R80) when given."""
    return check_system(name).build(qa_parts(ctx, state, frozen))
