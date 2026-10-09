# R128: each retrieval technique alone, K = 5 to 50

Charts of the experiment of plan R126-R128 (`REFACTOR_PLAN.md`, "R128."). Nine retrieval techniques, each asked
alone for one ranked list 50 deep, on three datasets (furniture, held-out, generality):
- **Evidence:** the gold chunks found in the top K.
- **Start nodes:** the R89 target nodes found in the top K seeds.

Every score is an exact match computed by code. No reader and no judge are involved.

**Source of every number:** `tests/gold/r128/retrieve_table.json` (`kg retrieve-table`, MLflow `2f3ff6d4`); the
exploratory pairings come from `tests/gold/r128/explore_*.json` (MLflow `6e5155a0`, `891bccb2`, `2eb479ab`). The
27 retrieval runs behind them are listed in `tests/gold/r128/runs.json`.

**n:**

| | Evidence (questions with gold chunks, gold chunks) | Start nodes (questions with targets, targets) |
|---|---|---|
| Furniture | 33, 73 | 50, 86 |
| Held-out | 28, 36 | 51, 65 |
| Generality | 38, 51 | 40, 62 |
| Pooled | 99, 160 | 141, 213 |

**Encoding (all charts):**
- Colour is the family: text retrieval is blue, node cards are orange, the name linker is aqua.
- Shape is what is searched: circle chunk, triangle claim, square template card (A), diamond LLM summary (B),
  pentagon name linker.
- Fill is the mode: filled dense (by vector), hollow lexical (by words, BM25).

Rebuild: `uv run python -m figures.scripts.build`.

---

## Figure 1: Text retrieval finds evidence; node cards find start nodes

Files: [r128_fig1_evidence_vs_start_nodes.svg](r128_fig1_evidence_vs_start_nodes.svg) ·
[.png](r128_fig1_evidence_vs_start_nodes.png)

- **What it shows:** each technique as one point. x is evidence (Complete@K, the questions with all their gold
  chunks in the top K, of 99). y is start nodes (Seed Recall@K, the targets with a node in the top K seeds, of
  213). Two panels: K = 5 (the budget the reader reads) and K = 20. Pooled over the three datasets.
- **How to read it:** right is better at evidence, up is better at start nodes. The bars are 95 % Wilson
  intervals, the range another sample of the same size could land in. Points whose bars overlap a lot are
  not shown to differ.
- **Takeaway:**
  - At K = 5 the techniques split by kind. Chunks sit right and low: evidence 81-86/99, start nodes
    102-112/213. The cards and the name linker sit high: start nodes 147-164/213 and 160/213. The cards sit
    further left: evidence 73-79/99. Claims sit in between: 74-82/99 and 147-151/213.
  - At K = 20 every technique moves to the upper right (evidence 87-93/99, start nodes 171-201/213), and the
    template cards stay highest (195 and 201/213).
- **Caveats:**
  - Claims lexical and LLM summaries (B) dense have identical counts at K = 5 (74/99, 147/213); their markers
    are drawn 0.7 points apart so both are visible.
  - The figure shows rates, not paired tests. Figure 4 tests the differences between the kinds.

## Figure 2: How each technique's recall grows with the budget K

Files: [r128_fig2_recall_by_k.svg](r128_fig2_recall_by_k.svg) · [.png](r128_fig2_recall_by_k.png)

- **What it shows:** recall against K = 5, 10, 15, 20, 50, pooled. The top row is Complete@K (of 99
  questions), the bottom row Seed Recall@K (of 213 targets). One panel per family; its techniques are
  coloured, and the other eight are drawn in gray for context.
- **How to read it:** K is on a log scale, because 5 to 50 spans a factor of ten. Compare a coloured line
  with the gray band to see where the family stands among all techniques.
- **Takeaway:**
  - Evidence saturates early: by K = 20 every technique but claims lexical (87/99) completes 87-93/99.
  - Start nodes separate the families. Chunks rise from 102-112/213 at K = 5 to 188-200 at K = 50. Claims
    flatten after K = 10 (171/171 at K = 20, 178/175 at K = 50). The name linker stops at 191 from K = 20 on.
    The template cards keep rising to 201/208.
