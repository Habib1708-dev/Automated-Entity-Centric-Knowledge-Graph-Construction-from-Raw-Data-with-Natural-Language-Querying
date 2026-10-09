# R131: the seed grid, how the agent finds its start nodes

Charts of step R131 (plan R130-R136): every pre-registered way to fuse a question's dense and lexical
template-card lists, and an LLM reranker on top, scored on start nodes. They support the seeding decision
recorded as R131b in `REFACTOR_PLAN.md`: RRF k = 60 over 25 + 25 candidates, 15 seeds, no separate reranker.

## Fig. 1: Fusing dense and lexical cards finds more start nodes; 15 seeds reach 203 of 213 targets

**Files:** [r131_fig1_recall_by_k.svg](r131_fig1_recall_by_k.svg), [.png](r131_fig1_recall_by_k.png)

**What it shows:** Seed Recall@K (targets with a node among the top K start nodes; exact match against the
R89 targets, computed by code) at K = 5, 10, 15, 20, 25, 30 and 50, pooled (213 targets over 141 questions)
and on each dataset (furniture 86, held-out 65, generality 62 targets).

**How to read it:** one line per setting; K on a log axis. Orange squares are template cards: hollow the
lexical list alone, filled the dense list alone, half-filled a fusion of both. The thick orange line is the
chosen setting (RRF k = 60, 25 + 25), the dark line the reranker over the same pool, gray every other
setting the rule saw. The dotted line marks the chosen K = 15.

**Takeaway:** pooled, the chosen fusion finds 203/213 targets at K = 15, against 194/213 for the lexical
cards alone and 191/213 for the dense cards alone. The reranker leads only at small K (189/213 against
171/213 at K = 5; 198/213 against 194/213 at K = 10) and is level from K = 15 (204/213 against 203/213).

**Source:** `tests/gold/r131/seed_grid.json` (MLflow `12db9da4`, `kg seed-grid --rerank`, $0.530).

**Caveats:** the lists are R128's, read once; dense ranks move by about 2-3 targets between builds. The rule
chose at K = 20, where four furniture settings find all 86 targets, so the choice there is the grid's tie
order. The targets come from the same model family as the judge.

## Fig. 2: Fusion beats one card list at K = 15; the reranker helps only at K = 5

**Files:** [r131_fig2_paired_decision.svg](r131_fig2_paired_decision.svg), [.png](r131_fig2_paired_decision.png)

**What it shows:** paired comparisons, question by question (Seeds found@K: every target of a question among
its top K). Left: the chosen fusion against the lexical cards alone at K = 15 (pre-registered, step 4 of the
rule). Right: the reranker against its own pool, which is the chosen fusion, at K = 5 and 10 (exploratory).

**How to read it:** each bar counts the questions only one side gets fully right; questions both or neither
get right are left out. Orange is the chosen fusion in both panels, light gray the lexical cards alone, dark
gray the reranker. p is the exact McNemar test, bold below 0.05.

**Takeaway:** at K = 15 the fusion fully finds 11 questions the lexical cards miss and misses 2 they find
(pooled, p = 0.022). The reranker fully finds 22 questions its pool misses at K = 5 and misses 5 (pooled,
p = 0.0015), but at K = 10 only 9 / 5 (p = 0.424).

**Source:** `tests/gold/r131/seed_choice.json` (left) and `tests/gold/r131/explore_rerank_vs_pool.json`
(right), both from MLflow `12db9da4`.

**Caveats:** the right panel is exploratory: the pairing was chosen after the table was seen. Per dataset the
counts are small; held-out and generality decide nothing alone.
