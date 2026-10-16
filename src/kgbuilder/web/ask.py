"""Asking one question from the web interface (R222): `kg ask`'s stage, streamed.

Role in the pipeline: the web app's ask route calls `AskService.ask` in a worker thread; it runs the same
`AskStage` as `kg ask`, so the question gets its MLflow run, the security screen and the agent exactly as
the benchmark ran them, and emits the events of events.py as they happen.
Design: the concrete pipeline context comes from an injected `ContextFactory` (the composition root,
`kg serve`, opens the Neo4j driver, the LLM client and the tracker with the stream's listener attached), so
this module opens nothing itself. Each dataset is asked from its own working folder `out/web/<id>`, which
holds the build's plan, text schema, profile and staged tables, as the cached reload recipe lays them out
(tests/gold/r219/runs.json): the agent reads its tables and plan there, and `ask.json` is written there,
never into the build folder. A question goes only to the dataset whose digest the loaded graph has: asked
on another, the agent would answer from the wrong graph.
Must not: load or change a graph (R222b), or answer anything itself.
"""

import shutil
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from ..core.errors import ConfigurationError, KgBuilderError
from ..graph.digest import graph_digest
from ..llm.base import CallListener
from ..pipeline import PipelineContext, PipelineState, run_stages
from ..pipeline.qa_stages import AskStage
from ..tracking.base import ModelPrice
from .datasets import Dataset
from .events import AnswerEvent, Emit, ErrorEvent, StreamListener, StreamObserver

# A context for one dataset's preset over a working folder, every LLM call also reported to the listener.
ContextFactory = Callable[[str, Path, CallListener | None], AbstractContextManager[PipelineContext]]

# what the agent and the stage read from the folder they run in, copied from the build as the reload recipe
# copies them
_BUILD_FILES = ("plan.json", "text_schema.json", "profile.json")
_STAGING = "staging"


def _digest(driver: Driver) -> str:
    return graph_digest(driver).value


class AskService:
    """Asks a dataset's loaded graph one question with an agent variant, emitting events; one question at a
    time is the caller's rule (the graph and the cache are shared). `read_digest` reads the loaded graph's
    digest (graph/digest.py; injected so a test needs no Neo4j)."""

    def __init__(
        self, contexts: ContextFactory, prices: dict[str, ModelPrice] | None, folders: Path,
        read_digest: Callable[[Driver], str] = _digest,
    ):  # fmt: skip
        self._contexts = contexts
        self._prices = prices
        self._folders = folders
        self._read_digest = read_digest

    def loaded_digest(self, any_dataset: Dataset) -> str | None:
        """The digest of the graph Neo4j holds now, or None when Neo4j cannot be reached (logged by the
        driver; the page then says the graph is unavailable). Any dataset's preset serves: they share one
        Neo4j; the context's folder is the folders' root, since nothing is read or written there."""
        with self._contexts(any_dataset.preset, self._folders, None) as ctx:
            try:
                return self._read_digest(ctx.driver)
            except (DriverError, Neo4jError, OSError):
                return None

    def folder(self, dataset: Dataset) -> Path:
        """The dataset's working folder, made from its build on first use."""
        folder = self._folders / dataset.id
        for name in _BUILD_FILES:
            if not (folder / name).exists():
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dataset.build / name, folder / name)
        if not (folder / _STAGING).exists():
            shutil.copytree(dataset.build / _STAGING, folder / _STAGING)
        return folder

    def ask(self, dataset: Dataset, question: str, variant: str, emit: Emit) -> None:
        """Ask `question` with the agent variant `variant` and emit every event, the answer last; an expected
        failure (no graph of this dataset loaded, a missing key, a model that keeps failing) is emitted as
        an error event, never raised."""
        listener = StreamListener(emit, self._prices)
        started = time.perf_counter()
        try:
            with self._contexts(dataset.preset, self.folder(dataset), listener) as ctx:
                self._check_loaded(ctx, dataset)
                stage = AskStage(variant, StreamObserver(emit))
                answer = run_stages(ctx, PipelineState(question=question), [stage]).answer
        except KgBuilderError as e:
            emit(ErrorEvent(message=str(e)))
            return
        seconds = round(time.perf_counter() - started, 2)
        emit(AnswerEvent(answer=answer, seconds=seconds, cost_usd=listener.cost_usd()))

    def _check_loaded(self, ctx: PipelineContext, dataset: Dataset) -> None:
        try:
            loaded = self._read_digest(ctx.driver)
        except (DriverError, Neo4jError, OSError) as e:
            raise ConfigurationError(f"Neo4j cannot be reached ({e}): start it (docker compose up -d)") from e
        if loaded != dataset.digest:
            raise ConfigurationError(
                f"the graph loaded in Neo4j (digest {loaded}) is not {dataset.name}'s ({dataset.digest}): "
                "load this dataset first"
            )
