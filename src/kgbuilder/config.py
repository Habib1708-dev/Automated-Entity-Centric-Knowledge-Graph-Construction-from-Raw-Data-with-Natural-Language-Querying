"""All tunable settings, read from the environment or `.env` (pydantic-settings).

Role in the pipeline: the single source of models, connection details, thresholds and paths.
Every field that influences a result must also be logged as an MLflow param by the stage that uses it.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    schema_model: str = "gemini-2.5-pro"
    extract_model: str = "gemini-2.5-flash"
    embed_model: str = "gemini-embedding-001"
    llm_temperature: float = 0.0  # 0 keeps runs comparable and the disk cache meaningful
    llm_max_attempts: int = 3  # retries per call on API errors or unparsable replies
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
    domain_link_threshold: float = 90.0
