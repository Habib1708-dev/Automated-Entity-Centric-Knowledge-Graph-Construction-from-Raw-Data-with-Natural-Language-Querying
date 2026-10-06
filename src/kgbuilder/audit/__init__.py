"""The graph-correctness audit (R87): rebuild a finished build's graph offline and check it.

Role in the pipeline: after a build, never during one. It reads a run's `out/` folder (triples.jsonl,
resolve.json, plan, text schema, staged tables) and the dataset, rebuilds the graph as data (the snapshot)
with the build's own pure functions, proves the rebuild faithful against the counts the build logged
(fidelity), and runs the checks code can decide alone (provenance, cross-scope links, compound names,
label mismatches, splits, reach). The judge's semantic verdicts come in later parts of R87.
Must not: write to Neo4j, call an LLM, or change what the pipeline builds; a check that needs meaning is
left to the judge, never guessed here.
"""

from .checks import CodeChecks, run_checks
from .fidelity import FidelityReport, LoggedCounts, check_fidelity, load_logged
from .reach import ReachReport, compute_reach, gold_pairs
from .snapshot import GraphSnapshot, build_snapshot

__all__ = [
    "CodeChecks",
    "FidelityReport",
    "GraphSnapshot",
    "LoggedCounts",
    "ReachReport",
    "build_snapshot",
    "check_fidelity",
    "compute_reach",
    "gold_pairs",
    "load_logged",
    "run_checks",
]
