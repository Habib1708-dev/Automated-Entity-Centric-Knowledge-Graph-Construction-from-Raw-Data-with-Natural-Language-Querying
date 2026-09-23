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
# Settings a preset must never hold: presets.yaml is committed.
_PRESET_FORBIDDEN_KEYS = {"gemini_api_key", "gemini_free_api_key"}


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
    # SCHEMA_MODEL / EXTRACT_MODEL / EMBED_MODEL then name Ollama models (see README)
    llm_provider: Literal["gemini", "ollama"] = "gemini"
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

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password123"

    cache_dir: Path = Path(".cache/llm")

    mlflow_tracking_uri: str = "sqlite:///mlflow.db"
    mlflow_experiment: str = "kgbuilder"

    chunk_max_chars: int = 1500
    chunk_min_chars: int = 200
    chunk_overlap_chars: int = 0  # carried over between cuts of one oversized section; 0 = off
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