- **Caveats:** the corpora are small (70, 81 and 32 chunks), so at K = 50 a chunk list covers most of a
  corpus and evidence there is close to trivially high.

## Figure 3: Each technique on each dataset, at every budget

Files: [r128_fig3_per_dataset_heatmaps.svg](r128_fig3_per_dataset_heatmaps.svg) ·
[.png](r128_fig3_per_dataset_heatmaps.png)

- **What it shows:** one heatmap per dataset and measure. Rows are techniques, columns are K. The colour is
  the rate, with one blue scale per row of panels (evidence 60-100 %, start nodes 30-100 %, each with its
  colour bar). The number in each cell is the count, out of the n in the panel title.
- **How to read it:** darker is higher. Compare along a row for the effect of K, and down a column for the
  techniques at one K.
- **Takeaway:**
  - The gap between chunks and cards on start nodes holds on every dataset, and is widest on furniture: at
    K = 5, chunks 34 and 33 of 86 against template cards 68 and 64.
  - On held-out every technique completes at least 20 of 28 questions at K = 5.
  - On generality (32 chunks), chunks dense, chunks lexical and claims dense complete all 38 questions from
    K = 15; claims lexical stops at 35.
- **Caveats:** furniture is the tuning set of the LLM summaries' prompt and length cap (R124a). Held-out and
  generality decide.

## Figure 4: Where card and text techniques disagree, question by question

Files: [r128_fig4_paired_card_vs_text.svg](r128_fig4_paired_card_vs_text.svg) ·
[.png](r128_fig4_paired_card_vs_text.png)

- **What it shows:** five pooled paired comparisons, each at every K:
  - three on start nodes (every target found): template cards against chunks, lexical and dense, and
    template cards against claims;
  - two on evidence (every gold chunk in the top K): chunks against template cards.
- **How to read it:** each pair of bars counts the questions only one of the two techniques gets fully right.
  Orange to the right means only the card technique; blue to the left means only the text technique.
  Questions both or neither get right are left out, as the McNemar test leaves them out. p is the exact
  McNemar test; bold means p < 0.00125, which survives a Bonferroni correction over the 40 exploratory tests.
- **Takeaway:**
  - **Start nodes: the cards win clearly.** Template cards (lexical) against chunks (lexical) at K = 5: 58
    against 14 (p < 0.001). The cards stay ahead up to K = 20 (25 against 4). Against claims: 33 against 18
    at K = 5 (p = 0.049, not significant after correction), then p ≤ 0.001 from K = 10.
  - **Evidence: chunks lead only at K = 5,** and not beyond chance: 10 against 3 (p = 0.092) and 12 against
    4 (p = 0.077). From K = 10 the pairs are level.
- **Caveats:** these pairings were chosen after the table was seen, so they are exploratory. The
  pre-registered test (best technique against its runner-up) never reached p < 0.25.

## Figure 5: Why claims and the name linker level off: their lists run out

Files: [r128_fig5_short_lists.svg](r128_fig5_short_lists.svg) · [.png](r128_fig5_short_lists.png)

- **What it shows:** the share of lists shorter than K, pooled. On the left, chunk lists (of the 99
  questions with evidence); on the right, seed lists (of the 141 questions with targets). Claims and the name
  linker are coloured, the other techniques gray.
- **How to read it:** a list shorter than K is scored on what it holds. A line near 100 % means the
  technique cannot fill that budget.
- **Takeaway:**
  - Claims dense asks for K claims, but they share chunks: its chunk list is short at K = 20 on 56 of 99
    questions, and at K = 50 on all 99.
  - The name linker gives few seeds: its seed list is short at K = 10 on 113 of 141 questions, and at
    K = 20 on 140. That is why it stops at 191/213 in figure 2.
- **Caveats:** at K = 50 most chunk lists are short for every technique, because the corpora hold 32
  (generality), 70 (furniture) and 81 (held-out) chunks.
