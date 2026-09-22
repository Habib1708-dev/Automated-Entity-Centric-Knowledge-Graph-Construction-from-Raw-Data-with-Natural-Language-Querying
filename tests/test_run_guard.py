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


def test_the_committed_quality_preset_asks_and_the_cheap_ones_do_not():
    assert guard.preset_flags(REPO) == {"smoke": False, "dev": False, "quality": True}


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
