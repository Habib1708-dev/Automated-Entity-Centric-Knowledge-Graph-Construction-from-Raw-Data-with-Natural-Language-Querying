# R126-R128: how the technique experiment works

Diagrams of the method of plan R126-R128 (`REFACTOR_PLAN.md`, "Plan R126-R128", "R126.", "R128."). Its
results are charted in [results/retrieval/r128_techniques](../../../results/retrieval/r128_techniques/README.md).

Rebuild: `uv run python -m figures.scripts.build`.

---

## Diagram 1: How chunk and claim retrieval name start nodes (R126)

Files: [r126_diag1_seed_derivation.svg](r126_diag1_seed_derivation.svg) · [.png](r126_diag1_seed_derivation.png)

- **What it shows:** the rule R126 added so that chunk and claim retrieval can be scored on start nodes (seeds)
  as the node cards are.
  - **A chunk** gives the records it is about (its own ABOUT link, else its document's), then the nodes its
    mentions refer to, in the order their names first occur in its text.
  - **A claim** gives its subject's entity, its object's entity, then the things it is attached to.
  - Either way, the ranked items are read best first and each node is kept once.
- **How to read it:** left to right, from the ranked list a technique returns to the seed list it is scored
  on. The blue box is the technique's own output (text retrieval is blue in every figure); the gray boxes are
  the rule.
- **Worked example:** the hand-made test graph of `tests/graphs.py` (invented words).
  - The chunk "The spindle wobbles." gives Press:P1 (its document is about the press), then Part:S1
    ("spindle", at character 4), then k-wobble ("wobbles", at 12).
  - The claim "spindle HAS_CONDITION wobbles", attached to the press, gives Part:S1, k-wobble, Press:P1.
  - `test_r126_the_store_reads_what_a_chunk_concerns_and_what_a_claim_joins` (`tests/test_query_graph.py`)
    checks these nodes against Neo4j.
- **Source:** `src/kgbuilder/query/graph_store.py` (`chunk_nodes`, `claim_nodes`) and
  `src/kgbuilder/hybrid/seeds.py` (`chunk_order`, `ChunkSeeds`, `ClaimSeeds`).
- **Caveats:** the rule was fixed before any number and was not tuned. Another order (for example, chunks
  taking turns) could rank chunk seeds differently.

## Diagram 2: The R128 experiment: each retrieval technique alone, at five budgets

Files: [r128_diag2_experiment_design.svg](r128_diag2_experiment_design.svg) ·
[.png](r128_diag2_experiment_design.png)

- **What it shows:** the experiment end to end.
  1. Three frozen builds are reloaded from the cache: furniture 70 chunks and 737 nodes, held-out 81 and 629,
     generality 32 and 332.
  2. Their retrieval units are written into Neo4j: chunks, claim sentences (685, 568, 216), template cards
     and LLM summaries.
  3. Nine techniques each give one ranked list, 50 deep.
  4. Code scores the lists at K = 5, 10, 15, 20, 50: evidence against the gold chunks (99 questions), start
     nodes against the R89 targets (213 targets).
  5. `kg retrieve-table` reports them per dataset and pooled, with the pre-registered best against its
     runner-up, and the exploratory card-against-text pairings.
- **How to read it:** left to right. The technique boxes carry their family colour, as in the result charts.
- **Source:** `tests/gold/r128/runs.json` (commands, run ids, the reload checks), `REFACTOR_PLAN.md` "R128.".
- **Caveats:** no reader and no judge; the scores are exact counts. Cost: $0 logged, because every LLM answer
  came from the cache; the embeddings cost about $0.02.
