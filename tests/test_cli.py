"""The CLI as a user sees it: output, and expected failures as a message with exit code 1 (no traceback).
Uses a temporary MLflow store and no API key; never touches Neo4j."""

import json

import pytest
from typer.testing import CliRunner

from kgbuilder.cli import app, gemini_key
from kgbuilder.config import Settings
from kgbuilder.core.errors import ConfigurationError
from kgbuilder.text.schema import TextSchema
from kgbuilder.validation.coverage_sheet import CoverageSheet, SheetSentence

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


def test_coverage_prints_the_estimate_and_a_bad_verdict_file_is_a_message(tmp_path):
    sentence = SheetSentence(
        id="s1", doc_id="a.md", chunk_id="a.md#0", text="It broke.", chunk_text="It broke.", context="",
        things=[], observations=[],
    )  # fmt: skip
    sheet = CoverageSheet(
        seed=68, population=1, text_schema=TextSchema(entity_types=[], fact_types=[]), sentences=[sentence]
    )
    sheet_file, verdicts_file = tmp_path / "coverage_sheet.json", tmp_path / "verdicts.json"
    sheet_file.write_text(sheet.model_dump_json(), encoding="utf-8")
    missed = {"claim": "it broke", "polarity": "negative", "about": None, "reason": "the schema has no type"}
    verdicts = {
        "judge": {"model": "claude-fable-5-1", "date": "2026-09-28"},
        "sheet": str(sheet_file),
        "sentences": [{"id": "s1", "claims": [{**missed, "cause": "no_schema_type"}]}],
    }
    verdicts_file.write_text(json.dumps(verdicts), encoding="utf-8")
    command = ["coverage", str(sheet_file), str(verdicts_file), "--out", str(tmp_path / "out")]

    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert "missed_no_schema_type          1" in result.output and "coverage_low" in result.output

    verdicts["sentences"][0]["claims"] = [missed]  # neither covered nor given a cause
    verdicts_file.write_text(json.dumps(verdicts), encoding="utf-8")
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert "exactly one" in result.output and "Traceback" not in result.output
