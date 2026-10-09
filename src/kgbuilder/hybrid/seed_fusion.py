"""Start-node lists made one (R130): a question's dense and lexical card lists fused into one list of seeds.

Role in the pipeline: `kg seed-grid` (pipeline/seed_stages.py) fuses R128's saved card lists with every
setting of plan R130-R136's grid; the reranker (seed_rerank.py) orders the pool a `rerank` setting fuses;
R132 seeds live with the chosen setting.
Design: a `SeedSetting` names how a question's two lists become one. Each list is read only `candidates` deep,
so a setting says how far down it trusts a list. `rrf` scores by rank alone (fusion.py: a cosine and a BM25
score cannot be compared); `interleave` takes the lists in turns, dense first, the no-score reference; a
`rerank` setting fuses by RRF and keeps the best `pool` nodes for the reranker to order. Every node once.
Not here: the reranker (seed_rerank.py), the grid and its scores (validation/seed_grid.py).
"""

from collections.abc import Sequence
from itertools import zip_longest
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fusion import rrf

Method = Literal["rrf", "interleave", "rerank"]
Representation = Literal["template", "summary"]


class SeedSetting(BaseModel):
    """One way to make a question's dense and lexical card lists one list of start nodes."""

    model_config = ConfigDict(frozen=True)

    name: str
    representation: Representation  # whose cards' lists are fused: A (template) or B (summary)
    method: Method
    candidates: int = Field(ge=1)  # how deep each list is read
    rrf_k: int | None = Field(default=None, ge=0)  # the fusion constant of `rrf`, and of a `rerank` pool
    pool: int | None = Field(default=None, ge=1)  # `rerank`: the fused nodes the reranker orders

    @model_validator(mode="after")
    def _complete(self) -> "SeedSetting":
        """A fusing method needs its constant, and only a rerank has a pool."""
        if self.method != "interleave" and self.rrf_k is None:
            raise ValueError(f"{self.name}: {self.method} fuses by RRF and needs rrf_k")
        if (self.method == "rerank") != (self.pool is not None):
            raise ValueError(f"{self.name}: a pool belongs to a rerank setting, and only to it")
        return self


def fuse(dense: Sequence[str], lexical: Sequence[str], setting: SeedSetting) -> list[str]:
    """The setting's one list from a question's two, best first; for a `rerank` setting, its pool in RRF
    order (the order the reranker reads, and keeps for any node it leaves out)."""
    lists = {"dense": list(dense)[: setting.candidates], "lexical": list(lexical)[: setting.candidates]}
    if setting.method == "interleave":
        return interleave(list(lists.values()))
    fused = rrf(lists, setting.rrf_k or 0)
    return fused[: setting.pool] if setting.pool else fused


def interleave(lists: Sequence[Sequence[str]]) -> list[str]:
    """The lists' items in turns (first of each list, then second, ...), each item once."""
    turns = (item for row in zip_longest(*lists) for item in row if item is not None)
    return list(dict.fromkeys(turns))
