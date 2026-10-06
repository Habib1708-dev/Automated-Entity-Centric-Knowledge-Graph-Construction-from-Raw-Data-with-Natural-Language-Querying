"""The anchor-graph evaluation (R90): the graph judged as an index, over a build's offline snapshot.

Role in the pipeline: after a build, never during one (docs/direction/2026-10-06_anchor-graph). It reads
R87's snapshot (`audit/`), offers the navigation contract W1-W5 in two arms (navigation.py), and places the
target gold of R89 on the snapshot's nodes (targets.py).
Must not: call an LLM, read or write Neo4j, or change what the pipeline builds.
"""

from .navigation import AnchorGraph, Arm, Context, Found, ThingEdge
from .targets import PlacedTarget, TargetPlacer, read_staged, record_nodes

__all__ = [
    "AnchorGraph",
    "Arm",
    "Context",
    "Found",
    "PlacedTarget",
    "TargetPlacer",
    "ThingEdge",
    "read_staged",
    "record_nodes",
]
