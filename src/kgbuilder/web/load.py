"""Loading a dataset's graph into Neo4j from the web interface (R222b): the cached reload recipe, streamed.

Role in the pipeline: the web app's load route runs `GraphLoader.load` in a worker thread. It rebuilds the
graph of a finished build into the working Neo4j with the recipe every earlier reload used
(tests/gold/r178/runs.json, reused by R219), command by command, as the real `kg` commands, so the page loads
exactly what the benchmark asked.
Recipe, in the dataset's working folder `out/web/<id>` (the folder ask.py asks from), under its preset:
copy plan.json, text_schema.json, profile.json and staging/ from the build; `reset`; `build DATA`; copy the
pinned profile back (build profiles again); `ingest-text DATA`; `extract --from-build BUILD` (the build's
triples, no model); `link`; `mention-pass` with invalid Gemini keys (its findings come from the cache, and a
miss must fail rather than pay); `resolve`; `attach`; `index --cards template`. Then the loaded graph's digest
is read and compared with the dataset's.
Cost: no generation call is paid (every answer is in the cache); `ingest-text` and `index` send live embedding
requests (embeddings are not cached), which Gemini reports without token usage, a fraction of a cent.
Design: the command runner and the digest reader are injected (cli.py builds the real ones), so the recipe is
tested without Neo4j or a subprocess.
Must not: change a build folder; run while a question is answered (the app holds one lock for both).
"""

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .datasets import Dataset

# A runner takes the `kg` arguments after the program name and the environment overrides, and returns the
# exit code and the output's last lines.
Runner = Callable[[list[str], dict[str, str]], tuple[int, str]]

# invalid on purpose (see the module docstring): only the mention pass gets them
_NO_KEYS = {"GEMINI_API_KEY": "invalid", "GEMINI_FREE_API_KEY": "invalid"}
_COPIED = ("plan.json", "text_schema.json", "profile.json")


class LoadStep(BaseModel):
    """One command of the recipe as the page lists it."""

    name: str  # the `kg` command
    args: list[str]  # its arguments after the preset, `--out` included
    env: dict[str, str] = {}
    profile_after: bool = False  # copy the build's profile back once it ran (`build` profiles again)


class StepEvent(BaseModel):
    """A recipe step started, finished, or failed (`output`: the end of its output)."""

    event: Literal["step"] = "step"
    index: int
    total: int
    name: str
    status: Literal["running", "done", "failed"]
    seconds: float = 0.0
    output: str = ""


class LoadedEvent(BaseModel):
    """The load ended: whether every step ran and the graph's digest is the dataset's."""

    event: Literal["loaded"] = "loaded"
    ok: bool
    digest: str | None
    expected: str
    seconds: float
    message: str


LoadEvent = Annotated[StepEvent | LoadedEvent, Field(discriminator="event")]


def recipe(dataset: Dataset, data_dir: Path, folder: Path) -> list[LoadStep]:
    """The reload recipe for `dataset`, whose data is `data_dir`, into the working folder `folder`."""
    out = ["--out", str(folder)]
    build = str(dataset.build)
    return [
        LoadStep(name="reset", args=["reset", *out]),
        LoadStep(name="build", args=["build", str(data_dir), *out], profile_after=True),
        LoadStep(name="ingest-text", args=["ingest-text", str(data_dir), *out]),
        LoadStep(name="extract", args=["extract", "--from-build", build, *out]),
        LoadStep(name="link", args=["link", *out]),
        LoadStep(name="mention-pass", args=["mention-pass", *out], env=_NO_KEYS),
        LoadStep(name="resolve", args=["resolve", *out]),
        LoadStep(name="attach", args=["attach", *out]),
        LoadStep(name="index", args=["index", "--cards", "template", *out]),
    ]


class GraphLoader:
    """Runs the recipe for one dataset and emits its progress; `data_dir` gives a dataset's data folder (its
    preset's), `read_digest` the loaded graph's digest (None: Neo4j unreachable)."""

    def __init__(
        self, run: Runner, data_dir: Callable[[Dataset], Path], read_digest: Callable[[Dataset], str | None],
        folders: Path,
    ):  # fmt: skip
        self._run = run
        self._data_dir = data_dir
        self._read_digest = read_digest
        self._folders = folders

    def load(self, dataset: Dataset, emit: Callable[[LoadEvent], None]) -> None:
        """Load `dataset`'s graph, emitting a running and a done (or failed) event per step, then the end. A
        failed step stops the recipe: the graph is then partly built, and the end event says so."""
        started = time.perf_counter()
        folder = self._folders / dataset.id
        self._lay_out(dataset, folder)
        steps = recipe(dataset, self._data_dir(dataset), folder)
        for i, step in enumerate(steps, start=1):
            emit(StepEvent(index=i, total=len(steps), name=step.name, status="running"))
            began = time.perf_counter()
            code, output = self._run(["--preset", dataset.preset, *step.args], step.env)
            took = round(time.perf_counter() - began, 1)
            if code != 0:
                emit(StepEvent(index=i, total=len(steps), name=step.name, status="failed", seconds=took,
                               output=output))  # fmt: skip
                emit(self._end(dataset, started, f"`kg {step.name}` failed: Neo4j now holds a partial graph"))
                return
            if step.profile_after:
                shutil.copy2(dataset.build / "profile.json", folder / "profile.json")
            emit(StepEvent(index=i, total=len(steps), name=step.name, status="done", seconds=took))
        emit(self._end(dataset, started, ""))

    def _lay_out(self, dataset: Dataset, folder: Path) -> None:
        """The working folder as the recipe starts it: the build's plan, schema, profile and staged tables."""
        folder.mkdir(parents=True, exist_ok=True)
        for name in _COPIED:
            shutil.copy2(dataset.build / name, folder / name)
        shutil.rmtree(folder / "staging", ignore_errors=True)
        shutil.copytree(dataset.build / "staging", folder / "staging")

    def _end(self, dataset: Dataset, started: float, failure: str) -> LoadedEvent:
        digest = self._read_digest(dataset)
        ok = not failure and digest == dataset.digest
        if failure:
            message = failure
        elif digest is None:
            message = "Neo4j could not be reached after the load"
        elif digest != dataset.digest:
            message = f"the loaded graph's digest is {digest}, not {dataset.name}'s {dataset.digest}"
        else:
            message = f"{dataset.name} is loaded"
        return LoadedEvent(ok=ok, digest=digest, expected=dataset.digest,
                           seconds=round(time.perf_counter() - started, 1), message=message)  # fmt: skip
