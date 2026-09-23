"""The CLI as a user sees it: output, and expected failures as a message with exit code 1 (no traceback).
Uses a temporary MLflow store and no API key; never touches Neo4j."""

import pytest
from typer.testing import CliRunner

from kgbuilder.cli import app, gemini_key
from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Environment variables are the CLI's real configuration channel, so that is what the test sets."""
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    # the developer's .env may choose a preset or Ollama; these tests expect plain Gemini without a key
    monkeypatch.setenv("KG_PRESET", "")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_KEY", "paid")


def test_profile_prints_tables_and_foreign_keys(data_dir, tmp_path):
    result = runner.invoke(app, ["profile", str(data_dir), "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert "products.csv: 3 rows" in result.output
    assert "assemblies.csv.product_id -> products.csv.product_id [100%]" in result.output
    assert (tmp_path / "out" / "profile.json").exists()


def test_build_without_a_plan_explains_what_to_run_first(data_dir, tmp_path):
    result = runner.invoke(app, ["build", str(data_dir), "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "run `kg plan` first" in result.output and "Traceback" not in result.output


def test_plan_without_an_api_key_is_a_clear_error(data_dir, tmp_path):
    result = runner.invoke(app, ["plan", str(data_dir), "--goal", "g", "--out", str(tmp_path / "out")])
    assert result.exit_code == 1 and "GEMINI_API_KEY" in result.output


def test_an_unknown_preset_is_a_clear_error(data_dir, tmp_path):
    result = runner.invoke(
        app, ["--preset", "prod", "profile", str(data_dir), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == 1
    assert "unknown preset 'prod'" in result.output and "Traceback" not in result.output


def test_a_preset_chooses_the_experiment_and_tags_every_run(data_dir, tmp_path):
    import mlflow

    result = runner.invoke(app, ["--preset", "dev", "profile", str(data_dir), "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    runs = mlflow.search_runs(experiment_names=["kgbuilder-dev"])  # the dev preset's experiment
    assert list(runs["tags.preset"]) == ["dev"]


def test_without_a_directory_a_command_reads_the_data_dir_setting(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(data_dir))  # what a preset sets through its data_dir key
    result = runner.invoke(app, ["profile", "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert "products.csv: 3 rows" in result.output


def test_the_free_key_is_used_only_when_chosen():
    keys = {"gemini_api_key": "paid-key", "gemini_free_api_key": "free-key"}
    assert gemini_key(Settings(gemini_key="paid", **keys)) == "paid-key"
    assert gemini_key(Settings(gemini_key="free", **keys)) == "free-key"


def test_a_missing_free_key_is_an_error_not_a_fallback_to_the_paid_key():
    # falling back would bill a run that was meant to be free
    settings = Settings(gemini_key="free", gemini_api_key="paid-key", gemini_free_api_key="")
    with pytest.raises(ConfigurationError, match="GEMINI_FREE_API_KEY"):
        gemini_key(settings)


def test_a_missing_free_key_is_reported_by_the_cli(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_KEY", "free")
    monkeypatch.setenv("GEMINI_FREE_API_KEY", "")
    result = runner.invoke(app, ["plan", str(data_dir), "--goal", "g", "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "GEMINI_FREE_API_KEY" in result.output and "Traceback" not in result.output


def test_resolve_refuses_preview_and_undo_together(tmp_path):
    # checked before any connection is opened: the two flags contradict each other
    result = runner.invoke(app, ["resolve", "--preview", "--undo", "--out", str(tmp_path / "out")])
    assert result.exit_code != 0 and "exclude each other" in result.output
