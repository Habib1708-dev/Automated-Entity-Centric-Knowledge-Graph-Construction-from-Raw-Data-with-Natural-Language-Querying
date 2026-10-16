"""The web interface's HTTP API (R222): the dataset tabs, their graphs, the passages, and the streamed ask.

Role in the pipeline: `kg serve` (cli.py, the composition root) builds the concrete parts and passes them to
`create_app`; the page (R223) calls these routes. Every route names a dataset by its id from datasets.yaml.
Design: thin routes over injected parts: the datasets, a function giving a dataset's `KnowledgeGraph`
(cached here: it is rebuilt offline from the build, under a second, but the page asks it often), and an
`Asker` (ask.py's service, or a fake in tests). A question streams as server-sent events (`text/event-
stream`, one JSON event per `data:` line): the asker runs in a worker thread and emits into a queue the
response drains, so the page shows each step as it happens. One question at a time: the graph, the LLM cache
and the agent's tables are shared, and the reload of R222b will need the graph alone.
Must not: open Neo4j, read settings, or build any concrete adapter (cli.py does); answer anything itself.
"""

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .datasets import Dataset
from .events import Emit, ErrorEvent, StreamEvent
from .knowledge import ChunkView, KnowledgeGraph, SchemaView, Subgraph, Thing, ThingDetail

log = logging.getLogger(__name__)

# How much one view may hold: a drawn graph past ~150 nodes is unreadable on a laptop screen, and a thing's
# fact list past a few hundred is not read but scrolled.
MAX_THINGS = 150
MAX_FACTS = 300


class Asker(Protocol):
    """What the routes need of the ask service (ask.py's `AskService`)."""

    def loaded_digest(self, any_dataset: Dataset) -> str | None: ...

    def ask(self, dataset: Dataset, question: str, variant: str, emit: Emit) -> None: ...


class DatasetInfo(BaseModel):
    id: str
    name: str
    preset: str
    loaded: bool  # this dataset's graph is the one in Neo4j: questions can go to it


class DatasetsView(BaseModel):
    """The tabs, whether Neo4j answered, and the id of the dataset it holds (None: another graph, or none)."""

    datasets: list[DatasetInfo]
    neo4j: bool
    loaded: str | None
    variants: list[str]  # the agent variants a question may be asked with, the default first


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    variant: str


def create_app(
    datasets: list[Dataset], graphs: Callable[[Dataset], KnowledgeGraph], asker: Asker, variants: list[str]
) -> FastAPI:
    """The API over `datasets`; `graphs` gives a dataset's graph (called once per dataset), `asker` answers,
    `variants` are the agent variants offered, the default first."""
    app = FastAPI(title="kgbuilder")
    by_id = {d.id: d for d in datasets}
    built: dict[str, KnowledgeGraph] = {}
    building = threading.Lock()  # two first requests for one tab must not rebuild it twice
    asking = threading.Lock()

    def dataset(dataset_id: str) -> Dataset:
        found = by_id.get(dataset_id)
        if found is None:
            raise HTTPException(404, f"no dataset {dataset_id!r}")
        return found

    def graph(dataset_id: str) -> KnowledgeGraph:
        chosen = dataset(dataset_id)
        with building:
            if chosen.id not in built:
                built[chosen.id] = graphs(chosen)
            return built[chosen.id]

    @app.get("/api/datasets")
    def list_datasets() -> DatasetsView:
        digest = asker.loaded_digest(datasets[0])
        loaded = next((d.id for d in datasets if d.digest == digest), None)
        infos = [DatasetInfo(id=d.id, name=d.name, preset=d.preset, loaded=d.id == loaded) for d in datasets]
        return DatasetsView(datasets=infos, neo4j=digest is not None, loaded=loaded, variants=variants)

    @app.get("/api/datasets/{dataset_id}/schema")
    def schema(dataset_id: str) -> SchemaView:
        return graph(dataset_id).schema()

    @app.get("/api/datasets/{dataset_id}/graph")
    def overview(dataset_id: str, limit: int = Query(60, ge=1, le=MAX_THINGS)) -> Subgraph:
        return graph(dataset_id).overview(limit)

    @app.get("/api/datasets/{dataset_id}/neighbourhood")
    def neighbourhood(
        dataset_id: str, thing: str, limit: int = Query(40, ge=1, le=MAX_THINGS)
    ) -> Subgraph:  # fmt: skip
        found = graph(dataset_id).neighbourhood(thing, limit)
        if found is None:
            raise HTTPException(404, f"no thing {thing!r}")
        return found

    @app.get("/api/datasets/{dataset_id}/search")
    def search(dataset_id: str, q: str, limit: int = Query(20, ge=1, le=MAX_THINGS)) -> list[Thing]:
        return graph(dataset_id).search(q, limit)

    @app.get("/api/datasets/{dataset_id}/thing")
    def thing(dataset_id: str, id: str, limit: int = Query(50, ge=1, le=MAX_FACTS)) -> ThingDetail:
        found = graph(dataset_id).detail(id, limit)
        if found is None:
            raise HTTPException(404, f"no thing {id!r}")
        return found

    @app.get("/api/datasets/{dataset_id}/chunk")
    def chunk(dataset_id: str, id: str) -> ChunkView:
        found = graph(dataset_id).chunk(id)
        if found is None:
            raise HTTPException(404, f"no passage {id!r}")
        return found

    @app.post("/api/datasets/{dataset_id}/ask")
    def ask(dataset_id: str, request: AskRequest) -> StreamingResponse:
        chosen = dataset(dataset_id)
        if request.variant not in variants:
            raise HTTPException(422, f"variant must be one of {', '.join(variants)}")
        if not asking.acquire(blocking=False):
            raise HTTPException(409, "another question is being answered; ask again when it is done")
        events: queue.Queue[StreamEvent | None] = queue.Queue()

        def work() -> None:
            try:
                asker.ask(chosen, request.question, request.variant, events.put)
            # The asker emits expected failures itself; anything else is a bug, but the page must still hear
            # that the question ended, or its stream would wait forever. Logged with its traceback.
            except Exception as e:
                log.exception("asking %r failed", request.question)
                events.put(ErrorEvent(message=f"unexpected error: {e}"))
            finally:
                events.put(None)
                asking.release()

        threading.Thread(target=work, daemon=True).start()
        return StreamingResponse(_drain(events), media_type="text/event-stream")

    return app


def _drain(events: "queue.Queue[StreamEvent | None]") -> Iterator[str]:
    """Each event as one server-sent event, until the worker's end mark."""
    while (event := events.get()) is not None:
        yield f"data: {event.model_dump_json()}\n\n"
