"""What every offline stage on a finished build shares: its params and its gated snapshot.

Role in the pipeline: the stages that read a finished build's `out/` folder instead of a graph (`kg
mention-eval` and `kg mention-pass --from-build` in mention_stages.py, `kg claim-eval` in claim_stages.py)
rebuild the build's graph offline and refuse it unless R87's fidelity gate (C0) says it is the graph the build
wrote.
Design: two functions over the pipeline state, so each stage logs the same build params and applies the same
gate. Not here: what a stage does with the snapshot, and the snapshot itself (audit/snapshot.py).
"""

from pathlib import Path

from ..audit import GraphSnapshot, build_snapshot, check_fidelity, load_logged
from ..core.errors import EvaluationError
from ..text.mention_pass import PassFinding
from .inputs import digest, input_file

# the build's judge sheet, when its eval run wrote one: the fidelity gate compares attachments fact by fact
_SHEET = "judge_sheet.json"


def build_params(ctx, state) -> dict[str, object]:
    """The build a stage rebuilds offline, the dataset and the logged counts that gate it, and the chunker
    (chunk ids depend on it)."""
    logged = input_file(state.need("audit_logged", "pass the build's logged counts"), "logged counts")
    s = ctx.settings
    return {
        "build": input_file(state.need("audit_source", "pass the build's out/ folder"), "build folder"),
        "data_dir": input_file(state.need("data_dir", "pass the dataset folder"), "data folder"),
        "logged": logged,
        "logged_hash": digest(logged),
        "chunk_max_chars": s.chunk_max_chars,
        "chunk_min_chars": s.chunk_min_chars,
        "chunk_overlap_chars": s.chunk_overlap_chars,
    }


def gated_snapshot(ctx, state, findings: list[PassFinding] | None = None) -> GraphSnapshot:
    """The build's graph rebuilt offline, refused unless it is the graph the build wrote (R87's C0 gate).
    With `findings` it is then rebuilt again with those in place of the build's own pass (an empty list: the
    graph as the pass found it): the gate proves everything before the pass, the findings are the caller's.

    Raises EvaluationError when the gate fails."""
    source, s = Path(state.audit_source), ctx.settings
    chunking = (s.chunk_max_chars, s.chunk_min_chars, s.chunk_overlap_chars)
    snapshot = build_snapshot(source, Path(state.data_dir), chunking)
    sheet = source / _SHEET
    if not check_fidelity(
        snapshot, load_logged(Path(state.audit_logged)), sheet if sheet.exists() else None
    ).passed:
        raise EvaluationError(["the snapshot is not the build's graph (C0 failed)"])
    return snapshot if findings is None else build_snapshot(source, Path(state.data_dir), chunking, findings)
