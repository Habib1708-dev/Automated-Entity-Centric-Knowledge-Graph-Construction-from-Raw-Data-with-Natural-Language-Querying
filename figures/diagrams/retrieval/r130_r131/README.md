# Plan R130-R131: the seeding layer of the agentic query engine

## Diag. 1: The seeding decision: RRF over 25 + 25 template cards, 15 seeds, no separate reranker

**Files:** [r131_diag1_seeding_decision.svg](r131_diag1_seeding_decision.svg), [.png](r131_diag1_seeding_decision.png)

**What it shows:** how the agent will find its start nodes (decision R131b): the question searches the
template cards twice (top 25 by vector, top 25 by words), RRF k = 60 fuses the two lists by rank alone, and
the agent is given the top 15 nodes and reads their cards. The dashed branch is the LLM reranker, measured in
R131 and left out of the path.

**How to read it:** orange boxes are node cards (the family hue), gray boxes are the question and the agent,
the dashed box is the road not taken.

**Takeaway:** the chosen path finds 203/213 targets at K = 15 (pooled, Seed Recall@K, exact match), for free.
The reranker's gain is at K = 5 only (189/213 against 171/213), and the agent, itself a model reading the
cards, already reorders them, so a separate call would repeat its judgement.

**Source:** `tests/gold/r131/seed_grid.json` and `seed_choice.json` (MLflow `12db9da4`); the reranker's cost
is that run's `cost_usd` (0.530) over its 282 calls.

**Caveats:** the decision holds while the agent can read 15 cards; if R135-R136 show it cannot, the reranker
is the measured alternative.
