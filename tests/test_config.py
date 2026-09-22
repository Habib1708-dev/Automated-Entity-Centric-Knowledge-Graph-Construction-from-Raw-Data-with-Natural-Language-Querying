"""Settings and model presets: loading presets.yaml, rejecting typos, and the priority of the sources
(environment > preset > .env > defaults). No Neo4j, no network."""

from pathlib import Path

import pytest

from kgbuilder.config import Settings, load_preset
from kgbuilder.core.errors import ConfigurationError

REPO_PRESETS = Path(__file__).resolve().parent.parent / "presets.yaml"
SETTING_NAMES = set(Settings.model_fields) - {"gemini_api_key", "kg_preset"}


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_preset_is_its_settings_without_the_description(tmp_path):
    presets = write(tmp_path / "p.yaml", "dev:\n  description: cheap\n  extract_model: lite\n")
    assert load_preset(presets, "dev", SETTING_NAMES) == {"extract_model": "lite"}


def test_unknown_presets_and_misspelled_keys_are_errors_not_silently_ignored(tmp_path):
    presets = write(tmp_path / "p.yaml", "dev:\n  extract_modle: lite\n")
    with pytest.raises(ConfigurationError, match="unknown preset 'prod'.*defines: dev"):
        load_preset(presets, "prod", SETTING_NAMES)
    with pytest.raises(ConfigurationError, match="unknown or forbidden keys: extract_modle"):
        load_preset(presets, "dev", SETTING_NAMES)


def test_the_api_key_is_not_allowed_in_a_preset(tmp_path):
    presets = write(tmp_path / "p.yaml", "dev:\n  gemini_api_key: secret\n")
    with pytest.raises(ConfigurationError, match="gemini_api_key"):
        load_preset(presets, "dev", SETTING_NAMES)


@pytest.mark.parametrize("name", ["smoke", "dev", "quality"])
def test_every_committed_preset_is_valid(name):
    values = load_preset(REPO_PRESETS, name, SETTING_NAMES)
    assert {"llm_provider", "schema_model", "extract_model", "mlflow_experiment"} <= set(values)


def test_environment_beats_preset_beats_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # presets.yaml and .env are read from the working directory
    for name in ("KG_PRESET", "SCHEMA_MODEL", "EXTRACT_MODEL", "EMBED_MODEL"):
        monkeypatch.delenv(name, raising=False)
    write(tmp_path / "presets.yaml", "dev:\n  schema_model: from-preset\n  extract_model: from-preset\n")
    write(tmp_path / ".env", "KG_PRESET=dev\nEXTRACT_MODEL=from-dotenv\nEMBED_MODEL=from-dotenv\n")
    monkeypatch.setenv("SCHEMA_MODEL", "from-environment")

    settings = Settings()
    assert settings.schema_model == "from-environment"  # a terminal variable wins for one run
    assert settings.extract_model == "from-preset"  # the preset replaces the model lines of .env
    assert settings.embed_model == "from-dotenv"  # what the preset leaves out still comes from .env
