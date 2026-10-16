# Figures

The thesis figures, each drawn by code from a committed record and explained in its folder's `README.md`.
Rebuild all of them with `uv run python -m figures.scripts.build`. The rules for adding a figure are in
[CLAUDE.md](CLAUDE.md).

## Results (charts of measured numbers)

### Construction: [r229_build_accuracy](results/construction/r229_build_accuracy/README.md), the build accuracy of the five benchmarked graphs

| Figure | Shows |
|---|---|
| [r229_fig1_claim_precision_by_dataset](results/construction/r229_build_accuracy/r229_fig1_claim_precision_by_dataset.png) | strict and content precision of every stored claim per dataset, content 95 % or more on all five (dots, Wilson intervals) |
| [r229_fig2_claim_recall_by_dataset](results/construction/r229_build_accuracy/r229_fig2_claim_recall_by_dataset.png) | a reader's claims stored, missed with a fact type, missed without one, 11/73 to 42/72 stored (stacked bars) |
| [r229_fig3_mention_recall_and_precision](results/construction/r229_build_accuracy/r229_fig3_mention_recall_and_precision.png) | mention recall by exact name and judged, and the pass's judged precision (dots, Wilson intervals) |
| [r229_fig4_precision_against_recall](results/construction/r229_build_accuracy/r229_fig4_precision_against_recall.png) | content precision against claim recall per dataset (scatter, Wilson intervals on both axes) |
| [r229_fig5_audit_by_dataset](results/construction/r229_build_accuracy/r229_fig5_audit_by_dataset.png) | C3, C4, C6 judged rates with their pass or fail; lighthouses fails C4 34/36 and so C3 (dots, the 95 % bound) |

### Retrieval: [r128_techniques](results/retrieval/r128_techniques/README.md), each technique alone, K = 5 to 50

| Figure | Shows |
|---|---|
| [r128_fig1_evidence_vs_start_nodes](results/retrieval/r128_techniques/r128_fig1_evidence_vs_start_nodes.png) | each technique's evidence against start nodes at K = 5 and 20 (scatter, Wilson intervals) |
| [r128_fig2_recall_by_k](results/retrieval/r128_techniques/r128_fig2_recall_by_k.png) | recall against K per family, the others in gray (small multiples) |
| [r128_fig3_per_dataset_heatmaps](results/retrieval/r128_techniques/r128_fig3_per_dataset_heatmaps.png) | every technique x K on each dataset (heatmaps with counts) |
| [r128_fig4_paired_card_vs_text](results/retrieval/r128_techniques/r128_fig4_paired_card_vs_text.png) | questions only cards or only text get right, with McNemar p (diverging bars) |
| [r128_fig5_short_lists](results/retrieval/r128_techniques/r128_fig5_short_lists.png) | the share of lists shorter than K (lines) |

### Retrieval: [r131_seed_grid](results/retrieval/r131_seed_grid/README.md), seeding by fused card lists, K = 5 to 50

| Figure | Shows |
|---|---|
| [r131_fig1_recall_by_k](results/retrieval/r131_seed_grid/r131_fig1_recall_by_k.png) | Seed Recall against K for the chosen fusion, the reranker and each card list alone (small multiples) |
| [r131_fig2_paired_decision](results/retrieval/r131_seed_grid/r131_fig2_paired_decision.png) | fusion against one card list at K = 15, reranker against its pool at K = 5 and 10 (diverging bars) |

### Evaluation: [r157_benchmark](results/evaluation/r157_benchmark/README.md), the frozen agent on 250 questions written before any output

| Figure | Shows |
|---|---|
| [r157_fig1_accuracy_by_dataset](results/evaluation/r157_benchmark/r157_fig1_accuracy_by_dataset.png) | exact match and by meaning per dataset and pooled, 239/250 and 243/250 (dots, Wilson intervals) |
| [r157_fig2_accuracy_by_type](results/evaluation/r157_benchmark/r157_fig2_accuracy_by_type.png) | both scores per question type, aggregation and negation-sensitive hardest, 42/46 and 33/35 by meaning (dots, Wilson intervals) |
| [r157_fig3_failures_by_cause](results/evaluation/r157_benchmark/r157_fig3_failures_by_cause.png) | the 11 wrong answers by cause, with their ids, exact-only misses apart (bars) |

### Evaluation: [r173_confirmation](results/evaluation/r173_confirmation/README.md), the agent measured three times on unseen questions (R157, R166, R173)

| Figure | Shows |
|---|---|
| [r173_fig1_accuracy_by_dataset_three_measurements](results/evaluation/r173_confirmation/r173_fig1_accuracy_by_dataset_three_measurements.png) | both scores per dataset and pooled for the three measurements, 243/250, 94/100, 98/100 by meaning (dots, Wilson intervals, one panel each) |
| [r173_fig2_accuracy_by_type_three_measurements](results/evaluation/r173_confirmation/r173_fig2_accuracy_by_type_three_measurements.png) | both scores per question type across the three, aggregation 42/46, 14/19, 17/18 by meaning (dots, Wilson intervals) |
| [r173_fig3_failures_by_cause_three_measurements](results/evaluation/r173_confirmation/r173_fig3_failures_by_cause_three_measurements.png) | the 11, 6 and 2 wrong answers by cause, with their ids (bars, one scale) |
| [r173_fig4_cost_per_run](results/evaluation/r173_confirmation/r173_fig4_cost_per_run.png) | what each agent run cost and the cents a question, the wasted command apart (bars) |

## Diagrams (how methods work)

### Retrieval: [r126_r128](diagrams/retrieval/r126_r128/README.md)

| Diagram | Shows |
|---|---|
| [r126_diag1_seed_derivation](diagrams/retrieval/r126_r128/r126_diag1_seed_derivation.png) | how chunk and claim retrieval name start nodes |
| [r128_diag2_experiment_design](diagrams/retrieval/r126_r128/r128_diag2_experiment_design.png) | the R128 experiment end to end |

### Retrieval: [r130_r131](diagrams/retrieval/r130_r131/README.md)

| Diagram | Shows |
|---|---|
| [r131_diag1_seeding_decision](diagrams/retrieval/r130_r131/r131_diag1_seeding_decision.png) | the chosen seeding path and the reranker left out |

### Evaluation: [r152_r157](diagrams/evaluation/r152_r157/README.md)

| Diagram | Shows |
|---|---|
| [r157_diag1_benchmark_protocol](diagrams/evaluation/r152_r157/r157_diag1_benchmark_protocol.png) | the benchmark from datasets and gold to the two scores and the outcomes record |

### Evaluation: [r160_r173](diagrams/evaluation/r160_r173/README.md)

| Diagram | Shows |
|---|---|
| [r173_diag1_fix_and_confirm_protocol](diagrams/evaluation/r160_r173/r173_diag1_fix_and_confirm_protocol.png) | measure, fix, measure again: three blind question sets, the engine fixed between them from its traced failures |
| [r173_diag2_agent_loop](diagrams/evaluation/r160_r173/r173_diag2_agent_loop.png) | the agent loop: one typed call per turn, checked and run by code, observed, until the answer (ReAct in shape) |
