"""Settings and model presets: loading presets.yaml, rejecting typos, the priority of the sources
(environment > preset > .env > defaults), and the committed data subsets the cheap presets read.
No Neo4j, no network."""

import csv
from pathlib import Path

import pytest

from kgbuilder.config import Settings, load_preset, read_presets, read_prices
from kgbuilder.core.errors import ConfigurationError

REPO = Path(__file__).resolve().parent.parent
REPO_PRESETS = REPO / "presets.yaml"
SETTING_NAMES = set(Settings.model_fields) - {"gemini_api_key", "gemini_free_api_key", "kg_preset"}


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
    assert {"llm_provider", "schema_model", "extract_model", "mlflow_experiment", "data_dir"} <= set(values)
    assert (REPO / values["data_dir"]).is_dir()


@pytest.mark.parametrize("name", ["smoke", "dev"])
def test_cheap_presets_read_a_subset_outside_data(name):
    # every stage reads its data directory recursively, so a subset inside data/ would be read twice
    data_dir = Path(load_preset(REPO_PRESETS, name, SETTING_NAMES)["data_dir"])
    assert data_dir.parts[0] == "samples"


def read(path: Path, column: str) -> set[str]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row[column] for row in csv.DictReader(f)}


@pytest.mark.parametrize("subset", ["smoke", "dev"])
def test_every_key_in_a_subset_points_at_a_row_of_the_same_subset(subset):
    # a subset with dangling keys would make the build and the link checks fail for a reason that has
    # nothing to do with the code under test
    d = REPO / "samples" / subset
    assert read(d / "assemblies.csv", "product_id") <= read(d / "products.csv", "product_id")
    assert read(d / "components.csv", "assembly_id") <= read(d / "assemblies.csv", "assembly_id")
    assert read(d / "part_supplier_mapping.csv", "part_id") <= read(d / "components.csv", "part_id")
    assert read(d / "part_supplier_mapping.csv", "supplier_id") <= read(d / "suppliers.csv", "supplier_id")


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


def test_the_price_table_is_read_and_a_malformed_one_is_an_error(tmp_path):
    prices = write(tmp_path / "prices.yaml", "checked: 2026-09-22\nmodels:\n  m: {input: 0.3, output: 2.5}\n")
    assert read_prices(prices)["m"].output == 2.5
    assert read_prices(tmp_path / "missing.yaml") is None  # no table: runs log no cost
    with pytest.raises(ConfigurationError, match="malformed"):
        read_prices(write(tmp_path / "bad.yaml", "models:\n  m: {input: cheap}\n"))


def test_every_model_a_committed_preset_uses_has_a_price():
    # otherwise its runs would silently log no cost_usd
    prices = read_prices(REPO / "prices.yaml")
    models = {
        values[key]
        for values in read_presets(REPO_PRESETS).values()
        for key in ("schema_model", "extract_model")
        if key in values
    }
    assert models <= set(prices), sorted(models - set(prices))
