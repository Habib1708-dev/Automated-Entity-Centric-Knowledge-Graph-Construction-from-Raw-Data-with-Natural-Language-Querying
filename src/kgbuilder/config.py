"""All tunable settings, read from the environment, a preset in `presets.yaml`, or `.env` (pydantic-settings).

Role in the pipeline: the single source of models, connection details, thresholds and paths.
Every field that influences a result must also be logged as an MLflow param by the stage that uses it.
Design: a preset is one more settings source, placed between the process environment and `.env`, so it
replaces the model lines of `.env` but a variable set in the terminal still wins for a single run.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .core.errors import ConfigurationError
from .llm.base import ThinkingLevel
from .resolution.blocking import BlockingMode
from .tracking.base import ModelPrice

PRESETS_FILE = Path("presets.yaml")  # relative to the working directory, like `.env`
PRICES_FILE = Path("prices.yaml")  # model prices for the cost_usd metric

# Keys a preset may hold that are about the preset, not settings: its description, whether a run with it
# needs the user's permission first (read by .claude/hooks/run_guard.py), and how its data_dir is made
# from the full dataset (read by `kg sample`). The pipeline never sees them.
_PRESET_META_KEYS = {"description", "ask_permission", "sample"}
# Settings a preset must never hold: presets.yaml is committed, and a database URL can carry a password.
_PRESET_FORBIDDEN_KEYS = {"gemini_api_key", "gemini_free_api_key", "postgres_url"}


def load_preset(path: Path, name: str, allowed: set[str]) -> dict[str, Any]:
    """The settings of preset `name` in the YAML file `path`, without its meta keys (description, permission).

    Raises `ConfigurationError` when the file or the preset is missing, or a key is not in `allowed`
    (a typo would otherwise be ignored silently and the run would use a different model than intended).
    """
    if not path.exists():
        raise ConfigurationError(f"preset '{name}' requested but {path} does not exist")
    presets = read_presets(path)
    if name not in presets:
        raise ConfigurationError(f"unknown preset '{name}'; {path} defines: {', '.join(presets)}")
    values = {k: v for k, v in presets[name].items() if k not in _PRESET_META_KEYS}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigurationError(f"preset '{name}' has unknown or forbidden keys: {', '.join(unknown)}")
    return values


def read_presets(path: Path) -> dict[str, dict[str, Any]]:
    """Every preset in the YAML file `path` with all its keys; `ConfigurationError` if the file is missing."""
    if not path.exists():
        raise ConfigurationError(f"{path} does not exist")
    return {
        name: values or {}
        for name, values in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items()
    }


def read_prices(path: Path) -> dict[str, ModelPrice] | None:
    """The `models` table of prices.yaml; None when the file is missing (runs then log no cost).

    Raises `ConfigurationError` for a malformed file: a typo must not silently change reported costs.
    """
    if not path.exists():
        return None
    try:
        models = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models") or {}
        return {name: ModelPrice.model_validate(price) for name, price in models.items()}
    except (yaml.YAMLError, AttributeError, ValidationError) as e:
        raise ConfigurationError(f"{path} is malformed: {e}") from e


class PresetSettingsSource(PydanticBaseSettingsSource):
    """Settings source for the preset named by `kg_preset` (constructor argument, environment or `.env`)."""

    def __init__(self, settings_cls: type[BaseSettings], *name_sources: PydanticBaseSettingsSource):
        super().__init__(settings_cls)
        self._name_sources = name_sources

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # not used: __call__ returns the whole preset at once
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        # the first source that names a preset wins, in the same priority order as the settings themselves
        name = next((n for s in self._name_sources if (n := s().get("kg_preset"))), None)
        if not name:
            return {}
        allowed = set(self.settings_cls.model_fields) - _PRESET_FORBIDDEN_KEYS - {"kg_preset"}
        return load_preset(PRESETS_FILE, name, allowed)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # name of a preset in presets.yaml (smoke, dev, quality); empty = settings from .env and defaults only
    kg_preset: str = ""
    # the dataset a command reads when none is given on the command line; the smoke and dev presets point
    # at the small subsets in samples/, so a cheap run cannot read the whole dataset by accident
    data_dir: Path = Path("data")

    # "ollama" runs a local model for free smoke runs that check the code, not the method's quality;
    # SCHEMA_MODEL / EXTRACT_MODEL / EMBED_MODEL then name Ollama models (see README). "deepseek" (R69)
    # generates with DeepSeek and embeds with Gemini (DeepSeek has no embedding model), so it needs both keys
    llm_provider: Literal["gemini", "ollama", "deepseek"] = "gemini"
    deepseek_api_key: str = ""
    deepseek_url: str = "https://api.deepseek.com"
    gemini_api_key: str = ""
    # a key from a Google project without billing: its requests are free but capped per day, and Google
    # may use them to improve its products (acceptable for the synthetic data in samples/)
    gemini_free_api_key: str = ""
    # which of the two keys Gemini requests use; the smoke preset chooses "free"
    gemini_key: Literal["paid", "free"] = "paid"
    ollama_url: str = "http://localhost:11434"
    # context window per Ollama request, in tokens: must hold the longest prompt (the plan prompt is
    # about 8,000) plus the answer, or Ollama cuts the prompt without an error
    ollama_num_ctx: int = 16384
    # Gemini 2.5 is closed to new API keys (404 "no longer available to new users") since 2026-09.
    # Flash for schema work too, to keep development runs cheap (~4x cheaper than Pro per token); set
    # SCHEMA_MODEL=gemini-3.1-pro-preview for runs whose results are reported.
    schema_model: str = "gemini-3.8-flash"
    extract_model: str = "gemini-3.8-flash"
    embed_model: str = "gemini-embedding-001"
    # thinking level per role ("" = the model's default, which for Gemini 3 Flash is long and billed as
    # output): schema work (plan, text schema) is one hard task per stage, extraction and adjudication
    # are many small ones, so they get less. Flash-Lite ignores it in practice: it hardly thinks.
    schema_thinking: ThinkingLevel = ""
    extract_thinking: ThinkingLevel = ""
    llm_temperature: float = 0.0  # 0 keeps runs comparable and the disk cache meaningful
    llm_max_attempts: int = 3  # retries per call on API errors or unparsable replies
    # per request, in seconds: a stalled request becomes a failed, retried attempt instead of a hang (a
    # preview model once kept a run waiting for an hour). Generous, because a local model with 8 queued
    # extraction requests can take minutes to answer the last one.
    llm_timeout_s: float = 300.0
    extract_workers: int = 8  # parallel per-chunk extraction calls
    # extraction calls per chunk (R61): 1 = one pass; 2 = a second pass that shows the accepted facts and
    # asks for the claims they miss. Each pass costs about as much as the first.
    extract_passes: int = Field(default=1, ge=1, le=3)

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password123"

    # PostgreSQL as the source of the tables (R114): with a schema set, `kg profile` stages that schema's
    # tables and views instead of the data dir's CSV and JSON files; documents still come from the data dir.
    # Empty = the files. The default URL is the `postgres` service of docker-compose.yml.
    postgres_url: str = "postgresql://kgbuilder:password123@localhost:5434/kgbuilder"
    postgres_schema: str = ""

    cache_dir: Path = Path(".cache/llm")

    mlflow_tracking_uri: str = "sqlite:///mlflow.db"
    mlflow_experiment: str = "kgbuilder"

    chunk_max_chars: int = 1500
    chunk_min_chars: int = 200
    chunk_overlap_chars: int = 0  # carried over between cuts of one oversized section; 0 = off
    # text the schema proposer sees whole (R55): every chunk up to this many characters (about 50,000 tokens),
    # else an even sample that fits; both corpora of the thesis (35,000 and 11,500 characters) go in whole
    schema_context_chars: int = 200_000
    er_auto_merge: float = 92.0  # rapidfuzz token_sort_ratio at or above: merge without asking
    er_borderline: float = 80.0  # between this and auto_merge: ask the LLM (if available)
    # which pairs close in meaning are also sent to the LLM (resolution/blocking.py): "off", "threshold"
    # (cosine*100 at or above er_embedding_candidates; that scale depends on the embedding model and the
    # data, so it is chosen per dataset) or "mutual_nearest" (each name among the other's er_neighbours
    # most similar names of its type: rank-based, no scale to choose)
    er_embedding_blocking: BlockingMode = "off"
    er_embedding_candidates: float = 0.0
    er_neighbours: int = Field(default=2, ge=1)
    domain_link_threshold: float = 90.0
    gold_min_recall: float = 0.5  # `kg validate --gold` fails below this triple recall

    # Question answering (`kg ask`, `kg qa`; layered-model Step 2, R71). The model reads the chosen chunks
    # and answers with citations; its thinking level is billed as output like any other.
    qa_model: str = "gemini-3.8-flash"
    qa_thinking: ThinkingLevel = ""
    # chunks the reader is given, by the graph arm and the vector-only baseline alike, so the two systems
    # differ only in how they choose the chunks
    qa_top_k: int = Field(default=5, ge=1)
    # relationship hops from a thing to related records: one reaches a vehicle's recalls, two a product's
    # parts through its assemblies; more would reach most of a small domain graph
    qa_hops: int = Field(default=2, ge=1, le=4)
    # a word span of the question links to a node whose name or alias it spells alike at or above this
    # (the token-sort score of entity resolution and domain linking)
    qa_link_fuzzy: float = 90.0
    # the nodes whose names lie nearest the question in meaning are linked too; rank-based, like the
    # mutual-nearest ER blocking, so no similarity scale has to be chosen per embedding model
    qa_link_neighbours: int = Field(default=3, ge=0)
    qa_workers: int = Field(default=8, ge=1)  # questions answered in parallel
    # the exact route's Cypher (R71 part b): rows it may return (a LIMIT is added when the query has none,
    # a larger one is refused) and seconds it may run before the database cancels it
    qa_cypher_limit: int = Field(default=100, ge=1)
    qa_cypher_timeout_s: float = Field(default=10.0, gt=0)
    # query plans (R74): items one step may produce (a bound on every step), candidates read_check may read
    # one by one (more must be narrowed by the plan first: each costs a model call), and the chunks of one
    # candidate it is shown, nearest the statement first
    qa_step_cap: int = Field(default=200, ge=1)
    qa_check_limit: int = Field(default=30, ge=1)
    qa_check_chunks: int = Field(default=3, ge=1)
    # the retrieval benchmark (R117, `kg retrieve-eval`): the chunk and seed budgets K of Evidence and Seed
    # Recall@K. R117 fixed 5 (the reader's k) and 10 (what a larger k would add); the plan change after R121
    # adds 1, 3 and 20, the K of the A-against-B headline (R121 computed them offline). A source ranks at
    # least the largest, so no budget is cut short
    retrieval_budgets: list[int] = Field(default=[1, 3, 5, 10, 20], min_length=1)
    # node evidence and cards (R118, plan R116-R125): the names a card shows per relation and as aliases (a
    # hub keeps its count, "+k more"), the claims per node (the best supported; this is the evidence every
    # representation gets, so A and B read the same), and a card's length in characters, well inside the
    # 2,048 input tokens the embedding model reads
    index_card_names: int = Field(default=5, ge=1)
    index_card_claims: int = Field(default=3, ge=0)
    index_card_max_chars: int = Field(default=1500, ge=100)
    # LLM node summaries (R123, representation B, `kg index --cards summary`): the model that writes them (the
    # user's choice, 2026-10-08: the cheapest Gemini; the builder is gemini-3.8-flash), its thinking level (""
    # = the model's default: Flash-Lite hardly thinks) and a summary's length cap in characters, R124's knob.
    # The temperature is `llm_temperature`. Each is part of the summaries' version: a change re-writes them
    index_summary_model: str = "gemini-3.5-flash-lite"
    index_summary_thinking: ThinkingLevel = ""
    index_summary_max_chars: int = Field(default=600, ge=100)
    # hybrid retrieval (R120b, plan R116-R125): the retrievers fused, the constant k of reciprocal rank fusion
    # and how many chunks each list and the fused list hold. Sealed by R121 (tests/gold/r121/tuning.json):
    # chunks and claim sentences, each by vector and by words, with k 10 completed the most furniture
    # questions at 5 chunks (27 of 33) of the pre-registered grid; held-out and generality runs take these
    # values unchanged, so change them only through a new tuning step on furniture
    hybrid_retrievers: list[str] = ["chunk_dense", "chunk_lexical", "claim_dense", "claim_lexical"]
    hybrid_rrf_k: int = Field(default=10, ge=0)
    hybrid_depth: int = Field(default=20, ge=1)
    # the anchor-graph evaluation (R90): the chunk budgets of evidence reach (C5), fixed at 5 and 10 by the
    # direction before measuring, and the share of the corpus above which a node a lookup returns is
    # listed as a hub (C7): a start that leads to a fifth of the corpus no longer narrows anything
    anchor_budgets: list[int] = [5, 10]
    anchor_hub_share: float = Field(default=0.2, gt=0, le=1)
    # The judged hard rules (direction section 7.2, R93), fixed before judging: a text mention's record link
    # must be right in at least this share (C4), and of the chunks W2 reaches from a record or an individual
    # at least this share must concern it (C6). Below them the graph leads a reader to the wrong evidence.
    anchor_min_link_precision: float = Field(default=0.95, gt=0, le=1)
    anchor_min_purity: float = Field(default=0.95, gt=0, le=1)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Earlier sources win: arguments > environment > preset > .env > defaults."""
        preset = PresetSettingsSource(settings_cls, init_settings, env_settings, dotenv_settings)
        return init_settings, env_settings, preset, dotenv_settings, file_secret_settings
