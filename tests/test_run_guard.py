"""The permission gate for comprehensive runs (.claude/hooks/run_guard.py): which shell commands make
Claude Code ask the user first. Pure string and file logic: no Neo4j, no network, no LLM."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARD_FILE = REPO / ".claude" / "hooks" / "run_guard.py"

_spec = importlib.util.spec_from_file_location("run_guard", GUARD_FILE)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

FLAGS = {"smoke": False, "dev": False, "quality": True}


@pytest.mark.parametrize(
    "command",
    [
        'uv run kg --preset quality run --goal "root cause"',
        "uv run kg --preset=quality extract",
        '$env:KG_PRESET="quality"; uv run kg text-schema --goal g',
        "KG_PRESET=quality uv run kg resolve",
        "uv run kg reset && uv run kg --preset quality run --goal g",
        "uv run kg --preset dev run data/ --goal g",  # cheap models, but the whole dataset
        "uv run kg plan data --goal g",
        # question answering calls the reader model (R71)
        "uv run kg --preset quality qa tests/gold/qa/furniture_qa.json",
        'uv run kg --preset quality ask "Which parts crack?"',
        # arm C of the anchor evaluation embeds chunks and questions (R92)
        "uv run kg --preset quality anchor-compare out/r77d_furniture --data data",
        # the replay asks the record chooser only with --choose (R95b)
        "uv run kg --preset quality audit-relink out/r77d_furniture --data data --logged l.json --choose",
        # and the individuals' adjudicator (and the embedder) with --join, faithful or not (R98)
        "uv run kg --preset quality audit-relink out/r77d_furniture --data data --logged l.json --join",
        "uv run kg --preset quality audit-relink out/b --data data --logged l.json --join --faithful",
    ],
)
def test_comprehensive_runs_ask(command):
    assert guard.reason_to_ask(command, FLAGS, default_preset="dev")


@pytest.mark.parametrize(
    "command",
    [
        "uv run kg --preset smoke run --goal g",
        "uv run kg --preset dev run samples/dev --goal g",
        "uv run kg --preset quality profile",  # no LLM call
        "uv run kg --preset quality qa-score gold.json out/answers_graph.jsonl",  # scores a file
        "uv run kg --preset quality anchor-eval out/r77d_furniture --data data",  # reads files only
        # the replay without --choose is offline code
        "uv run kg --preset quality audit-relink out/r77d_furniture --data data --logged l.json",
        "uv run kg reset",
        "uv run pytest -q",
        "git status",
    ],
)
def test_cheap_or_llm_free_commands_do_not_ask(command):
    assert guard.reason_to_ask(command, FLAGS, default_preset="dev") == ""


def test_without_a_preset_in_the_command_the_dotenv_preset_decides():
    assert guard.reason_to_ask("uv run kg extract", FLAGS, default_preset="quality")
    assert guard.reason_to_ask("uv run kg extract", FLAGS, default_preset="dev") == ""


def test_the_flags_and_the_default_preset_come_from_the_project_files(tmp_path):
    (tmp_path / "presets.yaml").write_text("dev: {}\nquality:\n  ask_permission: true\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=x\nKG_PRESET=quality\n", encoding="utf-8")
    assert guard.preset_flags(tmp_path) == {"dev": False, "quality": True}
    assert guard.dotenv_preset(tmp_path) == "quality"
    assert guard.preset_flags(tmp_path / "missing") == {} and guard.dotenv_preset(tmp_path / "missing") == ""


def test_the_committed_full_dataset_presets_ask_and_the_cheap_ones_do_not():
    assert guard.preset_flags(REPO) == {
        "smoke": False,
        "dev": False,
        "quality": True,
        "heldout": True,
        # the DeepSeek presets inherit ask_permission from quality through the YAML merge key (R69)
        "quality_deepseek": True,
        "heldout_deepseek": True,
        "generality": True,  # the synthetic corpus of R70, built and questioned with DeepSeek (R71)
        "generality_gemini": True,  # the same corpus built on Gemini (R73)
    }


def test_the_hook_answers_ask_in_the_format_claude_code_reads():
    call = {"tool_name": "Bash", "tool_input": {"command": "uv run kg --preset quality run --goal g"}}
    out = subprocess.run(
        [sys.executable, str(GUARD_FILE)], input=json.dumps(call), capture_output=True, text=True, check=True
    ).stdout
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "ask"


def test_the_hook_is_silent_when_nothing_needs_asking():
    call = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    result = subprocess.run(
        [sys.executable, str(GUARD_FILE)], input=json.dumps(call), capture_output=True, text=True, check=True
    )
    assert result.stdout == ""  # no output: Claude Code applies its normal permission rules
