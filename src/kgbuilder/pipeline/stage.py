"""The stage abstraction: what every pipeline stage looks like to the runner.

Role in the pipeline: stages.py implements `Stage` nine times; runner.py executes them.
Design: Pipeline pattern. A stage declares its name, its MLflow params and whether it applies; the runner
opens the tracked run, so a stage cannot forget tracking. Stages communicate only through
`PipelineState`, and every piece of state that a human may review is also a file in `out/`, which is how
a stage run on its own (`kg build`) picks up where an earlier command (`kg plan`) stopped.
Dependencies (LLM, Neo4j, tracker, settings) arrive in `PipelineContext`, built by the composition root.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from neo4j import Driver

from ..config import Settings
from ..core.errors import LLMUnavailableError, MissingInputError
from ..llm.base import Embedder, LLMClient
from ..resolution.linking import LinkReport
from ..resolution.resolver import ResolvePreview, ResolveReport
from ..structured.plan import ConstructionPlan
from ..structured.profiler import DataProfile
from ..text.chunking import Chunk
from ..text.extraction import ExtractionResult
from ..text.lexical import read_chunks
from ..text.schema import TextSchema
from ..tracking.base import NullTracker, Run, Tracker
from ..validation.evaluate import EvalReport
from ..validation.report import ValidationReport

PLAN_FILE = "plan.json"
TEXT_SCHEMA_FILE = "text_schema.json"


@dataclass
class PipelineContext:
    """Everything a stage needs from the outside world. `llm`/`embedder` are None without an API key."""

    settings: Settings
    driver: Driver
    out: Path
    llm: LLMClient | None = None
    embedder: Embedder | None = None
    tracker: Tracker = field(default_factory=NullTracker)

    def require_llm(self) -> LLMClient:
        """Return the LLM client, or fail with a clear message for stages that cannot work without one."""
        if self.llm is None:
            raise LLMUnavailableError("this stage needs an LLM: set GEMINI_API_KEY (see .env.example)")
        return self.llm

    def write(self, name: str, text: str) -> Path:
        """Write an output file under `out/` and return its path (for `run.artifact`)."""
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


@dataclass
class PipelineState:
    """Inputs of the run and the results the stages have produced so far."""

    data_dir: Path | None = None
    goal: str | None = None
    gold: Path | None = None
    verdicts: Path | None = None  # the judge's verdict file for `kg eval --verdicts`
    embed: bool = True

    staged_dir: Path | None = None
    profile: DataProfile | None = None
    plan: ConstructionPlan | None = None
    expected_counts: dict[str, int] | None = None
    chunks: list[Chunk] | None = None
    text_schema: TextSchema | None = None

    extraction: ExtractionResult | None = None
    resolution: ResolveReport | None = None
    resolve_preview: ResolvePreview | None = None
    links: LinkReport | None = None
    validation: ValidationReport | None = None
    evaluation: EvalReport | None = None

    def need(self, attribute: str, produced_by: str):
        """The value of an input like `data_dir` or `goal`, or an error naming what is missing."""
        value = getattr(self, attribute)
        if value is None:
            raise MissingInputError(f"{attribute} is missing; {produced_by}")
        return value

    def load_plan(self, ctx: PipelineContext, required: bool = True) -> ConstructionPlan | None:
        """The plan from this run, else the reviewed `out/plan.json` of an earlier command."""
        path = ctx.out / PLAN_FILE
        if self.plan is None and path.exists():
            self.plan = ConstructionPlan.model_validate_json(path.read_text(encoding="utf-8"))
        if self.plan is None and required:
            raise MissingInputError(f"{path} not found; run `kg plan` first")
        return self.plan

    def load_text_schema(self, ctx: PipelineContext, required: bool = True) -> TextSchema | None:
        """The schema from this run, else the reviewed `out/text_schema.json`."""
        path = ctx.out / TEXT_SCHEMA_FILE
        if self.text_schema is None and path.exists():
            self.text_schema = TextSchema.model_validate_json(path.read_text(encoding="utf-8"))
        if self.text_schema is None and required:
            raise MissingInputError(f"{path} not found; run `kg text-schema` first")
        return self.text_schema

    def load_chunks(self, ctx: PipelineContext) -> list[Chunk]:
        """The chunks as ingested. Never re-chunked: chunk ids depend on the chunk settings, and ids that
        differ from the stored ones would silently break MENTIONS and fact provenance."""
        if self.chunks is None:
            self.chunks = read_chunks(ctx.driver)
        if not self.chunks:
            raise MissingInputError("no chunks in the graph; run `kg ingest-text` first")
        return self.chunks


class Stage(Protocol):
    """One step of the pipeline. Implementations are stateless; everything lives in context and state."""

    name: str  # the MLflow run name; part of the tracking contract, keep stable
    review_file: str | None  # output a human should review before the pipeline continues, if any

    def applies(self, state: PipelineState) -> bool:
        """False when the full pipeline should skip this stage (for example: no text, no plan)."""
        ...

    def params(self, ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
        """Everything that influences this stage's result; logged before the stage runs."""
        ...

    def run(self, ctx: PipelineContext, state: PipelineState, run: Run) -> None:
        """Do the work: read inputs from `state`, write results to `state` and `out/`, log to `run`."""
        ...

    def reload(self, ctx: PipelineContext, state: PipelineState) -> None:
        """After a human edited `review_file`: replace the in-memory result with the file's content."""
        ...
