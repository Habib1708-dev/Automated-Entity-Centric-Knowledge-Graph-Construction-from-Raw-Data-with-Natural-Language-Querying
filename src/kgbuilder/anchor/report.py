"""The anchor-graph evaluation of one build in one arm (R90 part b): every code-computed criterion and its
MLflow metrics.

Role in the pipeline: built by `kg anchor-eval` (pipeline/anchor_stages.py) from a snapshot, its fidelity
gate and provenance checks (R87, C0 and C1), the placed targets and the QA gold; written to `out/` as the
run's artifact, so a later step can pair two arms (or an arm and vector retrieval) question by question.
Design: one pydantic model holding the criteria's results as they are, and `metrics()` flattening them to
stable names prefixed by the criterion (`c2_hit_at_1`, `c5_gold_start_recall_at_5`). A rate without a
denominator logs nothing rather than a made-up 0.
Not here: computing the criteria (criteria.py) or comparing arms.
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from ..audit.snapshot import GraphSnapshot
from ..validation.interval import Proportion
from ..validation.qa_gold import QAGold
from .criteria import (
    Connectivity,
    EvidenceReach,
    Findability,
    Selectivity,
    Size,
    connectivity,
    evidence_reach,
    findability,
    selectivity,
    size,
)
from .navigation import AnchorGraph, Arm
from .targets import PlacedTarget

START_MODES = ("gold_start", "end_to_end")


class AnchorReport(BaseModel):
    arm: Arm
    fidelity_passed: bool  # C0 (hard): the snapshot is the build's graph
    provenance: dict[str, Proportion]  # C1 (hard): every text-made item has its chunk
    placed: dict[str, list[PlacedTarget]]
    findability: Findability  # C2
    reach: dict[str, EvidenceReach]  # C5, by start mode
    selectivity: Selectivity  # C7
    connectivity: Connectivity  # C8
    size: Size  # C9

    def metrics(self) -> dict[str, float]:
        out: dict[str, float] = {"c0_fidelity_passed": float(self.fidelity_passed)}
        rates = [p.rate for p in self.provenance.values() if p.rate is not None]
        if rates:
            out["c1_provenance_min"] = min(rates)
        out |= {
            "c2_targets": float(len(self.findability.targets)),
            "c2_unplaced": float(self.findability.unplaced),
            "c2_loose": float(sum(t.loose for ts in self.placed.values() for t in ts)),
        }
        out |= _rates({f"c2_hit_at_{k}": p for k, p in self.findability.hit_at.items()})
        for mode, r in self.reach.items():
            out |= {
                f"c5_{mode}_questions": float(len(r.questions)),
                f"c5_{mode}_gold_chunks": float(r.reached.n),
                f"c5_{mode}_no_start": float(len(r.no_start)),
            }
            out |= _rates({f"c5_{mode}_recall_at_{k}": p for k, p in r.recall_at.items()})
            out |= _rates({f"c5_{mode}_complete_at_{k}": p for k, p in r.complete_at.items()})
            out |= _rates({f"c5_{mode}_reached": r.reached})
        s, c, z = self.selectivity, self.connectivity, self.size
        out |= {"c7_nodes": float(s.nodes), "c7_hubs": float(len(s.hubs))}
        out |= {
            k: v
            for k, v in (("c7_median_share", s.median_share), ("c7_p90_share", s.p90_share))
            if v is not None
        }
        out |= {
            "c8_questions": float(len(c.questions)),
            "c8_gold_connections": float(c.connections.n),
            "c8_thing_hops": float(c.thing_hops),
            "c8_unwitnessed_hops": float(c.unwitnessed_hops),
        }
        out |= _rates({"c8_connections": c.connections})
        out |= {"c9_nodes_per_chunk": z.nodes_per_chunk, "c9_edges_per_chunk": z.edges_per_chunk}
        out |= {
            k: v
            for k, v in (("c9_build_cost_usd", z.cost_usd), ("c9_build_tokens", z.tokens))
            if v is not None
        }
        return out


def _rates(named: Mapping[str, Proportion]) -> dict[str, float]:
    return {name: p.rate for name, p in named.items() if p.rate is not None}


def evaluate(
    s: GraphSnapshot,
    arm: Arm,
    qa: QAGold,
    placed: Mapping[str, Sequence[PlacedTarget]],
    record_nodes: Mapping[str, Sequence[str]],
    *,
    budgets: Sequence[int],
    hub_share: float,
    usage: Mapping[str, float],
    fidelity_passed: bool,
    provenance: Mapping[str, Proportion],
) -> AnchorReport:
    """Every code-computed criterion of `s` in `arm`. `record_nodes` maps a question id to the record nodes
    of its record evidence (for C8); `usage` is the build's logged `<stage>.<metric>` usage (for C9)."""
    graph = AnchorGraph(s, arm)
    return AnchorReport(
        arm=arm,
        fidelity_passed=fidelity_passed,
        provenance=dict(provenance),
        placed={qid: list(ts) for qid, ts in placed.items()},
        findability=findability(graph, placed),
        reach={mode: evidence_reach(graph, qa, placed, budgets, mode) for mode in START_MODES},
        selectivity=selectivity(graph, placed, hub_share),
        connectivity=connectivity(graph, qa, placed, record_nodes),
        size=size(s, arm, usage),
    )
