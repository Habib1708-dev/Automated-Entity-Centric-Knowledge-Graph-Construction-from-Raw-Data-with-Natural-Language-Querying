"""Claude Code PreToolUse hook: ask the user before any comprehensive kgbuilder run.

Role: enforces the rule "a comprehensive test runs only with the user's permission" (CLAUDE.md, run-policy
skill), so it does not depend on the assistant remembering it. Claude Code runs this before every Bash and
PowerShell command and passes the tool call as JSON on stdin.
Decision: a command is a comprehensive run when it starts a `kg` command that calls an LLM and either the
preset it uses has `ask_permission: true` in presets.yaml, or it names a data directory outside samples/
(the whole dataset, whatever the models). Then the hook answers "ask" and Claude Code shows a permission
prompt, in every permission mode. Otherwise it prints nothing and the normal permission rules apply.
Not here: anything that changes the command, and any rule for a person typing `kg` in a terminal.
"""

import json
import re
import shlex
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# kg commands that call an LLM, so a run of them costs money or free quota (`ask` and `qa` answer
# questions with the reader model, R71; `anchor-compare` embeds chunks and questions, R92; `qa-score`,
# `qa-compare` and `anchor-eval` only read files and are not listed; `mention-pass` lists each chunk's
# things, R101; `retrieve-eval` embeds the questions and graph_retrieval's node names, R117, while
# `retrieve-compare` only reads its reports; `index` embeds the retrieval units, R119, `units` writes them
# without a model)
LLM_COMMANDS = {
    "run",
    "plan",
    "text-schema",
    "extract",
    "mention-pass",
    "resolve",
    "ask",
    "qa",
    "anchor-compare",
    "retrieve-eval",
    "index",
}
# kg commands that call an LLM only with a flag: `audit-relink --choose` asks the record chooser (R95b),
# `--join` the individuals' adjudicator and the embedder (R98); without them the replay is offline code
FLAG_LLM_COMMANDS = {"audit-relink": {"--choose", "--join"}}
# kg options that take a value: the token after them is not the data directory
VALUE_OPTIONS = {"--goal", "--out", "--gold", "--preset"}
# a shell separates commands with these; each part is judged on its own
_SEPARATORS = re.compile(r"&&|\|\||[;|\n]")
# `KG_PRESET=x`, `$env:KG_PRESET="x"`, `set KG_PRESET=x`: a preset chosen in the command itself
_PRESET_VARIABLE = re.compile(r"KG_PRESET\s*=\s*[\"']?([\w-]+)")


def preset_flags(root: Path) -> dict[str, bool]:
    """`ask_permission` of every preset in presets.yaml; empty when the file is missing or unreadable."""
    try:
        presets = yaml.safe_load((root / "presets.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {name: bool((values or {}).get("ask_permission")) for name, values in presets.items()}


def dotenv_preset(root: Path) -> str:
    """The KG_PRESET line of .env, the preset a command uses when it names none."""
    try:
        text = (root / ".env").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"^\s*KG_PRESET\s*=\s*[\"']?([\w-]*)", text, re.MULTILINE)
    return match.group(1) if match else ""


def _kg_arguments(part: str) -> list[str] | None:
    """The arguments after `kg` in one shell command, or None when the part does not run kg."""
    try:
        tokens = shlex.split(part, posix=True)
    except ValueError:  # an unbalanced quote: fall back to plain splitting rather than miss a run
        tokens = part.split()
    for i, token in enumerate(tokens):
        if token == "kg" or token.endswith(("/kg", "\\kg", "kg.exe")):
            return tokens[i + 1 :]
    return None


def _parse(args: list[str]) -> tuple[str, str, str]:
    """(preset from --preset, subcommand, first positional argument after it) of kg's arguments."""
    preset = command = positional = ""
    skip = False
    for i, token in enumerate(args):
        if skip:
            skip = False
            continue
        if token.startswith("--preset="):
            preset = token.split("=", 1)[1]
        elif token in VALUE_OPTIONS:
            if token == "--preset" and i + 1 < len(args):
                preset = args[i + 1]
            skip = True
        elif token.startswith("-"):
            continue
        elif not command:
            command = token
        elif not positional:
            positional = token
    return preset, command, positional


def reason_to_ask(command_line: str, flags: dict[str, bool], default_preset: str) -> str:
    """Why this command needs the user's permission, or "" when it does not."""
    variable = _PRESET_VARIABLE.search(command_line)
    for part in _SEPARATORS.split(command_line):
        args = _kg_arguments(part)
        if args is None:
            continue
        preset, command, data_dir = _parse(args)
        if command not in LLM_COMMANDS and not FLAG_LLM_COMMANDS.get(command, set()) & set(args):
            continue
        preset = preset or (variable.group(1) if variable else "") or default_preset
        if flags.get(preset):
            return f"`kg {command}` with the '{preset}' preset is a comprehensive run (ask_permission)"
        if command in {"run", "plan"} and data_dir and Path(data_dir).parts[:1] != ("samples",):
            return f"`kg {command} {data_dir}` reads a dataset outside samples/, a comprehensive run"
    return ""


def main() -> None:
    call = json.load(sys.stdin)
    command_line = (call.get("tool_input") or {}).get("command", "")
    reason = reason_to_ask(command_line, preset_flags(ROOT), dotenv_preset(ROOT))
    if reason:
        decision = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"{reason}: it needs the user's permission (run-policy).",
        }
        print(json.dumps({"hookSpecificOutput": decision}))


if __name__ == "__main__":
    main()
